"""
Overlay Composition Engine

Validates overlay manifests, detects conflicts, and generates composed
artifacts for the System2 pipeline.

Uses only Python 3.8+ stdlib. No external dependencies.

Public API:
    main()                - CLI entry point
    compose()             - full composition pipeline
    validate_manifest()   - manifest validation against schema + anchor map
    detect_conflicts()    - conflict detection across multiple overlays
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

# Import hook_security from the same directory.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from hook_security import check_hook_security  # noqa: E402

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


class ConflictReport:
    """Container for conflict detection outcomes.

    Attributes:
        structural_conflicts: list of dicts — composition must be blocked.
        additive_overlaps: list of dicts — compose with deterministic order.
        semantic_tensions: list of dicts — warning, do not block.
    """

    __slots__ = ("structural_conflicts", "additive_overlaps", "semantic_tensions")

    def __init__(self) -> None:
        self.structural_conflicts: List[dict] = []
        self.additive_overlaps: List[dict] = []
        self.semantic_tensions: List[dict] = []

    @property
    def has_structural_conflicts(self) -> bool:
        return len(self.structural_conflicts) > 0


# ---------------------------------------------------------------------------
# Schema / anchor-map loading
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_schema(base_path: str) -> dict:
    return _load_json(os.path.join(base_path, "schemas", "overlay.schema.json"))


def _load_anchor_map(base_path: str) -> dict:
    return _load_json(os.path.join(base_path, "schemas", "anchor-map.json"))


# ---------------------------------------------------------------------------
# Manifest reading
# ---------------------------------------------------------------------------

def _read_manifest(overlay_path: str) -> dict:
    """Read and parse ``system2.overlay.json`` from *overlay_path*.

    Raises ``FileNotFoundError`` or ``json.JSONDecodeError`` on failure.
    """
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

    Mirrors the anchor-filtering logic in ``_build_contribution_index`` so
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
# Contribution indexing
# ---------------------------------------------------------------------------

def _build_contribution_index(
    manifests: List[dict],
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> dict:
    """Build a map of (type, target) -> [(overlay_name, contribution)].

    Walks all contribution types in each manifest and produces a flat
    index keyed by a tuple of (contribution_type_path, target_key).
    For most additive types the target_key equals the type path.
    For auxiliary_agents the target_key is the agent name (exclusive slot).

    If *valid_anchors_by_agent* is provided, prompt_section contributions
    targeting unknown anchors are silently excluded.
    """
    index: Dict[Tuple[str, str], List[Tuple[str, dict]]] = {}

    for manifest in manifests:
        overlay_name = manifest.get("name", "<unknown>")
        contribs = manifest.get("contributions", {})

        # orchestrator.principles
        orch = contribs.get("orchestrator", {})
        for entry in orch.get("principles", []):
            key = ("orchestrator.principles", "orchestrator.principles")
            index.setdefault(key, []).append((overlay_name, entry))

        # orchestrator.gates.<N>.consultation
        for gate_num, gate_obj in orch.get("gates", {}).items():
            scope = f"orchestrator.gates.{gate_num}.consultation"
            for entry in gate_obj.get("consultation", []):
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))

        # delegation.advisory_sources
        deleg = contribs.get("delegation", {})
        for entry in deleg.get("advisory_sources", []):
            key = ("delegation.advisory_sources", "delegation.advisory_sources")
            index.setdefault(key, []).append((overlay_name, entry))

        # agents.<name>.prompt_sections.<anchor>
        agents_block = contribs.get("agents", {})
        for agent_name, agent_obj in agents_block.items():
            for anchor_name, entries in agent_obj.get("prompt_sections", {}).items():
                if valid_anchors_by_agent is not None:
                    agent_anchors = set(valid_anchors_by_agent.get(agent_name, []))
                    if anchor_name not in agent_anchors:
                        continue
                scope = f"agents.{agent_name}.prompt_sections.{anchor_name}"
                for entry in entries:
                    key = (scope, scope)
                    index.setdefault(key, []).append((overlay_name, entry))
            # agents.<name>.tools
            for entry in agent_obj.get("tools", []):
                scope = f"agents.{agent_name}.tools"
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))
            # agents.<name>.hooks
            for entry in agent_obj.get("hooks", []):
                scope = f"agents.{agent_name}.hooks"
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))

        # spec.<artifact>.required_sections
        spec_block = contribs.get("spec", {})
        for artifact_name, artifact_obj in spec_block.items():
            for entry in artifact_obj.get("required_sections", []):
                scope = f"spec.{artifact_name}.required_sections"
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))

        # auxiliary_agents — keyed by agent name for collision detection
        for entry in contribs.get("auxiliary_agents", []):
            agent_name = entry.get("name", "<unnamed>")
            key = ("auxiliary_agents", agent_name)
            index.setdefault(key, []).append((overlay_name, entry))

        # mcp_servers
        for entry in contribs.get("mcp_servers", []):
            key = ("mcp_servers", "mcp_servers")
            index.setdefault(key, []).append((overlay_name, entry))

        # permissions
        for entry in contribs.get("permissions", []):
            key = ("permissions", "permissions")
            index.setdefault(key, []).append((overlay_name, entry))

    return index


# ---------------------------------------------------------------------------
# Topological sorting within a scope
# ---------------------------------------------------------------------------

def _topological_sort(
    contributions: List[Tuple[str, dict]], scope: str
) -> List[Tuple[str, dict]]:
    """Sort contributions within a scope by after-declarations, then
    overlay name (lexicographic), then contribution ID (lexicographic).

    Raises ``ValueError`` if a cycle is detected in ``after`` declarations.
    """
    # Pre-sort by (overlay_name, id) so id_to_idx resolution is
    # deterministic regardless of CLI argument order.
    def _sort_key(idx: int) -> Tuple[str, str]:
        oname, entry = contributions[idx]
        return (oname, entry.get("id", ""))

    sorted_indices = sorted(range(len(contributions)), key=_sort_key)

    id_to_idx: Dict[str, int] = {}
    unresolved_after: List[str] = []
    for i in sorted_indices:
        oname, entry = contributions[i]
        cid = entry.get("id")
        if cid is not None:
            if cid in id_to_idx:
                prev_oname = contributions[id_to_idx[cid]][0]
                if prev_oname != oname:
                    unresolved_after.append(
                        f"contribution ID {cid!r} in scope {scope!r} appears "
                        f"in overlays {prev_oname!r} and {oname!r}; 'after' "
                        f"references to this ID will resolve to {prev_oname!r}"
                    )
            else:
                id_to_idx[cid] = i

    n = len(contributions)
    # Build adjacency: edges[i] = list of j where j must come after i
    edges: Dict[int, List[int]] = {i: [] for i in range(n)}
    in_degree = [0] * n

    for i, (oname, entry) in enumerate(contributions):
        after = entry.get("after")
        if after is None:
            continue
        if after in id_to_idx:
            dep_idx = id_to_idx[after]
            edges[dep_idx].append(i)
            in_degree[i] += 1
        else:
            cid = entry.get("id", f"index-{i}")
            unresolved_after.append(
                f"{oname}/{cid}: after target {after!r} not found in scope "
                f"{scope!r}; ordering will fall back to lexicographic"
            )

    # Kahn's algorithm with stable tie-breaking by (overlay_name, id)
    def sort_key(idx: int) -> Tuple[str, str]:
        overlay_name, entry = contributions[idx]
        return (overlay_name, entry.get("id", ""))

    # Start with nodes that have no dependencies
    queue = sorted(
        [i for i in range(n) if in_degree[i] == 0],
        key=sort_key,
    )
    result: List[Tuple[str, dict]] = []

    while queue:
        idx = queue.pop(0)
        result.append(contributions[idx])
        for neighbor in sorted(edges[idx], key=sort_key):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                # Insert into queue maintaining sorted order
                queue.append(neighbor)
                queue.sort(key=sort_key)

    if len(result) != n:
        # Cycle detected — find the participating IDs
        cycle_ids = [
            contributions[i][1].get("id", f"<index-{i}>")
            for i in range(n)
            if in_degree[i] > 0
        ]
        raise ValueError(
            f"Cycle detected in after-declarations within scope "
            f"{scope!r}: {cycle_ids}"
        )

    return result, unresolved_after


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

# High-leverage surfaces that trigger semantic tension warnings when
# multiple overlays contribute to them.
_HIGH_LEVERAGE_SURFACES = {
    "orchestrator.principles",
    "spec.requirements.required_sections",
    "spec.design.required_sections",
}

# High-leverage anchor name patterns (wildcard matching against
# agents.*.prompt_sections.<anchor>).
_HIGH_LEVERAGE_ANCHORS = {
    "safety_rules", "constraints", "guidelines",
    "design_constraints", "style_requirements",
    "guardrails", "planning_rules", "review_criteria",
}

# Pipeline agent names for auxiliary agent collision checks.
_PIPELINE_AGENTS = {
    "code-reviewer", "design-architect", "docs-release", "eval-engineer",
    "executor", "mcp-toolsmith", "postmortem-scribe", "repo-governor",
    "requirements-engineer", "security-sentinel", "spec-coordinator",
    "task-planner", "test-engineer",
}


def detect_conflicts(
    manifests: List[dict], anchor_map: dict
) -> ConflictReport:
    """Detect structural conflicts, additive overlaps, and semantic tensions.

    Args:
        manifests: List of parsed, validated overlay manifest dicts.
        anchor_map: Parsed anchor-map.json.

    Returns:
        A ``ConflictReport`` with all three categories populated.
    """
    report = ConflictReport()
    anchors_by_agent = {
        name: list(info.get("anchors", {}).keys())
        for name, info in anchor_map.get("agents", {}).items()
    }
    index = _build_contribution_index(manifests, anchors_by_agent)

    overlay_names = {m.get("name", "<unknown>") for m in manifests}
    overlay_tags: Dict[str, List[str]] = {}
    for m in manifests:
        overlay_tags[m.get("name", "<unknown>")] = m.get("tags", [])

    # --- Structural: known_conflicts declarations -------------------------

    for m in manifests:
        name = m.get("name", "<unknown>")
        compat = m.get("compatibility", {})
        for conflict_name in compat.get("known_conflicts", []):
            if conflict_name in overlay_names:
                report.structural_conflicts.append({
                    "type": "known_conflicts",
                    "message": (
                        f"Overlay {name!r} declares a known conflict with "
                        f"{conflict_name!r}, which is also being composed."
                    ),
                    "overlays": [name, conflict_name],
                    "contribution_type": "compatibility.known_conflicts",
                    "target": conflict_name,
                    "suggested_resolution": (
                        f"Remove {conflict_name!r} from the overlay set, "
                        f"or remove {name!r}'s known_conflicts declaration "
                        f"if the conflict has been resolved."
                    ),
                })

    # --- Structural: auxiliary agent name collision across overlays --------

    for (contrib_type, target), entries in index.items():
        if contrib_type != "auxiliary_agents":
            continue
        # Collect distinct overlay names contributing this agent name
        contributing_overlays = list({e[0] for e in entries})
        if len(contributing_overlays) > 1:
            report.structural_conflicts.append({
                "type": "auxiliary_agent_collision",
                "message": (
                    f"Auxiliary agent {target!r} is declared by multiple "
                    f"overlays: {contributing_overlays}. Each auxiliary "
                    f"agent name must be unique across overlays."
                ),
                "overlays": contributing_overlays,
                "contribution_type": "auxiliary_agents",
                "target": target,
                "suggested_resolution": (
                    f"Rename the auxiliary agent in one of the overlays "
                    f"so names are unique across overlays."
                ),
            })

    # --- Structural: cycles + Additive overlaps + Semantic tensions -------

    for (contrib_type, target), entries in index.items():
        if contrib_type == "auxiliary_agents":
            # Already handled above; not an additive scope.
            continue

        # Determine contributing overlay set
        contributing_overlays = list({e[0] for e in entries})

        # Attempt topological sort — cycles and duplicate IDs become structural conflicts
        try:
            ordered, sort_warnings = _topological_sort(entries, f"{contrib_type}")
            for sw in sort_warnings:
                report.semantic_tensions.append({
                    "type": "unresolved_after",
                    "message": sw,
                    "overlays": contributing_overlays,
                })
        except ValueError as exc:
            report.structural_conflicts.append({
                "type": "ordering_cycle",
                "message": str(exc),
                "overlays": contributing_overlays,
                "contribution_type": contrib_type,
                "target": contrib_type,
                "suggested_resolution": (
                    f"Remove or adjust 'after' declarations in the overlay "
                    f"manifests to break the cycle in scope {contrib_type!r}."
                ),
            })
            continue

        # Record additive overlaps (multiple overlays in same scope)
        if len(contributing_overlays) > 1:
            report.additive_overlaps.append({
                "scope": contrib_type,
                "overlays": sorted(contributing_overlays),
                "order": ordered,
            })

        # Semantic tension: high-leverage surfaces
        if len(contributing_overlays) > 1:
            is_high_leverage = contrib_type in _HIGH_LEVERAGE_SURFACES
            # Check for high-leverage anchor patterns
            if not is_high_leverage and contrib_type.startswith("agents."):
                parts = contrib_type.split(".")
                if (
                    len(parts) == 4
                    and parts[2] == "prompt_sections"
                    and parts[3] in _HIGH_LEVERAGE_ANCHORS
                ):
                    is_high_leverage = True
            if is_high_leverage:
                report.semantic_tensions.append({
                    "type": "high_leverage_surface",
                    "scope": contrib_type,
                    "message": (
                        f"Overlays {sorted(contributing_overlays)} both "
                        f"contribute to {contrib_type} (high-leverage "
                        f"surface). Review for coherence."
                    ),
                    "overlays": sorted(contributing_overlays),
                })

    # --- Semantic tension: shared tags / review_when_combined_with_tags ----

    # Collect review tags declared by each overlay
    review_tags: Dict[str, List[str]] = {}
    for m in manifests:
        name = m.get("name", "<unknown>")
        compat = m.get("compatibility", {})
        rtags = compat.get("review_when_combined_with_tags", [])
        if rtags:
            review_tags[name] = rtags

    # For each overlay that declares review tags, check if any other
    # overlay has a matching tag.
    reported_tag_pairs: set = set()
    for declaring_name, rtags in review_tags.items():
        for other_name, other_tags in overlay_tags.items():
            if other_name == declaring_name:
                continue
            for tag in rtags:
                if tag in other_tags:
                    pair_key = tuple(sorted([declaring_name, other_name]))
                    tag_pair = (pair_key, tag)
                    if tag_pair not in reported_tag_pairs:
                        reported_tag_pairs.add(tag_pair)
                        report.semantic_tensions.append({
                            "type": "shared_review_tag",
                            "tag": tag,
                            "message": (
                                f"Overlays {sorted([declaring_name, other_name])} "
                                f"share tag {tag!r} which is listed in "
                                f"review_when_combined_with_tags. Review "
                                f"their combined behavior."
                            ),
                            "overlays": sorted([declaring_name, other_name]),
                        })

    return report


# ---------------------------------------------------------------------------
# Content resolution and rendering
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


def _scan_for_injection(content: str, file_label: str) -> List[str]:
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


def _render_contribution(
    contribution: dict,
    overlay_name: str,
    overlay_local_path: str,
    contribution_type: str,
) -> str:
    """Render a single contribution for inclusion in composed CLAUDE.md.

    Args:
        contribution: The contribution dict from the manifest.
        overlay_name: Name of the overlay providing this contribution.
        overlay_local_path: The overlay source directory path (for reading
            content files).
        contribution_type: Dotted type path such as
            ``orchestrator.principles`` or
            ``agents.executor.prompt_sections.implementation_discipline``.

    Returns:
        Rendered markdown string (without trailing newline).
    """
    # --- orchestrator.principles: always inline ---
    if contribution_type == "orchestrator.principles":
        content_file = contribution.get("content_file", "")
        content = _resolve_content_file(overlay_local_path, content_file)
        return content.rstrip("\n")

    # --- orchestrator.gates.N.consultation: always inline ---
    if contribution_type.startswith("orchestrator.gates.") and contribution_type.endswith(".consultation"):
        content_file = contribution.get("content_file", "")
        content = _resolve_content_file(overlay_local_path, content_file)
        phase = contribution.get("phase", "pre-delegation")
        cid = contribution.get("id", "")
        return f"[{phase}] ({overlay_name}/{cid}) {content.rstrip(chr(10))}"

    # --- delegation.advisory_sources: always inline (metadata) ---
    if contribution_type == "delegation.advisory_sources":
        name = contribution.get("name", "")
        desc = contribution.get("description", "")
        resolution = contribution.get("resolution", "")
        return f"- **{name}**: {desc} (resolution: {resolution})"

    # --- agents.*.prompt_sections.*: summary+pointer or inline ---
    if ".prompt_sections." in contribution_type:
        parts = contribution_type.split(".")
        anchor_name = parts[3]
        inline = contribution.get("inline", False)
        content_file = contribution.get("content_file", "")
        local_path = f".system2/overlays/{overlay_name}/{content_file}"

        if inline:
            content = _resolve_content_file(overlay_local_path, content_file)
            return (
                f"- **{anchor_name}** (from {overlay_name}): "
                f"{content.rstrip(chr(10))}"
            )
        else:
            summary = contribution.get("summary", "")
            return (
                f"- **{anchor_name}** (from {overlay_name}): {summary} "
                f"Full guidance: read `{local_path}`."
            )

    # --- spec.*.required_sections: always inline (heading+description) ---
    if contribution_type.startswith("spec.") and contribution_type.endswith(".required_sections"):
        heading = contribution.get("section_heading", "")
        desc = contribution.get("description", "")
        return f'- "{heading}" (from {overlay_name}): {desc}'

    # --- auxiliary_agents: always inline (delegation guidance) ---
    if contribution_type == "auxiliary_agents":
        name = contribution.get("name", "")
        role = contribution.get("role", "")
        policy = contribution.get("delegation_policy", "")
        local_agent = f".claude/agents/{name}.md"
        policy_text = (
            "Consider delegating when relevant"
            if policy == "orchestrator_optional"
            else "Recommended for applicable workflows"
        )
        return (
            f"### {name} (from {overlay_name})\n"
            f"- **Role:** {role}\n"
            f"- **When to delegate:** {policy_text}\n"
            f"- **Delegation policy:** {policy}\n"
            f"- **Inputs:** Provide objective, relevant file paths, "
            f"and scope constraints per the standard delegation contract\n"
            f"- **Expected outputs:** Completion summary per the agent's "
            f"defined role\n"
            f"- **Agent file:** {local_agent} (read for full capabilities)"
        )

    # --- mcp_servers: always inline (config metadata) ---
    if contribution_type == "mcp_servers":
        name = contribution.get("name", "")
        desc = contribution.get("description", "")
        config = contribution.get("config", {})
        config_str = json.dumps(config)
        return f"- **{name}**: {desc} | config: `{config_str}`"

    # --- permissions: always inline ---
    if contribution_type == "permissions":
        tool = contribution.get("tool", "")
        reason = contribution.get("reason", "")
        return f"- `{tool}`: {reason}"

    return f"- [{contribution_type}] (from {overlay_name}): unsupported contribution type"


# ---------------------------------------------------------------------------
# Composed CLAUDE.md generation
# ---------------------------------------------------------------------------

# Section heading patterns in the base CLAUDE.md.
_SECTION_RE = re.compile(r"^## (.+)$")
_GATE_LINE_RE = re.compile(r"^- Gate (\d+) ")

# Contribution type suffixes that are deferred (declared but not applied
# in Phase 1).
_DEFERRED_SUFFIXES = (".tools", ".hooks")


def _generate_claude_md(
    base_claude_md: str,
    ordered_contributions: dict,
    overlays: list,
    conflict_report: "ConflictReport",
    timestamp: str = "",
) -> Tuple[str, Dict[str, int]]:
    """Produce a composed CLAUDE.md from the base content and ordered contributions.

    Args:
        base_claude_md: Full text of the base CLAUDE.md.
        ordered_contributions: Dict mapping ``(type_path, target_key)``
            to a list of ``(overlay_name, contribution_dict, overlay_path)``
            tuples, already topologically sorted.
        overlays: List of ``(overlay_name, overlay_version, overlay_path)``
            tuples for the header.
        conflict_report: The conflict report (for warnings/deferred info).

    Returns:
        Tuple of (composed CLAUDE.md text, deferred contributions dict).
    """
    lines = base_claude_md.split("\n")

    # --- Locate section boundaries ---
    # Each entry: (line_index, heading_text)
    sections: List[Tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            sections.append((i, m.group(1)))

    def _find_section(heading_prefix: str) -> Optional[int]:
        for idx, (line_idx, heading) in enumerate(sections):
            if heading.startswith(heading_prefix):
                return idx
        return None

    def _section_end(sec_idx: int) -> int:
        """Return the line index where the section content ends
        (just before the next ## heading or EOF)."""
        if sec_idx + 1 < len(sections):
            return sections[sec_idx + 1][0]
        return len(lines)

    op_idx = _find_section("Operating principles")
    gate_idx = _find_section("Gate checklist")
    deleg_idx = _find_section("Delegation contract")
    post_exec_idx = _find_section("Post-Execution Workflow")

    # --- Build overlay name@version list ---
    overlay_labels = [f"{n}@{v}" for n, v, _ in overlays]

    # --- Collect contributions by category ---
    principles: List[Tuple[str, dict, str]] = []
    gate_consultations: Dict[str, List[Tuple[str, dict, str]]] = {}
    advisory_sources: List[Tuple[str, dict, str]] = []
    agent_sections: Dict[str, List[Tuple[str, Tuple[str, dict, str]]]] = {}
    spec_sections: Dict[str, List[Tuple[str, Tuple[str, dict, str]]]] = {}
    aux_agents: List[Tuple[str, dict, str]] = []
    mcp_servers: List[Tuple[str, dict, str]] = []
    permissions_list: List[Tuple[str, dict, str]] = []
    deferred: Dict[str, int] = {}

    for (type_path, _target), entries in ordered_contributions.items():
        if any(type_path.endswith(s) for s in _DEFERRED_SUFFIXES):
            deferred[type_path] = deferred.get(type_path, 0) + len(entries)
            continue

        if type_path == "orchestrator.principles":
            principles.extend(entries)
        elif type_path.startswith("orchestrator.gates.") and type_path.endswith(".consultation"):
            parts = type_path.split(".")
            gate_num = parts[2]
            gate_consultations.setdefault(gate_num, []).extend(entries)
        elif type_path == "delegation.advisory_sources":
            advisory_sources.extend(entries)
        elif ".prompt_sections." in type_path:
            parts = type_path.split(".")
            agent_name = parts[1]
            agent_sections.setdefault(agent_name, []).append(
                (type_path, entries)
            )
        elif type_path.startswith("spec.") and type_path.endswith(".required_sections"):
            parts = type_path.split(".")
            artifact = parts[1]
            spec_sections.setdefault(artifact, []).append(
                (type_path, entries)
            )
        elif type_path == "auxiliary_agents":
            aux_agents.extend(entries)
        elif type_path == "mcp_servers":
            mcp_servers.extend(entries)
        elif type_path == "permissions":
            permissions_list.extend(entries)

    # --- Assemble composed output ---
    out: List[str] = []

    # Header comment
    overlay_str = ", ".join(overlay_labels) if overlay_labels else "none"
    out.append(f"<!-- COMPOSED: system2 + overlays: {overlay_str} -->")
    out.append(f"<!-- Composed at: {timestamp} -->")
    out.append("<!-- Re-compose with: /system2:compose -->")
    out.append("")

    # Determine insertion point indices.
    # Strategy: walk through sections and emit content in order, inserting
    # overlay blocks at the right positions.

    # Insertion points for inline overlay content:
    # 1. After "Operating principles" section content → principles
    # 2. Inside "Gate checklist" → consultations after gate lines
    # 3. End of "Delegation contract" → advisory sources
    # All other overlay sections (agent augmentation, spec augmentation,
    # auxiliary agents, MCP, permissions) are appended at EOF after all
    # base content including safety blocks.

    # Process line by line
    i = 0
    while i < len(lines):
        line = lines[i]

        # Check if we are at the end of Operating principles section
        if op_idx is not None:
            op_end = _section_end(op_idx)
            if i == op_end and principles:
                # Insert overlay principles before the next section
                out.append("### Overlay-contributed principles")
                out.append("")
                for overlay_name, contrib, overlay_path in principles:
                    rendered = _render_contribution(
                        contrib, overlay_name, overlay_path,
                        "orchestrator.principles",
                    )
                    out.append(rendered)
                    out.append("")
                # Continue — do not skip this line, it is the next heading

        # Handle Gate checklist lines
        if gate_idx is not None:
            gate_start = sections[gate_idx][0]
            gate_end = _section_end(gate_idx)
            if gate_start < i < gate_end:
                gm = _GATE_LINE_RE.match(line)
                if gm:
                    gate_num = gm.group(1)
                    out.append(line)
                    i += 1
                    # Insert consultations for this gate
                    if gate_num in gate_consultations:
                        consultations = gate_consultations[gate_num]
                        # Group by phase
                        pre_deleg = [
                            (n, c, p) for n, c, p in consultations
                            if c.get("phase", "pre-delegation") == "pre-delegation"
                        ]
                        post_comp = [
                            (n, c, p) for n, c, p in consultations
                            if c.get("phase") == "post-completion"
                        ]
                        for phase_label, phase_entries in [
                            ("pre-delegation", pre_deleg),
                            ("post-completion", post_comp),
                        ]:
                            if phase_entries:
                                out.append(f"  - Overlay consultation ({phase_label}):")
                                for oname, cdata, opath in phase_entries:
                                    scope = f"orchestrator.gates.{gate_num}.consultation"
                                    rendered = _render_contribution(
                                        cdata, oname, opath, scope,
                                    )
                                    out.append(f"    - {rendered}")
                    continue

        # Handle Delegation contract end — insert advisory sources
        if deleg_idx is not None:
            deleg_end = _section_end(deleg_idx)
            if i == deleg_end and advisory_sources:
                out.append("### Advisory sources (overlay-contributed)")
                out.append("")
                out.append(
                    "When delegating, consult these advisory sources if "
                    "available and include relevant findings in the Inputs field:"
                )
                for overlay_name, contrib, overlay_path in advisory_sources:
                    rendered = _render_contribution(
                        contrib, overlay_name, overlay_path,
                        "delegation.advisory_sources",
                    )
                    out.append(rendered)
                out.append("")
                # Continue — do not skip, i is the next section heading

        out.append(line)
        i += 1

    # Append overlay sections at EOF, after all base content including
    # safety blocks.
    _insert_overlay_sections(
        out, agent_sections, spec_sections, aux_agents,
        mcp_servers, permissions_list, deferred,
    )

    composed = "\n".join(out)
    return composed, deferred


def _insert_overlay_sections(
    out: List[str],
    agent_sections: Dict[str, List[Tuple[str, List[Tuple[str, dict, str]]]]],
    spec_sections: Dict[str, List[Tuple[str, List[Tuple[str, dict, str]]]]],
    aux_agents: List[Tuple[str, dict, str]],
    mcp_servers: List[Tuple[str, dict, str]],
    permissions_list: List[Tuple[str, dict, str]],
    deferred: Dict[str, int],
) -> None:
    """Append overlay-specific sections to *out*."""

    has_any = (
        agent_sections or spec_sections or aux_agents
        or mcp_servers or permissions_list or deferred
    )
    if not has_any:
        return

    # --- Agent augmentation ---
    if agent_sections:
        out.append("## Agent augmentation (overlay-contributed)")
        out.append("")
        out.append(
            "When delegating to the following agents, include the "
            "overlay-contributed context in the delegation contract's "
            "Constraints field. For entries marked \"Full guidance: "
            'read ...", read the referenced file and include its '
            "content in the delegation."
        )
        out.append("")
        for agent_name in sorted(agent_sections.keys()):
            out.append(f"### {agent_name}")
            for type_path, entries in agent_sections[agent_name]:
                for overlay_name, contrib, overlay_path in entries:
                    rendered = _render_contribution(
                        contrib, overlay_name, overlay_path, type_path,
                    )
                    out.append(rendered)
            out.append("")

    # --- Spec artifact augmentation ---
    if spec_sections:
        out.append("## Spec artifact augmentation (overlay-contributed)")
        out.append("")
        out.append(
            "When delegating to spec-chain agents, include these "
            "additional required sections in the delegation contract:"
        )
        out.append("")
        for artifact in sorted(spec_sections.keys()):
            out.append(f"### spec/{artifact}.md")
            for type_path, entries in spec_sections[artifact]:
                for overlay_name, contrib, overlay_path in entries:
                    rendered = _render_contribution(
                        contrib, overlay_name, overlay_path, type_path,
                    )
                    out.append(rendered)
            out.append("")

    # --- Auxiliary agents ---
    if aux_agents:
        out.append("## Auxiliary agents (overlay-contributed)")
        out.append("")
        out.append(
            "These agents are not part of the 13-agent pipeline. They are "
            "available for optional delegation at the orchestrator's discretion."
        )
        out.append("")
        for overlay_name, contrib, overlay_path in aux_agents:
            rendered = _render_contribution(
                contrib, overlay_name, overlay_path, "auxiliary_agents",
            )
            out.append(rendered)
            out.append("")

    # --- MCP servers ---
    if mcp_servers:
        out.append("## MCP servers (overlay-suggested)")
        out.append("")
        out.append(
            "The following MCP servers are suggested by overlays. "
            "Configure in .mcp.json if not already present:"
        )
        out.append("")
        for overlay_name, contrib, overlay_path in mcp_servers:
            rendered = _render_contribution(
                contrib, overlay_name, overlay_path, "mcp_servers",
            )
            out.append(rendered)
        out.append("")

    # --- Permissions ---
    if permissions_list:
        out.append("## Permissions (overlay-suggested)")
        out.append("")
        out.append(
            "The following permissions are suggested by overlays. "
            "Add to .claude/settings.json if desired:"
        )
        out.append("")
        for overlay_name, contrib, overlay_path in permissions_list:
            rendered = _render_contribution(
                contrib, overlay_name, overlay_path, "permissions",
            )
            out.append(rendered)
        out.append("")

    # --- Deferred contributions ---
    if deferred:
        out.append("## Deferred contributions")
        out.append("")
        out.append(
            "The following contributions are declared by overlays but "
            "not applied in this composition phase:"
        )
        out.append("")
        for scope in sorted(deferred.keys()):
            count = deferred[scope]
            out.append(f"- {scope}: {count} contribution(s)")
        out.append("")


# ---------------------------------------------------------------------------
# Content copying
# ---------------------------------------------------------------------------

def _collect_content_files(
    manifest: dict,
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Extract content_file and agent_file paths from a manifest.

    If *valid_anchors_by_agent* is provided, prompt_section contributions
    targeting unknown anchors are excluded (matching the composition filter).
    """
    files: List[str] = []
    contribs = manifest.get("contributions", {})

    orch = contribs.get("orchestrator", {})
    for entry in orch.get("principles", []):
        if "content_file" in entry:
            files.append(entry["content_file"])
    for _gate_num, gate_obj in orch.get("gates", {}).items():
        for entry in gate_obj.get("consultation", []):
            if "content_file" in entry:
                files.append(entry["content_file"])
    for agent_name, agent_obj in contribs.get("agents", {}).items():
        for anchor, entries in agent_obj.get("prompt_sections", {}).items():
            if valid_anchors_by_agent is not None:
                agent_anchors = set(valid_anchors_by_agent.get(agent_name, []))
                if anchor not in agent_anchors:
                    continue
            for entry in entries:
                if "content_file" in entry:
                    files.append(entry["content_file"])
    for entry in contribs.get("auxiliary_agents", []):
        if "agent_file" in entry:
            files.append(entry["agent_file"])

    return files


def _copy_overlay_content(
    overlay_path: str,
    manifest: dict,
    target_dir: str,
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Copy content_file and agent_file references into *target_dir*.

    Preserves directory structure relative to the overlay root.
    If *valid_anchors_by_agent* is provided, only copies files for
    contributions targeting valid anchors.

    Returns:
        content_hash (``sha256:<hex>``) computed over all copied file
        contents concatenated in sorted relative-path order.
    """
    content_files = _collect_content_files(manifest, valid_anchors_by_agent)

    for rel_path in content_files:
        src = os.path.join(overlay_path, rel_path)
        dst = os.path.join(target_dir, rel_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    hasher = hashlib.sha256()
    for rel_path in sorted(content_files):
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(b"\x00")
        dst = os.path.join(target_dir, rel_path)
        with open(dst, "rb") as fh:
            hasher.update(fh.read())
        hasher.update(b"\x00")

    return f"sha256:{hasher.hexdigest()}"


# ---------------------------------------------------------------------------
# Lock file generation
# ---------------------------------------------------------------------------

def _generate_lock(
    overlays: List[dict],
    contributions_applied: Dict[str, List[str]],
    warnings: List[dict],
    system2_version: str,
    timestamp: str = "",
    content_fingerprint: str = "",
) -> dict:
    """Generate the lock file structure per spec/design.md schema.

    Args:
        overlays: List of overlay info dicts, each with keys:
            name, version, source_path, local_path, manifest_hash,
            content_hash.
        contributions_applied: Dict mapping type_path to list of
            contribution IDs.
        warnings: List of warning dicts (from conflict report).
        system2_version: The System2 version string.
        timestamp: ISO 8601 timestamp string (actual composition time).
        content_fingerprint: Deterministic hash of all inputs for
            idempotency comparison.

    Returns:
        Lock file dict ready for JSON serialization.
    """

    return {
        "composed_at": timestamp,
        "content_fingerprint": content_fingerprint,
        "system2_version": system2_version,
        "schema_version": "1.0.0",
        "overlays": overlays,
        "contributions_applied": contributions_applied,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Base template reader
# ---------------------------------------------------------------------------

def _read_base_template(init_skill_path: str, fallback_path: str) -> str:
    """Read the base System2 CLAUDE.md template.

    Tries the init skill template block first, then falls back to a plain
    file read of *fallback_path*.  Returns empty string if both fail.
    """
    base_claude_md = ""

    if os.path.isfile(init_skill_path):
        try:
            with open(init_skill_path, "r", encoding="utf-8") as fh:
                skill_content = fh.read()
            begin = skill_content.find("---BEGIN TEMPLATE---")
            end = skill_content.find("---END TEMPLATE---")
            if begin != -1 and end != -1:
                begin += len("---BEGIN TEMPLATE---\n")
                base_claude_md = skill_content[begin:end].rstrip("\n") + "\n"
        except OSError:
            pass

    if not base_claude_md and os.path.isfile(fallback_path):
        try:
            with open(fallback_path, "r", encoding="utf-8") as fh:
                base_claude_md = fh.read()
        except OSError:
            pass

    return base_claude_md


def _compute_stale_artifacts(
    project_path: str, overlay_name: str, lock_data: dict
) -> List[str]:
    """Return absolute paths of artifacts to remove for *overlay_name*.

    Inspects the overlay's cached content directory and the lock file's
    ``contributions_applied.auxiliary_agents`` to determine which files and
    directories belong to the target overlay.

    Only paths that exist on disk are included.

    Args:
        project_path: Absolute path to the project root.
        overlay_name: Kebab-case overlay name to uninstall.
        lock_data: Parsed lock file dict (``spec/overlay-manifest.lock``).

    Returns:
        List of absolute paths (directories and files) to remove.
    """
    if not _KEBAB_RE.match(overlay_name):
        return []

    stale: List[str] = []

    # 1. Overlay cached content directory.
    overlay_dir = os.path.join(
        project_path, ".system2", "overlays", overlay_name
    )
    if os.path.isdir(overlay_dir):
        stale.append(overlay_dir)

    # 2. Auxiliary agent files contributed by this overlay.
    #    Determine ownership by checking whether the agent's source file
    #    exists in the overlay's cached directory (agents/<name>.md).
    aux_names = (
        lock_data
        .get("contributions_applied", {})
        .get("auxiliary_agents", [])
    )
    agents_dir = os.path.join(project_path, ".claude", "agents")
    for agent_name in aux_names:
        if not isinstance(agent_name, str) or not _KEBAB_RE.match(agent_name):
            continue
        cached_agent = os.path.join(overlay_dir, "agents", f"{agent_name}.md")
        if os.path.isfile(cached_agent):
            deployed_agent = os.path.join(agents_dir, f"{agent_name}.md")
            if os.path.isfile(deployed_agent):
                stale.append(deployed_agent)

    return stale


def _uninstall_last_overlay(
    base_path: str,
    project_path: str,
    overlay_entry: dict,
    lock_data: dict,
    dry_run: bool = False,
) -> dict:
    """Handle uninstall when zero overlays remain after removal.

    Reads the base System2 template, computes stale artifacts, and (unless
    *dry_run*) atomically writes the base template to ``CLAUDE.md``, removes
    the lock file, removes stale artifacts, and cleans up the empty
    ``.system2/overlays/`` directory.

    All mutations are wrapped in a try/except that restores backups on any
    failure, so the project is never left in a half-written state.

    Args:
        base_path: Path to the System2 plugin root (e.g. ``plugin/``).
        project_path: Absolute path to the target project root.
        overlay_entry: Lock-file dict for the overlay being removed.
        lock_data: Full parsed lock-file dict.
        dry_run: If True, return a preview without touching the filesystem.

    Returns:
        Standard result dict with keys ``claude_md``, ``lock``,
        ``auxiliary_agents``, ``files_to_write``, ``report``, ``errors``.
    """
    overlay_name = overlay_entry["name"]
    overlay_version = overlay_entry.get("version", "unknown")

    # 1. Read base template.
    init_skill_path = os.path.join(base_path, "skills", "init", "SKILL.md")
    repo_claude_path = os.path.join(os.path.dirname(base_path), "CLAUDE.md")
    base_claude_md = _read_base_template(init_skill_path, repo_claude_path)

    if not base_claude_md:
        return {
            "claude_md": "",
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": [],
            "report": {},
            "errors": [
                f"Cannot read base CLAUDE.md template: checked "
                f"{init_skill_path} and {repo_claude_path}"
            ],
        }

    # 2. Compute artifacts to remove.
    artifacts_to_remove = _compute_stale_artifacts(
        project_path, overlay_name, lock_data
    )

    # 3. Build report.
    report: Dict[str, Any] = {
        "uninstall": {
            "removed": {"name": overlay_name, "version": overlay_version},
            "remaining": [],
            "artifacts_removed": artifacts_to_remove,
        },
        "overlays": [],
        "contributions_applied": {},
        "composed_lines": base_claude_md.count("\n") + 1,
        "files_to_write": [os.path.join(project_path, "CLAUDE.md")],
    }

    files_to_write = [os.path.join(project_path, "CLAUDE.md")]
    files_to_remove = [
        os.path.join(project_path, "spec", "overlay-manifest.lock")
    ]

    # 4. Dry-run: return preview without writing.
    if dry_run:
        return {
            "claude_md": base_claude_md,
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": files_to_write + [
                "(remove) " + f
                for f in files_to_remove + artifacts_to_remove
            ],
            "report": report,
            "errors": [],
        }

    # 5. Atomic write-and-cleanup.
    claude_path = os.path.join(project_path, "CLAUDE.md")
    lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")

    backups: List[Tuple[str, str]] = []
    dir_backups: List[Tuple[str, str]] = []

    try:
        # Back up CLAUDE.md.
        if os.path.exists(claude_path):
            dir_name = os.path.dirname(claude_path)
            fd, bak = tempfile.mkstemp(
                prefix=".CLAUDE.md.", suffix=".bak", dir=dir_name
            )
            os.close(fd)
            shutil.copy2(claude_path, bak)
            backups.append((claude_path, bak))

        # Back up lock file.
        if os.path.exists(lock_path):
            dir_name = os.path.dirname(lock_path)
            fd, bak = tempfile.mkstemp(
                prefix=".overlay-manifest.lock.",
                suffix=".bak",
                dir=dir_name,
            )
            os.close(fd)
            shutil.copy2(lock_path, bak)
            backups.append((lock_path, bak))

        # Back up stale artifacts before removal.
        for artifact_path in artifacts_to_remove:
            if os.path.isdir(artifact_path):
                parent = os.path.dirname(artifact_path)
                bak = tempfile.mkdtemp(
                    prefix=f".{os.path.basename(artifact_path)}.",
                    suffix=".bak",
                    dir=parent,
                )
                shutil.rmtree(bak)
                shutil.copytree(artifact_path, bak)
                dir_backups.append((artifact_path, bak))
            elif os.path.isfile(artifact_path):
                dir_name = os.path.dirname(artifact_path)
                fd, bak = tempfile.mkstemp(
                    prefix=f".{os.path.basename(artifact_path)}.",
                    suffix=".bak",
                    dir=dir_name,
                )
                os.close(fd)
                shutil.copy2(artifact_path, bak)
                backups.append((artifact_path, bak))

        # Write base template to CLAUDE.md via temp + os.replace().
        dir_name = os.path.dirname(claude_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(base_claude_md)
            os.replace(tmp_path, claude_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        # Remove lock file.
        if os.path.exists(lock_path):
            os.unlink(lock_path)

        # Remove stale artifacts.
        for artifact_path in artifacts_to_remove:
            if os.path.isdir(artifact_path):
                shutil.rmtree(artifact_path)
            elif os.path.isfile(artifact_path):
                os.unlink(artifact_path)

    except Exception:
        # Rollback: restore all file backups.
        for orig, bak in backups:
            if os.path.exists(bak):
                shutil.copy2(bak, orig)
                os.unlink(bak)
        # Rollback: restore all directory backups.
        for orig, bak in dir_backups:
            if os.path.exists(bak):
                if os.path.exists(orig):
                    shutil.rmtree(orig)
                shutil.move(bak, orig)
        raise

    # Success: clean up backup files.
    for _, bak in backups:
        try:
            if os.path.exists(bak):
                os.unlink(bak)
        except OSError:
            pass
    for _, bak in dir_backups:
        try:
            if os.path.exists(bak):
                shutil.rmtree(bak)
        except OSError:
            pass

    # Remove empty .system2/overlays/ parent directory (after backup
    # cleanup so the backup dirs no longer occupy the parent).
    overlays_parent = os.path.join(project_path, ".system2", "overlays")
    try:
        os.rmdir(overlays_parent)
    except OSError:
        pass

    return {
        "claude_md": base_claude_md,
        "lock": {},
        "auxiliary_agents": [],
        "files_to_write": [claude_path],
        "report": report,
        "errors": [],
    }


def _uninstall(
    base_path: str,
    project_path: str,
    overlay_name: str,
    dry_run: bool = False,
    allow_newer_schema: bool = False,
) -> dict:
    """Orchestrate overlay uninstallation.

    Reads the lock file, validates the overlay name, and dispatches to
    ``_uninstall_last_overlay()`` (zero remaining) or ``compose()`` (one
    or more remaining).

    Args:
        base_path: Path to the System2 plugin root (e.g. ``plugin/``).
        project_path: Absolute path to the target project root.
        overlay_name: Kebab-case name of the overlay to remove.
        dry_run: If True, return a preview without touching the filesystem.
        allow_newer_schema: Passed through to ``compose()`` for the
            multi-overlay path.

    Returns:
        Standard result dict with keys ``claude_md``, ``lock``,
        ``auxiliary_agents``, ``files_to_write``, ``report``, ``errors``.
    """
    _err = {
        "claude_md": "",
        "lock": {},
        "auxiliary_agents": [],
        "files_to_write": [],
        "report": {},
        "errors": [],
    }

    # 1. Validate overlay_name format (kebab-case).
    if not _KEBAB_RE.match(overlay_name):
        _err["errors"] = [
            f"Invalid overlay name {overlay_name!r}: must be kebab-case "
            f"(lowercase alphanumeric, hyphens only)"
        ]
        return _err

    # 2. Read lock file.
    lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
    if not os.path.isfile(lock_path):
        _err["errors"] = ["No lock file found; no overlays are composed"]
        return _err
    try:
        with open(lock_path, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
    except json.JSONDecodeError:
        _err["errors"] = ["Lock file is malformed (invalid JSON)"]
        return _err
    except OSError as exc:
        _err["errors"] = [f"Cannot read lock file: {exc}"]
        return _err

    # 3. Validate lock structure.
    overlays = lock_data.get("overlays", [])
    if not isinstance(overlays, list):
        _err["errors"] = ["Lock file is malformed: 'overlays' is not a list"]
        return _err

    # 4. Validate each overlay entry has required fields.
    for ov in overlays:
        if not isinstance(ov, dict) or "name" not in ov:
            _err["errors"] = [
                "Lock file overlay entry missing 'name' field"
            ]
            return _err

    # 5. Find and remove the target overlay.
    target_entry = None
    remaining = []
    for ov in overlays:
        if ov["name"] == overlay_name:
            target_entry = ov
        else:
            remaining.append(ov)

    if target_entry is None:
        installed = [ov["name"] for ov in overlays]
        _err["errors"] = [
            f"Overlay {overlay_name!r} is not installed. "
            f"Installed: {installed}"
        ]
        return _err

    # 6. Validate remaining overlay names (security).
    for ov in remaining:
        if not _KEBAB_RE.match(ov.get("name", "")):
            _err["errors"] = [
                f"Lock file contains invalid overlay name: "
                f"{ov.get('name')!r}"
            ]
            return _err

    # 7. Dispatch based on remaining count.
    if len(remaining) == 0:
        return _uninstall_last_overlay(
            base_path, project_path, target_entry, lock_data, dry_run,
        )

    # 8. Multi-overlay path: extract source_paths, call compose().
    remaining_paths = []
    for ov in remaining:
        sp = ov.get("source_path", "")
        if not sp:
            _err["errors"] = [
                f"Overlay {ov['name']!r} has no source_path in lock file"
            ]
            return _err
        remaining_paths.append(sp)

    result = compose(
        base_path, remaining_paths, project_path,
        dry_run=dry_run, allow_newer_schema=allow_newer_schema,
    )

    # If compose returned errors, augment with remediation hint.
    if result["errors"]:
        result["errors"].append(
            "Remediation: verify that all remaining overlay source paths "
            "are accessible, then retry. If an overlay source has moved, "
            "update the lock file with /system2:compose --from-lock after "
            "correcting the paths."
        )
        return result

    # 9. Augment result with uninstall metadata.
    target_version = target_entry.get("version", "unknown")
    result["report"]["uninstall"] = {
        "removed": {"name": overlay_name, "version": target_version},
        "remaining": [
            {"name": ov["name"], "version": ov.get("version", "")}
            for ov in remaining
        ],
        "artifacts_removed": _compute_stale_artifacts(
            project_path, overlay_name, lock_data,
        ),
    }

    return result


# ---------------------------------------------------------------------------
# Drift check (read-only)
# ---------------------------------------------------------------------------

def _get_system2_version(base_path: str) -> str:
    """Resolve installed System2 version from plugin.json or VERSION."""
    plugin_json = os.path.join(base_path, ".claude-plugin", "plugin.json")
    repo_version = os.path.join(os.path.dirname(base_path), "VERSION")
    if os.path.isfile(plugin_json):
        try:
            with open(plugin_json, "r", encoding="utf-8") as fh:
                return json.load(fh).get("version", "unknown")
        except (OSError, json.JSONDecodeError):
            pass
    if os.path.isfile(repo_version):
        try:
            with open(repo_version, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            pass
    return "unknown"


def drift_check(base_path: str, project_path: str) -> dict:
    """Read-only drift/status check for a composed project.

    Compares current installed state against the lock file without
    modifying any files.

    Args:
        base_path: Path to the System2 plugin root.
        project_path: Path to the target project root.

    Returns:
        Dict with keys:
            status: one of "current", "stale_base", "stale_overlay",
                    "broken", "no_lock"
            details: list of detail dicts describing each finding
            system2_version: dict with "locked" and "installed" keys
            overlays: list of per-overlay status dicts
            claude_md_composed: bool indicating whether CLAUDE.md
                appears to be composed
    """
    lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
    claude_md_path = os.path.join(project_path, "CLAUDE.md")

    if not os.path.isfile(lock_path):
        return {
            "status": "no_lock",
            "details": [{"type": "no_lock", "message": "No lock file found at spec/overlay-manifest.lock"}],
            "system2_version": {"locked": None, "installed": _get_system2_version(base_path)},
            "overlays": [],
            "claude_md_composed": False,
        }

    try:
        with open(lock_path, "r", encoding="utf-8") as fh:
            lock = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "broken",
            "details": [{"type": "lock_unreadable", "message": f"Cannot read lock file: {exc}"}],
            "system2_version": {"locked": None, "installed": _get_system2_version(base_path)},
            "overlays": [],
            "claude_md_composed": False,
        }

    details: List[dict] = []
    overlay_statuses: List[dict] = []
    installed_version = _get_system2_version(base_path)
    locked_version = lock.get("system2_version", "unknown")

    # Check base version drift.
    base_stale = installed_version != locked_version
    if base_stale:
        details.append({
            "type": "stale_base",
            "message": (
                f"Installed System2 version ({installed_version}) differs "
                f"from locked version ({locked_version})"
            ),
        })

    # Check CLAUDE.md composed state.
    claude_md_composed = False
    if os.path.isfile(claude_md_path):
        try:
            with open(claude_md_path, "r", encoding="utf-8") as fh:
                first_line = fh.readline()
            claude_md_composed = first_line.startswith("<!-- COMPOSED:")
        except OSError:
            pass
    if not claude_md_composed:
        details.append({
            "type": "claude_md_not_composed",
            "message": "CLAUDE.md does not appear to be composed (missing COMPOSED header)",
        })

    # Check each overlay.
    any_stale_overlay = False
    any_broken = False

    for ov in lock.get("overlays", []):
        ov_name = ov.get("name", "<unknown>")
        source_path = ov.get("source_path", "")
        local_path = ov.get("local_path", "")
        locked_manifest_hash = ov.get("manifest_hash", "")
        locked_content_hash = ov.get("content_hash", "")

        ov_status: dict = {
            "name": ov_name,
            "source_path": source_path,
            "source_exists": False,
            "local_exists": False,
            "manifest_match": None,
            "content_match": None,
            "local_match": None,
        }

        # Check source path existence.
        if not source_path or not os.path.isdir(source_path):
            ov_status["source_exists"] = False
            details.append({
                "type": "missing_source",
                "overlay": ov_name,
                "message": f"Overlay source path missing: {source_path}",
            })
            any_broken = True
        else:
            ov_status["source_exists"] = True

            # Check manifest hash.
            manifest_file = os.path.join(source_path, "system2.overlay.json")
            if os.path.isfile(manifest_file):
                try:
                    with open(manifest_file, "rb") as fh:
                        current_hash = f"sha256:{hashlib.sha256(fh.read()).hexdigest()}"
                    ov_status["manifest_match"] = current_hash == locked_manifest_hash
                    if not ov_status["manifest_match"]:
                        details.append({
                            "type": "stale_manifest",
                            "overlay": ov_name,
                            "message": (
                                f"Overlay {ov_name!r} manifest has changed "
                                f"(locked: {locked_manifest_hash[:20]}..., "
                                f"current: {current_hash[:20]}...)"
                            ),
                        })
                        any_stale_overlay = True
                except OSError:
                    ov_status["manifest_match"] = False
                    any_broken = True
            else:
                ov_status["manifest_match"] = False
                details.append({
                    "type": "missing_source",
                    "overlay": ov_name,
                    "message": f"Overlay manifest not found at {manifest_file}",
                })
                any_broken = True

            # Check content hash (recompute from source files).
            if locked_content_hash and ov_status["manifest_match"] is not False:
                try:
                    manifest = _read_manifest(source_path)
                    anchor_map = _load_anchor_map(base_path)
                    anchors_by_agent = {
                        name: list(info.get("anchors", {}).keys())
                        for name, info in anchor_map.get("agents", {}).items()
                    }
                    content_files = _collect_content_files(manifest, anchors_by_agent)
                    hasher = hashlib.sha256()
                    for rel_path in sorted(content_files):
                        hasher.update(rel_path.encode("utf-8"))
                        hasher.update(b"\x00")
                        cf_path = os.path.join(source_path, rel_path)
                        if os.path.isfile(cf_path):
                            with open(cf_path, "rb") as fh:
                                hasher.update(fh.read())
                        hasher.update(b"\x00")
                    current_content_hash = f"sha256:{hasher.hexdigest()}"
                    ov_status["content_match"] = current_content_hash == locked_content_hash
                    if not ov_status["content_match"]:
                        details.append({
                            "type": "stale_content",
                            "overlay": ov_name,
                            "message": (
                                f"Overlay {ov_name!r} content files have changed "
                                f"(locked: {locked_content_hash[:20]}..., "
                                f"current: {current_content_hash[:20]}...)"
                            ),
                        })
                        any_stale_overlay = True
                except (OSError, json.JSONDecodeError, KeyError):
                    ov_status["content_match"] = False
                    any_stale_overlay = True

        # Check project-local overlay copy existence and content integrity.
        if local_path:
            full_local = os.path.join(project_path, local_path)
            ov_status["local_exists"] = os.path.isdir(full_local)
            if not ov_status["local_exists"]:
                details.append({
                    "type": "missing_local",
                    "overlay": ov_name,
                    "message": f"Project-local overlay copy missing: {local_path}",
                })
                any_broken = True
            elif locked_content_hash:
                try:
                    manifest = _read_manifest(source_path) if ov_status["source_exists"] else {}
                    anchor_map = _load_anchor_map(base_path)
                    anchors_by_agent = {
                        name: list(info.get("anchors", {}).keys())
                        for name, info in anchor_map.get("agents", {}).items()
                    }
                    content_files = _collect_content_files(manifest, anchors_by_agent) if manifest else []
                    local_hasher = hashlib.sha256()
                    for rel_path in sorted(content_files):
                        local_hasher.update(rel_path.encode("utf-8"))
                        local_hasher.update(b"\x00")
                        lf_path = os.path.join(full_local, rel_path)
                        if os.path.isfile(lf_path):
                            with open(lf_path, "rb") as fh:
                                local_hasher.update(fh.read())
                        local_hasher.update(b"\x00")
                    local_content_hash = f"sha256:{local_hasher.hexdigest()}"
                    ov_status["local_match"] = local_content_hash == locked_content_hash
                    if not ov_status["local_match"]:
                        details.append({
                            "type": "stale_local",
                            "overlay": ov_name,
                            "message": (
                                f"Project-local copy of {ov_name!r} has been "
                                f"modified (does not match locked content hash)"
                            ),
                        })
                        any_stale_overlay = True
                except (OSError, json.JSONDecodeError, KeyError):
                    ov_status["local_match"] = False
                    any_stale_overlay = True

        overlay_statuses.append(ov_status)

    # Determine overall status.
    if any_broken:
        status = "broken"
    elif any_stale_overlay:
        status = "stale_overlay"
    elif base_stale:
        status = "stale_base"
    else:
        status = "current"

    return {
        "status": status,
        "details": details,
        "system2_version": {"locked": locked_version, "installed": installed_version},
        "overlays": overlay_statuses,
        "claude_md_composed": claude_md_composed,
    }


# ---------------------------------------------------------------------------
# Atomic file output
# ---------------------------------------------------------------------------

def _makedirs_tracked(dir_path: str, dirs_created: List[str]) -> None:
    """Create directory and all parents, recording every newly created level."""
    if os.path.isdir(dir_path):
        return
    # Walk up to find the first existing ancestor.
    to_create: List[str] = []
    current = dir_path
    while not os.path.isdir(current):
        to_create.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    os.makedirs(dir_path, exist_ok=True)
    # Record deepest-first so rollback removes leaves before parents.
    for d in to_create:
        dirs_created.append(d)


def _write_outputs(
    project_path: str,
    claude_md: str,
    lock: dict,
    auxiliary_agents: List[dict],
    pending_content_copies: Optional[List[Tuple[str, str, dict]]] = None,
    overlay_info_for_lock: Optional[List[dict]] = None,
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Write composed artifacts atomically.

    Uses temp file + os.replace() for each file. Backs up existing files
    and overlay directories before writing. On failure, restores all
    backups and removes newly created files/directories.

    Args:
        project_path: Target project root.
        claude_md: Composed CLAUDE.md content.
        lock: Lock file dict.
        auxiliary_agents: List of dicts with ``name`` and ``source_file``.
        pending_content_copies: List of (source_path, overlay_name, manifest)
            tuples for content files to copy into .system2/overlays/.
        overlay_info_for_lock: Mutable list of overlay info dicts; updated
            with content_hash after copying.

    Returns:
        List of absolute paths of files written.

    Raises:
        OSError: If any write fails (after restoring backups).
    """
    if pending_content_copies is None:
        pending_content_copies = []
    if overlay_info_for_lock is None:
        overlay_info_for_lock = []
    files_to_write: List[Tuple[str, str]] = []  # (path, content)
    binary_copies: List[Tuple[str, str]] = []  # (src, dst)

    # CLAUDE.md
    claude_path = os.path.join(project_path, "CLAUDE.md")
    files_to_write.append((claude_path, claude_md))

    # spec/overlay-manifest.lock
    lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
    lock_content = json.dumps(lock, indent=2) + "\n"
    files_to_write.append((lock_path, lock_content))

    # Auxiliary agent files
    agents_dir = os.path.join(project_path, ".claude", "agents")
    for agent_info in auxiliary_agents:
        agent_name = agent_info["name"]
        src_file = agent_info["source_file"]
        dst_file = os.path.join(agents_dir, f"{agent_name}.md")
        binary_copies.append((src_file, dst_file))

    # Phase 0: Identify stale artifacts from previous composition.
    stale_agents: List[str] = []
    stale_overlay_dirs: List[str] = []
    prev_lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
    if os.path.isfile(prev_lock_path):
        try:
            with open(prev_lock_path, "r", encoding="utf-8") as fh:
                prev_lock = json.load(fh)
        except (OSError, json.JSONDecodeError):
            prev_lock = {}

        current_overlay_names = {info["name"] for info in auxiliary_agents}
        current_overlay_dir_names = {
            name for _, name, _ in pending_content_copies
        }

        for prev_ov in prev_lock.get("overlays", []):
            prev_name = prev_ov.get("name", "")
            if prev_name not in current_overlay_dir_names:
                prev_dir = os.path.join(
                    project_path, ".system2", "overlays", prev_name
                )
                if os.path.isdir(prev_dir):
                    stale_overlay_dirs.append(prev_dir)

        prev_aux_names = set()
        for prev_id in prev_lock.get("contributions_applied", {}).get("auxiliary_agents", []):
            prev_aux_names.add(prev_id)
        current_aux_names = {a["name"] for a in auxiliary_agents}
        for prev_name in prev_aux_names - current_aux_names:
            prev_agent = os.path.join(
                project_path, ".claude", "agents", f"{prev_name}.md"
            )
            if os.path.isfile(prev_agent):
                stale_agents.append(prev_agent)

    # Phase 1: Back up existing files and overlay directories.
    backups: List[Tuple[str, str]] = []  # (original_path, backup_path)
    dir_backups: List[Tuple[str, str]] = []  # (original_dir, backup_dir)
    newly_created: List[str] = []
    newly_created_dirs: List[str] = []

    all_targets = [p for p, _ in files_to_write] + [d for _, d in binary_copies]

    for target_path in all_targets:
        if os.path.exists(target_path):
            dir_name = os.path.dirname(target_path)
            base_name = os.path.basename(target_path)
            fd, backup_path = tempfile.mkstemp(
                prefix=f".{base_name}.", suffix=".bak", dir=dir_name
            )
            os.close(fd)
            shutil.copy2(target_path, backup_path)
            backups.append((target_path, backup_path))

    for source_path, overlay_name, manifest in pending_content_copies:
        overlay_dir = os.path.join(
            project_path, ".system2", "overlays", overlay_name
        )
        if os.path.isdir(overlay_dir):
            backup_dir = tempfile.mkdtemp(
                prefix=f".{overlay_name}.", suffix=".bak",
                dir=os.path.dirname(overlay_dir),
            )
            shutil.rmtree(backup_dir)
            shutil.copytree(overlay_dir, backup_dir)
            dir_backups.append((overlay_dir, backup_dir))

    # Phase 2: All writes inside a single try block for rollback.
    written: List[str] = []
    dirs_created: List[str] = []
    stale_backups: List[Tuple[str, str]] = []
    stale_dir_backups: List[Tuple[str, str]] = []
    try:
        # Ensure parent directories exist (tracked for rollback).
        for file_path, _ in files_to_write:
            _makedirs_tracked(os.path.dirname(file_path), dirs_created)
        for _, dst_file in binary_copies:
            _makedirs_tracked(os.path.dirname(dst_file), dirs_created)

        # Copy overlay content into staging dirs, then move to final paths.
        for source_path, overlay_name, manifest in pending_content_copies:
            overlay_dir = os.path.join(
                project_path, ".system2", "overlays", overlay_name
            )
            parent_dir = os.path.dirname(overlay_dir)
            _makedirs_tracked(parent_dir, dirs_created)

            staging_dir = tempfile.mkdtemp(
                prefix=f".{overlay_name}.", suffix=".staging",
                dir=parent_dir,
            )
            try:
                content_hash = _copy_overlay_content(
                    source_path, manifest, staging_dir,
                    valid_anchors_by_agent=valid_anchors_by_agent,
                )
                for info in overlay_info_for_lock:
                    if info["name"] == overlay_name:
                        info["content_hash"] = content_hash
                if os.path.isdir(overlay_dir):
                    shutil.rmtree(overlay_dir)
                os.rename(staging_dir, overlay_dir)
            except Exception:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise

            written.append(overlay_dir)
            if overlay_dir not in [orig for orig, _ in dir_backups]:
                newly_created_dirs.append(overlay_dir)

        # Remove stale artifacts inside the atomic block so rollback
        # can restore them if later writes fail.
        stale_backups: List[Tuple[str, str]] = []
        stale_dir_backups: List[Tuple[str, str]] = []
        for stale_agent in stale_agents:
            if os.path.isfile(stale_agent):
                dir_name = os.path.dirname(stale_agent)
                base_name = os.path.basename(stale_agent)
                fd, bak = tempfile.mkstemp(
                    prefix=f".{base_name}.", suffix=".stalebak", dir=dir_name
                )
                os.close(fd)
                shutil.copy2(stale_agent, bak)
                os.unlink(stale_agent)
                stale_backups.append((stale_agent, bak))
        for stale_dir in stale_overlay_dirs:
            if os.path.isdir(stale_dir):
                parent = os.path.dirname(stale_dir)
                bak = tempfile.mkdtemp(
                    prefix=f".{os.path.basename(stale_dir)}.",
                    suffix=".stalebak", dir=parent,
                )
                shutil.rmtree(bak)
                shutil.copytree(stale_dir, bak)
                shutil.rmtree(stale_dir)
                stale_dir_backups.append((stale_dir, bak))

        # Update lock with final content hashes.
        lock["overlays"] = overlay_info_for_lock
        lock_content = json.dumps(lock, indent=2) + "\n"
        for i, (path, _) in enumerate(files_to_write):
            if path.endswith("overlay-manifest.lock"):
                files_to_write[i] = (path, lock_content)

        # Write text files via temp + os.replace().
        for file_path, content in files_to_write:
            dir_name = os.path.dirname(file_path)
            fd, tmp_path = tempfile.mkstemp(
                dir=dir_name, suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                os.replace(tmp_path, file_path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
            if file_path not in [orig for orig, _ in backups]:
                newly_created.append(file_path)
            written.append(file_path)

        # Copy binary files (auxiliary agents) via temp + os.replace().
        for src_file, dst_file in binary_copies:
            dir_name = os.path.dirname(dst_file)
            fd, tmp_path = tempfile.mkstemp(
                dir=dir_name, suffix=".tmp"
            )
            os.close(fd)
            try:
                shutil.copy2(src_file, tmp_path)
                os.replace(tmp_path, dst_file)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
            if dst_file not in [orig for orig, _ in backups]:
                newly_created.append(dst_file)
            written.append(dst_file)

    except Exception:
        # Phase 3: Restore backups and remove newly created files/dirs.
        for original_path, backup_path in backups:
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, original_path)
                os.unlink(backup_path)
        for original_dir, backup_dir in dir_backups:
            if os.path.exists(backup_dir):
                if os.path.exists(original_dir):
                    shutil.rmtree(original_dir)
                shutil.move(backup_dir, original_dir)
        for created_path in newly_created:
            if os.path.exists(created_path):
                os.unlink(created_path)
        for created_dir in newly_created_dirs:
            if os.path.isdir(created_dir):
                shutil.rmtree(created_dir)
        for created_dir in dirs_created:
            try:
                os.rmdir(created_dir)
            except OSError:
                pass
        # Restore stale artifacts that were removed inside the atomic block.
        for orig, bak in stale_backups:
            if os.path.exists(bak):
                shutil.copy2(bak, orig)
                os.unlink(bak)
        for orig, bak in stale_dir_backups:
            if os.path.exists(bak):
                shutil.move(bak, orig)
        raise

    # Phase 4: Success — clean up backups.
    # must not fail composition since new artifacts are already written.
    for _, backup_path in backups:
        try:
            if os.path.exists(backup_path):
                os.unlink(backup_path)
        except OSError:
            pass
    for _, backup_dir in dir_backups:
        try:
            if os.path.exists(backup_dir):
                shutil.rmtree(backup_dir)
        except OSError:
            pass
    for _, bak in stale_backups:
        try:
            if os.path.exists(bak):
                os.unlink(bak)
        except OSError:
            pass
    for _, bak in stale_dir_backups:
        try:
            if os.path.exists(bak):
                shutil.rmtree(bak)
        except OSError:
            pass

    return written


# ---------------------------------------------------------------------------
# Composition orchestrator
# ---------------------------------------------------------------------------

def compose(
    base_path: str,
    overlay_paths: List[str],
    project_path: str,
    dry_run: bool = False,
    allow_newer_schema: bool = False,
) -> dict:
    """Orchestrate the full composition pipeline.

    Args:
        base_path: Path to the System2 plugin root (e.g., ``plugin/``).
        overlay_paths: List of overlay directory paths.
        project_path: Path to the target project root.
        dry_run: If True, skip writing content files to project-local paths.

    Returns:
        Dict with keys:
            claude_md: Composed CLAUDE.md text.
            lock: Lock file dict.
            auxiliary_agents: List of auxiliary agent info dicts.
            files_to_write: List of file paths that would be written.
            report: Composition report dict.
            errors: List of error strings (empty on success).
    """
    errors: List[str] = []

    # 1. Load schema and anchor map.
    try:
        schema = _load_schema(base_path)
        anchor_map = _load_anchor_map(base_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "claude_md": "",
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": [],
            "report": {},
            "errors": [f"Cannot load schema or anchor map: {exc}"],
        }

    # Guard: project_path must not be inside or equal to base_path.
    real_base = os.path.realpath(base_path)
    real_project = os.path.realpath(project_path)
    if real_project == real_base or real_project.startswith(real_base + os.sep):
        return {
            "claude_md": "",
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": [],
            "report": {},
            "errors": [
                f"project path {project_path!r} is inside or equal to the "
                f"plugin directory {base_path!r}; composition must not write "
                f"into the installed plugin"
            ],
        }

    # 2. Read and validate each overlay manifest.
    validated_manifests: List[dict] = []
    overlay_path_map: Dict[str, str] = {}
    validation_warnings: List[str] = []

    for overlay_path in overlay_paths:
        try:
            manifest = _read_manifest(overlay_path)
        except FileNotFoundError:
            errors.append(
                f"system2.overlay.json not found in {overlay_path}"
            )
            continue
        except json.JSONDecodeError as exc:
            errors.append(
                f"system2.overlay.json is not valid JSON in {overlay_path}: {exc}"
            )
            continue

        vr = validate_manifest(manifest, schema, overlay_path, anchor_map)
        overlay_label = manifest.get("name", overlay_path)
        if vr.warnings:
            for w in vr.warnings:
                validation_warnings.append(f"[{overlay_label}] {w}")
        if not vr.valid:
            if allow_newer_schema:
                remaining_errors = []
                for e in vr.errors:
                    if "schema_version:" in e and "is not supported" in e:
                        validation_warnings.append(
                            e.replace("Use --allow-newer-schema", "Degraded mode active")
                        )
                    else:
                        remaining_errors.append(e)
                if remaining_errors:
                    errors.extend(remaining_errors)
                    continue
                vr.valid = True
            else:
                errors.extend(vr.errors)
                continue

        name = manifest.get("name", "<unknown>")
        if name in overlay_path_map:
            errors.append(
                f"duplicate overlay name {name!r}: provided from both "
                f"{overlay_path_map[name]} and {overlay_path}"
            )
            continue

        validated_manifests.append(manifest)
        overlay_path_map[name] = overlay_path

    if errors:
        return {
            "claude_md": "",
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": [],
            "report": {"validation_errors": errors},
            "errors": errors,
        }

    # 3. Run conflict detection.
    conflict_report = detect_conflicts(validated_manifests, anchor_map)

    if conflict_report.has_structural_conflicts:
        for sc in conflict_report.structural_conflicts:
            parts = [f"Structural conflict: {sc['message']}"]
            if "contribution_type" in sc:
                parts.append(f"  Type: {sc['contribution_type']}")
            if "target" in sc:
                parts.append(f"  Target: {sc['target']}")
            if "suggested_resolution" in sc:
                parts.append(f"  Resolution: {sc['suggested_resolution']}")
            errors.append("\n".join(parts))
        return {
            "claude_md": "",
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": [],
            "report": {"conflicts": _conflict_report_to_dict(conflict_report)},
            "errors": errors,
        }

    # Pre-compute valid anchors for filtering (used by injection scan and fingerprint).
    anchors_by_agent = {
        name: list(info.get("anchors", {}).keys())
        for name, info in anchor_map.get("agents", {}).items()
    }

    # 3a. Scan applied overlay content files for prompt injection patterns.
    injection_warnings: List[str] = []
    for manifest in validated_manifests:
        name = manifest.get("name", "<unknown>")
        source_path = overlay_path_map.get(name, "")
        content_files: List[str] = []
        _collect_applied_content_files(manifest, content_files, anchors_by_agent)
        for cf in content_files:
            full = os.path.join(source_path, cf)
            if os.path.isfile(full):
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                injection_warnings.extend(
                    _scan_for_injection(content, f"{name}/{cf}")
                )

    # 4. Prepare overlay content info (copying is deferred to _write_outputs).
    overlay_local_paths: Dict[str, str] = {}
    overlay_info_for_lock: List[dict] = []
    input_mtimes: List[float] = []
    pending_content_copies: List[Tuple[str, str, dict]] = []

    for manifest in validated_manifests:
        name = manifest.get("name", "<unknown>")
        version = manifest.get("version", "")
        source_path = overlay_path_map.get(name, "")

        manifest_file = os.path.join(source_path, "system2.overlay.json")
        with open(manifest_file, "rb") as fh:
            manifest_bytes = fh.read()
            manifest_hash = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
        input_mtimes.append(os.path.getmtime(manifest_file))

        pending_content_copies.append((source_path, name, manifest))
        if dry_run:
            local_path = source_path
            content_hash = ""
        else:
            local_path = os.path.join(
                project_path, ".system2", "overlays", name
            )
            content_hash = ""

        overlay_local_paths[name] = local_path

        overlay_info_for_lock.append({
            "name": name,
            "version": version,
            "source_path": source_path,
            "local_path": f".system2/overlays/{name}/",
            "manifest_hash": manifest_hash,
            "content_hash": content_hash,
        })

    # 5. Read base CLAUDE.md template.
    # Try the init skill template first (works in installed plugin),
    # then fall back to sibling CLAUDE.md (works in repo checkout).
    base_claude_md = ""
    init_skill_path = os.path.join(base_path, "skills", "init", "SKILL.md")
    repo_claude_path = os.path.join(os.path.dirname(base_path), "CLAUDE.md")

    if os.path.isfile(init_skill_path):
        try:
            with open(init_skill_path, "r", encoding="utf-8") as fh:
                skill_content = fh.read()
            begin = skill_content.find("---BEGIN TEMPLATE---")
            end = skill_content.find("---END TEMPLATE---")
            if begin != -1 and end != -1:
                begin += len("---BEGIN TEMPLATE---\n")
                base_claude_md = skill_content[begin:end].rstrip("\n") + "\n"
        except OSError:
            pass

    if not base_claude_md and os.path.isfile(repo_claude_path):
        try:
            with open(repo_claude_path, "r", encoding="utf-8") as fh:
                base_claude_md = fh.read()
        except OSError:
            pass

    if not base_claude_md:
        return {
            "claude_md": "",
            "lock": {},
            "auxiliary_agents": [],
            "files_to_write": [],
            "report": {},
            "errors": [
                f"Cannot read base CLAUDE.md: checked {init_skill_path} "
                f"and {repo_claude_path}"
            ],
        }

    # Read System2 version from plugin.json (works installed) or VERSION (works in repo).
    system2_version = "unknown"
    plugin_json_path = os.path.join(base_path, ".claude-plugin", "plugin.json")
    repo_version_path = os.path.join(os.path.dirname(base_path), "VERSION")
    if os.path.isfile(plugin_json_path):
        try:
            with open(plugin_json_path, "r", encoding="utf-8") as fh:
                pj = json.load(fh)
            system2_version = pj.get("version", "unknown")
        except (OSError, json.JSONDecodeError):
            pass
    if system2_version == "unknown" and os.path.isfile(repo_version_path):
        try:
            with open(repo_version_path, "r", encoding="utf-8") as fh:
                system2_version = fh.read().strip()
        except OSError:
            pass

    # Deterministic content fingerprint for idempotency.
    fp_hasher = hashlib.sha256()
    fp_hasher.update(system2_version.encode())
    fp_hasher.update(base_claude_md.encode())
    for info in sorted(overlay_info_for_lock, key=lambda x: x["name"]):
        fp_hasher.update(info["manifest_hash"].encode())
        source_path = overlay_path_map.get(info["name"], "")
        if source_path:
            manifest = next(
                (m for m in validated_manifests if m.get("name") == info["name"]),
                {},
            )
            content_files: List[str] = []
            _collect_applied_content_files(manifest, content_files, anchors_by_agent)
            for cf in sorted(content_files):
                cf_path = os.path.join(source_path, cf)
                if os.path.isfile(cf_path):
                    with open(cf_path, "rb") as fh:
                        fp_hasher.update(fh.read())
    content_fingerprint = f"sha256:{fp_hasher.hexdigest()}"

    # Timestamp: reuse previous composed_at when fingerprint matches
    # Reuse previous timestamp when fingerprint matches (idempotency).
    # Use fresh time only on first composition or when inputs have changed.
    prev_lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
    composition_timestamp = ""
    if os.path.isfile(prev_lock_path):
        try:
            with open(prev_lock_path, "r", encoding="utf-8") as fh:
                prev_lock = json.load(fh)
            if prev_lock.get("content_fingerprint") == content_fingerprint:
                composition_timestamp = prev_lock.get("composed_at", "")
        except (OSError, json.JSONDecodeError):
            pass
    if not composition_timestamp:
        composition_timestamp = datetime.datetime.now(
            datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 6. Build ordered contributions and generate composed CLAUDE.md.
    index = _build_contribution_index(validated_manifests, anchors_by_agent)
    ordered: Dict[Tuple[str, str], List[Tuple[str, dict, str]]] = {}
    contributions_applied: Dict[str, List[str]] = {}

    for (type_path, target), entries in index.items():
        try:
            sorted_entries, sort_warnings = _topological_sort(entries, type_path)
            for sw in sort_warnings:
                validation_warnings.append(sw)
        except ValueError:
            continue

        augmented = []
        for oname, contrib in sorted_entries:
            source_path = overlay_path_map.get(oname, "")
            augmented.append((oname, contrib, source_path))

        ordered[(type_path, target)] = augmented

        is_deferred = any(type_path.endswith(s) for s in _DEFERRED_SUFFIXES)
        if not is_deferred:
            ids = []
            for oname, contrib in sorted_entries:
                cid = (
                    contrib.get("id")
                    or contrib.get("name")
                    or contrib.get("tool")
                    or ""
                )
                if cid:
                    ids.append(cid)
            if ids:
                contributions_applied[type_path] = ids

    overlay_info = [
        (
            m.get("name", ""),
            m.get("version", ""),
            overlay_local_paths.get(m.get("name", ""), ""),
        )
        for m in validated_manifests
    ]

    composed_claude_md, deferred = _generate_claude_md(
        base_claude_md, ordered, overlay_info, conflict_report,
        timestamp=composition_timestamp,
    )

    # 7. Generate lock file.
    warnings_for_lock = []
    for st in conflict_report.semantic_tensions:
        warnings_for_lock.append(st)

    lock = _generate_lock(
        overlay_info_for_lock,
        contributions_applied,
        warnings_for_lock,
        system2_version,
        timestamp=composition_timestamp,
        content_fingerprint=content_fingerprint,
    )

    # Collect auxiliary agents.
    auxiliary_agents: List[dict] = []
    for manifest in validated_manifests:
        name = manifest.get("name", "<unknown>")
        source_path = overlay_path_map.get(name, "")
        for aux in manifest.get("contributions", {}).get("auxiliary_agents", []):
            agent_file = aux.get("agent_file", "")
            auxiliary_agents.append({
                "name": aux.get("name", ""),
                "source_file": os.path.join(source_path, agent_file),
            })

    # Determine files that would be written.
    files_to_write = [
        os.path.join(project_path, "CLAUDE.md"),
        os.path.join(project_path, "spec", "overlay-manifest.lock"),
    ]
    for agent_info in auxiliary_agents:
        files_to_write.append(
            os.path.join(
                project_path, ".claude", "agents",
                f"{agent_info['name']}.md",
            )
        )
    for pcc_source, pcc_name, pcc_manifest in pending_content_copies:
        overlay_dir = os.path.join(
            project_path, ".system2", "overlays", pcc_name
        )
        files_to_write.append(f"{overlay_dir}/ (overlay content)")

    # Build report.
    composed_lines = composed_claude_md.count("\n") + 1
    report = {
        "overlays": [
            {"name": info["name"], "version": info["version"]}
            for info in overlay_info_for_lock
        ],
        "contributions_applied": contributions_applied,
        "deferred": deferred,
        "injection_warnings": injection_warnings,
        "validation_warnings": validation_warnings,
        "conflicts": _conflict_report_to_dict(conflict_report),
        "composed_lines": composed_lines,
        "files_to_write": files_to_write,
    }
    if composed_lines > 500:
        report["size_warning"] = (
            f"Composed CLAUDE.md is {composed_lines} lines "
            f"(exceeds 500-line threshold)."
        )

    return {
        "claude_md": composed_claude_md,
        "lock": lock,
        "auxiliary_agents": auxiliary_agents,
        "files_to_write": files_to_write,
        "pending_content_copies": pending_content_copies,
        "overlay_info_for_lock": overlay_info_for_lock,
        "valid_anchors_by_agent": anchors_by_agent,
        "report": report,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="System2 overlay composition engine",
    )
    parser.add_argument(
        "--base",
        required=True,
        help="Path to the System2 plugin root (e.g., plugin/)",
    )
    parser.add_argument(
        "--overlays",
        default="",
        help="Comma-separated overlay directory paths",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Path to the target project root",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report without writing files",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--allow-injection",
        action="store_true",
        help="Proceed despite prompt injection warnings",
    )
    parser.add_argument(
        "--allow-newer-schema",
        action="store_true",
        help="Allow overlays with unsupported schema_version (degraded mode)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Read-only drift check against the lock file",
    )
    parser.add_argument(
        "--from-lock",
        action="store_true",
        help="Read overlay source paths from the existing lock file",
    )
    parser.add_argument(
        "--uninstall",
        metavar="NAME",
        default="",
        help="Remove a named overlay from the current composition",
    )

    args = parser.parse_args()

    base_path = os.path.abspath(args.base)
    project_path = os.path.abspath(args.project)

    # Doctor mode: read-only drift check.
    if args.doctor:
        result = drift_check(base_path, project_path)
        if args.format == "json":
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _print_doctor_report(result)
        sys.exit(0 if result["status"] == "current" else 1)

    # Uninstall mode.
    if args.uninstall:
        if args.overlays or args.from_lock:
            _emit_error(
                "--uninstall is mutually exclusive with --overlays and --from-lock",
                args.format,
            )
            sys.exit(1)

        try:
            result = _uninstall(
                base_path,
                project_path,
                args.uninstall,
                dry_run=args.dry_run,
                allow_newer_schema=args.allow_newer_schema,
            )
        except OSError as exc:
            _emit_error(f"I/O error during uninstall: {exc}", args.format)
            sys.exit(3)

        if result["errors"]:
            errors = result["errors"]
            exit_code = 1
            if any("Structural conflict" in e for e in errors):
                exit_code = 2
            elif any("Cannot" in e or "I/O" in e for e in errors):
                exit_code = 3
            if args.format == "json":
                sys.stdout.write(json.dumps({
                    "status": "error",
                    "errors": errors,
                    "report": result.get("report", {}),
                }, indent=2) + "\n")
            else:
                for err in errors:
                    sys.stderr.write(f"ERROR: {err}\n")
            sys.exit(exit_code)

        report = result["report"]
        uninstall_meta = report.get("uninstall", {})

        _emit_stderr_warnings(report)

        if args.dry_run:
            if args.format == "json":
                sys.stdout.write(json.dumps({
                    "status": "success",
                    "report": report,
                }, indent=2) + "\n")
            else:
                sys.stdout.write("Composition Report\n")
                sys.stdout.write("=" * 40 + "\n")
                for ov in report.get("overlays", []):
                    sys.stdout.write(f"  Overlay: {ov['name']}@{ov['version']}\n")
                sys.stdout.write(
                    f"\nComposed CLAUDE.md: {report.get('composed_lines', 0)} lines\n"
                )
                contribs = report.get("contributions_applied", {})
                if contribs:
                    sys.stdout.write("\nContributions applied:\n")
                    for scope, ids in contribs.items():
                        sys.stdout.write(f"  {scope}: {ids}\n")
                removed = uninstall_meta.get("removed", {})
                sys.stdout.write(
                    f"\nUninstall: {removed.get('name', '')}@"
                    f"{removed.get('version', '')}\n"
                )
                remaining = uninstall_meta.get("remaining", [])
                if remaining:
                    remaining_str = ", ".join(
                        f"{r['name']}@{r.get('version', '')}" for r in remaining
                    )
                    sys.stdout.write(f"Remaining overlays: {remaining_str}\n")
                else:
                    sys.stdout.write("Remaining overlays: (none)\n")
                artifacts = uninstall_meta.get("artifacts_removed", [])
                if artifacts:
                    sys.stdout.write("Files/directories to remove:\n")
                    for a in artifacts:
                        sys.stdout.write(f"  {a}\n")
                sys.stdout.write("\n--- Composed CLAUDE.md (preview) ---\n")
                preview_lines = result["claude_md"].split("\n")[:20]
                for pl in preview_lines:
                    sys.stdout.write(pl + "\n")
                if len(result["claude_md"].split("\n")) > 20:
                    sys.stdout.write("... (truncated)\n")
                sys.stdout.write(f"\nFiles that would be written:\n")
                for fp in result["files_to_write"]:
                    sys.stdout.write(f"  {fp}\n")
            sys.exit(0)

        injection_warns = report.get("injection_warnings", [])
        if injection_warns and not args.allow_injection:
            if args.format == "json":
                sys.stdout.write(json.dumps({
                    "status": "injection_blocked",
                    "injection_warnings": injection_warns,
                    "report": report,
                    "message": (
                        "Prompt injection warnings detected. "
                        "Re-run with --allow-injection to proceed."
                    ),
                }, indent=2) + "\n")
            else:
                sys.stderr.write(
                    "\nERROR: Prompt injection warnings detected in overlay "
                    "content files. Composition blocked in write mode. Review "
                    "the warnings above, then re-run with --allow-injection to "
                    "proceed, or fix the overlay content files.\n"
                )
            sys.exit(4)

        is_last_overlay = not result["lock"]

        if not is_last_overlay:
            try:
                written = _write_outputs(
                    project_path,
                    result["claude_md"],
                    result["lock"],
                    result["auxiliary_agents"],
                    pending_content_copies=result.get("pending_content_copies", []),
                    overlay_info_for_lock=result.get("overlay_info_for_lock", []),
                    valid_anchors_by_agent=result.get("valid_anchors_by_agent"),
                )
            except OSError as exc:
                _emit_error(f"I/O error writing outputs: {exc}", args.format)
                sys.exit(3)
        else:
            written = result["files_to_write"]

        removed = uninstall_meta.get("removed", {})
        remaining = uninstall_meta.get("remaining", [])
        artifacts = uninstall_meta.get("artifacts_removed", [])

        if args.format == "json":
            report["files_written"] = written
            sys.stdout.write(json.dumps({
                "status": "success",
                "report": report,
            }, indent=2) + "\n")
        else:
            sys.stdout.write("Uninstall complete.\n")
            sys.stdout.write(
                f"  Removed: {removed.get('name', '')}@"
                f"{removed.get('version', '')}\n"
            )
            if remaining:
                remaining_str = ", ".join(
                    f"{r['name']}@{r.get('version', '')}" for r in remaining
                )
                sys.stdout.write(f"  Remaining: {remaining_str}\n")
            else:
                sys.stdout.write("  Remaining: (none)\n")
            sys.stdout.write("  Files written:\n")
            for fp in written:
                sys.stdout.write(f"    {fp}\n")
            if artifacts:
                sys.stdout.write("  Files/directories removed:\n")
                for a in artifacts:
                    sys.stdout.write(f"    {a}\n")

        sys.exit(0)

    # Resolve overlay paths: --from-lock reads from the lock file.
    if args.from_lock:
        lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
        if not os.path.isfile(lock_path):
            _emit_error(
                "No lock file found at spec/overlay-manifest.lock; "
                "cannot use --from-lock",
                args.format,
            )
            sys.exit(1)
        try:
            with open(lock_path, "r", encoding="utf-8") as fh:
                lock_data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            _emit_error(f"Cannot read lock file: {exc}", args.format)
            sys.exit(1)
        overlay_paths = [
            ov["source_path"]
            for ov in lock_data.get("overlays", [])
            if ov.get("source_path")
        ]
        if not overlay_paths:
            _emit_error(
                "Lock file contains no overlay source paths", args.format,
            )
            sys.exit(1)
    elif args.overlays:
        overlay_paths = [
            os.path.abspath(p.strip())
            for p in args.overlays.split(",")
            if p.strip()
        ]
    else:
        _emit_error(
            "Either --overlays or --from-lock is required", args.format,
        )
        sys.exit(1)

    # Run the full composition pipeline.
    result = compose(
        base_path, overlay_paths, project_path,
        dry_run=args.dry_run,
        allow_newer_schema=args.allow_newer_schema,
    )

    # Handle errors — classify by type for exit code.
    if result["errors"]:
        errors = result["errors"]
        # Determine exit code: 1 for validation, 2 for conflicts, 3 for I/O.
        exit_code = 1
        if any("Structural conflict" in e for e in errors):
            exit_code = 2
        elif any("Cannot" in e or "I/O" in e for e in errors):
            exit_code = 3

        if args.format == "json":
            sys.stdout.write(json.dumps({
                "status": "error",
                "errors": errors,
                "report": result.get("report", {}),
            }, indent=2) + "\n")
        else:
            for err in errors:
                sys.stderr.write(f"ERROR: {err}\n")
        sys.exit(exit_code)

    # Success path — print report.
    report = result["report"]

    # Emit stderr warnings for all formats (the skill inspects stderr).
    _emit_stderr_warnings(report)

    if args.format == "json" and args.dry_run:
        sys.stdout.write(json.dumps({
            "status": "success",
            "report": report,
        }, indent=2) + "\n")
    elif args.format == "json":
        pass  # JSON output deferred until after writes succeed.
    else:
        sys.stdout.write("Composition Report\n")
        sys.stdout.write("=" * 40 + "\n")
        for ov in report.get("overlays", []):
            sys.stdout.write(f"  Overlay: {ov['name']}@{ov['version']}\n")
        sys.stdout.write(f"\nComposed CLAUDE.md: {report.get('composed_lines', 0)} lines\n")
        sys.stdout.write("\nContributions applied:\n")
        for scope, ids in report.get("contributions_applied", {}).items():
            sys.stdout.write(f"  {scope}: {ids}\n")
        deferred_report = report.get("deferred", {})
        if deferred_report:
            sys.stdout.write("\nDeferred (declared but not applied in this phase):\n")
            for scope, count in deferred_report.items():
                sys.stdout.write(f"  {scope}: {count}\n")
        conflicts = report.get("conflicts", {})
        if conflicts.get("additive_overlaps"):
            sys.stdout.write("\nAdditive overlaps (deterministic order):\n")
            for ao in conflicts["additive_overlaps"]:
                sys.stdout.write(f"  {ao['scope']}: {ao['order']}\n")

    if args.dry_run:
        if args.format == "text":
            sys.stdout.write("\n--- Composed CLAUDE.md (preview) ---\n")
            preview_lines = result["claude_md"].split("\n")[:20]
            for pl in preview_lines:
                sys.stdout.write(pl + "\n")
            if len(result["claude_md"].split("\n")) > 20:
                sys.stdout.write("... (truncated)\n")
            sys.stdout.write(f"\nFiles that would be written:\n")
            for fp in result["files_to_write"]:
                sys.stdout.write(f"  {fp}\n")
        sys.exit(0)

    # Block write mode if injection warnings are present and not acknowledged.
    injection_warns = report.get("injection_warnings", [])
    if injection_warns and not args.allow_injection:
        if args.format == "json":
            sys.stdout.write(json.dumps({
                "status": "injection_blocked",
                "injection_warnings": injection_warns,
                "report": report,
                "message": (
                    "Prompt injection warnings detected. "
                    "Re-run with --allow-injection to proceed."
                ),
            }, indent=2) + "\n")
        else:
            sys.stderr.write(
                "\nERROR: Prompt injection warnings detected in overlay "
                "content files. Composition blocked in write mode. Review "
                "the warnings above, then re-run with --allow-injection to "
                "proceed, or fix the overlay content files.\n"
            )
        sys.exit(4)

    # Write outputs.
    try:
        written = _write_outputs(
            project_path,
            result["claude_md"],
            result["lock"],
            result["auxiliary_agents"],
            pending_content_copies=result.get("pending_content_copies", []),
            overlay_info_for_lock=result.get("overlay_info_for_lock", []),
            valid_anchors_by_agent=result.get("valid_anchors_by_agent"),
        )
    except OSError as exc:
        _emit_error(f"I/O error writing outputs: {exc}", args.format)
        sys.exit(3)

    if args.format == "json":
        report["files_written"] = written
        sys.stdout.write(json.dumps({
            "status": "success",
            "report": report,
        }, indent=2) + "\n")
    else:
        sys.stdout.write("\nFiles written:\n")
        for fp in written:
            sys.stdout.write(f"  {fp}\n")

    sys.exit(0)


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _build_json_report(
    results: List[Tuple[str, ValidationResult]], any_failure: bool
) -> dict:
    overlays = []
    for path, vr in results:
        overlays.append({
            "path": path,
            "valid": vr.valid,
            "errors": vr.errors,
            "warnings": vr.warnings,
        })
    return {
        "status": "validation_failed" if any_failure else "validation_passed",
        "overlays": overlays,
    }


def _print_text_report(
    results: List[Tuple[str, ValidationResult]], any_failure: bool
) -> None:
    for path, vr in results:
        header = f"Overlay: {path}"
        sys.stdout.write(header + "\n")
        if vr.valid:
            sys.stdout.write("  validation passed\n")
        else:
            for err in vr.errors:
                sys.stderr.write(f"  ERROR: {err}\n")
        for warn in vr.warnings:
            sys.stderr.write(f"  WARNING: {warn}\n")
        sys.stdout.write("\n")

    if any_failure:
        sys.stderr.write("Validation failed.\n")
    else:
        sys.stdout.write("All overlays: validation passed\n")


def _conflict_report_to_dict(cr: ConflictReport) -> dict:
    """Serialize a ConflictReport to a JSON-safe dict."""

    def _serialize_order(order: List[Tuple[str, dict]]) -> list:
        return [
            {"overlay": overlay, "id": entry.get("id")}
            for overlay, entry in order
        ]

    return {
        "structural_conflicts": cr.structural_conflicts,
        "additive_overlaps": [
            {
                "scope": ao["scope"],
                "overlays": ao["overlays"],
                "order": _serialize_order(ao["order"]),
            }
            for ao in cr.additive_overlaps
        ],
        "semantic_tensions": cr.semantic_tensions,
    }


def _print_conflict_report(cr: ConflictReport) -> None:
    """Print human-readable conflict report to stdout/stderr."""
    if cr.structural_conflicts:
        sys.stderr.write("\nStructural conflicts (composition blocked):\n")
        for sc in cr.structural_conflicts:
            sys.stderr.write(f"  CONFLICT: {sc['message']}\n")

    if cr.additive_overlaps:
        sys.stdout.write("\nAdditive overlaps (deterministic order):\n")
        for ao in cr.additive_overlaps:
            ids = [e[1].get("id", "?") for e in ao["order"]]
            sys.stdout.write(
                f"  {ao['scope']}: overlays {ao['overlays']}, "
                f"order: {ids}\n"
            )

    if cr.semantic_tensions:
        sys.stderr.write("\nSemantic tensions (warnings):\n")
        for st in cr.semantic_tensions:
            sys.stderr.write(f"  WARNING: {st['message']}\n")

    if not cr.structural_conflicts and not cr.semantic_tensions:
        sys.stdout.write("\nNo conflicts detected.\n")


def _emit_stderr_warnings(report: dict) -> None:
    """Emit all warning categories to stderr (used by both text and JSON modes)."""
    if "size_warning" in report:
        sys.stderr.write(f"  WARNING: {report['size_warning']}\n")
    for vw in report.get("validation_warnings", []):
        sys.stderr.write(f"  WARNING: {vw}\n")
    for iw in report.get("injection_warnings", []):
        sys.stderr.write(f"  WARNING: {iw}\n")
    conflicts = report.get("conflicts", {})
    if conflicts.get("semantic_tensions"):
        sys.stderr.write("Semantic tensions (warnings):\n")
        for st in conflicts["semantic_tensions"]:
            sys.stderr.write(f"  WARNING: {st['message']}\n")


def _emit_error(msg: str, fmt: str) -> None:
    if fmt == "json":
        sys.stdout.write(json.dumps({"status": "error", "message": msg}) + "\n")
    else:
        sys.stderr.write(f"ERROR: {msg}\n")


def _print_doctor_report(result: dict) -> None:
    """Print human-readable drift check report."""
    status = result["status"]
    version_info = result["system2_version"]
    overlays = result["overlays"]

    status_labels = {
        "current": "Current",
        "stale_base": "Stale base",
        "stale_overlay": "Stale overlay",
        "broken": "Broken",
        "no_lock": "No lock file",
    }
    sys.stdout.write(f"Status: {status_labels.get(status, status)}\n")
    sys.stdout.write(
        f"System2 version: installed={version_info['installed']}, "
        f"locked={version_info['locked']}\n"
    )
    sys.stdout.write(
        f"CLAUDE.md composed: {'yes' if result['claude_md_composed'] else 'no'}\n"
    )

    if overlays:
        sys.stdout.write(f"\nOverlays ({len(overlays)}):\n")
        for ov in overlays:
            src_ok = "ok" if ov["source_exists"] else "MISSING"
            local_ok = "ok" if ov["local_exists"] else "MISSING"
            manifest_ok = (
                "ok" if ov["manifest_match"] is True
                else "CHANGED" if ov["manifest_match"] is False
                else "n/a"
            )
            content_ok = (
                "ok" if ov["content_match"] is True
                else "CHANGED" if ov["content_match"] is False
                else "n/a"
            )
            local_content_ok = (
                "ok" if ov.get("local_match") is True
                else "CHANGED" if ov.get("local_match") is False
                else "n/a"
            )
            sys.stdout.write(
                f"  {ov['name']}: source={src_ok}, local={local_ok}, "
                f"manifest={manifest_ok}, content={content_ok}, "
                f"local_content={local_content_ok}\n"
            )

    if result["details"]:
        sys.stdout.write("\nFindings:\n")
        for d in result["details"]:
            sys.stdout.write(f"  - {d['message']}\n")

    if status == "current":
        sys.stdout.write("\nAll overlays match the lock file. No action needed.\n")
    elif status in ("stale_base", "stale_overlay"):
        sys.stdout.write(
            "\nRun /system2:compose --from-lock to refresh composition.\n"
        )
    elif status == "broken":
        sys.stdout.write(
            "\nOverlay source paths or local copies are missing. "
            "Fix the paths, then run /system2:compose --from-lock.\n"
        )


if __name__ == "__main__":
    main()
