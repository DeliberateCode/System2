"""Codex plugin backend: workflow skills, role skills, and an honest lock."""

import json
import os
from typing import List, Tuple

from system2_compiler.channel_version import CHANNEL_VERSION
from system2_compiler.ir.graph import System2Graph

from . import _degradation, _yaml
from ._generated import GeneratedArtifactBackend
from .base import (
    build_artifact_ownership,
    render_human_communication_policy,
    render_workflow_contract,
)

__all__ = ["CodexBackend"]

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "codex.json"
)
_CODEX_PLUGIN_VERSION = CHANNEL_VERSION
_CODEX_LOCK = "system2.codex.lock.json"

_RUNTIME_BOUNDARY = (
    "System2 supplies workflow and role guidance through Codex skills. Codex's own "
    "sandbox, approval policy, and tool permissions remain authoritative."
)


def _load_descriptor() -> dict:
    with open(_DESCRIPTOR_PATH, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _build_manifest() -> dict:
    return {
        "name": "system2",
        "version": _CODEX_PLUGIN_VERSION,
        "description": "Deliberate, spec-driven engineering workflows for Codex.",
        "publisher": {
            "name": "DeliberateCode",
            "url": "https://github.com/DeliberateCode/System2",
        },
        "interface": {"displayName": "System2"},
        "skills": "./skills",
    }


def _gate_order(gate_graph) -> List[int]:
    numbers = [gate.number for gate in gate_graph.gates]
    if not gate_graph.edges:
        return sorted(numbers)
    successors = {source: target for source, target in gate_graph.edges}
    targets = {target for _source, target in gate_graph.edges}
    starts = [number for number in numbers if number not in targets]
    current = min(starts) if starts else min(numbers)
    order: List[int] = []
    seen = set()
    while current not in seen:
        order.append(current)
        seen.add(current)
        if current not in successors:
            break
        current = successors[current]
    order.extend(number for number in sorted(numbers) if number not in seen)
    return order


def _frontmatter(name: str, description: str) -> List[str]:
    yaml = _yaml.dump({"name": name, "description": description}).rstrip("\n")
    return ["---", *yaml.splitlines(), "---", ""]


def _build_orchestrator_skill(ir: System2Graph) -> str:
    lines = _frontmatter(
        "system2", "Run the System2 gate workflow and role contracts in Codex."
    )
    lines.extend([
        "# System2 orchestrator (Codex)",
        "",
        "## Runtime boundary",
        "",
        _RUNTIME_BOUNDARY,
        "System2 does not install hooks or modify Codex configuration.",
        "Role switching is in-session skill adoption, not isolated subagent execution.",
        "",
    ])
    lines.extend(render_human_communication_policy(ir))
    lines.extend(["", "## Gate graph (advance 0 -> 5)"])
    gates = {gate.number: gate for gate in ir.gate_graph.gates}
    for number in _gate_order(ir.gate_graph):
        gate = gates[number]
        lines.append(f"- Gate {gate.number} ({gate.name}): {gate.checklist_text}")
    if ir.gate_graph.approval_rule:
        lines.append(f"- Approval rule: {ir.gate_graph.approval_rule}")

    lines.extend([
        "",
        "## Roles (same-session skill adoption)",
        "Invoke the target role skill in the current session. This changes the active "
        "role guidance; it does not create an isolated subagent.",
        "",
        "Available roles (listed in workflow order; use only when relevant):",
    ])
    for index, role_name in enumerate(ir.delegation_contract.preferred_order, 1):
        lines.append(f"{index}. `$system2-role-{role_name}`")
    lines.extend(["", "Give each role:"])
    lines.extend(f"- {field}" for field in ir.delegation_contract.required_fields)
    lines.append("")
    lines.extend(render_workflow_contract(ir))
    lines.extend([
        "",
        "Use `$system2-doctor` for a read-only summary of project workflow state.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_doctor_skill() -> str:
    lines = _frontmatter(
        "system2-doctor", "Summarize System2 workflow state in the current project."
    )
    lines.extend([
        "# System2 doctor (Codex)",
        "",
        "Inspect the current project without editing it:",
        "",
        "1. Check `spec/context.md`, `spec/requirements.md`, `spec/design.md`, and `spec/tasks.md`.",
        "2. Check the current diff and note unfinished work.",
        "3. Report the next gate or implementation step and any blocker.",
        "",
        _RUNTIME_BOUNDARY,
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_role_skill(role) -> str:
    lines = _frontmatter(
        f"system2-role-{role.name}", f"Use the System2 {role.name} role in Codex."
    )
    lines.extend([
        f"# System2 role: {role.name} (Codex)",
        "",
        f"You are the System2 {role.name} specialist. Follow the contract below in "
        "the current session. This skill supplies guidance only; Codex controls "
        "permissions and approvals.",
        "",
        "## Role contract",
        "",
        *role.contract_text.splitlines(),
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_degradation_report(ir: System2Graph) -> dict:
    capabilities = _degradation.build_capability_records(
        _load_descriptor(),
        _degradation.ir_capability_union(ir.capabilities.by_agent),
        fields=("status", "mechanism", "enforced", "gated"),
        allow_native=False,
    )
    return {
        "backend": "codex",
        "codex_plugin_version": _CODEX_PLUGIN_VERSION,
        "enforcement": "host-managed",
        "subagent_isolation": "unsupported",
        "FIDELITY": (
            _RUNTIME_BOUNDARY
            + " System2's role scopes, dangerous-command guidance, sensitive-path "
            "guidance, formatting, type-checking, and budget reporting are advisory."
        ),
        "capabilities": capabilities,
    }


def _planned_files(ir: System2Graph) -> List[Tuple[str, str]]:
    planned: List[Tuple[str, str]] = [
        (
            os.path.join(".codex-plugin", "plugin.json"),
            json.dumps(_build_manifest(), indent=2) + "\n",
        ),
        (
            os.path.join("skills", "system2", "SKILL.md"),
            _build_orchestrator_skill(ir),
        ),
        (
            os.path.join("skills", "system2-doctor", "SKILL.md"),
            _build_doctor_skill(),
        ),
    ]
    roles = {role.name: role for role in ir.roles}
    for role_name in ir.delegation_contract.preferred_order:
        role = roles.get(role_name)
        if role is not None:
            planned.append((
                os.path.join("skills", f"system2-role-{role_name}", "SKILL.md"),
                _build_role_skill(role),
            ))

    ownership = build_artifact_ownership(planned, _CODEX_LOCK)
    lock = _build_degradation_report(ir)
    lock["ownership"] = ownership
    lock["overlay_sources"] = list(ir.overlay_sources)
    planned.append((_CODEX_LOCK, json.dumps(lock, indent=2) + "\n"))
    return planned


class CodexBackend(GeneratedArtifactBackend):
    """Project a ``System2Graph`` onto a Codex plugin and capability lock."""

    name = "codex"
    lock_name = _CODEX_LOCK
    version = _CODEX_PLUGIN_VERSION
    version_key = "codex_plugin_version"
    runtime_boundary = _RUNTIME_BOUNDARY

    def _planned_files(self, ir: System2Graph) -> List[Tuple[str, str]]:
        return _planned_files(ir)
