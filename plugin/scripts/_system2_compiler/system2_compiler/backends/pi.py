"""Pi backend: prompt templates, role skills, and an honest lock."""

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

__all__ = ["PiBackend"]

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "pi.json"
)
_PI_PACKAGE_VERSION = CHANNEL_VERSION
_PI_LOCK = "system2.pi.lock.json"

_RUNTIME_BOUNDARY = (
    "System2 supplies workflow and role guidance through Pi prompt templates and "
    "skills, and delegates through Pi's official example `subagent` tool. The "
    "child runs in a separate Pi process with fresh context, not a new permission "
    "sandbox. Pi's tools, project trust, and user configuration remain authoritative."
)
_DISPATCH = (
    "For each specialist step, use the exact role skill path listed below when "
    "present. Otherwise resolve the skill from the active inventory; for a direct "
    "project build prefer its `.pi/skills` role over the base package copy. Call "
    "`subagent` in single mode with `agent: \"system2-specialist\"`, "
    "`agentScope: \"user\"`, and `task` containing the absolute role `SKILL.md` "
    "path and the handoff below. Keep the project working directory. Instruct the "
    "child to read that skill before acting and wait for its returned summary; "
    "do not use chain mode across approval gates. If the tool or agent is "
    "unavailable after automatic setup, ask the user to check it with `/system2-setup`; do not do the work in "
    "the parent."
)


def _load_descriptor() -> dict:
    with open(_DESCRIPTOR_PATH, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _frontmatter(fields: dict) -> List[str]:
    yaml = _yaml.dump(fields).rstrip("\n")
    return ["---", *yaml.splitlines(), "---", ""]


def _build_orchestrator_prompt(ir: System2Graph, workspace: bool) -> str:
    lines = _frontmatter({
        "description": "Start the System2 engineering workflow.",
        "argument-hint": "<task>",
    })
    lines.extend(["# System2 orchestrator (Pi)", ""])
    if not workspace:
        lines.extend(["## Request", "", "$ARGUMENTS", ""])
    lines.extend([
        "## Runtime boundary",
        "",
        _RUNTIME_BOUNDARY,
        "System2 adds no custom enforcement or permission layer.",
        "",
    ])
    if workspace:
        role_ref = lambda name: f"`.system2/pi/.pi/skills/system2-role-{name}/SKILL.md`"
    else:
        lines.extend(use_workspace_overlay(
            "pi", f".system2/pi/{_PI_LOCK}", ".system2/pi/.pi/prompts/system2.md",
            "`/system2-compose`",
        ))
        role_ref = lambda name: f"`system2-role-{name}`"
    lines.extend(render_orchestrator_guidance(ir, role_ref, _DISPATCH))
    lines.extend([
        "",
        "Use `/system2-doctor` for a read-only summary of project workflow state.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_doctor_prompt() -> str:
    lines = _frontmatter({
        "description": "Summarize System2 workflow state in the current project.",
    })
    lines.extend([
        "# System2 doctor (Pi)",
        "",
        *workflow_status(), "",
        *overlay_doctor_guidance("pi", f".system2/pi/{_PI_LOCK}"), _RUNTIME_BOUNDARY,
    ])
    return "\n".join(lines).rstrip() + "\n"


def _build_role_skill(ir: System2Graph, name: str, contract: str) -> str:
    lines = _frontmatter({
        "name": f"system2-role-{name}",
        "description": f"Use the System2 {name} role in Pi.",
    })
    lines.extend([
        f"# System2 role: {name} (Pi)",
        "",
        f"You are the System2 {name} specialist. Follow the contract below in "
        "the current session. This skill supplies guidance only; Pi controls active "
        "tools and project trust.",
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
        "backend": "pi",
        "pi_package_version": _PI_PACKAGE_VERSION,
        "enforcement": "host-managed",
        "subagent_isolation": "separate-process-context",
        "delegation_mode": "pi-subagent-example",
        "FIDELITY": (
            _RUNTIME_BOUNDARY
            + " System2's role scopes, dangerous-command guidance, sensitive-path "
            "guidance, formatting, type-checking, and budget reporting are advisory."
        ),
        "capabilities": capabilities,
    }


def _planned_files(ir: System2Graph, workspace: bool) -> List[Tuple[str, str]]:
    planned = [(".pi/prompts/system2.md", _build_orchestrator_prompt(ir, workspace))]
    if not workspace:
        planned.append((".pi/prompts/system2-doctor.md", _build_doctor_prompt()))
        for name, description, profiles in (
            ("system2-compose", "Preview and manage System2 project overlays and profiles.", False),
            ("system2-profile", "List or inspect saved System2 overlay profiles.", True),
        ):
            lines = _frontmatter({"description": description})
            lines.extend([f"# {description}", "", *overlay_commands("pi", profiles=profiles),
                          "", "## User request", "", "$ARGUMENTS"])
            planned.append((f".pi/prompts/{name}.md", "\n".join(lines).rstrip() + "\n"))
    for name, contract in role_contracts(ir):
        planned.append((
            os.path.join(".pi", "skills", f"system2-role-{name}", "SKILL.md"),
            _build_role_skill(ir, name, contract),
        ))

    return planned


class PiBackend(GeneratedArtifactBackend):
    """Project a ``System2Graph`` onto Pi prompt templates and role skills."""

    name = "pi"
    lock_name = _PI_LOCK
    version = _PI_PACKAGE_VERSION
    version_key = "pi_package_version"
    runtime_boundary = _RUNTIME_BOUNDARY

    def _planned_files(self, ir: System2Graph) -> List[Tuple[str, str]]:
        return _planned_files(ir, bool(self._prefix))

    def _lock_details(self, ir: System2Graph) -> dict:
        return _build_degradation_report(ir)
