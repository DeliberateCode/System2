"""The Pi backend (the third backend) — the first MIXED-status backend.

Lowers the same harness-neutral ``System2Graph`` onto a Pi **extension**
(generated TypeScript text) plus Pi context / skill / prompt markdown and a
standalone degradation report. Same boundary as ``claude_code``:
imports only ``ir.graph`` + ``backends._degradation`` + stdlib (it structurally
satisfies the backend Protocol without importing ``backends.base``), and reads its
own ``backends/capabilities/pi.json`` data file (a JSON read, not an ``ir/*``
import). It never reads overlay manifests, the anchor map, profiles, or the
schema, and it MUST NOT consume ``ir.base_template`` / ``ir.overlay_inputs`` (the
Claude-targeted byte-fidelity carriers) — Pi renders from the *structured* IR
fields only.

The honesty job here is proving native enforcement is real: Pi has a real native
enforcement seam. Pi has NO built-in permission system — the generated extension's ``on("tool_call")``
handler fires BEFORE a tool and can ``return { block: true, reason }``, so the
handler IS the gate. ``enforce-lease`` / ``block-dangerous`` / ``protect-sensitive``
are therefore genuinely **native** (a deterministic pre-execution block); ``budget``
is **adapted** (``on("agent_end")`` report, not a block); ``format`` / ``typecheck``
are **advisory** (SYSTEM.md instruction only). The degradation report
(``system2.pi.lock.json``) and a loud ``FIDELITY`` banner make the mixed story
machine-readable; when a role's ``write_scope`` is empty the lease gate is wired but
unscoped and the report says so loudly (never a silent vacuous native claim).

The compiler emits TypeScript / markdown / JSON as **text**; it never runs or
transpiles TS (node / pi live only in the eval tests). Output is a pure function of
the IR + backend-owned constants — no timestamps, sorted / insertion-ordered
emission, LF endings, single trailing newline. Identical IR -> byte-identical tree.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, List, Optional, Tuple

from system2_compiler.ir.graph import System2Graph

from . import _degradation, _yaml
from ._enforcement import (
    build_dangerous_command_patterns,
    build_sensitive_path_patterns,
)
from .base import DoctorReport, UninstallResult, lock_sources_outside_project

__all__ = ["PiBackend"]

# Overlay-name validation (kebab-case), shared with the other backends' contract.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "pi.json"
)

_PI_VERSION_ASSUMED = "0.80.2"

# The dangerous-command and sensitive-path matcher sets moved to backends/_enforcement.py
# (shared with the codex backend) and are consumed here VERBATIM via
# build_dangerous_command_patterns() / build_sensitive_path_patterns() — pi calls the
# default (no canary) so its emitted TS bytes are unchanged. Each returned entry is
# (js_regex_source, js_flags, reason), fixed order (deterministic, NOT sorted — regex
# evaluation order is semantic); emitted into the generated TS as `new RegExp(source,
# flags)`, escaped via _ts_escape like every other literal.

# The role whose write_scope governs an un-delegated turn. The /delegate dispatcher
# switches the active role; until then the executor scope is the default (it is the
# implementation role). Chosen deterministically: executor if present, else the
# first role in the preferred order.
_DEFAULT_ACTIVE_ROLE = "executor"

_ADVISORY_LABEL = "ADVISORY — NOT ENFORCED ON PI (instruction only)"

_CAPABILITY_NOTE = {
    "enforce-lease": (
        "NATIVE on Pi: the generated extension's on(\"tool_call\") handler blocks a "
        "write/edit outside your role's write scope before the tool runs. The path is "
        "project-normalized and the scope is start-anchored (a ../ or absolute escape "
        "fails closed). A role with an empty write scope (read-only) has EVERY write "
        "blocked (fail-closed)."
    ),
    "block-dangerous": (
        "NATIVE on Pi: the generated extension's on(\"tool_call\") handler hard-blocks "
        "a dangerous bash command before it runs."
    ),
    "protect-sensitive": (
        "NATIVE on Pi: the generated extension's on(\"tool_call\") handler hard-blocks "
        "any read/write/edit/bash touching a sensitive path before it runs."
    ),
    "budget": (
        "ADAPTED on Pi: the generated extension's on(\"agent_end\") handler REPORTS "
        "your change budget at turn end — a report, not a block."
    ),
    "format": (
        f"[{_ADVISORY_LABEL}: format] Format every file you edit before finishing. "
        "Pi does not run formatters for you; this is not enforced."
    ),
    "typecheck": (
        f"[{_ADVISORY_LABEL}: typecheck] Type-check every file you edit before "
        "finishing. Pi does not type-check for you; this is not enforced."
    ),
}


def _load_descriptor(descriptor_path: str = _DESCRIPTOR_PATH) -> dict:
    with open(descriptor_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _ir_capabilities(ir: System2Graph) -> List[str]:
    """Every intent capability present in the IR, in descriptor order.

    A capability present in the IR but absent from the descriptor would raise in
    the shared helper (no silent drop); here it is filtered to descriptor order for
    deterministic prose rendering.
    """
    union = _degradation.ir_capability_union(ir.capabilities.by_agent)
    descriptor = _load_descriptor()
    return [c for c in descriptor.get("capabilities", {}) if c in union]


def _status_by_capability() -> dict:
    descriptor = _load_descriptor()
    return {
        name: entry.get("status")
        for name, entry in descriptor.get("capabilities", {}).items()
    }


# ---------------------------------------------------------------------------
# Escaping (untrusted IR strings -> TS / JSON literals; never raw-spliced)
# ---------------------------------------------------------------------------

def _ts_escape(value: str) -> str:
    """Escape a Python string for a double-quoted TypeScript string literal.

    Every IR-derived string interpolated into the generated ``.ts`` (role names,
    write-scope regexes, reasons, the SYSTEM prompt) passes through here so no raw
    overlay/IR text is spliced into executable TS (REQ-042 injection posture).
    Uses ``json.dumps`` (a superset-safe JS string literal) so quotes, backslashes,
    newlines, and control characters are all escaped canonically.
    """
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Structured-IR markdown rendering (shared by SYSTEM.md / prompts)
# ---------------------------------------------------------------------------

def _gate_order(gate_graph) -> List[int]:
    """Gate numbers in edge order (0 first), falling back to sorted nodes."""
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


def _orchestrator_scoped_lines(ir: System2Graph) -> List[str]:
    """Render orchestrator-scoped contribution metadata into markdown lines.

    Renders only the structured metadata already on each ``Contribution.raw`` (ids,
    names, descriptions, summaries, headings) — it does NOT read overlay content
    files (that is the Claude backend's job). Deterministic: scopes follow the IR's
    insertion order; entries follow the front-end's topological order.
    """
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
                principles.append(f"- ({origin}/{cid}) overlay-contributed principle")
            elif type_path.startswith("orchestrator.gates.") and type_path.endswith(
                ".consultation"
            ):
                parts = type_path.split(".")
                gate_num = parts[2]
                cid = raw.get("id", "")
                phase = raw.get("phase", "pre-delegation")
                consultations.append(
                    f"- Gate {gate_num} [{phase}] ({origin}/{cid}) consultation"
                )
            elif type_path == "delegation.advisory_sources":
                name = raw.get("name", "")
                desc = raw.get("description", "")
                resolution = raw.get("resolution", "")
                advisory_sources.append(
                    f"- {name}: {desc} (resolution: {resolution})"
                )
            elif type_path.startswith("spec.") and type_path.endswith(
                ".required_sections"
            ):
                parts = type_path.split(".")
                artifact = parts[1]
                heading = raw.get("section_heading", "")
                desc = raw.get("description", "")
                spec_sections.append(
                    f'- spec/{artifact}.md: "{heading}" — {desc}'
                )
            elif type_path == "auxiliary_agents":
                name = raw.get("name", "")
                role = raw.get("role", "")
                aux_agents.append(f"- {name} (from {origin}): {role}")

    lines: List[str] = []
    if principles:
        lines.append("### Overlay-contributed principles")
        lines.extend(principles)
        lines.append("")
    if consultations:
        lines.append("### Overlay gate consultations")
        lines.extend(consultations)
        lines.append("")
    if advisory_sources:
        lines.append("### Advisory sources (consult when delegating)")
        lines.extend(advisory_sources)
        lines.append("")
    if spec_sections:
        lines.append("### Overlay-required spec sections")
        lines.extend(spec_sections)
        lines.append("")
    if aux_agents:
        lines.append("### Auxiliary agents (optional delegation)")
        lines.extend(aux_agents)
        lines.append("")
    return lines


def _enforcement_summary(ir: System2Graph) -> List[str]:
    """Per-capability native/adapted/advisory notes for SYSTEM.md (honest)."""
    status = _status_by_capability()
    native: List[str] = []
    adapted: List[str] = []
    advisory: List[str] = []
    for cap in _ir_capabilities(ir):
        note = _CAPABILITY_NOTE.get(cap, "")
        st = status.get(cap)
        if st == "native":
            native.append(f"- {cap}: {note}")
        elif st == "adapted":
            adapted.append(f"- {cap}: {note}")
        else:
            advisory.append(f"- {note}")

    lines: List[str] = ["## Enforcement on Pi (read this — it is MIXED)"]
    lines.append(
        "Pi has no built-in permission system; the generated "
        "`.pi/extensions/system2.ts` extension IS the gate."
    )
    lines.append("")
    if native:
        lines.append("### NATIVE — hard pre-execution blocks (real gates)")
        lines.extend(native)
        lines.append("")
    if adapted:
        lines.append("### ADAPTED — reported, not blocked")
        lines.extend(adapted)
        lines.append("")
    if advisory:
        lines.append("### ADVISORY — NOT enforced on Pi (instruction only)")
        lines.extend(advisory)
        lines.append("")
    if _any_empty_write_scope(ir):
        lines.append(
            "> NOTE: one or more roles carry an empty write_scope (read-only roles, "
            "e.g. code-reviewer). For these the lease gate FAILS CLOSED — every "
            "write/edit is BLOCKED before it runs (an unscoped role cannot write). "
            "This is enforcement, not a gap. See system2.pi.lock.json."
        )
        lines.append("")
    return lines


def _any_empty_write_scope(ir: System2Graph) -> bool:
    return any(not (r.write_scope or "").strip() for r in ir.roles)


# ---------------------------------------------------------------------------
# Markdown artifact builders (.pi/SYSTEM.md, AGENTS.md, prompts, skills)
# ---------------------------------------------------------------------------

def _build_system_md(ir: System2Graph) -> str:
    lines: List[str] = ["# System2 orchestrator context (Pi)"]
    lines.append("")
    lines.append(
        "You are driving the System2 multi-agent workflow on Pi. Advance the gate "
        "graph, delegate to the 13 roles via `/delegate <role>`, and run the "
        "post-execution and maintenance policy."
    )
    lines.append("")

    lines.append("## Gate graph (advance 0 -> 5; do not skip a gate)")
    gate_by_number = {g.number: g for g in ir.gate_graph.gates}
    for number in _gate_order(ir.gate_graph):
        gate = gate_by_number.get(number)
        if gate is None:
            continue
        lines.append(f"- Gate {gate.number} ({gate.name}): {gate.checklist_text}")
        for con in gate.consultations:
            cid = con.raw.get("id", "")
            phase = con.raw.get("phase", "pre-delegation")
            lines.append(
                f"  - Overlay consultation [{phase}] ({con.overlay_name}/{cid})"
            )
    lines.append("")

    lines.append("## Delegation contract")
    lines.append("Every delegation must specify:")
    for fieldname in ir.delegation_contract.required_fields:
        lines.append(f"- {fieldname}")
    lines.append("")
    lines.append("Preferred delegation order (the 13-role pipeline):")
    for idx, role_name in enumerate(ir.delegation_contract.preferred_order, start=1):
        lines.append(f"{idx}. {role_name}")
    lines.append("")

    lines.append("## Post-execution workflow")
    lines.append("- Execution order: " + ", ".join(ir.post_execution.execution_order))
    for tr in ir.post_execution.trigger_rules:
        when = "always" if tr.always else f"when {tr.condition}"
        lines.append(f"- Run {tr.agent} ({when})")
    lines.append(
        f"- Boomerang cap: {ir.post_execution.boomerang_cap}; on blockers: "
        f"{ir.post_execution.blocker_policy.get('on_blockers', '')} "
        f"(options: {', '.join(ir.post_execution.blocker_policy.get('options', []))})"
    )
    lines.append("")

    lines.append("## Maintenance & regression loop")
    lines.append(
        f"- Corrective-cycle cap: {ir.maintenance_loop.corrective_cycle_cap}"
    )
    lines.append(
        "- Classification: " + ", ".join(ir.maintenance_loop.classification)
    )
    lines.append("- Regression ledger fields:")
    for ledger_field in ir.maintenance_loop.regression_ledger_fields:
        lines.append(f"  - {ledger_field}")
    lines.append("")

    scoped = _orchestrator_scoped_lines(ir)
    if scoped:
        lines.append("## Overlay-contributed orchestrator material")
        lines.extend(scoped)

    lines.extend(_enforcement_summary(ir))
    return "\n".join(lines).rstrip("\n") + "\n"


def _build_agents_md(ir: System2Graph) -> str:
    lines: List[str] = ["# AGENTS.md — System2 (Pi project context)"]
    lines.append("")
    lines.append(
        "This project runs the System2 multi-agent workflow on Pi. The generated "
        "`.pi/extensions/system2.ts` extension provides the native safety gates "
        "(see `.pi/SYSTEM.md` for the MIXED enforcement story)."
    )
    lines.append("")
    lines.append("## The 13-role pipeline")
    for idx, role_name in enumerate(ir.delegation_contract.preferred_order, start=1):
        lines.append(f"{idx}. {role_name}")
    lines.append("")
    lines.append("## Gate pipeline")
    gate_by_number = {g.number: g for g in ir.gate_graph.gates}
    gate_names = [
        f"Gate {n} ({gate_by_number[n].name})"
        for n in _gate_order(ir.gate_graph)
        if n in gate_by_number
    ]
    lines.append(" -> ".join(gate_names))
    lines.append("")
    lines.append("## Where to look")
    lines.append("- `.pi/SYSTEM.md` — full orchestrator context + enforcement honesty.")
    lines.append("- `.pi/skills/system2-{init,compose,doctor}/SKILL.md` — the skills.")
    lines.append(
        "- `.pi/skills/system2-{codex,gemini,stateless-loop}/SKILL.md` — utility "
        "skills (each requires its external CLI; see the skill body)."
    )
    lines.append(
        "- `/delegate <role>` — dispatch a sub-task to one of the 13 roles."
    )
    lines.append("- `system2.pi.lock.json` — the per-capability degradation report.")
    return "\n".join(lines) + "\n"


def _role_capability_notes(ir: System2Graph, role_name: str) -> List[str]:
    status = _status_by_capability()
    role_caps = set(ir.capabilities.by_agent.get(role_name, []))
    ordered = [c for c in _ir_capabilities(ir) if c in role_caps]
    native = [c for c in ordered if status.get(c) == "native"]
    adapted = [c for c in ordered if status.get(c) == "adapted"]
    advisory = [c for c in ordered if status.get(c) not in ("native", "adapted")]

    lines: List[str] = []
    if native:
        lines.append("Native gates (enforced by the extension before the tool runs):")
        for cap in native:
            lines.append(f"- {cap}: {_CAPABILITY_NOTE.get(cap, '')}")
    if adapted:
        lines.append("Adapted (reported, not blocked):")
        for cap in adapted:
            lines.append(f"- {cap}: {_CAPABILITY_NOTE.get(cap, '')}")
    if advisory:
        lines.append("Advisory (NOT enforced on Pi — honor anyway):")
        for cap in advisory:
            lines.append(f"- {_CAPABILITY_NOTE.get(cap, '')}")
    return lines


def _build_role_prompt(ir: System2Graph, role) -> str:
    lines: List[str] = [f"# System2 role: {role.name}"]
    lines.append("")
    lines.append(
        f"You are the System2 {role.name} agent, dispatched via `/delegate "
        f"{role.name}`. Operate within your gate role and write scope."
    )
    lines.append("")
    if role.gate_role:
        lines.append(f"- Gate role: {role.gate_role}")
    scope = (role.write_scope or "").strip()
    if scope:
        lines.append(
            f"- Write scope (NATIVE lease — edits outside this are BLOCKED): "
            f"`{scope}`"
        )
    else:
        lines.append(
            "- Write scope: none (read-only role). The lease gate FAILS CLOSED for "
            "this role — any write/edit is BLOCKED before it runs. Produce review "
            "output, not file edits."
        )
    if role.model_hint:
        lines.append(f"- Model hint: {role.model_hint} (recorded; Pi model is session-level)")
    else:
        lines.append("- Model: session default model (no hint; not silently assumed)")
    lines.append("")
    notes = _role_capability_notes(ir, role.name)
    if notes:
        lines.append("## Capabilities")
        lines.extend(notes)
    return "\n".join(lines).rstrip("\n") + "\n"


def _build_orchestrator_prompt(ir: System2Graph) -> str:
    lines: List[str] = ["# System2 orchestrator"]
    lines.append("")
    lines.append(
        "Drive the System2 gate graph 0 -> 5, delegate to the 13 roles via "
        "`/delegate <role>`, and run the post-execution and maintenance policy. The "
        "full context is in `.pi/SYSTEM.md`."
    )
    lines.append("")
    lines.append("## Roles you may delegate to")
    for role_name in ir.delegation_contract.preferred_order:
        lines.append(f"- /delegate {role_name}")
    return "\n".join(lines) + "\n"


# Per-skill frontmatter (K1a; Decision D5,
# decisions/pi-base-skill-frontmatter-description-source.md). All six Pi skills carry a YAML
# frontmatter block ahead of the body: the K0 format oracle (pi-skill-format-oracle.md,
# branch (b)) found a non-empty `description` field is the minimum Pi needs to discover and
# invoke a skill at all — the prior frontmatter-less form for init/compose/doctor was latently
# undiscoverable.
def _skill_frontmatter(name: str, description: str) -> str:
    """Emit frontmatter through the canonical YAML serializer.

    Skill descriptions are backend-owned today, but treating them as data rather
    than interpolating plain scalars keeps every future description valid YAML
    (notably values containing ``: ``).
    """
    return "---\n" + _yaml.dump({"name": name, "description": description}) + "---\n\n"


# K1a-pinned frontmatter descriptions for all six Pi skills. The three base skills' descriptions
# are sourced from Pi's OWN already-emitted purpose line (Decision D5 option (b)) rather than
# the Claude counterpart's frontmatter description, which names Claude-specific mechanics that
# would be factually wrong here (e.g. init's Claude description cites writing CLAUDE.md, which
# the Pi body never does). The three new skills' descriptions are adapted from each merged
# source SKILL.md's own frontmatter description — verbatim match to codex.py's identical
# constant, since the description names only the external-CLI prerequisite, not any
# channel-specific mechanic.
_SKILL_DESCRIPTIONS = {
    "init": "Set up the System2 workflow on Pi.",
    "compose": "Run the System2 gate pipeline and delegation.",
    "doctor": "Verify the System2 extension loads and the gates are live.",
    "codex": (
        "Run a prompt through OpenAI's Codex CLI (`codex exec`) non-interactively for "
        "a second opinion or code review from a fresh Codex instance."
    ),
    "gemini": (
        "Run a prompt through Google's Antigravity CLI (`agy`) non-interactively for "
        "a second opinion or code review from a fresh instance."
    ),
    "stateless-loop": (
        "Run an instruction in a stateless subprocess loop using `claude -p` until "
        "the task reports a CLEAN status or max iterations are reached."
    ),
}

# K2 rule 4 (CC-REQ-090): each new utility skill's external-CLI prerequisite.
_UTILITY_SKILL_PREREQUISITES = {
    "codex": "the OpenAI Codex CLI (`codex`) on PATH",
    "gemini": "Google's Antigravity CLI (`agy`) on PATH",
    "stateless-loop": (
        "the Claude Code CLI (`claude`) on PATH — required even though this host is "
        "not Claude Code"
    ),
}

# K2 rule 5: the fresh-non-interactive-codex honesty sentence, system2-codex ONLY. Verbatim
# match to codex.py's identical constant.
_FRESH_CODEX_HONESTY = (
    "This spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex "
    "instance with none of this session's context. It is a second opinion from a "
    "clean slate, not a fork of the current session."
)

# K2 rule 7 (Decision D3): the OA-14 honest note, mandatory in each of the three NEW
# utility-skill bodies only. The base init/compose/doctor skills invoke no external CLI and
# sit outside the OA-14 gate's utility-skill framing, so they carry no such note.
_KNOWN_PI_LIMITATION = (
    "The System2 Pi extension's protect-sensitive gate scans the ENTIRE bash command, "
    "with no override, and Pi has no permission prompt to bypass it — so a prompt "
    "containing a token like `credentials` or `secrets` is hard-blocked before the CLI "
    "runs. Workaround: reword the prompt to avoid those tokens. This is the gate doing "
    "its declared job, not a bug — stated here so a block is never a surprise."
)


def _build_skill(name: str, ir: System2Graph) -> str:
    skill_name = f"system2-{name}"
    if name == "init":
        body = (
            "# system2-init\n"
            "\n"
            "Set up the System2 workflow on Pi.\n"
            "\n"
            "1. The `.pi/extensions/system2.ts` extension is auto-discovered by Pi "
            "from `.pi/extensions/`; it installs the native safety gates "
            "(enforce-lease, block-dangerous, protect-sensitive) and the budget "
            "report.\n"
            "2. Read `.pi/SYSTEM.md` for the orchestrator context and the MIXED "
            "enforcement story.\n"
            "3. Use `/delegate <role>` to dispatch to one of the 13 roles.\n"
        )
    elif name == "compose":
        order = ", ".join(ir.delegation_contract.preferred_order)
        body = (
            "# system2-compose\n"
            "\n"
            "Run the System2 gate pipeline and delegation.\n"
            "\n"
            "- Advance the gate graph 0 -> 5; do not skip a gate.\n"
            f"- Delegate to the 13 roles in the preferred order: {order}.\n"
            "- Every delegation specifies the delegation-contract fields "
            "(see `.pi/SYSTEM.md`).\n"
            "- `/delegate <role>` switches the active role; the lease gate then "
            "enforces that role's write scope.\n"
        )
    elif name == "doctor":
        body = (
            "# system2-doctor\n"
            "\n"
            "Verify the System2 extension loads and the gates are live.\n"
            "\n"
            "1. Confirm Pi discovered `.pi/extensions/system2.ts` (no load error).\n"
            "2. Confirm the `tool_call` handler is registered (the native gates).\n"
            "3. The operator analogue of the proven-blocking test: a dangerous bash "
            "command and a sensitive-path read must be BLOCKED before they run; an "
            "off-scope write must be BLOCKED when the active role has a write scope.\n"
            "4. Read `system2.pi.lock.json` for the per-capability degradation "
            "report and the FIDELITY banner.\n"
        )
    elif name == "codex":
        # Adapted from plugin/skills/codex/SKILL.md — keep the sync-guard invariants
        # (--ephemeral, history.persistence=none, "Never pass the prompt unquoted") intact
        # on any future edit.
        lines: List[str] = []
        lines.append(f"# {skill_name} -- Codex CLI Runner")
        lines.append("")
        lines.append(f"You are executing the {skill_name} skill. Follow these steps exactly.")
        lines.append("")
        lines.append(_FRESH_CODEX_HONESTY)
        lines.append("")
        lines.append("## Prerequisite")
        lines.append("")
        lines.append(
            f"Requires {_UTILITY_SKILL_PREREQUISITES[name]}. If the CLI is missing, "
            "stop and report the missing prerequisite verbatim; do not improvise a "
            "substitute."
        )
        lines.append("")
        lines.append("## Known Pi limitation")
        lines.append("")
        lines.append(_KNOWN_PI_LIMITATION)
        lines.append("")
        lines.append("## Arguments")
        lines.append("")
        lines.append("The user provides:")
        lines.append(
            "- **prompt** (required): the instruction to send to Codex. May be bare "
            "text or quoted."
        )
        lines.append(
            "- **additional flags** (optional): any flags supported by `codex exec` "
            "(e.g. `--model <model>`, `--sandbox <mode>`, `--config <key=value>`). "
            "These are passed through verbatim."
        )
        lines.append("")
        lines.append("## Execution")
        lines.append("")
        lines.append("### Argument parsing")
        lines.append("")
        lines.append(
            "1. Split the user's input into the **prompt** portion and any **flags** "
            "(tokens starting with `-` or `--`, plus their values)."
        )
        lines.append(
            "2. Known Codex exec flags that take a value: `--model`/`-m`, "
            "`--config`/`-c`, `--image`/`-i`, `--sandbox`/`-s`, `--profile`/`-p`, "
            "`--local-provider`, `--remote-auth-token-env`. When encountered, consume "
            "the next token as the flag's value."
        )
        lines.append(
            "3. Known Codex exec boolean flags: `--oss`, `--strict-config`, "
            "`--dangerously-bypass-approvals-and-sandbox`. Also `--enable` and "
            "`--disable` take a value each."
        )
        lines.append(
            "4. Everything that is not a recognized flag or a flag's value is the "
            "**prompt**."
        )
        lines.append("")
        lines.append("### Running Codex")
        lines.append("")
        lines.append(
            "Run a **single shell command**; allow up to 10 minutes before assuming a "
            "hang:"
        )
        lines.append("")
        lines.append("```")
        lines.append("codex exec --ephemeral -c history.persistence=none '<prompt>' [flags...]")
        lines.append("```")
        lines.append("")
        lines.append(
            "**Statelessness (required).** This skill is a one-shot second opinion; "
            "each call must be hermetic and must not see or leave behind any record of "
            "other invocations. Codex otherwise persists state to two on-disk stores "
            "under `$CODEX_HOME` (default `~/.codex`): session rollout transcripts in "
            "`sessions/`, and a running prompt log in `history.jsonl` (default "
            "persistence `save-all`). Plain `codex exec` does not auto-resume those, "
            "but the running agent can read them mid-task, and every run appends to "
            "them. To prevent both:"
        )
        lines.append("")
        lines.append(
            "- Always pass `--ephemeral` (do not write session rollout files), unless "
            "the user explicitly passed it."
        )
        lines.append(
            "- Always pass `-c history.persistence=none` (do not append to "
            "`history.jsonl`), unless the user already supplied a "
            "`history.persistence` override via their own `-c`/`--config`."
        )
        lines.append(
            "- Never add `resume` / `--last`, and never set `experimental_resume` — "
            "those deliberately reload prior context."
        )
        lines.append("")
        lines.append("Shell-quoting rules for the prompt:")
        lines.append("- If the prompt contains no single quotes, wrap it in single quotes.")
        lines.append(
            "- If it contains single quotes, wrap it in `$'...'` syntax with internal "
            "single quotes escaped as `\\'`."
        )
        lines.append("- Never pass the prompt unquoted.")
        lines.append("")
        lines.append("### Error handling")
        lines.append("")
        lines.append(
            "If `codex exec` exits non-zero, report the exit code and any stderr "
            "output to the user."
        )
        lines.append("")
        lines.append("## Output")
        lines.append("")
        lines.append(
            "Present Codex's output directly to the user. After completion, report "
            "success or failure status."
        )
        body = "\n".join(lines).rstrip("\n") + "\n"
    elif name == "gemini":
        # Adapted from plugin/skills/gemini/SKILL.md — keep the sync-guard invariants
        # (agy -p, --print-timeout, "Never pass the prompt unquoted") intact on any future
        # edit.
        lines = []
        lines.append(f"# {skill_name} -- Antigravity (agy) CLI Runner")
        lines.append("")
        lines.append(
            f"You are executing the {skill_name} skill. It drives Google's "
            "Antigravity CLI, whose binary is `agy` (this replaced the older "
            "standalone `gemini` CLI). Follow these steps exactly."
        )
        lines.append("")
        lines.append("## Prerequisite")
        lines.append("")
        lines.append(
            f"Requires {_UTILITY_SKILL_PREREQUISITES[name]}. If the CLI is missing, "
            "stop and report the missing prerequisite verbatim; do not improvise a "
            "substitute."
        )
        lines.append("")
        lines.append("## Known Pi limitation")
        lines.append("")
        lines.append(_KNOWN_PI_LIMITATION)
        lines.append("")
        lines.append("## Arguments")
        lines.append("")
        lines.append("The user provides:")
        lines.append(
            "- **prompt** (required): the instruction to send to the model. May be "
            "bare text or quoted."
        )
        lines.append(
            "- **additional flags** (optional): any flags supported by `agy` (e.g. "
            "`--model <model>`, `--sandbox`, `--dangerously-skip-permissions`). These "
            "are passed through verbatim."
        )
        lines.append("")
        lines.append("## Execution")
        lines.append("")
        lines.append("### Argument parsing")
        lines.append("")
        lines.append(
            "1. Split the user's input into the **prompt** portion and any **flags** "
            "(tokens starting with `-` or `--`, plus their values)."
        )
        lines.append(
            "2. Known `agy` flags that take a value: `--model`, `--add-dir` "
            "(repeatable), `--conversation`, `--log-file`, `--print-timeout`. When "
            "encountered, consume the next token as the flag's value."
        )
        lines.append(
            "3. Known `agy` boolean flags: `--continue`/`-c`, `--sandbox`, "
            "`--dangerously-skip-permissions`. These stand alone."
        )
        lines.append(
            "4. Everything that is not a recognized flag or a flag's value is the "
            "**prompt**."
        )
        lines.append("")
        lines.append(
            "Note on migration: the old `gemini` flags map as follows — "
            "`--yolo`/`-y` -> `--dangerously-skip-permissions`; "
            "`--include-directories` -> `--add-dir`; `--resume`/`-r` -> "
            "`--continue`/`-c` (most recent) or `--conversation <id>` (specific "
            "session); `-m` short alias is gone (use `--model`). Flags like "
            "`--debug`/`-d`, `--output-format`, `--extensions`, `--yolo` have no "
            "`agy` equivalent — drop them if a user passes them, and tell the user "
            "you did."
        )
        lines.append("")
        lines.append("### Running agy")
        lines.append("")
        lines.append(
            "Run a **single shell command**; allow up to 10 minutes before assuming a "
            "hang:"
        )
        lines.append("")
        lines.append("```")
        lines.append("agy -p '<prompt>' [flags...]")
        lines.append("```")
        lines.append("")
        lines.append(
            "`agy`'s print mode has its own `--print-timeout` (default 5m), which is "
            "shorter than the 10-minute window above. To avoid `agy` cutting off a "
            "long run early, append `--print-timeout 9m` unless the user already "
            "supplied a `--print-timeout` flag."
        )
        lines.append("")
        lines.append("Shell-quoting rules for the prompt:")
        lines.append("- If the prompt contains no single quotes, wrap it in single quotes.")
        lines.append(
            "- If it contains single quotes, wrap it in `$'...'` syntax with internal "
            "single quotes escaped as `\\'`."
        )
        lines.append("- Never pass the prompt unquoted.")
        lines.append("")
        lines.append("### Error handling")
        lines.append("")
        lines.append(
            "If `agy` exits non-zero, report the exit code and any stderr output to "
            "the user. If the failure is `command not found: agy`, tell the user the "
            "Antigravity CLI is not installed or not on PATH (it is typically "
            "installed at `/opt/homebrew/bin/agy`)."
        )
        lines.append("")
        lines.append("## Output")
        lines.append("")
        lines.append(
            "Present agy's output directly to the user. After completion, report "
            "success or failure status."
        )
        body = "\n".join(lines).rstrip("\n") + "\n"
    elif name == "stateless-loop":
        # Adapted from plugin/skills/stateless-loop/SKILL.md — keep the sync-guard
        # invariants (STATUS: CLEAN, claude -p, max_iterations) intact on any future edit.
        lines = []
        lines.append(f"# {skill_name} -- Stateless Subprocess Loop")
        lines.append("")
        lines.append(f"You are executing the {skill_name} skill. Follow these steps exactly.")
        lines.append("")
        lines.append("## Prerequisite")
        lines.append("")
        lines.append(
            f"Requires {_UTILITY_SKILL_PREREQUISITES[name]}. If the CLI is missing, "
            "stop and report the missing prerequisite verbatim; do not improvise a "
            "substitute."
        )
        lines.append("")
        lines.append("## Known Pi limitation")
        lines.append("")
        lines.append(_KNOWN_PI_LIMITATION)
        lines.append("")
        lines.append("## Arguments")
        lines.append("")
        lines.append("The user provides:")
        lines.append(
            "- **instruction** (required): the task to run each iteration. May be "
            "bare text or quoted."
        )
        lines.append("- **--max_iterations N** (optional): iteration cap, default 10.")
        lines.append("")
        lines.append("## Execution")
        lines.append("")
        lines.append(
            "Do NOT run the Python script. Orchestrate the loop yourself so each "
            "iteration is a separate shell command, each allowed up to 10 minutes "
            "before assuming a hang."
        )
        lines.append("")
        lines.append("### Prompt construction")
        lines.append("")
        lines.append(
            "Build the full prompt by appending this stop directive to the user's "
            "instruction:"
        )
        lines.append("")
        lines.append("```")
        lines.append("<instruction>")
        lines.append("")
        lines.append("---")
        lines.append(
            "STOP CONDITION: When the task above is fully resolved and no further "
            "action is needed, output exactly this line on its own:"
        )
        lines.append("STATUS: CLEAN")
        lines.append(
            "If the task is NOT fully resolved, do NOT output STATUS: CLEAN. "
            "Describe what remains."
        )
        lines.append("---")
        lines.append("```")
        lines.append("")
        lines.append("### Loop")
        lines.append("")
        lines.append("For each iteration from 1 to max_iterations:")
        lines.append("")
        lines.append("1. Print a header: `Iteration {i}/{max_iterations}`")
        lines.append(
            "2. Run a **single shell command**; allow up to 10 minutes before "
            "assuming a hang:"
        )
        lines.append("   ```")
        lines.append("   claude -p '<full_prompt>'")
        lines.append("   ```")
        lines.append("   Shell-quoting rules:")
        lines.append(
            "   - If the prompt contains no single quotes, wrap it in single quotes."
        )
        lines.append(
            '   - If it contains single quotes, wrap it in double quotes and escape '
            'any `"`, `$`, or `` ` `` inside.'
        )
        lines.append("   - Never pass the prompt unquoted.")
        lines.append("3. Check the output for `STATUS: CLEAN`.")
        lines.append("   - If found: report `Resolved after {i} iteration(s).` and stop.")
        lines.append("   - If not found: proceed to the next iteration.")
        lines.append(
            "4. If `claude -p` exits non-zero, log the exit code but continue to the "
            "next iteration."
        )
        lines.append("")
        lines.append("If all iterations complete without `STATUS: CLEAN`, report:")
        lines.append("`Max iterations ({max_iterations}) reached without STATUS: CLEAN.`")
        lines.append("")
        lines.append("## Output")
        lines.append("")
        lines.append(
            "Present subprocess output directly to the user. After completion, "
            "report the iteration count and final status."
        )
        body = "\n".join(lines).rstrip("\n") + "\n"
    else:
        raise ValueError(f"unknown Pi skill name: {name!r}")
    return _skill_frontmatter(skill_name, _SKILL_DESCRIPTIONS[name]) + body


# ---------------------------------------------------------------------------
# Degradation report (system2.pi.lock.json) via the shared helper
# ---------------------------------------------------------------------------

def _fidelity_banner(ir: System2Graph) -> str:
    banner = (
        "On Pi, the safety gates (enforce-lease, block-dangerous, protect-sensitive) "
        "are NATIVE: the generated extension's on(\"tool_call\") handler hard-blocks "
        "before the tool runs. budget is ADAPTED (reported at agent_end, not "
        "blocked). format/typecheck are ADVISORY (SYSTEM.md instruction only, not "
        "enforced)."
    )
    if _any_empty_write_scope(ir):
        banner += (
            " EMPTY-SCOPE (UNSCOPED) ROLES FAIL CLOSED: one or more roles carry an "
            "empty write_scope (read-only roles, e.g. code-reviewer); having no "
            "per-path scope, they are UNSCOPED, so the lease gate BLOCKS every "
            "write/edit before it runs (an unscoped role cannot write). This is "
            "enforcement (fail-closed), NOT an unenforced gap and NOT a silent allow."
        )
    return banner


def _build_degradation_report(ir: System2Graph) -> dict:
    descriptor = _load_descriptor()
    union = _degradation.ir_capability_union(ir.capabilities.by_agent)
    capabilities = _degradation.build_capability_records(
        descriptor,
        union,
        fields=("status", "mechanism", "enforced", "gated"),
        allow_native=True,
    )
    return {
        "backend": "pi",
        "pi_version_assumed": _PI_VERSION_ASSUMED,
        "enforcement": "extension-native-gates",
        "subagent_isolation": "adapted",
        "FIDELITY": _fidelity_banner(ir),
        "capabilities": capabilities,
    }


# ---------------------------------------------------------------------------
# The generated TypeScript extension (.pi/extensions/system2.ts) — emitted as TEXT
# ---------------------------------------------------------------------------

def _role_scope_entries(ir: System2Graph) -> List[Tuple[str, str]]:
    """``(role_name, write_scope)`` in preferred order; scopes may be empty."""
    role_by_name = {r.name: r for r in ir.roles}
    entries: List[Tuple[str, str]] = []
    for role_name in ir.delegation_contract.preferred_order:
        role = role_by_name.get(role_name)
        if role is None:
            continue
        entries.append((role_name, (role.write_scope or "").strip()))
    return entries


def _default_active_role(ir: System2Graph) -> str:
    names = ir.delegation_contract.preferred_order
    if _DEFAULT_ACTIVE_ROLE in names:
        return _DEFAULT_ACTIVE_ROLE
    return names[0] if names else _DEFAULT_ACTIVE_ROLE


def _build_extension_ts(ir: System2Graph) -> str:
    scope_entries = _role_scope_entries(ir)
    default_role = _default_active_role(ir)
    valid_roles = list(ir.delegation_contract.preferred_order)

    dangerous_lits = ",\n  ".join(
        f"[new RegExp({_ts_escape(src)}, {_ts_escape(flags)}), {_ts_escape(reason)}]"
        for src, flags, reason in build_dangerous_command_patterns()
    )
    sensitive_lits = ",\n  ".join(
        f"[new RegExp({_ts_escape(src)}, {_ts_escape(flags)}), {_ts_escape(reason)}]"
        for src, flags, reason in build_sensitive_path_patterns()
    )
    scope_lits = ",\n  ".join(
        f"[{_ts_escape(name)}, {_ts_escape(scope)}]" for name, scope in scope_entries
    )
    role_lits = ", ".join(_ts_escape(r) for r in valid_roles)
    # Roles that DO take writes but whose scope is empty must fail closed (a write
    # by an unscoped role is blocked, not allowed). A read-only role (code-reviewer)
    # simply has no scope entry; an empty-string scope entry marks "wired but the
    # allowlist produced no pattern" -> deny writes.
    writeable_roles = sorted(name for name, _scope in scope_entries)

    lines: List[str] = []
    lines.append("// Generated by the System2 compiler (Pi backend). Do not edit by hand.")
    lines.append("// Pi has no built-in permission system: this extension IS the gate.")
    lines.append("// The on(\"tool_call\") handler returns { block: true } BEFORE a tool")
    lines.append("// runs, so the safety capabilities are NATIVE (deterministic blocks).")
    lines.append("// The matchers below are PORTED from the proven Claude hooks")
    lines.append("// (dangerous-command-blocker.py, validate-file-paths.py / _hook_utils.py,")
    lines.append("// sensitive-file-protector.py) so the Pi gate and the Claude hook cannot")
    lines.append("// drift: real flag-permutation regexes (not substring includes), a")
    lines.append("// normalize_path-equivalent that fails closed on .. / absolute escapes,")
    lines.append("// and start-anchored scope matching (mirroring pattern.match).")
    lines.append("")
    lines.append('import type { ExtensionAPI, ToolCallEvent, BashToolCallEvent } from "@earendil-works/pi-coding-agent";')
    lines.append("")
    lines.append("// --- Backend-owned default pattern sets (fixed order, deterministic). ---")
    lines.append("// Ported from dangerous-command-blocker.py: [pattern, reason].")
    lines.append("const DANGEROUS_REGEXES: [RegExp, string][] = [")
    lines.append(f"  {dangerous_lits},")
    lines.append("];")
    lines.append("")
    lines.append("// Ported from sensitive-file-protector.py: segment/basename-anchored.")
    lines.append("const SENSITIVE_REGEXES: [RegExp, string][] = [")
    lines.append(f"  {sensitive_lits},")
    lines.append("];")
    lines.append("")
    lines.append("// Per-role write scope (regex string) compiled from IR Role.write_scope,")
    lines.append("// OR-joined from the role's allowlist (mirroring _hook_utils.load_patterns).")
    lines.append("// A role absent from this map is read-only (no writes expected). A role")
    lines.append("// present with an EMPTY scope writes nothing (fail-closed, see WRITEABLE_ROLES).")
    lines.append("const ROLE_WRITE_SCOPES: Map<string, string> = new Map([")
    if scope_lits:
        lines.append(f"  {scope_lits},")
    lines.append("]);")
    lines.append("")
    lines.append("// Roles that are expected to write; a write/edit by any other (read-only)")
    lines.append("// role is blocked by default (fail-closed for unscoped writers).")
    lines.append(
        "const WRITEABLE_ROLES: Set<string> = new Set(["
        + ", ".join(_ts_escape(r) for r in writeable_roles)
        + "]);"
    )
    lines.append("")
    lines.append(f"const VALID_ROLES: string[] = [{role_lits}];")
    lines.append(f"let activeRole: string = {_ts_escape(default_role)};")
    lines.append("")
    lines.append("// Path-bearing tool-input keys. Assumed tool-input shape (Pi tool set):")
    lines.append("//   read/write/edit -> { path | file_path }")
    lines.append("//   move/rename/patch/copy -> additionally { from, to, source, destination,")
    lines.append("//   old_path, new_path, target_file }")
    lines.append("// ALL of these are extracted so a rename/move shape cannot silently bypass")
    lines.append("// the sensitive-path and lease gates by carrying the path under an alias.")
    lines.append("const PATH_KEYS: string[] = [")
    lines.append('  "path", "file_path", "filepath", "filename", "file", "target_file",')
    lines.append('  "from", "to", "source", "destination", "old_path", "new_path",')
    lines.append("];")
    lines.append("")
    lines.append("function pathsOf(event: ToolCallEvent): string[] {")
    lines.append("  const input = (event.input ?? {}) as Record<string, unknown>;")
    lines.append("  const out: string[] = [];")
    lines.append("  for (const key of PATH_KEYS) {")
    lines.append("    const v = input[key];")
    lines.append('    if (typeof v === "string" && v.length > 0) out.push(v);')
    lines.append("  }")
    lines.append("  return out;")
    lines.append("}")
    lines.append("")
    lines.append("function dangerousReason(command: string): string | undefined {")
    lines.append("  for (const [re, reason] of DANGEROUS_REGEXES) {")
    lines.append("    if (re.test(command)) return `block-dangerous: ${reason}`;")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    lines.append("")
    lines.append("function sensitiveHit(text: string | undefined): string | undefined {")
    lines.append("  if (!text) return undefined;")
    lines.append("  for (const [re, description] of SENSITIVE_REGEXES) {")
    lines.append("    if (re.test(text)) return description;")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    lines.append("")
    lines.append("// normalize_path equivalent: project-relativize a path and FAIL CLOSED on")
    lines.append("// any escape. Returns null when the path leaves the project tree (a leading")
    lines.append("// `..`, an absolute path, or a `~` home path) so the lease blocks it")
    lines.append("// regardless of suffix — mirroring the reference's anchored allowlist over")
    lines.append("// normalize_path candidates. `.` and `./` are collapsed; interior `..` is")
    lines.append("// resolved and blocks only if it escapes the root.")
    lines.append("function normalizeProjectPath(p: string): string | null {")
    lines.append('  if (typeof p !== "string" || p.length === 0) return null;')
    lines.append('  if (p === "~" || p.startsWith("~/")) return null; // home dir: outside project')
    lines.append('  const slashed = p.replace(/\\\\/g, "/");')
    lines.append('  if (slashed.startsWith("/")) return null; // absolute: cannot confirm in-project -> block')
    lines.append("  const segs: string[] = [];")
    lines.append('  for (const part of slashed.split("/")) {')
    lines.append('    if (part === "" || part === ".") continue;')
    lines.append('    if (part === "..") {')
    lines.append("      if (segs.length === 0) return null; // escapes the project root -> block")
    lines.append("      segs.pop();")
    lines.append("      continue;")
    lines.append("    }")
    lines.append("    segs.push(part);")
    lines.append("  }")
    lines.append('  return segs.join("/");')
    lines.append("}")
    lines.append("")
    lines.append("function offLeasePath(event: ToolCallEvent): string | undefined {")
    lines.append('  if (event.toolName !== "write" && event.toolName !== "edit") return undefined;')
    lines.append("  const paths = pathsOf(event);")
    lines.append("  if (paths.length === 0) return undefined;")
    lines.append("  // Fail closed: a write/edit by a role with no write scope (read-only role")
    lines.append("  // or an empty allowlist) is blocked — an unscoped role cannot write.")
    lines.append("  const scope = ROLE_WRITE_SCOPES.get(activeRole);")
    lines.append("  if (!WRITEABLE_ROLES.has(activeRole) || !scope) return paths[0];")
    lines.append("  let re: RegExp;")
    lines.append("  try {")
    lines.append('    re = new RegExp("^(?:" + scope + ")");')
    lines.append("  } catch {")
    lines.append("    return paths[0]; // an uncompilable scope fails closed, not open")
    lines.append("  }")
    lines.append("  for (const p of paths) {")
    lines.append("    const norm = normalizeProjectPath(p);")
    lines.append("    if (norm === null || !re.test(norm)) return p; // escape or off-scope -> block")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    lines.append("")
    lines.append("export default function (pi: ExtensionAPI) {")
    lines.append("  // --- NATIVE safety gates: on(\"tool_call\") fires BEFORE the tool and CAN block. ---")
    lines.append("  pi.on(\"tool_call\", (event) => {")
    lines.append("    // block-dangerous: a bash command matching the ported regex set -> hard block.")
    lines.append('    if (event.toolName === "bash") {')
    lines.append("      const command = (event as BashToolCallEvent).input.command;")
    lines.append("      const reason = dangerousReason(command);")
    lines.append("      if (reason) return { block: true, reason };")
    lines.append("      const sBash = sensitiveHit(command);")
    lines.append("      if (sBash) {")
    lines.append("        return { block: true, reason: `protect-sensitive: ${sBash}` };")
    lines.append("      }")
    lines.append("      return;")
    lines.append("    }")
    lines.append("    // protect-sensitive: any path-bearing input touching a sensitive path -> hard block.")
    lines.append("    for (const p of pathsOf(event)) {")
    lines.append("      const sPath = sensitiveHit(p);")
    lines.append("      if (sPath) {")
    lines.append("        return { block: true, reason: `protect-sensitive: ${sPath}` };")
    lines.append("      }")
    lines.append("    }")
    lines.append("    // enforce-lease: a write/edit outside the active role's write scope -> hard block.")
    lines.append("    const off = offLeasePath(event);")
    lines.append("    if (off) {")
    lines.append("      return { block: true, reason: `enforce-lease: ${off} is outside the write scope for role ${activeRole}` };")
    lines.append("    }")
    lines.append("    return; // allow")
    lines.append("  });")
    lines.append("")
    lines.append("  // --- ADAPTED: budget reporting at agent_end (a report, NOT a block). ---")
    lines.append("  pi.on(\"agent_end\", (_event, ctx) => {")
    lines.append("    ctx.ui.notify(")
    lines.append("      \"System2 budget: report files touched and lines added/removed in your completion summary.\",")
    lines.append('      "info",')
    lines.append("    );")
    lines.append("  });")
    lines.append("")
    lines.append("  // --- Inject the System2 orchestrator context (OQ-P2: before_agent_start seam). ---")
    lines.append("  pi.on(\"before_agent_start\", (event) => ({")
    lines.append("    systemPrompt: `${event.systemPrompt}\\n\\nSystem2 orchestrator context is in .pi/SYSTEM.md. Drive the gate graph 0 -> 5 and delegate via /delegate <role>.`,")
    lines.append("  }));")
    lines.append("")
    lines.append("  // --- Bounded /delegate dispatcher over the 13 roles (isolation honest: adapted). ---")
    lines.append("  pi.registerCommand(\"/delegate\", {")
    lines.append("    description: \"Delegate a sub-task to one of the 13 System2 roles.\",")
    lines.append("    handler: async (args, ctx) => {")
    lines.append("      const role = args.trim();")
    lines.append("      if (!VALID_ROLES.includes(role)) {")
    lines.append("        ctx.ui.notify(")
    lines.append("          `Unknown role ${JSON.stringify(role)}. Valid roles: ${VALID_ROLES.join(\", \")}.`,")
    lines.append('          "error",')
    lines.append("        );")
    lines.append("        return;")
    lines.append("      }")
    lines.append("      activeRole = role; // role-switch: the lease gate now enforces this role's scope")
    lines.append("      ctx.ui.notify(`Delegated to ${role}. Load .pi/prompts/role-${role}.md.`, \"info\");")
    lines.append("    },")
    lines.append("  });")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Write posture (atomic write + backup/restore; mirrors claude-code)
# ---------------------------------------------------------------------------

def _build_lock(ir: System2Graph, overlay_sources: List[str]) -> dict:
    """Assemble the standalone Pi lock dict.

    The MIXED degradation report is unchanged; ``overlay_sources`` is appended LAST
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

    Deterministic order: the extension, SYSTEM.md, AGENTS.md, the orchestrator
    prompt, the 13 role prompts (preferred order), the three skills, then the
    degradation report.
    """
    planned: List[Tuple[str, str]] = []

    planned.append(
        (os.path.join(".pi", "extensions", "system2.ts"), _build_extension_ts(ir))
    )
    planned.append((os.path.join(".pi", "SYSTEM.md"), _build_system_md(ir)))
    planned.append(("AGENTS.md", _build_agents_md(ir)))
    planned.append(
        (os.path.join(".pi", "prompts", "orchestrator.md"), _build_orchestrator_prompt(ir))
    )

    role_by_name = {r.name: r for r in ir.roles}
    for role_name in ir.delegation_contract.preferred_order:
        role = role_by_name.get(role_name)
        if role is None:
            continue
        planned.append(
            (
                os.path.join(".pi", "prompts", f"role-{role_name}.md"),
                _build_role_prompt(ir, role),
            )
        )

    for skill in ("init", "compose", "doctor", "codex", "gemini", "stateless-loop"):
        planned.append(
            (
                os.path.join(".pi", "skills", f"system2-{skill}", "SKILL.md"),
                _build_skill(skill, ir),
            )
        )

    planned.append(
        (
            "system2.pi.lock.json",
            json.dumps(_build_lock(ir, overlay_sources), indent=2) + "\n",
        )
    )
    return planned


def _default_file_mode(existing_path: Optional[str] = None) -> int:
    """The mode a regenerated file should end up at: PRESERVE an existing
    destination's current mode if one is being overwritten (Codex second-opinion
    review, round 2: an earlier version unconditionally applied the umask-derived
    default even over an existing, more restrictive file), else 0o666 masked by the
    process umask for a genuinely new file (what ``open()``/``touch`` would
    produce, unlike ``mkstemp``'s fixed 0o600). Duplicated in backends/codex.py and
    backends/claude_code.py."""
    if existing_path is not None and os.path.exists(existing_path):
        return os.stat(existing_path).st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _write_outputs(project_path: str, planned: List[Tuple[str, str]]) -> List[str]:
    """Write planned files under ``project_path`` with backup/restore on failure.

    Backs up any existing target, writes via temp + ``os.replace``, and on any
    failure restores backups and removes newly created files/dirs. Writes ONLY
    under ``project_path`` — never touches ``$HOME`` / ``~/.pi``.
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
                # PR #10 review finding 3: mkstemp() defaults to mode 0600; os.replace()
                # does not change it. Codex's identical write path shipped 25/26 files at
                # 0600 in the committed distribution -- this backend has avoided the same
                # symptom only because build_pi_package.build() repackages the staged
                # tree afterward, not because this write path is itself safe.
                os.chmod(tmp, _default_file_mode(dst))
                os.replace(tmp, dst)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            if dst not in [orig for orig, _ in backups]:
                newly_created.append(dst)
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
# Lifecycle helpers (lock read / removal / hermetic extension-load validation)
# ---------------------------------------------------------------------------

# The fixed (non-prompt) Pi artifacts emit owns; uninstall removes these plus the
# 13 role prompts and the three skills when the last overlay is removed.
_PI_FIXED_ARTIFACTS = (
    os.path.join(".pi", "extensions", "system2.ts"),
    os.path.join(".pi", "SYSTEM.md"),
    "AGENTS.md",
    os.path.join(".pi", "prompts", "orchestrator.md"),
    "system2.pi.lock.json",
)

# The node harness that loads the generated extension through Pi's own
# discoverAndLoadExtensions and reports whether it loaded + registered the gate.
# Mirrors the proven-blocking eval harness; doctor only needs the load + seam
# check, so this is the minimal variant (no synthetic tool_call firing).
_PI_LOAD_HARNESS = """\
const PKG = process.argv[2];
const projectRoot = process.argv[3];
const pkg = await import(PKG);
const { discoverAndLoadExtensions } = pkg;
const { extensions, errors } = await discoverAndLoadExtensions(
  [], projectRoot, projectRoot,
);
const loadErrors = (errors || []).map(
  (e) => (e && e.message) ? e.message : String(e),
);
const ext = (extensions || []).find(
  (e) => e.path && e.path.endsWith("system2.ts"),
) || (extensions || [])[0];
const out = { loadErrors, loaded: !!ext, toolCallRegistered: false };
if (ext) {
  out.toolCallRegistered = ((ext.handlers.get("tool_call") || []).length > 0);
}
process.stdout.write(JSON.stringify(out));
"""


def _resolve_pi_pkg_entry(pi_bin: Optional[str]) -> Optional[str]:
    """Resolve the Pi package entry (dist/index.js) for the node load harness.

    Probes the env override, then the global npm/node_modules roots derived from
    the ``pi`` binary location. Returns the entry path when it exists, else None
    (doctor then reports the validator unavailable, never a silent pass).
    """
    override = os.environ.get("PI_PKG_ENTRY")
    if override and os.path.isfile(override):
        return override
    rel = os.path.join(
        "@earendil-works", "pi-coding-agent", "dist", "index.js"
    )
    roots: List[str] = []
    if pi_bin:
        prefix = os.path.dirname(os.path.dirname(os.path.realpath(pi_bin)))
        roots.append(os.path.join(prefix, "lib", "node_modules"))
        roots.append(os.path.join(prefix, "node_modules"))
    roots.append(
        os.path.join(os.path.expanduser("~"), ".npm-global", "lib", "node_modules")
    )
    for root in roots:
        candidate = os.path.join(root, rel)
        if os.path.isfile(candidate):
            return candidate
    return None


def _hermetic_env() -> dict:
    """A minimal env with HOME + a hermetic Pi discovery dir under a throwaway dir.

    The real ``~/.pi`` must never be touched by doctor's load probe, so HOME /
    XDG_CONFIG_HOME / PI_CONFIG_DIR all point under a fresh temp dir (the caller
    removes it).
    """
    home = tempfile.mkdtemp(prefix="system2-pi-doctor-home-")
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["PI_CONFIG_DIR"] = os.path.join(home, ".pi")
    return env


def _overlay_name_of(source_path: str) -> str:
    """Derive an overlay name from its source directory basename (boundary-safe)."""
    return os.path.basename(os.path.normpath(source_path))


class PiBackend:
    """Project a ``System2Graph`` onto a Pi extension + context/skill/prompt tree.

    ``emit`` writes (under ``project_path`` only) the generated TypeScript gate
    (``.pi/extensions/system2.ts``), the orchestrator context (``.pi/SYSTEM.md``,
    ``AGENTS.md``), the orchestrator + 13 role prompt templates, the three skills,
    and the standalone MIXED degradation report (``system2.pi.lock.json``). Output
    is a pure function of the IR (no timestamps). When the IR carries a ``dry_run``
    intent it returns the would-write set without touching the filesystem.
    """

    name = "pi"

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
        """The Pi target lock artifact: ``system2.pi.lock.json``."""
        return os.path.join(project_path, "system2.pi.lock.json")

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        """Read the additive ``overlay_sources[]`` key from the Pi lock.

        Raises ``FileNotFoundError`` when the lock is absent and
        ``json.JSONDecodeError`` when it is malformed. Skips empty entries.
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
        re-recording the same source set in the lock.
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
        """Remove a named overlay from the composed Pi tree.

        Reads ``overlay_sources[]`` from ``system2.pi.lock.json`` and dispatches:
        on >=1 remaining -> recompose the remaining sources via ``compose_fn`` then
        ``emit`` (re-recording the trimmed set); on 0 remaining -> remove the
        generated Pi tree (the extension, ``SYSTEM.md``, ``AGENTS.md``, the
        orchestrator + 13 role prompts, the three skills, the lock) and clean empty
        ``.pi/`` dirs, all under the atomic backup/restore (REQ-044). Writes only
        under ``project_path`` — never the operator's real ``~/.pi``.
        ``allow_newer_schema`` is threaded into the remaining-set recompose (matching
        the oracle's uninstall -> compose forwarding), so a remaining overlay
        declaring a newer schema is accepted exactly as on a direct compose.
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
                "accessible, then retry."
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
        """Remove the whole Pi artifact tree (zero overlays remain).

        Removes the extension, context, prompts, skills, and lock; cleans the
        now-empty ``.pi/`` subdirectories. Backs up every removed file and restores
        on any failure (REQ-044). Writes/removes only under ``project_path``.
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

        self._prune_empty_pi_dirs(project_path)

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
        """Every emitted Pi artifact path that exists under ``project_path``."""
        found: List[str] = []
        for rel in _PI_FIXED_ARTIFACTS:
            p = os.path.join(project_path, rel)
            if os.path.isfile(p):
                found.append(p)
        prompts_dir = os.path.join(project_path, ".pi", "prompts")
        if os.path.isdir(prompts_dir):
            for name in sorted(os.listdir(prompts_dir)):
                if name.startswith("role-") and name.endswith(".md"):
                    found.append(os.path.join(prompts_dir, name))
        skills_dir = os.path.join(project_path, ".pi", "skills")
        if os.path.isdir(skills_dir):
            for skill in sorted(os.listdir(skills_dir)):
                skill_md = os.path.join(skills_dir, skill, "SKILL.md")
                if os.path.isfile(skill_md):
                    found.append(skill_md)
        return found

    def _prune_empty_pi_dirs(self, project_path: str) -> None:
        """Remove now-empty ``.pi/`` subdirectories bottom-up (best-effort)."""
        pi_root = os.path.join(project_path, ".pi")
        if not os.path.isdir(pi_root):
            return
        for root, dirs, _files in os.walk(pi_root, topdown=False):
            for d in dirs:
                try:
                    os.rmdir(os.path.join(root, d))
                except OSError:
                    pass
        try:
            os.rmdir(pi_root)
        except OSError:
            pass

    # -----------------------------------------------------------------------
    # Phase 5 lifecycle: doctor
    # -----------------------------------------------------------------------

    def doctor(self, project_path: str) -> DoctorReport:
        """Read-only drift/status report for a composed Pi tree.

        Status model: ``no_lock`` (no ``system2.pi.lock.json``); ``broken`` (the
        generated ``.pi/extensions/system2.ts`` missing, or it fails to load via
        ``discoverAndLoadExtensions`` / does not register the ``tool_call`` gate);
        ``stale_overlay`` (a recorded ``overlay_sources[]`` path missing);
        ``current`` otherwise.

        The validity oracle is the real ``node`` + Pi ``discoverAndLoadExtensions``
        load probe run under a hermetic HOME (the real ``~/.pi`` is never touched).
        When ``node``/``pi`` is absent the structural checks still run and a LOUD
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
                "message": "No system2.pi.lock.json found; nothing is composed.",
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

        missing_sources = [s for s in sources if not os.path.isdir(s)]
        if missing_sources:
            status = "stale_overlay"
            for s in missing_sources:
                details.append({
                    "kind": "stale_overlay",
                    "message": f"recorded overlay source is missing: {s}",
                })

        extension = os.path.join(project_path, ".pi", "extensions", "system2.ts")
        if not os.path.isfile(extension):
            status = "broken"
            details.append({
                "kind": "broken",
                "message": "generated .pi/extensions/system2.ts is missing.",
            })

        # Validity oracle: real node + Pi discoverAndLoadExtensions, hermetic.
        node_bin = os.environ.get("NODE_BIN") or shutil.which("node")
        pi_bin = os.environ.get("PI_BIN") or shutil.which("pi")
        pkg_entry = _resolve_pi_pkg_entry(pi_bin)
        validator_available = bool(node_bin and pi_bin and pkg_entry)

        if not validator_available:
            details.append({
                "kind": "validator_unavailable",
                "message": (
                    "node/pi not available — extension load validation SKIPPED "
                    "(not a silent pass). Install node v22 + pi v0.80.2 (or set "
                    "NODE_BIN/PI_BIN/PI_PKG_ENTRY) to validate; structural checks ran."
                ),
            })
        elif os.path.isfile(extension):
            load = self._run_load_probe(node_bin, pkg_entry, project_path)
            if load is None or load.get("loadErrors") or not load.get("loaded"):
                status = "broken"
                details.append({
                    "kind": "broken",
                    "message": (
                        "Pi failed to load .pi/extensions/system2.ts: "
                        + (
                            ", ".join(load.get("loadErrors", []))
                            if load else "harness error"
                        )
                    ),
                })
            elif not load.get("toolCallRegistered"):
                status = "broken"
                details.append({
                    "kind": "broken",
                    "message": (
                        "loaded extension did not register the tool_call native gate."
                    ),
                })

        # Advisory (does NOT change status/exit): lock sources resolving out-of-tree.
        details.extend(lock_sources_outside_project(sources, project_path))

        locked_version = lock_data.get("pi_version_assumed", "")
        # Exit 0 when nothing is stale/broken. A LOUD validator_unavailable finding
        # is exit 0 (the operator is not faulted for an absent validator — OQ-5.2),
        # never a silent "current": the finding is always recorded above.
        exit_code = 0 if status == "current" else 1
        return DoctorReport(
            status=status,
            details=details,
            system2_version={"installed": _PI_VERSION_ASSUMED, "locked": locked_version},
            overlays=overlays,
            composed=True,
            exit_code=exit_code,
            validator_available=validator_available,
        )

    def _run_load_probe(
        self, node_bin: str, pkg_entry: str, project_path: str
    ) -> Optional[dict]:
        """Run the node load harness; return its parsed JSON, or None on failure.

        Writes the harness to a hermetic temp HOME and runs node with HOME / Pi
        discovery dir pointed there, so the real ``~/.pi`` is untouched.
        """
        env = _hermetic_env()
        home = env["HOME"]
        harness = os.path.join(home, "load_probe.mjs")
        try:
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write(_PI_LOAD_HARNESS)
            completed = subprocess.run(
                [node_bin, harness, pkg_entry, project_path],
                capture_output=True, text=True, env=env,
            )
            if completed.returncode != 0:
                return None
            return json.loads(completed.stdout)
        except (OSError, json.JSONDecodeError):
            return None
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def _require_base_path(self, verb: str) -> str:
        if not self._base_path:
            raise ValueError(
                f"PiBackend.{verb} requires base_path; construct "
                f"PiBackend(base_path=...) (the CLI supplies it)"
            )
        return self._base_path

    def _require_compose_fn(self, verb: str) -> Callable[..., object]:
        if self._compose_fn is None:
            raise ValueError(
                f"PiBackend.{verb} requires compose_fn to recompose the remaining "
                f"overlay set; construct PiBackend(compose_fn=ir.compose)"
            )
        return self._compose_fn
