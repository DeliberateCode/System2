"""Manifest read/validation, schema + anchor-map loaders, path containment,
content-file collection, and the prompt-injection scan.

Relocated verbatim from ``composer.py`` (the frozen oracle): ``ValidationResult``,
``validate_manifest`` + the ``_validate_*`` sub-validators, ``_read_manifest``,
``_load_schema``, ``_load_anchor_map``, ``_check_path_containment``, the
content-collection helpers, ``_scan_for_injection`` and ``_INJECTION_PATTERNS``.
Only the ``hook_security`` import is adjusted to the vendored ``ir/_hook_security``;
the four public entry points are exposed under the names in
``spec/interfaces.json`` (``read_manifest`` / ``load_schema`` / ``load_anchor_map``
/ ``scan_for_injection``).

All manifest and content-file text is treated as untrusted data: it is read,
validated, and scanned for injection patterns, but never executed. There is no
``eval``/``exec``/``__import__`` or other dynamic execution of overlay-supplied
content in this module.
"""

import os
import re
from typing import Any, Dict, List, Optional

from ._hook_security import check_hook_security

SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0.0"})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class ValidationResult:
    """Container for manifest validation outcomes."""

    __slots__ = ("valid", "errors", "warnings")

    def __init__(self) -> None:
        self.valid: bool = True
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def add_error(self, msg: str) -> None:
        self.valid = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


# ---------------------------------------------------------------------------
# Schema / anchor-map loading
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    import json
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_schema(base_path: str) -> dict:
    return _load_json(os.path.join(base_path, "schemas", "overlay.schema.json"))


def load_anchor_map(base_path: str) -> dict:
    return _load_json(os.path.join(base_path, "schemas", "anchor-map.json"))


# ---------------------------------------------------------------------------
# Manifest reading
# ---------------------------------------------------------------------------

