"""Claude Code backend for composed project artifacts."""

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from system2_compiler.ir.graph import System2Graph

from . import _degradation
from .base import DoctorReport, UninstallResult, lock_sources_outside_project

__all__ = ["ClaudeCodeBackend"]

# Overlay-name validation (kebab-case), lifted verbatim from ``composer._KEBAB_RE``.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Backend-owned capability metadata for the lock's degradation report.
_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "claude_code.json"
)


# Contribution rendering (lifted verbatim from composer._render_contribution)

def _resolve_content_file(overlay_path: str, content_file: str) -> str:
    """Read and return content of a referenced file from the overlay directory."""
    full = os.path.join(overlay_path, content_file)
    with open(full, "r", encoding="utf-8") as fh:
        return fh.read()


def _render_contribution(
    contribution: dict,
    overlay_name: str,
    overlay_local_path: str,
    contribution_type: str,
) -> str:
    """Render a single contribution for inclusion in composed CLAUDE.md."""
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


# Composed CLAUDE.md generation (lifted verbatim)

# Section heading patterns in the base CLAUDE.md.
_SECTION_RE = re.compile(r"^## (.+)$")
_GATE_LINE_RE = re.compile(r"^- Gate (\d+) ")

# Contribution types declared but not applied by this backend.
_DEFERRED_SUFFIXES = (".tools", ".hooks")


def _generate_claude_md(
    base_claude_md: str,
    ordered_contributions: dict,
    overlays: list,
    timestamp: str = "",
) -> Tuple[str, Dict[str, int]]:
    """Produce a composed CLAUDE.md from the base content and ordered contributions."""
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

    # Insert principles, consultations, and advisory sources in their base sections.
    # Append all other overlay sections after the base content.

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
                        # Group related consultation entries.
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


# Content copying (lifted verbatim)

