"""The Goose backend (the second backend).

Lowers the same harness-neutral ``System2Graph`` onto a Goose **recipe** workflow
plus enforcement artifacts. Same boundary as ``claude_code``: imports only
``ir.graph`` + ``backends._yaml`` + stdlib (it structurally satisfies the backend
Protocol without importing ``backends.base``), and reads its own
``backends/capabilities/goose.json`` data file (a JSON read, not an ``ir/*``
import). It never reads overlay manifests, the anchor map, profiles, or the
schema, and it MUST NOT consume ``ir.base_template`` / ``ir.overlay_inputs`` (the
Claude-targeted byte-fidelity carriers) — Goose renders from the *structured* IR
fields only.

The central honesty job (NFR-003): on Goose, enforced safety primitives cannot
block. ``block-dangerous`` / ``protect-sensitive`` degrade to **adapted**
permission gates; ``enforce-lease`` / ``format`` / ``typecheck`` / ``budget``
degrade to **advisory** instruction blocks. Nothing is ``native``. The degradation
report (``system2.goose.lock.json``) and a LOUD banner make that loud and
machine-readable; the launcher delivers the adapted policy via an ephemeral
``XDG_CONFIG_HOME`` config dir (the user's ``config.yaml`` + System2's
``permission.yaml``) and never mutates ``~/.config/goose``.

Determinism: output is a pure function of the IR — no timestamps, sorted /
insertion-ordered emission, canonical YAML via ``backends/_yaml.py``. Identical IR
→ byte-identical artifacts.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, List, Optional, Tuple

from system2_compiler.ir.graph import System2Graph

from . import _degradation
from . import _yaml
from .base import DoctorReport, UninstallResult, lock_sources_outside_project

__all__ = ["GooseBackend"]

# Overlay-name validation (kebab-case), shared with the Claude backend's contract.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "goose.json"
)

_GOOSE_VERSION_ASSUMED = "1.38.0"
_GOOSE_MODE = "smart_approve"
_RECIPE_VERSION = "1.0.0"

# The dangerous-command set the System2 policy names. Emitted (sorted) into the
# permission policy as the never-allow set for the shell tool. Fixed and neutral;
# the launcher delivers it via an ephemeral XDG_CONFIG_HOME, the report says it is
# a gate (not a block).
_DANGEROUS_COMMANDS = (
    "chmod -R 777",
    "curl | sh",
    "dd",
    "git push --force",
    "mkfs",
    "rm -rf /",
    "rm -rf ~",
    "shutdown",
    "sudo rm",
    "wget | sh",
)

_ADVISORY_LABEL = "ADVISORY — NOT ENFORCED ON GOOSE"

_CAPABILITY_ADVICE = {
    "enforce-lease": (
        "Honor the per-task write lease and your declared write scope. Goose "
        "has no per-path write gate, so this is NOT blocked: do not edit files "
        "outside your assigned scope even though nothing will stop you."
    ),
    "format": (
        "Format every file you edit before finishing. Goose has no PostToolUse "
        "hook, so formatting is NOT enforced; run the formatter yourself."
    ),
    "typecheck": (
        "Type-check every file you edit before finishing. Goose has no "
        "PostToolUse hook, so type-checking is NOT enforced; run the type "
        "checker yourself."
    ),
    "budget": (
        "Report your change budget (files touched, lines added/removed) when "
        "you finish. Goose has no SubagentStop hook, so this is NOT enforced; "
        "report it in your completion summary."
    ),
    "block-dangerous": (
        "Dangerous commands are gated by a Goose permission policy + "
        "smart_approve, NOT deterministically blocked. Refuse destructive "
        "commands; the gate is best-effort."
    ),
    "protect-sensitive": (
        "Sensitive files are gated by a Goose permission policy + "
        "smart_approve, NOT deterministically blocked. Do not read or write "
        "secrets / out-of-boundary files; the gate is best-effort."
    ),
}


def _load_descriptor(descriptor_path: str = _DESCRIPTOR_PATH) -> dict:
    with open(descriptor_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _ir_capabilities(ir: System2Graph) -> List[str]:
    """Return every intent capability present in the IR, in descriptor order.

    "Present in the IR" is the union of ``ir.capabilities.by_agent`` values;
    ordered by the descriptor so emission is deterministic. A capability in the IR
    but absent from the descriptor raises (no silent drop — NFR-003 analogue).
    """
    present = set()
    for caps in ir.capabilities.by_agent.values():
        present.update(caps)
    descriptor = _load_descriptor()
    descriptor_caps = list(descriptor.get("capabilities", {}).keys())
    missing = sorted(present - set(descriptor_caps))
    if missing:
        raise ValueError(
            "goose degradation report would silently drop IR capabilities "
            f"absent from the descriptor: {missing}"
        )
    return [c for c in descriptor_caps if c in present]


def _adapted_capabilities() -> frozenset:
    """The capabilities the descriptor marks ``adapted`` (the permission-gate path).

    Derived from ``goose.json`` status, not a hard-coded list, so the report and the
    advisory/instruction split track the descriptor with no drift surface.
    """
    descriptor = _load_descriptor()
    caps = descriptor.get("capabilities", {})
    return frozenset(
        name for name, entry in caps.items() if entry.get("status") == "adapted"
    )


# ---------------------------------------------------------------------------
# Instruction-text rendering (structured IR -> recipe prose)
# ---------------------------------------------------------------------------

def _advisory_block(capability: str) -> str:
    advice = _CAPABILITY_ADVICE.get(capability, "")
    return f"[{_ADVISORY_LABEL}: {capability}] {advice}".rstrip()


def _orchestrator_scoped_lines(ir: System2Graph) -> List[str]:
    """Render orchestrator-scoped contribution metadata into instruction lines.

    Renders only the structured metadata already on each ``Contribution.raw``
    (ids, names, descriptions, summaries, headings) — it does NOT read overlay
    content files (that is the Claude backend's job; Goose stays home-dir-free and
    IR-only). Deterministic: scopes follow the IR's insertion order; entries
    follow the front-end's topological order.
    """
    lines: List[str] = []
    principles: List[str] = []
    consultations: List[str] = []
    advisory_sources: List[str] = []
    spec_sections: List[str] = []
    aux_agents: List[str] = []

    for (type_path, _target), records in ir.contributions.scopes.items():
        for rec in records:
            raw = rec.raw
            origin = rec.overlay_name
            if type_path == "orchestrator.principles":
                cid = raw.get("id", "")
                principles.append(f"  - ({origin}/{cid}) overlay-contributed principle")
            elif type_path.startswith("orchestrator.gates.") and type_path.endswith(
                ".consultation"
            ):
                parts = type_path.split(".")
                gate_num = parts[2]
                cid = raw.get("id", "")
                phase = raw.get("phase", "pre-delegation")
                consultations.append(
                    f"  - Gate {gate_num} [{phase}] ({origin}/{cid}) consultation"
                )
            elif type_path == "delegation.advisory_sources":
                name = raw.get("name", "")
                desc = raw.get("description", "")
                resolution = raw.get("resolution", "")
                advisory_sources.append(
                    f"  - {name}: {desc} (resolution: {resolution})"
                )
            elif type_path.startswith("spec.") and type_path.endswith(
                ".required_sections"
            ):
                parts = type_path.split(".")
                artifact = parts[1]
                heading = raw.get("section_heading", "")
                desc = raw.get("description", "")
                spec_sections.append(
                    f'  - spec/{artifact}.md: "{heading}" — {desc}'
                )
            elif type_path == "auxiliary_agents":
                name = raw.get("name", "")
                role = raw.get("role", "")
                aux_agents.append(f"  - {name} (from {origin}): {role}")

    if principles:
        lines.append("Overlay-contributed principles:")
        lines.extend(principles)
    if consultations:
        lines.append("Overlay gate consultations:")
        lines.extend(consultations)
    if advisory_sources:
        lines.append("Advisory sources (consult when delegating):")
        lines.extend(advisory_sources)
    if spec_sections:
        lines.append("Overlay-required spec sections:")
        lines.extend(spec_sections)
    if aux_agents:
        lines.append("Auxiliary agents (optional delegation):")
        lines.extend(aux_agents)
    return lines


def _orchestrator_instructions(ir: System2Graph) -> str:
    lines: List[str] = []
    lines.append(
        "You are the System2 orchestrator. Drive the work through the gate "
        "graph, delegate to the role sub-recipes, and run the post-execution "
        "and maintenance policy."
    )
    lines.append("")

    # Gate graph 0->5 in edge order, starting from gate 0.
    lines.append("Gate graph (advance 0 -> 5; do not skip a gate):")
    order = _gate_order(ir.gate_graph)
    gate_by_number = {g.number: g for g in ir.gate_graph.gates}
    for number in order:
        gate = gate_by_number.get(number)
        if gate is None:
            continue
        lines.append(f"  - Gate {gate.number} ({gate.name}): {gate.checklist_text}")
        for con in gate.consultations:
            cid = con.raw.get("id", "")
            phase = con.raw.get("phase", "pre-delegation")
            lines.append(
                f"    - Overlay consultation [{phase}] "
                f"({con.overlay_name}/{cid})"
            )
    lines.append("")

    # Delegation contract.
    lines.append("Delegation contract — every delegation must specify:")
    for fieldname in ir.delegation_contract.required_fields:
        lines.append(f"  - {fieldname}")
    lines.append("Preferred delegation order (the 13-role pipeline):")
    for idx, role_name in enumerate(ir.delegation_contract.preferred_order, start=1):
        lines.append(f"  {idx}. {role_name}")
    lines.append("")

    # Post-execution policy.
    lines.append("Post-execution workflow:")
    lines.append(
        "  - Execution order: " + ", ".join(ir.post_execution.execution_order)
    )
    for tr in ir.post_execution.trigger_rules:
        when = "always" if tr.always else f"when {tr.condition}"
        lines.append(f"  - Run {tr.agent} ({when})")
    lines.append(
        f"  - Boomerang cap: {ir.post_execution.boomerang_cap}; on blockers: "
        f"{ir.post_execution.blocker_policy.get('on_blockers', '')} "
        f"(options: {', '.join(ir.post_execution.blocker_policy.get('options', []))})"
    )
    lines.append("")

    # Maintenance / regression policy.
    lines.append("Maintenance & regression loop:")
    lines.append(
        f"  - Corrective-cycle cap: {ir.maintenance_loop.corrective_cycle_cap}"
    )
    lines.append(
        "  - Classification: " + ", ".join(ir.maintenance_loop.classification)
    )
    lines.append("  - Regression ledger fields:")
    for ledger_field in ir.maintenance_loop.regression_ledger_fields:
        lines.append(f"    - {ledger_field}")
    lines.append("")

    # Overlay-contributed orchestrator-scoped material.
    scoped = _orchestrator_scoped_lines(ir)
    if scoped:
        lines.extend(scoped)
        lines.append("")

    # Advisory capability blocks (the loud honesty about non-enforcement).
    lines.append(
        "Enforcement on Goose is degraded. The following safety capabilities "
        "are NOT deterministically enforced on this backend:"
    )
    for cap in _ir_capabilities(ir):
        lines.append(f"  {_advisory_block(cap)}")

    return "\n".join(lines) + "\n"


def _gate_order(gate_graph) -> List[int]:
    """Return gate numbers in edge order (0 first), falling back to sorted nodes."""
    numbers = [g.number for g in gate_graph.gates]
    if not gate_graph.edges:
        return sorted(numbers)
    successors = {a: b for a, b in gate_graph.edges}
    targets = {b for _a, b in gate_graph.edges}
    starts = [n for n in numbers if n not in targets]
    order: List[int] = []
    current = min(starts) if starts else min(numbers)
    seen = set()
    while current is not None and current not in seen:
        order.append(current)
        seen.add(current)
        current = successors.get(current)
    for n in sorted(numbers):
        if n not in seen:
            order.append(n)
            seen.add(n)
    return order


def _role_advisory_capabilities(ir: System2Graph, role_name: str) -> List[str]:
    """Advisory capabilities for a role, in descriptor order."""
    descriptor_order = _ir_capabilities(ir)
    role_caps = set(ir.capabilities.by_agent.get(role_name, []))
    adapted = _adapted_capabilities()
    return [c for c in descriptor_order if c in role_caps and c not in adapted]


def _role_instructions(ir: System2Graph, role) -> str:
    lines: List[str] = []
    lines.append(
        f"You are the System2 {role.name} agent. Operate as an isolated "
        "session; you cannot spawn sub-agents."
    )
    if role.gate_role:
        lines.append(f"Gate role: {role.gate_role}.")
    lines.append("")

    # write-scope -> advisory enforce-lease block (Goose cannot gate per path).
    scope_text = role.write_scope or "your assigned task scope"
    lines.append(
        f"[{_ADVISORY_LABEL}: enforce-lease] Confine edits to {scope_text}. "
        "Goose has no per-path write gate, so this is NOT blocked — honor it "
        "anyway."
    )

    advisory = _role_advisory_capabilities(ir, role.name)
    advisory = [c for c in advisory if c != "enforce-lease"]
    for cap in advisory:
        lines.append(_advisory_block(cap))

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Artifact builders (pure dict -> YAML/JSON; no I/O)
# ---------------------------------------------------------------------------

def _build_orchestrator_recipe(ir: System2Graph) -> dict:
    sub_recipes = [
        {"name": role_name, "path": f"agents/{role_name}.recipe.yaml"}
        for role_name in ir.delegation_contract.preferred_order
    ]
    recipe = {
        "version": _RECIPE_VERSION,
        "title": "System2 Orchestrator",
        "description": (
            "System2 multi-agent workflow lowered to a Goose recipe: gate graph "
            "0->5, delegation contract, post-execution and maintenance policy."
        ),
        "instructions": _orchestrator_instructions(ir),
        "prompt": "System2 task: {{ task }}\n\nDrive it through the gate graph.\n",
        "parameters": [
            {
                "key": "task",
                "input_type": "string",
                "requirement": "required",
                "description": "The task for the System2 workflow to execute.",
            }
        ],
        "extensions": [
            {
                "type": "builtin",
                "name": "developer",
                "timeout": 300,
                "bundled": True,
            }
        ],
        "sub_recipes": sub_recipes,
    }
    _assert_parameters_referenced(recipe)
    return recipe


def _build_role_recipe(ir: System2Graph, role) -> dict:
    recipe = {
        "version": _RECIPE_VERSION,
        "title": f"System2 {role.name}",
        "description": f"System2 {role.name} role sub-recipe (isolated session).",
        "instructions": _role_instructions(ir, role),
    }
    if role.model_hint:
        recipe["settings"] = {"goose_model": role.model_hint}
    # Structural invariant: a role recipe MUST NOT declare sub_recipes (no nesting,
    # maps onto "subagents cannot spawn subagents").
    assert "sub_recipes" not in recipe, "role recipe must not nest sub_recipes"
    _assert_parameters_referenced(recipe)
    return recipe


def _assert_parameters_referenced(recipe: dict) -> None:
    """Assert every declared parameter is referenced via ``{{ key }}`` (goose rule).

    Goose fails validation with "Unnecessary parameter definitions" when a declared
    parameter is never interpolated. We assert referenced ⊇ declared before writing
    so a serializer/template drift is caught here, not only at ``goose recipe
    validate``.
    """
    declared = {p["key"] for p in recipe.get("parameters", [])}
    if not declared:
        return
    haystack = "".join(
        recipe.get(k, "")
        for k in ("instructions", "prompt", "title", "description")
        if isinstance(recipe.get(k), str)
    )
    for key in sorted(declared):
        if f"{{{{ {key} }}}}" not in haystack and f"{{{{{key}}}}}" not in haystack:
            raise ValueError(
                f"declared parameter {key!r} is never referenced via "
                "{{ ... }}; goose would reject the recipe"
            )


def _build_permission_policy(ir: System2Graph) -> dict:
    """Build the project-local Goose permission policy fragment.

    ``user:`` is keyed by tool name -> ``{permission}`` in a fixed sorted order so
    the emitted policy is deterministic. ``block-dangerous`` sets the shell/Bash tool
    to ``ask_before`` and names the dangerous set under ``never_allow_commands``
    (the launcher + smart_approve adjudicate the rest); ``protect-sensitive`` sets
    Read/Write/Edit to ``ask_before``. This is an ADAPTED gate, not a native block.
    """
    user = {
        "Bash": {"permission": "ask_before"},
        "Edit": {"permission": "ask_before"},
        "Read": {"permission": "ask_before"},
        "Write": {"permission": "ask_before"},
        "shell": {"permission": "ask_before"},
    }
    return {
        "user": user,
        "never_allow_commands": sorted(_DANGEROUS_COMMANDS),
    }


def _build_degradation_report(ir: System2Graph) -> dict:
    descriptor = _load_descriptor()
    union = _degradation.ir_capability_union(ir.capabilities.by_agent)
    capabilities = _degradation.build_capability_records(
        descriptor,
        union,
        fields=("status", "mechanism", "enforced", "gated"),
        allow_native=False,
    )
    return {
        "backend": "goose",
        "goose_version_assumed": _GOOSE_VERSION_ASSUMED,
        "mode": _GOOSE_MODE,
        "permission_delivery": "ephemeral-xdg-config",
        "DEGRADATION": (
            "On Goose, enforced safety primitives are DOWNGRADED: "
            "block-dangerous/protect-sensitive are best-effort permission gates "
            "(adapted, NOT enforced — the launcher delivers them via an ephemeral "
            "XDG_CONFIG_HOME permission.yaml + smart_approve, NOT native blocking); "
            "enforce-lease/format/typecheck/budget are advisory recipe instructions "
            "only (NOT enforced). adapted != enforced."
        ),
        "capabilities": capabilities,
    }


def _build_launcher() -> str:
    """Generate the thin ``run-system2.sh`` launcher (deterministic, no logic).

    Permission delivery is via an EPHEMERAL ``XDG_CONFIG_HOME`` config dir: the
    launcher copies the user's real ``config.yaml`` (provider/model/keys) and the
    System2-adapted ``goose/permission.yaml`` into a throwaway dir, then runs goose
    with ``XDG_CONFIG_HOME`` pointed at it. It NEVER mutates ``~/.config/goose``.
    The adapted policy is the safe DEFAULT (no global mutation to opt into);
    ``SYSTEM2_NO_PERMISSIONS=1`` runs against the user's own config instead.
    """
    return (
        "#!/usr/bin/env bash\n"
        "# Generated by the System2 compiler (Goose backend). Thin launcher only:\n"
        "# validate -> build an ephemeral XDG_CONFIG_HOME -> goose run. No workflow\n"
        "# logic, no translation, no policy lives here (C6/NFR-007). It NEVER writes\n"
        "# to ~/.config/goose.\n"
        "set -euo pipefail\n"
        "\n"
        "HERE=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        "NO_PERMISSIONS=\"${SYSTEM2_NO_PERMISSIONS:-0}\"\n"
        "KEEP_CONFIG=\"${SYSTEM2_KEEP_CONFIG:-0}\"\n"
        "USER_CONFIG_DIR=\"${XDG_CONFIG_HOME:-$HOME/.config}/goose\"\n"
        "USER_CONFIG=\"$USER_CONFIG_DIR/config.yaml\"\n"
        "LOCAL_PERMISSION=\"$HERE/goose/permission.yaml\"\n"
        "\n"
        "# 1. Validate the orchestrator and every role sub-recipe (fail fast).\n"
        "goose recipe validate \"$HERE/system2.recipe.yaml\"\n"
        "for recipe in \"$HERE/agents/\"*.recipe.yaml; do\n"
        "  [ -e \"$recipe\" ] || continue  # guard against an empty glob\n"
        "  goose recipe validate \"$recipe\"\n"
        "done\n"
        "\n"
        f"export GOOSE_MODE={_GOOSE_MODE}\n"
        "\n"
        "# 2. Permission delivery.\n"
        "if [ \"$NO_PERMISSIONS\" = \"1\" ]; then\n"
        "  echo \"NOTICE: SYSTEM2_NO_PERMISSIONS=1 — running against YOUR OWN goose\" >&2\n"
        "  echo \"NOTICE: config (~/.config/goose). The System2-adapted permission\" >&2\n"
        "  echo \"NOTICE: policy is NOT applied; block-dangerous/protect-sensitive are\" >&2\n"
        "  echo \"NOTICE: NOT active this run. See system2.goose.lock.json.\" >&2\n"
        "  goose run --recipe \"$HERE/system2.recipe.yaml\" --params task=\"${1:-}\"\n"
        "  exit $?\n"
        "fi\n"
        "\n"
        "# DEFAULT: build a throwaway XDG_CONFIG_HOME so the adapted policy governs\n"
        "# THIS run without ever touching ~/.config/goose. The ephemeral dir may hold\n"
        "# the user's provider keys (copied config.yaml), so it is created owner-only\n"
        "# (umask 077 -> dir 700, files 600) and removed on exit OR signal.\n"
        "umask 077\n"
        "EPHEMERAL_CONFIG=\"$(mktemp -d \"${TMPDIR:-/tmp}/system2-goose-config.XXXXXX\")\"\n"
        "chmod 700 \"$EPHEMERAL_CONFIG\"\n"
        "cleanup() {\n"
        "  if [ \"$KEEP_CONFIG\" = \"1\" ]; then\n"
        "    echo \"NOTICE: SYSTEM2_KEEP_CONFIG=1 — kept ephemeral config at $EPHEMERAL_CONFIG\" >&2\n"
        "  else\n"
        "    rm -rf \"$EPHEMERAL_CONFIG\"\n"
        "  fi\n"
        "}\n"
        "# Cover normal exit AND catchable signals (Ctrl-C / kill / hangup) so the\n"
        "# secret copy is not left in TMPDIR. SIGKILL is uncatchable by design.\n"
        "trap cleanup EXIT INT TERM HUP\n"
        "\n"
        "mkdir -p \"$EPHEMERAL_CONFIG/goose\"\n"
        "chmod 700 \"$EPHEMERAL_CONFIG/goose\"\n"
        "if [ -f \"$USER_CONFIG\" ]; then\n"
        "  cp \"$USER_CONFIG\" \"$EPHEMERAL_CONFIG/goose/config.yaml\"\n"
        "  chmod 600 \"$EPHEMERAL_CONFIG/goose/config.yaml\"\n"
        "  echo \"Carried your goose config.yaml (provider/model/keys) into the ephemeral config.\" >&2\n"
        "else\n"
        "  echo \"NOTICE: no ~/.config/goose/config.yaml found; the ephemeral config has\" >&2\n"
        "  echo \"NOTICE: only the System2 permission policy (set provider/model in env).\" >&2\n"
        "fi\n"
        "cp \"$LOCAL_PERMISSION\" \"$EPHEMERAL_CONFIG/goose/permission.yaml\"\n"
        "chmod 600 \"$EPHEMERAL_CONFIG/goose/permission.yaml\"\n"
        "\n"
        "echo \"MODE: ADAPTED permission policy active via an EPHEMERAL XDG_CONFIG_HOME.\" >&2\n"
        "echo \"MODE: This is enforcement-APPROXIMATE (smart_approve + permission.yaml),\" >&2\n"
        "echo \"MODE: NOT native blocking. ~/.config/goose is NEVER modified. See\" >&2\n"
        "echo \"MODE: system2.goose.lock.json for the full degradation report.\" >&2\n"
        "\n"
        "# 3. Run the workflow under smart_approve (a gate exists; never auto),\n"
        "#    reading config + permission policy from the ephemeral dir only.\n"
        "XDG_CONFIG_HOME=\"$EPHEMERAL_CONFIG\" goose run \\\n"
        "  --recipe \"$HERE/system2.recipe.yaml\" --params task=\"${1:-}\"\n"
    )


# ---------------------------------------------------------------------------
# Write posture
# ---------------------------------------------------------------------------

def _build_lock(ir: System2Graph, overlay_sources: List[str]) -> dict:
    """Assemble the standalone Goose lock dict.

    The degradation report is unchanged; ``overlay_sources`` is appended LAST
    (additive — OQ-5.1/T10), mirroring the Claude lock's additive
    ``degradation_report`` discipline: every prior key keeps its bytes and only a
    new trailing key is introduced. The recorded sources are the overlay
    ``source_path`` set that produced this tree, taken from the neutral IR
    (``active_profile.ordered_source_paths``) or the explicit list the lifecycle
    recompose path supplies — never from the Claude-targeted ``ir.overlay_inputs``.
    """
    lock = _build_degradation_report(ir)
    lock["overlay_sources"] = list(overlay_sources)
    return lock


def _planned_files(
    ir: System2Graph, project_path: str, overlay_sources: List[str]
) -> List[Tuple[str, str]]:
    """Return the ordered ``(relative_path, content)`` set emit writes.

    Deterministic order: orchestrator, the 13 role sub-recipes (preferred order),
    the permission policy, the degradation report, then the launcher.
    """
    planned: List[Tuple[str, str]] = []

    orchestrator = _build_orchestrator_recipe(ir)
    planned.append(("system2.recipe.yaml", _yaml.dump(orchestrator)))

    role_by_name = {r.name: r for r in ir.roles}
    for role_name in ir.delegation_contract.preferred_order:
        role = role_by_name.get(role_name)
        if role is None:
            continue
        recipe = _build_role_recipe(ir, role)
        planned.append(
            (os.path.join("agents", f"{role_name}.recipe.yaml"), _yaml.dump(recipe))
        )

    planned.append(
        (os.path.join("goose", "permission.yaml"), _yaml.dump(_build_permission_policy(ir)))
    )
    planned.append(
        (
            "system2.goose.lock.json",
            json.dumps(_build_lock(ir, overlay_sources), indent=2) + "\n",
        )
    )
    planned.append(("run-system2.sh", _build_launcher()))
    return planned


def _write_outputs(project_path: str, planned: List[Tuple[str, str]]) -> List[str]:
    """Write planned files under ``project_path`` with backup/restore on failure.

    Backs up any existing target, writes via temp + ``os.replace``, and on any
    failure restores backups and removes newly created files/dirs. Writes ONLY
    under ``project_path`` — never touches ``$HOME`` / ``~/.config/goose``.
    """
    backups: List[Tuple[str, str]] = []
    newly_created: List[str] = []
    dirs_created: List[str] = []
    written: List[str] = []
    try:
        for rel, content in planned:
            dst = os.path.join(project_path, rel)
            dir_name = os.path.dirname(dst)
            _makedirs_tracked(dir_name, dirs_created)
            if os.path.exists(dst):
                fd, bak = tempfile.mkstemp(
                    prefix=f".{os.path.basename(dst)}.", suffix=".bak", dir=dir_name
                )
                os.close(fd)
                shutil.copy2(dst, bak)
                backups.append((dst, bak))
            fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                os.replace(tmp, dst)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            if dst not in [orig for orig, _ in backups]:
                newly_created.append(dst)
            if rel == "run-system2.sh":
                os.chmod(dst, 0o755)
            written.append(dst)
    except Exception:
        for orig, bak in backups:
            if os.path.exists(bak):
                shutil.copy2(bak, orig)
                os.unlink(bak)
        for created in newly_created:
            if os.path.exists(created):
                os.unlink(created)
        for d in dirs_created:
            try:
                os.rmdir(d)
            except OSError:
                pass
        raise
    for _orig, bak in backups:
        try:
            if os.path.exists(bak):
                os.unlink(bak)
        except OSError:
            pass
    return written


def _makedirs_tracked(dir_path: str, dirs_created: List[str]) -> None:
    if not dir_path or os.path.isdir(dir_path):
        return
    to_create: List[str] = []
    current = dir_path
    while not os.path.isdir(current):
        to_create.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    os.makedirs(dir_path, exist_ok=True)
    for d in to_create:
        dirs_created.append(d)


# ---------------------------------------------------------------------------
# Lifecycle helpers (lock read / removal / hermetic validation)
# ---------------------------------------------------------------------------

# The Goose artifact tree emit owns; uninstall removes these (and cleans empty
# dirs) when the last overlay is removed. Fixed and deterministic.
_GOOSE_FIXED_ARTIFACTS = (
    "system2.recipe.yaml",
    os.path.join("goose", "permission.yaml"),
    "system2.goose.lock.json",
    "run-system2.sh",
)


def _hermetic_env() -> dict:
    """A minimal env with HOME + XDG_CONFIG_HOME forced under a throwaway dir.

    ``goose recipe validate`` reads ``~/.config/goose``; doctor must never touch
    the operator's real config, so every shell-out runs with HOME/XDG_CONFIG_HOME
    pointed at a fresh temp dir (the caller removes it).
    """
    home = tempfile.mkdtemp(prefix="system2-goose-doctor-home-")
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    return env


class GooseBackend:
    """Project a ``System2Graph`` onto a Goose recipe workflow + enforcement.

    ``emit`` writes (under ``project_path`` only) the orchestrator recipe, the 13
    role sub-recipes, the project-local permission policy, the standalone
    degradation report (now carrying the additive trailing ``overlay_sources[]``
    key), and the thin launcher. Output is a pure function of the IR (no
    timestamps). When the IR carries a ``dry_run`` intent it returns the
    would-write set without touching the filesystem.

    The Phase 5 lifecycle methods (``uninstall`` / ``doctor`` /
    ``recompose_from_lock``) read the target's OWN lock + artifacts under
    ``project_path``. Recompose needs the System2 plugin root (``base_path``) and a
    ``compose_fn`` (the CLI passes ``ir.compose``) to recompose the remaining /
    recorded overlay set; both are injected at construction so the backend imports
    no ``ir/*`` loader (module-boundaries hold — ``emit`` is fully IR-driven and
    needs neither). ``overlay_sources`` may also be injected so a plain ``emit``
    records the producing overlay set in the lock.
    """

    name = "goose"

    def __init__(
        self,
        base_path: Optional[str] = None,
        compose_fn: Optional[Callable[..., object]] = None,
        overlay_sources: Optional[List[str]] = None,
    ) -> None:
        self._base_path = base_path
        self._compose_fn = compose_fn
        self._overlay_sources = list(overlay_sources) if overlay_sources else None

    def _resolve_overlay_sources(self, ir: System2Graph) -> List[str]:
        """The overlay ``source_path`` set that produced this tree (neutral only).

        Prefers the explicit list injected at construction (the recompose
        lifecycle and CLI supply it), else the neutral
        ``active_profile.ordered_source_paths`` when a profile is active, else an
        empty list. Never reads the Claude-targeted ``ir.overlay_inputs``.
        """
        if self._overlay_sources is not None:
            return list(self._overlay_sources)
        profile = ir.active_profile
        if profile is not None and profile.ordered_source_paths:
            return list(profile.ordered_source_paths)
        return []

    def emit(self, ir: System2Graph, project_path: str) -> List[str]:
        return self._emit_with_sources(
            ir, project_path, self._resolve_overlay_sources(ir)
        )

    def _emit_with_sources(
        self, ir: System2Graph, project_path: str, overlay_sources: List[str]
    ) -> List[str]:
        planned = _planned_files(ir, project_path, overlay_sources)
        if bool(getattr(ir, "dry_run", False)):
            return [os.path.join(project_path, rel) for rel, _ in planned]
        return _write_outputs(project_path, planned)

    # -----------------------------------------------------------------------
    # Phase 5 lifecycle: lock helpers
    # -----------------------------------------------------------------------

    def lock_path(self, project_path: str) -> str:
        """The Goose target lock artifact: ``system2.goose.lock.json``."""
        return os.path.join(project_path, "system2.goose.lock.json")

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        """Read the additive ``overlay_sources[]`` key from the Goose lock.

        Raises ``FileNotFoundError`` when the lock is absent and
        ``json.JSONDecodeError`` when it is malformed (the CLI maps these to the
        parallel refusals). Skips empty entries.
        """
        lp = self.lock_path(project_path)
        if not os.path.isfile(lp):
            raise FileNotFoundError(lp)
        with open(lp, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
        return [s for s in lock_data.get("overlay_sources", []) if s]

    # -----------------------------------------------------------------------
    # Phase 5 lifecycle: recompose from lock
    # -----------------------------------------------------------------------

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        """Re-emit from a recomposed IR (the ``--from-lock`` recompose path).

        The CLI reads the lock's ``overlay_sources[]`` via
        ``read_lock_overlay_sources``, recomposes via ``ir.compose`` over those
        sources, and passes the resulting graph here; this method re-emits it,
        re-recording the same source set in the lock. ``dry_run`` is carried on the
        IR; the would-write set is returned without writing.
        """
        return self._emit_with_sources(
            ir, project_path, self._resolve_overlay_sources(ir)
        )

    # -----------------------------------------------------------------------
    # Phase 5 lifecycle: uninstall
    # -----------------------------------------------------------------------

    def uninstall(
        self,
        project_path: str,
        overlay_name: str,
        *,
        dry_run: bool = False,
        allow_newer_schema: bool = False,
    ) -> UninstallResult:
        """Remove a named overlay from the composed Goose tree.

        Validates the kebab-case name, reads ``overlay_sources[]`` from
        ``system2.goose.lock.json``, refuses on a malformed/absent lock or a name
        not recorded, and dispatches: on >=1 remaining -> recompose the remaining
        sources via ``compose_fn`` then ``emit`` (re-recording the trimmed source
        set); on 0 remaining -> remove the generated Goose tree
        (``system2.recipe.yaml`` / ``agents/*.recipe.yaml`` /
        ``goose/permission.yaml`` / ``system2.goose.lock.json`` / ``run-system2.sh``)
        and clean empty dirs, all under the atomic backup/restore (REQ-044). Writes
        only under ``project_path``. ``allow_newer_schema`` is threaded into the
        remaining-set recompose (matching the oracle's uninstall -> compose
        forwarding), so a remaining overlay declaring a newer schema is accepted
        exactly as on a direct compose.
        """
        def _err(errors: List[str]) -> UninstallResult:
            return UninstallResult(
                removed={}, remaining=[], artifacts_removed=[], files_written=[],
                is_last_overlay=False, injection_warnings=[], preview="",
                errors=errors,
            )

        if not _KEBAB_RE.match(overlay_name):
            return _err([
                f"Invalid overlay name {overlay_name!r}: must be kebab-case "
                f"(lowercase alphanumeric, hyphens only)"
            ])

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

        sources = lock_data.get("overlay_sources", [])
        if not isinstance(sources, list):
            return _err(["Lock file is malformed: 'overlay_sources' is not a list"])

        installed = [_overlay_name_of(s) for s in sources]
        if overlay_name not in installed:
            return _err([
                f"Overlay {overlay_name!r} is not installed. "
                f"Installed: {installed}"
            ])

        remaining_sources = [
            s for s in sources if _overlay_name_of(s) != overlay_name
        ]
        remaining_meta = [{"name": _overlay_name_of(s)} for s in remaining_sources]

        if not remaining_sources:
            return self._uninstall_last_overlay(
                project_path, overlay_name, dry_run
            )

        # Multi-overlay path: recompose the remaining sources, then emit.
        base_path = self._require_base_path("uninstall")
        compose_fn = self._require_compose_fn("uninstall")
        result = compose_fn(
            base_path, remaining_sources, project_path, dry_run=dry_run,
            allow_newer_schema=allow_newer_schema,
        )
        if getattr(result, "errors", None):
            errors = list(result.errors)
            errors.append(
                "Remediation: verify that all remaining overlay source paths are "
                "accessible, then retry. If an overlay source has moved, recompose "
                "with from-lock after correcting the paths."
            )
            return UninstallResult(
                removed={"name": overlay_name},
                remaining=remaining_meta,
                artifacts_removed=[],
                files_written=[],
                is_last_overlay=False,
                injection_warnings=[],
                preview="",
                errors=errors,
            )

        report = getattr(result, "report", {}) or {}
        injection_warnings = list(report.get("injection_warnings", []))
        if dry_run:
            files_written = list(getattr(result, "files_to_write", []))
        else:
            files_written = self._emit_with_sources(
                result.graph, project_path, remaining_sources
            )

        return UninstallResult(
            removed={"name": overlay_name},
            remaining=remaining_meta,
            artifacts_removed=[],
            files_written=files_written,
            is_last_overlay=False,
            injection_warnings=injection_warnings,
            preview="",
            errors=[],
        )

    def _uninstall_last_overlay(
        self, project_path: str, overlay_name: str, dry_run: bool
    ) -> UninstallResult:
        """Remove the whole Goose artifact tree (zero overlays remain).

        Removes the orchestrator recipe, every ``agents/*.recipe.yaml``, the
        permission policy, the lock, and the launcher; cleans the now-empty
        ``agents/`` and ``goose/`` dirs. Backs up every removed file and restores on
        any failure (REQ-044). Writes/removes only under ``project_path``.
        """
        artifacts = self._existing_artifacts(project_path)

        if dry_run:
            return UninstallResult(
                removed={"name": overlay_name},
                remaining=[],
                artifacts_removed=artifacts,
                files_written=["(remove) " + a for a in artifacts],
                is_last_overlay=True,
                injection_warnings=[],
                preview="",
                errors=[],
            )

        backups: List[Tuple[str, str]] = []
        try:
            for path in artifacts:
                dir_name = os.path.dirname(path)
                fd, bak = tempfile.mkstemp(
                    prefix=f".{os.path.basename(path)}.", suffix=".bak", dir=dir_name
                )
                os.close(fd)
                shutil.copy2(path, bak)
                backups.append((path, bak))
            for path in artifacts:
                os.unlink(path)
        except Exception:
            for orig, bak in backups:
                if os.path.exists(bak):
                    shutil.copy2(bak, orig)
                    os.unlink(bak)
            raise

        for _orig, bak in backups:
            try:
                if os.path.exists(bak):
                    os.unlink(bak)
            except OSError:
                pass

        for sub in ("agents", "goose"):
            try:
                os.rmdir(os.path.join(project_path, sub))
            except OSError:
                pass

        return UninstallResult(
            removed={"name": overlay_name},
            remaining=[],
            artifacts_removed=artifacts,
            files_written=[],
            is_last_overlay=True,
            injection_warnings=[],
            preview="",
            errors=[],
        )

    def _existing_artifacts(self, project_path: str) -> List[str]:
        """The emitted Goose artifact paths that exist under ``project_path``."""
        found: List[str] = []
        for rel in _GOOSE_FIXED_ARTIFACTS:
            p = os.path.join(project_path, rel)
            if os.path.isfile(p):
                found.append(p)
        agents_dir = os.path.join(project_path, "agents")
        if os.path.isdir(agents_dir):
            for name in sorted(os.listdir(agents_dir)):
                if name.endswith(".recipe.yaml"):
                    found.append(os.path.join(agents_dir, name))
        return found

    # -----------------------------------------------------------------------
    # Phase 5 lifecycle: doctor
    # -----------------------------------------------------------------------

    def doctor(self, project_path: str) -> DoctorReport:
        """Read-only drift/status report for a composed Goose tree.

        Status model: ``no_lock`` (no ``system2.goose.lock.json``); ``broken`` (a
        referenced recipe missing OR ``goose recipe validate`` rejects the
        orchestrator/any role recipe); ``stale_overlay`` (a recorded
        ``overlay_sources[]`` path missing on disk); ``current`` otherwise.

        The validity oracle is the real ``goose recipe validate`` run under a
        hermetic HOME (the real ``~/.config/goose`` is never touched). When
        ``goose`` is absent the structural checks still run and a LOUD
        ``validator_unavailable`` finding is recorded with ``validator_available =
        False`` — never a silent ``current`` (OQ-5.2). Per OQ-5.2 the exit code
        tracks ``status`` alone: exit 0 when ``status == current`` (an absent
        validator is surfaced LOUDLY, not punished with a non-zero exit), exit 1
        otherwise.
        """
        details: List[dict] = []
        lp = self.lock_path(project_path)
        if not os.path.isfile(lp):
            details.append({
                "kind": "no_lock",
                "message": "No system2.goose.lock.json found; nothing is composed.",
            })
            return DoctorReport(
                status="no_lock", details=details,
                system2_version={"installed": "", "locked": ""},
                overlays=[], composed=False, exit_code=1,
                validator_available=True,
            )

        try:
            with open(lp, "r", encoding="utf-8") as fh:
                lock_data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            details.append({
                "kind": "broken",
                "message": f"Lock file is unreadable: {exc}",
            })
            return DoctorReport(
                status="broken", details=details,
                system2_version={"installed": "", "locked": ""},
                overlays=[], composed=True, exit_code=1,
                validator_available=True,
            )

        sources = [s for s in lock_data.get("overlay_sources", []) if s]
        overlays = [{"name": _overlay_name_of(s), "source_present": os.path.isdir(s)}
                    for s in sources]
        status = "current"

        # Structural: recorded sources resolvable.
        missing_sources = [s for s in sources if not os.path.isdir(s)]
        if missing_sources:
            status = "stale_overlay"
            for s in missing_sources:
                details.append({
                    "kind": "stale_overlay",
                    "message": f"recorded overlay source is missing: {s}",
                })

        # Structural: emitted recipes present.
        recipes = self._recipe_paths(project_path)
        missing_recipes = [r for r in recipes if not os.path.isfile(r)]
        if missing_recipes or not recipes:
            status = "broken"
            for r in missing_recipes:
                details.append({
                    "kind": "broken",
                    "message": f"referenced recipe is missing: {r}",
                })
            if not recipes:
                details.append({
                    "kind": "broken",
                    "message": "no system2.recipe.yaml present to validate.",
                })

        # Validity oracle: real `goose recipe validate`, hermetic, LOUD-absent.
        goose_bin = os.environ.get("GOOSE_BIN") or shutil.which("goose")
        validator_available = goose_bin is not None
        present_recipes = [r for r in recipes if os.path.isfile(r)]
        if not validator_available:
            details.append({
                "kind": "validator_unavailable",
                "message": (
                    "goose not on PATH — recipe validation SKIPPED (not a silent "
                    "pass). Install goose v1.38.0 or set GOOSE_BIN to validate; "
                    "structural checks ran."
                ),
            })
        elif present_recipes:
            env = _hermetic_env()
            try:
                for rp in present_recipes:
                    completed = subprocess.run(
                        [goose_bin, "recipe", "validate", rp],
                        capture_output=True, text=True, env=env,
                    )
                    if completed.returncode != 0:
                        status = "broken"
                        details.append({
                            "kind": "broken",
                            "message": (
                                f"goose recipe validate rejected "
                                f"{os.path.relpath(rp, project_path)}: "
                                f"{completed.stderr.strip() or completed.stdout.strip()}"
                            ),
                        })
            finally:
                shutil.rmtree(env["HOME"], ignore_errors=True)

        # Advisory (does NOT change status/exit): lock sources resolving out-of-tree.
        details.extend(lock_sources_outside_project(sources, project_path))

        locked_version = lock_data.get("goose_version_assumed", "")
        # Exit 0 when nothing is stale/broken. A LOUD validator_unavailable finding
        # is exit 0 (the operator is not faulted for an absent validator — OQ-5.2),
        # never a silent "current": the finding is always recorded above.
        exit_code = 0 if status == "current" else 1
        return DoctorReport(
            status=status,
            details=details,
            system2_version={"installed": _GOOSE_VERSION_ASSUMED, "locked": locked_version},
            overlays=overlays,
            composed=True,
            exit_code=exit_code,
            validator_available=validator_available,
        )

    def _recipe_paths(self, project_path: str) -> List[str]:
        """The orchestrator recipe + every recorded role sub-recipe path."""
        paths: List[str] = []
        orchestrator = os.path.join(project_path, "system2.recipe.yaml")
        paths.append(orchestrator)
        agents_dir = os.path.join(project_path, "agents")
        if os.path.isdir(agents_dir):
            for name in sorted(os.listdir(agents_dir)):
                if name.endswith(".recipe.yaml"):
                    paths.append(os.path.join(agents_dir, name))
        return paths

    def _require_base_path(self, verb: str) -> str:
        if not self._base_path:
            raise ValueError(
                f"GooseBackend.{verb} requires base_path; construct "
                f"GooseBackend(base_path=...) (the CLI supplies it)"
            )
        return self._base_path

    def _require_compose_fn(self, verb: str) -> Callable[..., object]:
        if self._compose_fn is None:
            raise ValueError(
                f"GooseBackend.{verb} requires compose_fn to recompose the "
                f"remaining overlay set; construct GooseBackend(compose_fn=ir.compose)"
            )
        return self._compose_fn


def _overlay_name_of(source_path: str) -> str:
    """Derive an overlay name from its source directory basename.

    Goose records overlay *source paths* (not manifests) in the lock, so the
    operator-facing name is the directory basename — deterministic and never
    reading the manifest (boundary-safe).
    """
    return os.path.basename(os.path.normpath(source_path))