def read_manifest(overlay_path: str) -> dict:
    """Read and parse ``system2.overlay.json`` from *overlay_path*.

    Raises ``FileNotFoundError`` or ``json.JSONDecodeError`` on failure.
    """
    import json
    manifest_file = os.path.join(overlay_path, "system2.overlay.json")
    with open(manifest_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Path containment
# ---------------------------------------------------------------------------

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SAFETY_HOOK_FILENAMES = frozenset({
    "sensitive-file-protector.py",
    "dangerous-command-blocker.py",
})
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$")


def _check_path_containment(
    file_path: str, overlay_path: str, result: ValidationResult, field_label: str
) -> bool:
    """Reject absolute paths, ``..`` traversal, and symlinks escaping the overlay dir.

    Returns True if the path is safe, False otherwise (with errors added to *result*).
    """
    if os.path.isabs(file_path):
        result.add_error(
            f"{field_label}: absolute path rejected: {file_path}"
        )
        return False

    if ".." in file_path.split("/") or ".." in file_path.split(os.sep):
        result.add_error(
            f"{field_label}: path traversal rejected: {file_path}"
        )
        return False

    full = os.path.join(overlay_path, file_path)
    try:
        resolved = os.path.realpath(full)
    except OSError:
        result.add_error(
            f"{field_label}: cannot resolve path: {file_path}"
        )
        return False

    overlay_real = os.path.realpath(overlay_path)
    if not resolved.startswith(overlay_real + os.sep) and resolved != overlay_real:
        result.add_error(
            f"{field_label}: symlink resolves outside overlay directory: {file_path}"
        )
        return False

    return True


def _check_content_file(
    file_path: str, overlay_path: str, result: ValidationResult, field_label: str
) -> None:
    """Validate a content_file reference: containment + existence."""
    if not _check_path_containment(file_path, overlay_path, result, field_label):
        return
    full = os.path.join(overlay_path, file_path)
    if not os.path.isfile(full):
        result.add_error(
            f"{field_label}: content_file not found: {file_path}"
        )


# ---------------------------------------------------------------------------
# Type / value helpers
# ---------------------------------------------------------------------------

def _expect_type(
    value: Any,
    expected: str,
    field_label: str,
    result: ValidationResult,
) -> bool:
    """Check *value* is of *expected* JSON type. Returns True if OK."""
    type_map = {
        "string": str,
        "boolean": bool,
        "array": list,
        "object": dict,
        "number": (int, float),
    }
    py_type = type_map.get(expected)
    if py_type is None:
        return True
    # In Python, bool is a subclass of int. Guard against that for "number".
    if expected == "number" and isinstance(value, bool):
        result.add_error(f"{field_label}: expected {expected}, got boolean")
        return False
    if expected == "string" and isinstance(value, bool):
        result.add_error(f"{field_label}: expected string, got boolean")
        return False
    if not isinstance(value, py_type):
        actual = type(value).__name__
        result.add_error(f"{field_label}: expected {expected}, got {actual}")
        return False
    return True


def _expect_enum(
    value: Any,
    allowed: list,
    field_label: str,
    result: ValidationResult,
) -> bool:
    if value not in allowed:
        result.add_error(
            f"{field_label}: invalid value {value!r}, must be one of {allowed}"
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def validate_manifest(
    manifest: dict,
    schema: dict,
    overlay_path: str,
    anchor_map: dict,
) -> ValidationResult:
    """Validate *manifest* structure, content file references, and hook security.

    Args:
        manifest: Parsed overlay manifest dict.
        schema: Parsed overlay.schema.json (used for _meta lookups).
        overlay_path: Filesystem path to the overlay directory root.
        anchor_map: Parsed anchor-map.json.

    Returns:
        A ``ValidationResult`` with errors and warnings populated.
    """
    result = ValidationResult()

    meta = schema.get("_meta", {})
    valid_agents = set(meta.get("valid_pipeline_agents", []))
    valid_spec_artifacts = set(meta.get("valid_spec_artifacts", []))
    valid_gate_numbers = set(meta.get("valid_gate_numbers", []))

    # Anchor map is the single authoritative source for valid anchors.
    # Do NOT merge with schema _meta — removed anchors must not linger.
    valid_anchors_by_agent: Dict[str, List[str]] = {}
    anchor_map_agents = anchor_map.get("agents", {})
    for agent_name, agent_info in anchor_map_agents.items():
        valid_anchors_by_agent[agent_name] = list(
            agent_info.get("anchors", {}).keys()
        )

    # --- Top-level required fields ----------------------------------------

    for field in ("name", "version", "description", "schema_version", "contributions"):
        if field not in manifest:
            result.add_error(f"missing required top-level field: {field}")

    # If we are missing required fields we cannot validate further.
    if not result.valid:
        return result

    # --- Top-level field types and constraints ----------------------------

    if not _expect_type(manifest["name"], "string", "name", result):
        pass
    elif not _KEBAB_RE.match(manifest["name"]):
        result.add_error(
            f"name: must be kebab-case, got {manifest['name']!r}"
        )

    if _expect_type(manifest["version"], "string", "version", result):
        if not _SEMVER_RE.match(manifest["version"]):
            result.add_error(
                f"version: must be semver (e.g., 1.0.0), got {manifest['version']!r}"
            )
    # Block unsupported schema versions by default; --allow-newer-schema opts out.
    if _expect_type(manifest["schema_version"], "string", "schema_version", result):
        if manifest["schema_version"] not in SUPPORTED_SCHEMA_VERSIONS:
            result.add_error(
                f"schema_version: {manifest['schema_version']!r} is not "
                f"supported; supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}. "
                f"Use --allow-newer-schema to attempt degraded composition "
                f"(unknown contribution types will be skipped)"
            )

    if "description" in manifest:
        _expect_type(manifest["description"], "string", "description", result)

    if "tags" in manifest:
        if _expect_type(manifest["tags"], "array", "tags", result):
            for idx, tag in enumerate(manifest["tags"]):
                _expect_type(tag, "string", f"tags[{idx}]", result)

    # --- compatibility ----------------------------------------------------

    if "compatibility" in manifest:
        compat = manifest["compatibility"]
        if _expect_type(compat, "object", "compatibility", result):
            for arr_key in (
                "known_conflicts",
                "tested_with",
                "review_when_combined_with_tags",
            ):
                if arr_key in compat:
                    label = f"compatibility.{arr_key}"
                    if _expect_type(compat[arr_key], "array", label, result):
                        for idx, item in enumerate(compat[arr_key]):
                            _expect_type(item, "string", f"{label}[{idx}]", result)

    # --- contributions ----------------------------------------------------

    contribs = manifest["contributions"]
    if not _expect_type(contribs, "object", "contributions", result):
        return result

    known_contribution_keys = {
        "orchestrator", "delegation", "agents", "spec",
        "auxiliary_agents", "mcp_servers", "permissions",
    }
    unknown_keys = set(contribs.keys()) - known_contribution_keys
    schema_version = manifest.get("schema_version", "")
    for uk in sorted(unknown_keys):
        if schema_version in SUPPORTED_SCHEMA_VERSIONS:
            result.add_error(
                f"contributions.{uk}: unknown contribution type for "
                f"schema_version {schema_version!r}; check for typos"
            )
        else:
            result.add_warning(
                f"contributions.{uk}: unknown contribution type (ignored); "
                f"this may be from a newer overlay schema version"
            )

    # -- orchestrator --
    if "orchestrator" in contribs:
        orch = contribs["orchestrator"]
        if _expect_type(orch, "object", "contributions.orchestrator", result):
            _validate_orchestrator(orch, overlay_path, valid_gate_numbers, result)

    # -- delegation --
    if "delegation" in contribs:
        deleg = contribs["delegation"]
        if _expect_type(deleg, "object", "contributions.delegation", result):
            _validate_delegation(deleg, result)

    # -- agents --
    if "agents" in contribs:
        agents_block = contribs["agents"]
        if _expect_type(agents_block, "object", "contributions.agents", result):
            _validate_agents(
                agents_block, overlay_path, valid_agents,
                valid_anchors_by_agent, result,
            )

    # -- spec --
    if "spec" in contribs:
        spec_block = contribs["spec"]
        if _expect_type(spec_block, "object", "contributions.spec", result):
            _validate_spec(spec_block, valid_spec_artifacts, result)

    # -- auxiliary_agents --
    if "auxiliary_agents" in contribs:
        _validate_auxiliary_agents(
            contribs["auxiliary_agents"], overlay_path, valid_agents, result,
        )

    # -- mcp_servers --
    if "mcp_servers" in contribs:
        _validate_mcp_servers(contribs["mcp_servers"], result)

    # -- permissions --
    if "permissions" in contribs:
        _validate_permissions(contribs["permissions"], result)

    # --- Contribution ID uniqueness within this overlay --------------------
    # Only collect IDs from known contribution keys to avoid rejecting
    # valid forward-compatible overlays with unknown subtrees.
    known_contribs = {
        k: v for k, v in contribs.items()
        if k in known_contribution_keys
    }
    all_ids: List[str] = []
    _collect_ids(known_contribs, all_ids)
    seen_ids: Dict[str, int] = {}
    for cid in all_ids:
        seen_ids[cid] = seen_ids.get(cid, 0) + 1
    for cid, count in seen_ids.items():
        if count > 1:
            result.add_error(
                f"duplicate contribution ID {cid!r} appears {count} times "
                f"within this overlay; IDs must be unique"
            )

    return result


# ---------------------------------------------------------------------------
# Sub-validators
# ---------------------------------------------------------------------------

def _validate_orchestrator(
    orch: dict,
    overlay_path: str,
    valid_gate_numbers: set,
    result: ValidationResult,
) -> None:
    known_orch_keys = {"principles", "gates"}
    for uk in sorted(set(orch.keys()) - known_orch_keys):
        result.add_error(
            f"contributions.orchestrator.{uk}: unknown key; "
            f"valid keys: {sorted(known_orch_keys)}"
        )

    # principles
    if "principles" in orch:
        label_base = "contributions.orchestrator.principles"
        if _expect_type(orch["principles"], "array", label_base, result):
            for idx, entry in enumerate(orch["principles"]):
                label = f"{label_base}[{idx}]"
                if not _expect_type(entry, "object", label, result):
                    continue
                _validate_has_fields(entry, ["id", "content_file"], label, result)
                if "id" in entry:
                    _expect_type(entry["id"], "string", f"{label}.id", result)
                if "content_file" in entry:
                    if _expect_type(entry["content_file"], "string", f"{label}.content_file", result):
                        _check_content_file(
                            entry["content_file"], overlay_path, result,
                            f"{label}.content_file",
                        )
                if "after" in entry and entry["after"] is not None:
                    _expect_type(entry["after"], "string", f"{label}.after", result)

    # gates
    if "gates" in orch:
        gates = orch["gates"]
        gates_label = "contributions.orchestrator.gates"
        if _expect_type(gates, "object", gates_label, result):
            for gate_num, gate_obj in gates.items():
                if gate_num not in valid_gate_numbers:
                    result.add_error(
                        f"{gates_label}: invalid gate number {gate_num!r}, "
                        f"must be one of {sorted(valid_gate_numbers)}"
                    )
                    continue
                glabel = f"{gates_label}.{gate_num}"
                if not _expect_type(gate_obj, "object", glabel, result):
                    continue
                known_gate_keys = {"consultation"}
                for uk in sorted(set(gate_obj.keys()) - known_gate_keys):
                    result.add_error(
                        f"{glabel}.{uk}: unknown key; valid keys: {sorted(known_gate_keys)}"
                    )
                if "consultation" in gate_obj:
                    cons = gate_obj["consultation"]
                    clabel = f"{glabel}.consultation"
                    if _expect_type(cons, "array", clabel, result):
                        for cidx, centry in enumerate(cons):
                            celabel = f"{clabel}[{cidx}]"
                            if not _expect_type(centry, "object", celabel, result):
                                continue
                            _validate_has_fields(
                                centry,
                                ["id", "content_file", "phase"],
                                celabel,
                                result,
                            )
                            if "id" in centry:
                                _expect_type(centry["id"], "string", f"{celabel}.id", result)
                            if "content_file" in centry:
                                if _expect_type(centry["content_file"], "string", f"{celabel}.content_file", result):
                                    _check_content_file(
                                        centry["content_file"], overlay_path,
                                        result, f"{celabel}.content_file",
                                    )
                            if "phase" in centry:
                                if _expect_type(centry["phase"], "string", f"{celabel}.phase", result):
                                    _expect_enum(
                                        centry["phase"],
                                        ["pre-delegation", "post-completion"],
                                        f"{celabel}.phase",
                                        result,
                                    )
                            if "after" in centry and centry["after"] is not None:
                                _expect_type(centry["after"], "string", f"{celabel}.after", result)


def _validate_delegation(deleg: dict, result: ValidationResult) -> None:
    known_deleg_keys = {"advisory_sources"}
    for uk in sorted(set(deleg.keys()) - known_deleg_keys):
        result.add_error(
            f"contributions.delegation.{uk}: unknown key; "
            f"valid keys: {sorted(known_deleg_keys)}"
        )
    if "advisory_sources" not in deleg:
        return
    label_base = "contributions.delegation.advisory_sources"
    if not _expect_type(deleg["advisory_sources"], "array", label_base, result):
        return
    for idx, entry in enumerate(deleg["advisory_sources"]):
        label = f"{label_base}[{idx}]"
        if not _expect_type(entry, "object", label, result):
            continue
        _validate_has_fields(
            entry, ["id", "name", "description", "resolution"], label, result,
        )
        if "id" in entry:
            _expect_type(entry["id"], "string", f"{label}.id", result)
        if "name" in entry:
            _expect_type(entry["name"], "string", f"{label}.name", result)
        if "description" in entry:
            _expect_type(entry["description"], "string", f"{label}.description", result)
        if "resolution" in entry:
            if _expect_type(entry["resolution"], "string", f"{label}.resolution", result):
                _expect_enum(
                    entry["resolution"],
                    ["orchestrator-relay", "bash-cli", "mcp"],
                    f"{label}.resolution",
                    result,
                )


def _validate_agents(
    agents_block: dict,
    overlay_path: str,
    valid_agents: set,
    valid_anchors_by_agent: Dict[str, List[str]],
    result: ValidationResult,
) -> None:
    for agent_name, agent_obj in agents_block.items():
        agent_label = f"contributions.agents.{agent_name}"
        if agent_name not in valid_agents:
            result.add_error(
                f"{agent_label}: unknown pipeline agent {agent_name!r}"
            )
            continue
        if not _expect_type(agent_obj, "object", agent_label, result):
            continue

        known_agent_keys = {"prompt_sections", "tools", "hooks"}
        for uk in sorted(set(agent_obj.keys()) - known_agent_keys):
            result.add_error(
                f"{agent_label}.{uk}: unknown key; "
                f"valid keys: {sorted(known_agent_keys)}"
            )

        # prompt_sections
        if "prompt_sections" in agent_obj:
            ps = agent_obj["prompt_sections"]
            ps_label = f"{agent_label}.prompt_sections"
            if _expect_type(ps, "object", ps_label, result):
                agent_valid_anchors = set(
                    valid_anchors_by_agent.get(agent_name, [])
                )
                for anchor_name, entries in ps.items():
                    anchor_label = f"{ps_label}.{anchor_name}"
                    if anchor_name not in agent_valid_anchors:
                        affected_ids = [
                            e.get("id", "?") for e in entries
                            if isinstance(e, dict)
                        ] if isinstance(entries, list) else []
                        result.add_warning(
                            f"{anchor_label}: unknown anchor {anchor_name!r} "
                            f"for agent {agent_name!r} (contributions "
                            f"{affected_ids} will be skipped); valid anchors: "
                            f"{sorted(agent_valid_anchors)}"
                        )
                    if not _expect_type(entries, "array", anchor_label, result):
                        continue
                    for eidx, entry in enumerate(entries):
                        elabel = f"{anchor_label}[{eidx}]"
                        if not _expect_type(entry, "object", elabel, result):
                            continue
                        _validate_has_fields(
                            entry, ["id", "content_file"], elabel, result,
                        )
                        if "id" in entry:
                            _expect_type(entry["id"], "string", f"{elabel}.id", result)
                        if "content_file" in entry:
                            if _expect_type(entry["content_file"], "string", f"{elabel}.content_file", result):
                                _check_content_file(
                                    entry["content_file"], overlay_path,
                                    result, f"{elabel}.content_file",
                                )
                        if "after" in entry and entry["after"] is not None:
                            _expect_type(entry["after"], "string", f"{elabel}.after", result)

                        # inline / summary validation
                        inline = entry.get("inline", False)
                        if "inline" in entry:
                            _expect_type(inline, "boolean", f"{elabel}.inline", result)
                        if not inline and "summary" not in entry:
                            result.add_error(
                                f"{elabel}: when inline is false (or omitted), "
                                f"summary is required"
                            )
                        if "summary" in entry:
                            _expect_type(entry["summary"], "string", f"{elabel}.summary", result)

        # tools
        if "tools" in agent_obj:
            tools = agent_obj["tools"]
            tools_label = f"{agent_label}.tools"
            if _expect_type(tools, "array", tools_label, result):
                for tidx, tentry in enumerate(tools):
                    tlabel = f"{tools_label}[{tidx}]"
                    if not _expect_type(tentry, "object", tlabel, result):
                        continue
                    _validate_has_fields(
                        tentry, ["tool_name", "justification"], tlabel, result,
                    )
                    if "tool_name" in tentry:
                        _expect_type(tentry["tool_name"], "string", f"{tlabel}.tool_name", result)
                    if "justification" in tentry:
                        _expect_type(tentry["justification"], "string", f"{tlabel}.justification", result)

        # hooks — validate structure AND run security checks
        if "hooks" in agent_obj:
            hooks = agent_obj["hooks"]
            hooks_label = f"{agent_label}.hooks"
            if _expect_type(hooks, "array", hooks_label, result):
                for hidx, hentry in enumerate(hooks):
                    hlabel = f"{hooks_label}[{hidx}]"
                    if not _expect_type(hentry, "object", hlabel, result):
                        continue
                    _validate_has_fields(
                        hentry, ["event", "command"], hlabel, result,
                    )
                    if "event" in hentry:
                        if _expect_type(hentry["event"], "string", f"{hlabel}.event", result):
                            _expect_enum(
                                hentry["event"],
                                ["PreToolUse", "PostToolUse", "SubagentStop"],
                                f"{hlabel}.event",
                                result,
                            )
                            if hentry["event"] in ("PreToolUse", "PostToolUse"):
                                if "matcher" not in hentry:
                                    result.add_error(
                                        f"{hlabel}: matcher is required for {hentry['event']} hooks"
                                    )
                    if "matcher" in hentry:
                        if _expect_type(hentry["matcher"], "string", f"{hlabel}.matcher", result):
                            try:
                                re.compile(hentry["matcher"])
                            except re.error as exc:
                                result.add_error(
                                    f"{hlabel}.matcher: invalid regex: {exc}"
                                )
                    if "command" in hentry:
                        if _expect_type(hentry["command"], "string", f"{hlabel}.command", result):
                            cmd = hentry["command"]
                            cmd_basename = os.path.basename(cmd)
                            if cmd_basename in _SAFETY_HOOK_FILENAMES:
                                result.add_error(
                                    f"{hlabel}.command: targets safety hook "
                                    f"filename {cmd_basename!r}; overlay hooks "
                                    f"must not collide with base safety hooks"
                                )
                            elif _check_path_containment(cmd, overlay_path, result, f"{hlabel}.command"):
                                hook_full = os.path.join(overlay_path, cmd)
                                if not os.path.isfile(hook_full):
                                    result.add_error(
                                        f"{hlabel}.command: hook file does not exist: {cmd}"
                                    )
                                else:
                                    sec = check_hook_security(hook_full, overlay=True)
                                    if not sec["passed"]:
                                        for violation in sec["violations"]:
                                            result.add_error(
                                                f"{hlabel}: hook security violation: {violation}"
                                            )


def _validate_spec(
    spec_block: dict, valid_spec_artifacts: set, result: ValidationResult
) -> None:
    for artifact_name, artifact_obj in spec_block.items():
        art_label = f"contributions.spec.{artifact_name}"
        if artifact_name not in valid_spec_artifacts:
            result.add_error(
                f"{art_label}: unknown spec artifact {artifact_name!r}, "
                f"valid: {sorted(valid_spec_artifacts)}"
            )
            continue
        if not _expect_type(artifact_obj, "object", art_label, result):
            continue
        known_spec_keys = {"required_sections"}
        for uk in sorted(set(artifact_obj.keys()) - known_spec_keys):
            result.add_error(
                f"{art_label}.{uk}: unknown key; valid keys: {sorted(known_spec_keys)}"
            )
        if "required_sections" in artifact_obj:
            rs = artifact_obj["required_sections"]
            rs_label = f"{art_label}.required_sections"
            if _expect_type(rs, "array", rs_label, result):
                for ridx, rentry in enumerate(rs):
                    rlabel = f"{rs_label}[{ridx}]"
                    if not _expect_type(rentry, "object", rlabel, result):
                        continue
                    _validate_has_fields(
                        rentry,
                        ["id", "section_heading", "description"],
                        rlabel,
                        result,
                    )
                    if "id" in rentry:
                        _expect_type(rentry["id"], "string", f"{rlabel}.id", result)
                    if "section_heading" in rentry:
                        _expect_type(rentry["section_heading"], "string", f"{rlabel}.section_heading", result)
                    if "description" in rentry:
                        _expect_type(rentry["description"], "string", f"{rlabel}.description", result)


def _validate_auxiliary_agents(
    aux_list: Any,
    overlay_path: str,
    valid_agents: set,
    result: ValidationResult,
) -> None:
    label_base = "contributions.auxiliary_agents"
    if not _expect_type(aux_list, "array", label_base, result):
        return
    seen_names: Dict[str, int] = {}
    for idx, entry in enumerate(aux_list):
        label = f"{label_base}[{idx}]"
        if not _expect_type(entry, "object", label, result):
            continue
        _validate_has_fields(
            entry,
            ["name", "role", "pipeline", "delegation_policy", "agent_file"],
            label,
            result,
        )
        if "name" in entry:
            if _expect_type(entry["name"], "string", f"{label}.name", result):
                if not _KEBAB_RE.match(entry["name"]):
                    result.add_error(
                        f"{label}.name: must be kebab-case, got {entry['name']!r}"
                    )
                if entry["name"] in valid_agents:
                    result.add_error(
                        f"{label}.name: collides with pipeline agent name {entry['name']!r}"
                    )
                aname = entry["name"]
                if aname in seen_names:
                    result.add_error(
                        f"{label}.name: duplicate auxiliary agent name {aname!r} "
                        f"within this overlay (first at index {seen_names[aname]})"
                    )
                else:
                    seen_names[aname] = idx
        if "role" in entry:
            _expect_type(entry["role"], "string", f"{label}.role", result)
        if "pipeline" in entry:
            if _expect_type(entry["pipeline"], "boolean", f"{label}.pipeline", result):
                if entry["pipeline"] is not False:
                    result.add_error(
                        f"{label}.pipeline: must be false for auxiliary agents"
                    )
        if "delegation_policy" in entry:
            if _expect_type(entry["delegation_policy"], "string", f"{label}.delegation_policy", result):
                _expect_enum(
                    entry["delegation_policy"],
                    ["orchestrator_optional", "orchestrator_recommended"],
                    f"{label}.delegation_policy",
                    result,
                )
        if "agent_file" in entry:
            if _expect_type(entry["agent_file"], "string", f"{label}.agent_file", result):
                _check_content_file(
                    entry["agent_file"], overlay_path, result,
                    f"{label}.agent_file",
                )
                agent_full = os.path.join(overlay_path, entry["agent_file"])
                if os.path.isfile(agent_full):
                    _validate_auxiliary_agent_file(
                        agent_full, entry, f"{label}.agent_file", result
                    )


def _validate_auxiliary_agent_file(
    file_path: str,
    manifest_entry: dict,
    label: str,
    result: ValidationResult,
) -> None:
    """Validate that an auxiliary agent file has proper YAML frontmatter."""
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        result.add_error(f"{label}: cannot read agent file: {exc}")
        return

    if not content.startswith("---"):
        result.add_error(
            f"{label}: agent file must start with YAML frontmatter (---)"
        )
        return

    parts = content.split("---", 2)
    if len(parts) < 3:
        result.add_error(
            f"{label}: agent file has unclosed YAML frontmatter"
        )
        return

    frontmatter = parts[1].strip()
    required_fields = {"name", "description", "tools"}
    found_fields: dict = {}
    for line in frontmatter.splitlines():
        if ":" in line:
            key = line.split(":", 1)[0].strip()
            val = line.split(":", 1)[1].strip()
            found_fields[key] = val

    for field in required_fields:
        if field not in found_fields:
            result.add_error(
                f"{label}: agent file frontmatter missing required field: {field}"
            )

    # Parse tools list and reject forbidden tools.
    forbidden_tools = {"Task", "TaskCreate", "Agent"}
    declared_tools: List[str] = []

    # Handle inline format: tools: [Read, Bash, Task]
    tools_val = found_fields.get("tools", "")
    if tools_val.startswith("[") and tools_val.endswith("]"):
        for item in tools_val[1:-1].split(","):
            t = item.strip().strip("'\"")
            if t:
                declared_tools.append(t)
    elif tools_val and not tools_val.startswith("["):
        # Scalar format: tools: Task
        declared_tools.append(tools_val)
    if not declared_tools:
        # Handle multi-line format:
        #   tools:
        #     - Read
        #     - Bash
        in_tools = False
        for line in frontmatter.splitlines():
            stripped = line.strip()
            if stripped.startswith("tools:"):
                in_tools = True
                continue
            if in_tools:
                if stripped.startswith("- "):
                    declared_tools.append(stripped[2:].strip())
                elif stripped and not stripped.startswith("#"):
                    in_tools = False

    for tool_name in declared_tools:
        if tool_name in forbidden_tools:
            result.add_error(
                f"{label}: auxiliary agent declares forbidden tool "
                f"{tool_name!r}; auxiliary agents cannot spawn subagents"
            )

    expected_name = manifest_entry.get("name", "")
    if "name" in found_fields and found_fields["name"] != expected_name:
        result.add_error(
            f"{label}: agent file frontmatter name {found_fields['name']!r} "
            f"does not match manifest name {expected_name!r}"
        )


def _validate_mcp_servers(servers: Any, result: ValidationResult) -> None:
    label_base = "contributions.mcp_servers"
    if not _expect_type(servers, "array", label_base, result):
        return
    for idx, entry in enumerate(servers):
        label = f"{label_base}[{idx}]"
        if not _expect_type(entry, "object", label, result):
            continue
        _validate_has_fields(
            entry, ["name", "description", "config", "required_by"], label, result,
        )
        if "name" in entry:
            _expect_type(entry["name"], "string", f"{label}.name", result)
        if "description" in entry:
            _expect_type(entry["description"], "string", f"{label}.description", result)
        if "config" in entry:
            _expect_type(entry["config"], "object", f"{label}.config", result)
        if "required_by" in entry:
            if _expect_type(entry["required_by"], "array", f"{label}.required_by", result):
                for ridx, rval in enumerate(entry["required_by"]):
                    _expect_type(rval, "string", f"{label}.required_by[{ridx}]", result)


def _validate_permissions(perms: Any, result: ValidationResult) -> None:
    label_base = "contributions.permissions"
    if not _expect_type(perms, "array", label_base, result):
        return
    for idx, entry in enumerate(perms):
        label = f"{label_base}[{idx}]"
        if not _expect_type(entry, "object", label, result):
            continue
        _validate_has_fields(
            entry, ["tool", "reason", "required_by"], label, result,
        )
        if "tool" in entry:
            _expect_type(entry["tool"], "string", f"{label}.tool", result)
        if "reason" in entry:
            _expect_type(entry["reason"], "string", f"{label}.reason", result)
        if "required_by" in entry:
            if _expect_type(entry["required_by"], "array", f"{label}.required_by", result):
                for ridx, rval in enumerate(entry["required_by"]):
                    _expect_type(rval, "string", f"{label}.required_by[{ridx}]", result)


def _validate_has_fields(
    obj: dict, required: List[str], label: str, result: ValidationResult
) -> None:
    for field in required:
        if field not in obj:
            result.add_error(f"{label}: missing required field: {field}")


# ---------------------------------------------------------------------------
# Content-file collection
# ---------------------------------------------------------------------------

def _collect_content_files_from_manifest(manifest: dict, out: List[str]) -> None:
    """Collect all content_file and agent_file paths from a manifest."""
    contribs = manifest.get("contributions", {})
    _collect_file_refs(contribs, out)


def _collect_applied_content_files(
    manifest: dict,
    out: List[str],
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> None:
    """Collect content_file/agent_file paths only for applied contributions.

    Mirrors the anchor-filtering logic in ``build_contribution_index`` so
    that injection scanning and fingerprinting operate on the same set of
    contributions that composition will actually apply.
    """
    contribs = manifest.get("contributions", {})

    # Everything except agents.*.prompt_sections is always applied.
    for key in ("orchestrator", "delegation", "spec"):
        if key in contribs:
            _collect_file_refs(contribs[key], out)
    for key in ("auxiliary_agents", "mcp_servers", "permissions"):
        if key in contribs:
            _collect_file_refs(contribs[key], out)

    # agents.*.prompt_sections: skip unknown anchors when filter is provided.
    agents_block = contribs.get("agents", {})
    for agent_name, agent_obj in agents_block.items():
        for section_key in ("tools", "hooks"):
            if section_key in agent_obj:
                _collect_file_refs(agent_obj[section_key], out)
        ps = agent_obj.get("prompt_sections", {})
        for anchor_name, entries in ps.items():
            if valid_anchors_by_agent is not None:
                agent_anchors = set(valid_anchors_by_agent.get(agent_name, []))
                if anchor_name not in agent_anchors:
                    continue
            _collect_file_refs(entries, out)


def _collect_file_refs(obj: Any, out: List[str]) -> None:
    """Recursively collect content_file and agent_file values."""
    if isinstance(obj, dict):
        for key in ("content_file", "agent_file"):
            if key in obj and isinstance(obj[key], str):
                out.append(obj[key])
        for val in obj.values():
            _collect_file_refs(val, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_file_refs(item, out)


def _collect_ids(contribs: dict, out: List[str]) -> None:
    """Recursively collect all contribution IDs from a contributions object."""
    if isinstance(contribs, dict):
        if "id" in contribs and isinstance(contribs["id"], str):
            out.append(contribs["id"])
        for val in contribs.values():
            _collect_ids(val, out)
    elif isinstance(contribs, list):
        for item in contribs:
            _collect_ids(item, out)


# ---------------------------------------------------------------------------
# Prompt-injection scan (untrusted content treated as data, never executed)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    (re.compile(r"modify\s+CLAUDE\.md\s+directly", re.IGNORECASE), "modify CLAUDE.md directly"),
    (re.compile(r"skip\s+security", re.IGNORECASE), "skip security"),
    (re.compile(r"ignore\s+safety\s+rules", re.IGNORECASE), "ignore safety rules"),
    (re.compile(r"escalate\s+privileges", re.IGNORECASE), "escalate privileges"),
    (re.compile(r"bypass\s+hooks", re.IGNORECASE), "bypass hooks"),
    (re.compile(r"bypass\s+allowlists", re.IGNORECASE), "bypass allowlists"),
    (re.compile(r"spawn\s+agents", re.IGNORECASE), "spawn agents"),
    (re.compile(r"modify\s+(the\s+)?delegation\s+map", re.IGNORECASE), "modify delegation map"),
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE), "ignore previous instructions"),
    (re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE), "role override attempt"),
]


def scan_for_injection(content: str, file_label: str) -> List[str]:
    """Scan content for suspected prompt injection patterns.

    Returns a list of warning strings. Empty list means no patterns found.
    """
    warnings = []
    for pattern, description in _INJECTION_PATTERNS:
        match = pattern.search(content)
        if match:
            warnings.append(
                f"{file_label}: suspected prompt injection ({description}): "
                f"...{match.group()}..."
            )
    return warnings


def _resolve_content_file(overlay_path: str, content_file: str) -> str:
    """Read and return content of a referenced file from the overlay directory.

    The caller is responsible for path-containment validation (done during
    the manifest validation phase).
    """
    full = os.path.join(overlay_path, content_file)
    with open(full, "r", encoding="utf-8") as fh:
        return fh.read()
