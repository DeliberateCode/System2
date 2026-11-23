"""Codex plugin backend: workflow skills, role skills, and an honest lock."""

import json
import os
from typing import List, Tuple

from system2_compiler.channel_version import CHANNEL_VERSION
from system2_compiler.ir.graph import System2Graph

from . import _degradation, _yaml
from ._generated import GeneratedArtifactBackend
from ._guidance import (
    overlay_commands,
    overlay_doctor_guidance,
    render_orchestrator_guidance,
    render_role_guidance,
    role_contracts,
    use_workspace_overlay,
    workflow_status,
)

__all__ = ["CodexBackend"]

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "codex.json"
)
_CODEX_PLUGIN_VERSION = CHANNEL_VERSION
_CODEX_LOCK = "system2.codex.lock.json"

_RUNTIME_BOUNDARY = (
    "System2 supplies workflow and role guidance through Codex skills and delegates "
    "through Codex's native subagent tools. Codex's sandbox, approval policy, and "
    "tool permissions remain authoritative. A separate agent thread is not a "
    "separate permission boundary."
)
_DISPATCH = (
    "For each specialist step, resolve the matching System2 role skill and use "
    "Codex's native `spawn_agent` tool to start a fresh subagent thread. Do not "
    "fork the parent conversation. Pass the handoff as a text item and attach the "
    "resolved role `SKILL.md` path as a skill item when the tool accepts `items`; "
    "otherwise give the path and instruct the child to read the skill before acting. "
    "If the child cannot read the path, include the full role guidance in the handoff. "
    "Wait for the result and close the completed thread once its evidence is collected. "
    "If the tools are unavailable, ask the user to enable subagents (`agents.enabled`) and start "
    "a new session; do not emulate them in the parent."
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


def _frontmatter(name: str, description: str) -> List[str]:
    yaml = _yaml.dump({"name": name, "description": description}).rstrip("\n")
    return ["---", *yaml.splitlines(), "---", ""]


def _build_orchestrator_skill(ir: System2Graph, workspace: bool) -> str:
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
        "",
    ])
    if workspace:
        lines.extend(render_orchestrator_guidance(
            ir, lambda name: f"`.system2/codex/skills/system2-role-{name}/SKILL.md`",
            "Delegate using Codex's native `spawn_agent` with fresh context. "
            "Use the exact composed role file below, not the installed base skill. "
            "Read it and pass its full guidance and the task handoff in the spawn message; "
            "these project files are not registered Codex skills. Wait for the result "
            "and close the completed thread after collecting evidence. If delegation "
            "is unavailable, report it and stop.",
        ))
    else:
        lines.extend(use_workspace_overlay(
            "codex", f".system2/codex/{_CODEX_LOCK}", ".system2/codex/skills/system2/SKILL.md",
            "`$system2:system2-compose`",
        ))
        lines.extend(render_orchestrator_guidance(
            ir, lambda name: f"`$system2:system2-role-{name}`", _DISPATCH
        ))
    lines.extend([
        "",
        "Use `$system2:system2-doctor` for a read-only summary of project workflow state.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_doctor_skill() -> str:
    lines = _frontmatter(
        "system2-doctor", "Summarize System2 workflow state in the current project."
    )
    lines.extend([
        "# System2 doctor (Codex)",
        "",
        *workflow_status(), "",
        *overlay_doctor_guidance("codex", f".system2/codex/{_CODEX_LOCK}"), _RUNTIME_BOUNDARY,
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_role_skill(ir: System2Graph, name: str, contract: str) -> str:
    lines = _frontmatter(f"system2-role-{name}", f"Use the System2 {name} role in Codex.")
    lines.extend([
        f"# System2 role: {name} (Codex)",
        "",
        f"You are the System2 {name} specialist. Follow the contract below in "
        "the current session. This skill supplies guidance only; Codex controls "
        "permissions and approvals.",
        "",
        *render_role_guidance(ir, name, contract),
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
        "subagent_isolation": "separate-thread-context",
        "delegation_mode": "native-subagents",
        "FIDELITY": (
            _RUNTIME_BOUNDARY
            + " System2's role scopes, dangerous-command guidance, sensitive-path "
            "guidance, formatting, type-checking, and budget reporting are advisory."
        ),
        "capabilities": capabilities,
    }


def _planned_files(ir: System2Graph, workspace: bool) -> List[Tuple[str, str]]:
    planned = [("skills/system2/SKILL.md", _build_orchestrator_skill(ir, workspace))]
    if not workspace:
        planned.extend([
            (".codex-plugin/plugin.json", json.dumps(_build_manifest(), indent=2) + "\n"),
            ("skills/system2-doctor/SKILL.md", _build_doctor_skill()),
        ])
        for name, description, profiles in (
            ("system2-compose", "Preview and manage System2 project overlays and profiles.", False),
            ("system2-profile", "List or inspect saved System2 overlay profiles.", True),
        ):
            lines = _frontmatter(name, description)
            lines.extend([f"# {description}", "", *overlay_commands("codex", profiles=profiles)])
            planned.append((f"skills/{name}/SKILL.md", "\n".join(lines).rstrip() + "\n"))
    for name, contract in role_contracts(ir):
        planned.append((
            os.path.join("skills", f"system2-role-{name}", "SKILL.md"),
            _build_role_skill(ir, name, contract),
        ))

    return planned


class CodexBackend(GeneratedArtifactBackend):
    """Project a ``System2Graph`` onto a Codex plugin and capability lock."""

    name = "codex"
    lock_name = _CODEX_LOCK
    version = _CODEX_PLUGIN_VERSION
    version_key = "codex_plugin_version"
    runtime_boundary = _RUNTIME_BOUNDARY

    def _planned_files(self, ir: System2Graph) -> List[Tuple[str, str]]:
        return _planned_files(ir, bool(self._prefix))

    def _lock_details(self, ir: System2Graph) -> dict:
        return _build_degradation_report(ir)