def _collect_content_files(
    manifest: dict,
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Extract content_file and agent_file paths from a manifest."""
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


def _collect_file_refs(obj: object, out: List[str]) -> None:
    """Recursively collect content_file and agent_file values (lifted from the oracle)."""
    if isinstance(obj, dict):
        for key in ("content_file", "agent_file"):
            if key in obj and isinstance(obj[key], str):
                out.append(obj[key])
        for val in obj.values():
            _collect_file_refs(val, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_file_refs(item, out)


def _collect_applied_content_files(
    manifest: dict,
    out: List[str],
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> None:
    """Collect content_file/agent_file paths only for applied contributions."""
    contribs = manifest.get("contributions", {})

    for key in ("orchestrator", "delegation", "spec"):
        if key in contribs:
            _collect_file_refs(contribs[key], out)
    for key in ("auxiliary_agents", "mcp_servers", "permissions"):
        if key in contribs:
            _collect_file_refs(contribs[key], out)

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


def _copy_overlay_content(
    overlay_path: str,
    manifest: dict,
    target_dir: str,
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Copy content_file and agent_file references into *target_dir*."""
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


# Lock file generation (lifted verbatim)

def _build_degradation_report(
    ir: System2Graph, descriptor_path: str = _DESCRIPTOR_PATH
) -> dict:
    """Build the lock's ``degradation_report`` from the IR + the backend descriptor."""
    with open(descriptor_path, "r", encoding="utf-8") as fh:
        descriptor = json.load(fh)

    union = _degradation.ir_capability_union(ir.capabilities.by_agent)
    report_caps = _degradation.build_capability_records(
        descriptor, union, fields=("status", "mechanism"), allow_native=True
    )

    return {
        "backend": descriptor.get("backend", ClaudeCodeBackend.name),
        "capabilities": report_caps,
    }


def _generate_lock(
    overlays: List[dict],
    contributions_applied: Dict[str, List[str]],
    warnings: List[dict],
    system2_version: str,
    timestamp: str = "",
    content_fingerprint: str = "",
    degradation_report: Optional[dict] = None,
) -> dict:
    """Generate the stable Claude lock structure."""

    lock = {
        "composed_at": timestamp,
        "content_fingerprint": content_fingerprint,
        "system2_version": system2_version,
        "schema_version": "1.0.0",
        "overlays": overlays,
        "contributions_applied": contributions_applied,
        "warnings": warnings,
    }
    if degradation_report is not None:
        lock["degradation_report"] = degradation_report
    return lock


# Atomic write/restore (lifted verbatim)

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


def _default_file_mode(existing_path: Optional[str] = None) -> int:
    """Return the mode a regenerated file should have."""
    if existing_path is not None and os.path.exists(existing_path):
        return os.stat(existing_path).st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _write_outputs(
    project_path: str,
    claude_md: str,
    lock: dict,
    auxiliary_agents: List[dict],
    pending_content_copies: Optional[List[Tuple[str, str, dict]]] = None,
    overlay_info_for_lock: Optional[List[dict]] = None,
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """Write composed artifacts atomically."""
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

    # Identify stale artifacts from previous composition.
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

    # Back up existing files and overlay directories.
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

    # Keep all writes inside the rollback boundary.
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
        stale_backups = []
        stale_dir_backups = []
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
                # Apply the final mode because mkstemp creates files as 0600.
                os.chmod(tmp_path, _default_file_mode(file_path))
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
        # Restore backups and remove newly created paths.
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

    # The write succeeded; clean up backups.
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


# IR -> Claude lowering driver

def _ordered_from_ir(ir: System2Graph) -> dict:
    """Reconstruct the oracle's ``ordered`` dict from the IR's ordered contributions."""
    ordered: dict = {}
    for key, records in ir.contributions.scopes.items():
        ordered[key] = [
            (rec.overlay_name, rec.raw, rec.overlay_path) for rec in records
        ]
    return ordered


def _contributions_applied_from_ir(ir: System2Graph) -> Dict[str, List[str]]:
    """Reconstruct ``contributions_applied`` (lock) from the IR ordered contributions."""
    contributions_applied: Dict[str, List[str]] = {}
    for (type_path, _target), records in ir.contributions.scopes.items():
        is_deferred = any(type_path.endswith(s) for s in _DEFERRED_SUFFIXES)
        if is_deferred:
            continue
        ids = []
        for rec in records:
            contrib = rec.raw
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
    return contributions_applied


def _compute_idempotency(
    ir: System2Graph,
    overlay_info_for_lock: List[dict],
    project_path: str,
) -> Tuple[str, str]:
    """Compute (content_fingerprint, composed_at) exactly as the oracle does."""
    valid_anchors_by_agent = ir.anchors.anchors_by_agent()
    fp_hasher = hashlib.sha256()
    fp_hasher.update(ir.system2_version.encode())
    fp_hasher.update(ir.base_template.text.encode())
    overlay_by_name = {
        oi.manifest.get("name", "<unknown>"): oi for oi in ir.overlay_inputs
    }
    for info in sorted(overlay_info_for_lock, key=lambda x: x["name"]):
        fp_hasher.update(info["manifest_hash"].encode())
        oi = overlay_by_name.get(info["name"])
        source_path = oi.source_path if oi is not None else ""
        if source_path and oi is not None:
            content_files: List[str] = []
            _collect_applied_content_files(
                oi.manifest, content_files, valid_anchors_by_agent
            )
            for cf in sorted(content_files):
                cf_path = os.path.join(source_path, cf)
                if os.path.isfile(cf_path):
                    with open(cf_path, "rb") as fh:
                        fp_hasher.update(fh.read())
    content_fingerprint = f"sha256:{fp_hasher.hexdigest()}"

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

    return content_fingerprint, composition_timestamp


# Lifecycle helpers ported from composer.py

def _read_base_template(init_skill_path: str, fallback_path: str) -> str:
    """Read the base System2 CLAUDE.md template (lifted ``composer._read_base_template``)."""
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


def _get_system2_version(base_path: str) -> str:
    """Resolve installed System2 version (lifted ``composer._get_system2_version``)."""
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


def _read_manifest(overlay_path: str) -> dict:
    """Read ``system2.overlay.json`` from *overlay_path* (lifted ``composer._read_manifest``)."""
    manifest_file = os.path.join(overlay_path, "system2.overlay.json")
    with open(manifest_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_anchor_map(base_path: str) -> dict:
    """Read ``schemas/anchor-map.json`` (lifted ``composer._load_anchor_map``)."""
    with open(
        os.path.join(base_path, "schemas", "anchor-map.json"), "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def _compute_stale_artifacts(
    project_path: str, overlay_name: str, lock_data: dict
) -> List[str]:
    """Return absolute paths of artifacts to remove for *overlay_name*."""
    if not _KEBAB_RE.match(overlay_name):
        return []

    stale: List[str] = []

    overlay_dir = os.path.join(
        project_path, ".system2", "overlays", overlay_name
    )
    if os.path.isdir(overlay_dir):
        stale.append(overlay_dir)

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


def _drift_check(base_path: str, project_path: str) -> dict:
    """Read-only drift/status check (lifted verbatim from ``composer.drift_check``)."""
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

    base_stale = installed_version != locked_version
    if base_stale:
        details.append({
            "type": "stale_base",
            "message": (
                f"Installed System2 version ({installed_version}) differs "
                f"from locked version ({locked_version})"
            ),
        })

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

    # Optional source-path advisory; it never changes status or exit code.
    if os.environ.get("SYSTEM2_DOCTOR_ADVISORIES") == "1":
        lock_sources = [ov.get("source_path", "") for ov in lock.get("overlays", [])]
        details.extend(lock_sources_outside_project(lock_sources, project_path))

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


class ClaudeCodeBackend:
    """Project a ``System2Graph`` onto Claude Code artifacts (the only backend)."""

    name = "claude-code"

    def __init__(
        self,
        base_path: Optional[str] = None,
        compose_fn: Optional[Callable[..., object]] = None,
    ) -> None:
        self._base_path = base_path
        self._compose_fn = compose_fn

    def emit(self, ir: System2Graph, project_path: str) -> List[str]:
        return self._emit_graph(ir, project_path, dry_run=False)

    def plan(self, ir: System2Graph, project_path: str) -> List[str]:
        """Return the Claude write plan without mutating the project."""
        return self._emit_graph(ir, project_path, dry_run=True)

    def _emit_graph(
        self, ir: System2Graph, project_path: str, *, dry_run: bool
    ) -> List[str]:
        # Recheck that output cannot overwrite an overlay source tree.
        real_project = os.path.realpath(project_path)
        for oi in ir.overlay_inputs:
            if not oi.source_path:
                continue
            real_src = os.path.realpath(oi.source_path)
            if real_project == real_src or real_project.startswith(real_src + os.sep):
                raise ValueError(
                    f"project path {project_path!r} is inside overlay source "
                    f"{oi.source_path!r}; refusing to write into an overlay tree"
                )

        # Prepare per-overlay lock metadata in front-end order.
        overlay_info_for_lock: List[dict] = []
        pending_content_copies: List[Tuple[str, str, dict]] = []
        overlay_local_paths: Dict[str, str] = {}
        dry_run = dry_run or bool(getattr(ir, "dry_run", False))

        for oi in ir.overlay_inputs:
            manifest = oi.manifest
            name = manifest.get("name", "<unknown>")
            version = manifest.get("version", "")
            source_path = oi.source_path

            manifest_file = os.path.join(source_path, "system2.overlay.json")
            with open(manifest_file, "rb") as fh:
                manifest_bytes = fh.read()
                manifest_hash = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"

            pending_content_copies.append((source_path, name, manifest))
            if dry_run:
                local_path = source_path
            else:
                local_path = os.path.join(
                    project_path, ".system2", "overlays", name
                )
            overlay_local_paths[name] = local_path

            overlay_info_for_lock.append({
                "name": name,
                "version": version,
                "source_path": source_path,
                "local_path": f".system2/overlays/{name}/",
                "manifest_hash": manifest_hash,
                "content_hash": "",
            })

        # Reuse composed_at when the content fingerprint is unchanged.
        content_fingerprint, timestamp = _compute_idempotency(
            ir, overlay_info_for_lock, project_path
        )

        # Read ordered contributions and their applied identifiers.
        ordered = _ordered_from_ir(ir)
        contributions_applied = _contributions_applied_from_ir(ir)

        overlay_info = [
            (
                oi.manifest.get("name", ""),
                oi.manifest.get("version", ""),
                overlay_local_paths.get(oi.manifest.get("name", ""), ""),
            )
            for oi in ir.overlay_inputs
        ]

        # Render CLAUDE.md.
        composed_claude_md, _deferred = _generate_claude_md(
            ir.base_template.text, ordered, overlay_info, timestamp=timestamp,
        )

        # Append degradation_report without affecting the content fingerprint.
        warnings_for_lock = list(ir.warnings.semantic_tensions)
        lock = _generate_lock(
            overlay_info_for_lock,
            contributions_applied,
            warnings_for_lock,
            ir.system2_version,
            timestamp=timestamp,
            content_fingerprint=content_fingerprint,
            degradation_report=_build_degradation_report(ir),
        )

        # Collect auxiliary agents.
        auxiliary_agents: List[dict] = []
        for oi in ir.overlay_inputs:
            name = oi.manifest.get("name", "<unknown>")
            source_path = oi.source_path
            for aux in oi.manifest.get("contributions", {}).get("auxiliary_agents", []):
                agent_file = aux.get("agent_file", "")
                auxiliary_agents.append({
                    "name": aux.get("name", ""),
                    "source_file": os.path.join(source_path, agent_file),
                })

        if dry_run:
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
            for _src, ov_name, _man in pending_content_copies:
                overlay_dir = os.path.join(
                    project_path, ".system2", "overlays", ov_name
                )
                files_to_write.append(f"{overlay_dir}/ (overlay content)")
            return files_to_write

        # Commit all artifacts atomically.
        written = _write_outputs(
            project_path,
            composed_claude_md,
            lock,
            auxiliary_agents,
            pending_content_copies=pending_content_copies,
            overlay_info_for_lock=overlay_info_for_lock,
            valid_anchors_by_agent=ir.anchors.anchors_by_agent(),
        )
        return written

    # Lock helpers

    def lock_path(self, project_path: str) -> str:
        """The Claude target lock artifact: ``spec/overlay-manifest.lock``."""
        return os.path.join(project_path, "spec", "overlay-manifest.lock")

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        """Read the applied overlays' ``source_path`` set from the lock."""
        lp = self.lock_path(project_path)
        if not os.path.isfile(lp):
            raise FileNotFoundError(lp)
        with open(lp, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
        return [
            ov["source_path"]
            for ov in lock_data.get("overlays", [])
            if ov.get("source_path")
        ]

    # Drift reporting

    def doctor(self, project_path: str) -> DoctorReport:
        """Read-only drift/status report (ports ``composer.drift_check``)."""
        base_path = self._require_base_path("doctor")
        result = _drift_check(base_path, project_path)
        status = result["status"]
        return DoctorReport(
            status=status,
            details=result["details"],
            system2_version=result["system2_version"],
            overlays=result["overlays"],
            composed=result["claude_md_composed"],
            exit_code=0 if status == "current" else 1,
            validator_available=True,
        )

    # Lock-based recomposition

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        """Re-emit from a recomposed IR (ports the ``--from-lock`` recompose path)."""
        return self.emit(ir, project_path)

    # Uninstall

    def uninstall(
        self,
        project_path: str,
        overlay_name: str,
        *,
        dry_run: bool = False,
        allow_newer_schema: bool = False,
    ) -> UninstallResult:
        """Remove a named overlay (ports ``composer._uninstall``)."""
        base_path = self._require_base_path("uninstall")

        def _err(errors: List[str]) -> UninstallResult:
            return UninstallResult(
                removed={}, remaining=[], artifacts_removed=[], files_written=[],
                is_last_overlay=False, injection_warnings=[], preview="",
                errors=errors,
            )

        # 1. Validate overlay_name format (kebab-case).
        if not _KEBAB_RE.match(overlay_name):
            return _err([
                f"Invalid overlay name {overlay_name!r}: must be kebab-case "
                f"(lowercase alphanumeric, hyphens only)"
            ])

        # 2. Read lock file.
        lp = self.lock_path(project_path)
        if not os.path.isfile(lp):
            return _err(["No lock file found; no overlays are composed"])
        try:
            with open(lp, "r", encoding="utf-8") as fh:
                lock_data = json.load(fh)
        except json.JSONDecodeError:
            return _err(["Lock file is malformed (invalid JSON)"])
        except OSError as exc:
            return _err([f"Cannot read lock file: {exc}"])

        # 3. Validate lock structure.
        overlays = lock_data.get("overlays", [])
        if not isinstance(overlays, list):
            return _err(["Lock file is malformed: 'overlays' is not a list"])

        # 4. Validate each overlay entry has required fields.
        for ov in overlays:
            if not isinstance(ov, dict) or "name" not in ov:
                return _err(["Lock file overlay entry missing 'name' field"])

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
            return _err([
                f"Overlay {overlay_name!r} is not installed. "
                f"Installed: {installed}"
            ])

        # 6. Validate remaining overlay names (security).
        for ov in remaining:
            if not _KEBAB_RE.match(ov.get("name", "")):
                return _err([
                    f"Lock file contains invalid overlay name: {ov.get('name')!r}"
                ])

        target_version = target_entry.get("version", "unknown")

        # 7. Dispatch based on remaining count.
        if len(remaining) == 0:
            return self._uninstall_last_overlay(
                base_path, project_path, target_entry, lock_data, dry_run,
            )

        # 8. Multi-overlay path: extract source_paths, recompose, emit.
        remaining_paths = []
        for ov in remaining:
            sp = ov.get("source_path", "")
            if not sp:
                return _err([
                    f"Overlay {ov['name']!r} has no source_path in lock file"
                ])
            remaining_paths.append(sp)

        compose_fn = self._require_compose_fn("uninstall")
        result = compose_fn(
            base_path, remaining_paths, project_path, dry_run=dry_run,
            allow_newer_schema=allow_newer_schema,
        )

        artifacts_removed = _compute_stale_artifacts(
            project_path, overlay_name, lock_data,
        )
        remaining_meta = [
            {"name": ov["name"], "version": ov.get("version", "")}
            for ov in remaining
        ]

        if getattr(result, "errors", None):
            errors = list(result.errors)
            errors.append(
                "Remediation: verify that all remaining overlay source paths "
                "are accessible, then retry. If an overlay source has moved, "
                "update the lock file with /system2:compose --from-lock after "
                "correcting the paths."
            )
            return UninstallResult(
                removed={"name": overlay_name, "version": target_version},
                remaining=remaining_meta,
                artifacts_removed=artifacts_removed,
                files_written=[],
                is_last_overlay=False,
                injection_warnings=[],
                preview="",
                errors=errors,
            )

        report = getattr(result, "report", {}) or {}
        injection_warnings = list(report.get("injection_warnings", []))

        files_written: List[str] = []
        preview = ""
        if dry_run:
            files_written = list(getattr(result, "files_to_write", []))
        else:
            files_written = self.emit(result.graph, project_path)

        return UninstallResult(
            removed={"name": overlay_name, "version": target_version},
            remaining=remaining_meta,
            artifacts_removed=artifacts_removed,
            files_written=files_written,
            is_last_overlay=False,
            injection_warnings=injection_warnings,
            preview=preview,
            errors=[],
        )

    def _uninstall_last_overlay(
        self,
        base_path: str,
        project_path: str,
        overlay_entry: dict,
        lock_data: dict,
        dry_run: bool,
    ) -> UninstallResult:
        """Handle uninstall when zero overlays remain (ports ``_uninstall_last_overlay``)."""
        overlay_name = overlay_entry["name"]
        overlay_version = overlay_entry.get("version", "unknown")

        # 1. Read base template.
        init_skill_path = os.path.join(base_path, "skills", "init", "SKILL.md")
        repo_claude_path = os.path.join(os.path.dirname(base_path), "CLAUDE.md")
        base_claude_md = _read_base_template(init_skill_path, repo_claude_path)

        if not base_claude_md:
            return UninstallResult(
                removed={}, remaining=[], artifacts_removed=[], files_written=[],
                is_last_overlay=True, injection_warnings=[], preview="",
                errors=[
                    f"Cannot read base CLAUDE.md template: checked "
                    f"{init_skill_path} and {repo_claude_path}"
                ],
            )

        # 2. Compute artifacts to remove.
        artifacts_to_remove = _compute_stale_artifacts(
            project_path, overlay_name, lock_data
        )

        files_to_write = [os.path.join(project_path, "CLAUDE.md")]
        files_to_remove = [
            os.path.join(project_path, "spec", "overlay-manifest.lock")
        ]

        # 3. Dry-run: return preview without writing.
        if dry_run:
            return UninstallResult(
                removed={"name": overlay_name, "version": overlay_version},
                remaining=[],
                artifacts_removed=artifacts_to_remove,
                files_written=files_to_write + [
                    "(remove) " + f
                    for f in files_to_remove + artifacts_to_remove
                ],
                is_last_overlay=True,
                injection_warnings=[],
                preview=base_claude_md,
                errors=[],
            )

        # 4. Atomic write-and-cleanup.
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
                # Apply the final mode because mkstemp creates files as 0600.
                os.chmod(tmp_path, _default_file_mode(claude_path))
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

        # Remove empty .system2/overlays/ parent directory.
        overlays_parent = os.path.join(project_path, ".system2", "overlays")
        try:
            os.rmdir(overlays_parent)
        except OSError:
            pass

        return UninstallResult(
            removed={"name": overlay_name, "version": overlay_version},
            remaining=[],
            artifacts_removed=artifacts_to_remove,
            files_written=[claude_path],
            is_last_overlay=True,
            injection_warnings=[],
            preview=base_claude_md,
            errors=[],
        )

    # Lifecycle prerequisites

    def _require_base_path(self, verb: str) -> str:
        if not self._base_path:
            raise ValueError(
                f"ClaudeCodeBackend.{verb} requires base_path; construct "
                f"ClaudeCodeBackend(base_path=...) (the CLI supplies it)"
            )
        return self._base_path

    def _require_compose_fn(self, verb: str) -> Callable[..., object]:
        if self._compose_fn is None:
            raise ValueError(
                f"ClaudeCodeBackend.{verb} requires compose_fn to recompose the "
                f"remaining overlay set; construct "
                f"ClaudeCodeBackend(compose_fn=ir.compose)"
            )
        return self._compose_fn
