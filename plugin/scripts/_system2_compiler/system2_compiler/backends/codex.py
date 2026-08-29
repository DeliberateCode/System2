"""The Codex backend (the fourth backend) — CONDITIONAL, never-native enforcement.

Lowers the same harness-neutral ``System2Graph`` IR onto a Codex plugin: a
manifest (``.codex-plugin/plugin.json``), the 13 agents recast as role skills plus
an orchestrator skill, Node (JavaScript) command hooks, and a standalone lock
(``system2.codex.lock.json``). Same boundary as the other backends: imports only
``ir.graph`` + ``backends._degradation`` + ``backends._enforcement`` + stdlib (it
structurally satisfies the Backend Protocol without importing ``backends.base``'s
Protocol type), and reads its own ``backends/capabilities/codex.json`` data file. It
never reads overlay manifests, the anchor map, profiles, or the schema, and it MUST
NOT consume ``ir.base_template`` / ``ir.overlay_inputs`` (the Claude byte-fidelity
carriers) — Codex renders from the *structured* IR fields only.

The honesty job is the hardest of the four backends: Codex's safety is ADAPTED,
never total. Codex hooks are user-trust-gated (untrusted or admin-disabled hooks
never run) and cover only shell + ``apply_patch``/``Edit``/``Write`` — not
WebSearch or other non-shell, non-MCP tools. So NOTHING here is classified
``native`` and NOTHING is ``enforced: true`` at rest; every safety gate is
``adapted`` (``enforced: false``, ``gated: true``). The trust one-liner and the
coverage-gap statement are carried verbatim across three surfaces (manifest
description, orchestrator-skill preamble, lock FIDELITY banner) so a downstream
honesty check can byte-verify agreement.

Hooks are PORTED, not re-implemented: the dangerous-command / sensitive-path
matcher sets and the path-normalizing fail-closed lease algorithm come from
``backends/_enforcement.py`` (the same proven constants the Pi backend uses).
Codex-specific hardening (F11) lives in the generated Node glue: stdin/command
length is capped before matching, a watchdog timeout resolves to BLOCK (exit 2),
and any internal error (malformed JSON, exception, oversize) fails closed — never
a silent allow. Defense-in-depth (F6): each independently-registered enforcement
hook carries its own ``system2-hook-canary`` sentinel, and a canary block echoes
the nonce parsed from the offending command (``system2-canary-blocked:<nonce>``).

The compiler emits JavaScript / markdown / JSON as **text**; it never runs node.
Output is a pure function of the IR + backend-owned constants — no timestamps,
insertion-ordered emission, LF endings, single trailing newline. Identical IR ->
byte-identical tree.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
import time
from typing import Callable, List, Optional, Tuple

from system2_compiler.ir.graph import System2Graph

from . import _degradation, _yaml
from ._enforcement import (
    build_dangerous_command_patterns,
    build_lease_gate_source,
    build_sensitive_path_patterns,
)
from .base import DoctorReport, UninstallResult, lock_sources_outside_project

__all__ = ["CodexBackend"]

# Overlay-name validation (kebab-case), shared with the other backends' contract.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "codex.json"
)

# PR #10 review finding 11: hand-pinned, with no automated guard forcing a bump when
# emitted content changes -- nothing today would stop a real behavior change from
# shipping under an unchanged version number. Bump policy (manual, until guarded):
# bump the PATCH digit for a bug fix that changes emitted bytes with no new
# user-facing capability (e.g. this PR's fixes); bump MINOR for a new skill/
# capability; bump MAJOR for a breaking change to the emitted surface (e.g. a
# skill/capability removal). Mirrors PACKAGE_VERSION's policy in
# compiler/tools/build_pi_package.py.
_CODEX_PLUGIN_VERSION = "0.2.1"

# Both enforcement guards are PreToolUse; the modern block schema carries this as
# ``hookSpecificOutput.hookEventName`` (§4a.B).
_HOOK_EVENT_NAME = "PreToolUse"

# The role whose write_scope governs a turn when no explicit in-session role switch
# has set SYSTEM2_ACTIVE_ROLE. Chosen deterministically: executor if present, else
# the first role in the preferred order (matches the Pi /delegate default).
_DEFAULT_ACTIVE_ROLE = "executor"

_ADVISORY_LABEL = "ADVISORY — NOT ENFORCED ON CODEX (instruction only)"

# The trust one-liner (OQ-L, design §4). Carried VERBATIM as a byte-substring in the
# manifest description, the orchestrator-skill preamble, and the lock FIDELITY banner
# (three-surface honesty, machine-checked downstream by test_codex_honesty.py / F4).
_TRUST_ONELINER = (
    "System2 workflows for Codex. NOTE: safety enforcement is INACTIVE until you "
    "review and trust the bundled hooks via /hooks; until then System2 runs "
    "advisory-only."
)

# The coverage-gap statement (OQ-L, design §4) — carried verbatim in the
# orchestrator-skill preamble (and the README, the implementation work) and the lock banner.
_COVERAGE_GAP = (
    "Even with hooks trusted, Codex hooks intercept shell commands and "
    "apply_patch-matched edits; they do NOT intercept WebSearch or other "
    "non-shell, non-MCP tools. Enforcement on Codex is therefore ADAPTED, never "
    "total."
)

# The canary sentinel + its base block reason, taken from the SAME _enforcement
# builder the shell hook consumes (F6). Codex-only inclusion path so Pi's bytes
# are unaffected. The consumer appends ``:<nonce>`` parsed from the canary command.
_CANARY_ENTRY = next(
    e for e in build_dangerous_command_patterns(include_canary=True)
    if e[2] == "system2-canary-blocked"
)
_CANARY_SENTINEL = _CANARY_ENTRY[0]        # "system2-hook-canary"
_CANARY_BASE_REASON = _CANARY_ENTRY[2]     # "system2-canary-blocked"

_CAPABILITY_NOTE = {
    "enforce-lease": (
        "ADAPTED on Codex: WHEN the guard is active (materialized to "
        "~/.codex/hooks.json by `system2 codex init` and reviewed+trusted via "
        "/hooks), the PreToolUse edit/shell hook hard-blocks a write outside your "
        "role's write scope BEFORE the tool runs. The path is project-normalized and "
        "the scope start-anchored (a ../ or absolute escape fails closed); a role "
        "with an empty write scope (read-only) has every write BLOCKED. Until the "
        "hooks are trusted this is advisory only, and coverage is partial (shell + "
        "apply_patch/Edit/Write; not WebSearch/other). Never native."
    ),
    "block-dangerous": (
        "ADAPTED on Codex: WHEN the guard is active (materialized to "
        "~/.codex/hooks.json by `system2 codex init` and reviewed+trusted via "
        "/hooks), the PreToolUse shell hook hard-blocks a dangerous command BEFORE "
        "it runs. Until trusted, advisory only; shell coverage only. Never native."
    ),
    "protect-sensitive": (
        "ADAPTED on Codex: WHEN the guard is active (materialized to "
        "~/.codex/hooks.json by `system2 codex init` and reviewed+trusted via "
        "/hooks), the PreToolUse hook hard-blocks sensitive edit paths and "
        "slash-delimited sensitive shell paths BEFORE the tool runs. Bare relative "
        "shell arguments (for example `cat .env`) are not parsed as paths and remain "
        "advisory. Until trusted, coverage is partial; never native."
    ),
    "budget": (
        "ADAPTED on Codex: the Stop/SubagentStop hook REPORTS your change budget at "
        "turn end — a report, not a block."
    ),
    "format": (
        f"[{_ADVISORY_LABEL}: format] Format every file you edit before finishing. "
        "Codex does not run formatters for you; this is not enforced."
    ),
    "typecheck": (
        f"[{_ADVISORY_LABEL}: typecheck] Type-check every file you edit before "
        "finishing. Codex does not type-check for you; this is not enforced."
    ),
}


def _load_descriptor(descriptor_path: str = _DESCRIPTOR_PATH) -> dict:
    with open(descriptor_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _ir_capabilities(ir: System2Graph) -> List[str]:
    """Every intent capability present in the IR, in descriptor order."""
    union = _degradation.ir_capability_union(ir.capabilities.by_agent)
    descriptor = _load_descriptor()
    return [c for c in descriptor.get("capabilities", {}) if c in union]


def _status_by_capability() -> dict:
    descriptor = _load_descriptor()
    return {
        name: entry.get("status")
        for name, entry in descriptor.get("capabilities", {}).items()
    }


def _default_active_role(ir: System2Graph) -> str:
    names = ir.delegation_contract.preferred_order
    if _DEFAULT_ACTIVE_ROLE in names:
        return _DEFAULT_ACTIVE_ROLE
    return names[0] if names else _DEFAULT_ACTIVE_ROLE


def _any_empty_write_scope(ir: System2Graph) -> bool:
    return any(not (r.write_scope or "").strip() for r in ir.roles)


# ---------------------------------------------------------------------------
# Escaping (untrusted IR strings -> JS / JSON literals; never raw-spliced)
# ---------------------------------------------------------------------------

def _js_escape(value: str) -> str:
    """Escape a Python string for a double-quoted JS string literal (json.dumps)."""
    return json.dumps(value)


# ---------------------------------------------------------------------------
# Manifest (.codex-plugin/plugin.json)
# ---------------------------------------------------------------------------

def _build_manifest() -> dict:
    """The Codex plugin manifest. All pointers are ./-relative, in-root (no .., no absolute)."""
    return {
        "name": "system2",
        "version": _CODEX_PLUGIN_VERSION,
        "description": _TRUST_ONELINER,
        "publisher": {
            "name": "DeliberateCode",
            "url": "https://github.com/DeliberateCode/System2",
        },
        "interface": {"displayName": "System2"},
        "skills": "./skills",
    }


# ---------------------------------------------------------------------------
# Skills (orchestrator + 13 role skills)
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


def _skill_frontmatter(name: str, description: str) -> List[str]:
    """Emit frontmatter through the canonical YAML serializer.

    Descriptions are data, not pre-validated YAML plain scalars; serializing
    them prevents future ``: `` and other YAML syntax from breaking discovery.
    """
    return ["---", *_yaml.dump({"name": name, "description": description}).rstrip("\n").split("\n"), "---", ""]


def _trust_state_block_lines() -> List[str]:
    """The shared trust-state honesty block (OQ-L): the trust one-liner, the three-row
    trust-state table, the activation paragraph, and the coverage-gap sentence.

    Emitted VERBATIM into BOTH the orchestrator-skill preamble and the README so the
    two honesty surfaces (design line 175) cannot drift from each other or from the
    manifest/lock — ``_TRUST_ONELINER`` and ``_COVERAGE_GAP`` are the single source
    (machine-checked by test_codex_honesty.py / F4).
    """
    return [
        _TRUST_ONELINER,
        "",
        "| Trust state | Enforcement |",
        "|---|---|",
        "| Hooks not reviewed / untrusted | ADVISORY ONLY — nothing is blocked; the "
        "hooks do not run. |",
        "| Hooks materialized to `~/.codex/hooks.json` and trusted via `/hooks` | "
        "CONDITIONAL ENFORCEMENT — dangerous shell commands, sensitive-path access, "
        "and off-lease edits are blocked before they run, with the coverage gap "
        "below. |",
        "| Admin-disabled (`requirements.toml`) | ADVISORY ONLY — immutable; hooks "
        "cannot run and this cannot be overridden. |",
        "",
        "To activate enforcement: run `system2 codex init` to materialize the guards "
        "into `~/.codex/hooks.json`, then review and trust them via `/hooks`. An "
        "administrator may disable hooks via `requirements.toml`; when disabled, "
        "System2 is advisory-only and this cannot be overridden. Nothing here "
        "auto-enables hooks or instructs blanket approval — review each hook before "
        "trusting it.",
        "",
        f"Coverage gap: {_COVERAGE_GAP}",
    ]


def _build_orchestrator_skill(ir: System2Graph) -> str:
    lines: List[str] = _skill_frontmatter(
        "system2",
        "Drive the System2 gate graph and delegate to the 13 roles (Codex, adapted).",
    )
    lines.append("# System2 orchestrator (Codex)")
    lines.append("")
    lines.append("## Trust state (READ THIS FIRST — enforcement is CONDITIONAL on Codex)")
    lines.append("")
    lines.extend(_trust_state_block_lines())
    lines.append("")

    lines.append("## Gate graph (advance 0 -> 5; do not skip a gate)")
    gate_by_number = {g.number: g for g in ir.gate_graph.gates}
    for number in _gate_order(ir.gate_graph):
        gate = gate_by_number.get(number)
        if gate is None:
            continue
        lines.append(f"- Gate {gate.number} ({gate.name}): {gate.checklist_text}")
    lines.append("")

    lines.append("## Delegation (in-session role-switching — the Pi /delegate precedent)")
    lines.append(
        "No Codex subagent component exists, so delegation is an in-session role "
        "switch: adopt the target role's skill and, so the hooks enforce that role's "
        "write lease, set the `SYSTEM2_ACTIVE_ROLE` environment variable to the role "
        "name for subsequent tool calls. This is ADAPTED (subagent_isolation is "
        "never native): the role switch shares the session, it is not an isolated "
        "sub-agent."
    )
    lines.append("")
    lines.append("Preferred delegation order (the 13-role pipeline):")
    for idx, role_name in enumerate(ir.delegation_contract.preferred_order, start=1):
        lines.append(
            f"{idx}. {role_name} — adopt `skills/system2-role-{role_name}/SKILL.md`"
        )
    lines.append("")
    lines.append("Every delegation must specify:")
    for fieldname in ir.delegation_contract.required_fields:
        lines.append(f"- {fieldname}")
    lines.append("")

    lines.append("## Post-execution workflow")
    lines.append("- Execution order: " + ", ".join(ir.post_execution.execution_order))
    for tr in ir.post_execution.trigger_rules:
        when = "always" if tr.always else f"when {tr.condition}"
        lines.append(f"- Run {tr.agent} ({when})")
    lines.append(
        f"- Boomerang cap: {ir.post_execution.boomerang_cap}; on blockers: "
        f"{ir.post_execution.blocker_policy.get('on_blockers', '')}"
    )
    lines.append("")
    lines.append("## Maintenance & regression loop")
    lines.append(f"- Corrective-cycle cap: {ir.maintenance_loop.corrective_cycle_cap}")
    lines.append("- Classification: " + ", ".join(ir.maintenance_loop.classification))
    lines.append("")
    lines.append(
        "See `system2.codex.lock.json` for the per-capability fidelity report and the "
        "FIDELITY banner. Run the `system2-doctor` skill to verify hook liveness (the "
        "compiler cannot read Codex trust state)."
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def _build_readme() -> str:
    """The committed ``README.md`` honesty surface (OQ-L, design line 175).

    Carries the SAME trust-state block as the orchestrator preamble (trust one-liner +
    state table + coverage-gap sentence, all from the shared constants) plus the
    `system2 codex init` materialization step, the `/hooks` review-and-trust step, and
    the admin `requirements.toml` note. GENERATED (never hand-written) so regen_all
    --check keeps it fresh and test_codex_honesty validates it.
    """
    lines: List[str] = []
    lines.append("# System2 for Codex")
    lines.append("")
    lines.append(
        "GENERATED plugin — do not hand-edit. Regenerate via "
        "`python3 compiler/tools/regen_all.py`."
    )
    lines.append("")
    lines.append("## Trust state (READ THIS FIRST — enforcement is CONDITIONAL on Codex)")
    lines.append("")
    lines.extend(_trust_state_block_lines())
    lines.append("")
    lines.append("## Activating enforcement")
    lines.append("")
    lines.append(
        "1. Run `system2 codex init` to materialize the guards into "
        "`~/.codex/hooks.json` (the hook `command` is written as an absolute path)."
    )
    lines.append(
        "2. Review and trust the materialized hooks via `/hooks` — read each hook "
        "before trusting it; never blanket-approve."
    )
    lines.append(
        "3. An administrator may force-disable hooks via `requirements.toml`; when "
        "disabled, System2 is advisory-only and this cannot be overridden in-session."
    )
    lines.append(
        "4. Run the `system2-doctor` skill to verify hook liveness (the compiler "
        "cannot read Codex trust state)."
    )
    lines.append("")
    lines.append("## Utility skills")
    lines.append("")
    lines.append(
        "Three adapted second-opinion skills ship alongside the 13-role workflow, "
        "each requiring its own external CLI on PATH:"
    )
    lines.append("")
    lines.append(
        "- `system2-codex` — run a prompt through OpenAI's Codex CLI (`codex exec`) "
        "non-interactively for a second opinion or code review from a fresh Codex "
        "instance. " + _FRESH_CODEX_HONESTY + " Requires "
        + _UTILITY_SKILL_PREREQUISITES["codex"] + "."
    )
    lines.append(
        "- `system2-gemini` — run a prompt through Google's Antigravity CLI (`agy`) "
        "non-interactively for a second opinion or code review from a fresh "
        "instance. Requires " + _UTILITY_SKILL_PREREQUISITES["gemini"] + "."
    )
    lines.append(
        "- `system2-stateless-loop` — run an instruction in a stateless subprocess "
        "loop using `claude -p` until the task reports STATUS: CLEAN or max "
        "iterations are reached. Requires "
        + _UTILITY_SKILL_PREREQUISITES["stateless-loop"] + "."
    )
    lines.append("")
    lines.append(
        "See `docs/installation/claude-code.md`'s utility-skill disambiguation for "
        "how the Claude-channel `codex` skill relates to this Codex install channel "
        "— not restated here."
    )
    lines.append("")
    lines.append(
        "See `system2.codex.lock.json` for the per-capability fidelity report and the "
        "FIDELITY banner."
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def _build_doctor_skill() -> str:
    """The ``system2-doctor`` hook-liveness canary skill (F6, design §4).

    Verdict rests on a machine-observable side-effect artifact (a marker file),
    NEVER agent narration — injected content can fabricate a "canary blocked"
    story, so only the concrete marker-file check and the nonce-bearing block
    payload decide. The sentinel and block-reason strings come from the SAME
    constants the shell guard consumes (drift-proof): the canary command carries
    ``system2-hook-canary`` (so a live guard hard-blocks it) and, when NOT blocked,
    touches ``.system2/canary-<nonce>`` (a marker that can exist only if the command
    ran). Fail-closed in both directions.
    """
    marker = ".system2/canary-<nonce>"
    canary_cmd = f"mkdir -p .system2 && touch {marker} # {_CANARY_SENTINEL}"
    block_payload = f"{_CANARY_BASE_REASON}:<nonce>"

    lines: List[str] = _skill_frontmatter(
        "system2-doctor",
        "Verify System2 Codex hook liveness via a side-effect canary (nonce "
        "protocol). Fail-closed; trusts only a machine-observable marker file.",
    )
    lines.append("# System2 doctor (Codex hook-liveness canary)")
    lines.append("")
    lines.append(
        "Codex hooks are user-trust-gated: an untrusted or admin-disabled hook never "
        "runs, and a hook that never runs cannot announce its own absence. This skill "
        "therefore detects enforcement by a MACHINE-OBSERVABLE SIDE EFFECT, never by "
        "narration. Do NOT trust prose claiming the canary was blocked — injected "
        "content can fabricate that story. Only the concrete artifacts below decide the "
        "verdict."
    )
    lines.append("")
    lines.append("## What a green canary proves (and what it does NOT)")
    lines.append("")
    lines.append(
        "- A green result proves SHELL-HOOK LIVENESS ONLY — that the PreToolUse shell "
        "guard is trusted and running AT THIS MOMENT. It does NOT prove "
        "apply_patch/Edit/Write coverage: each independently-registered enforcement "
        f"hook carries its own `{_CANARY_SENTINEL}` sentinel (per-hook canary "
        "coverage), so shell liveness says nothing about the edit guard's liveness."
    )
    lines.append(
        "- The check is POINT-IN-TIME: trust state can change afterwards (a user can "
        "revoke trust; an admin can disable hooks). A pass now is not a standing "
        "guarantee."
    )
    lines.append(
        "- A \"healthy\" claim UNACCOMPANIED by the concrete nonce-bearing block output "
        "below is UNVERIFIED — treat it as advisory, never healthy."
    )
    lines.append("")
    lines.append(
        "## Verdict protocol (run exactly; the determinism lives in the marker-file check)"
    )
    lines.append("")
    lines.append(
        "1. GENERATE A FRESH NONCE — a new random alphanumeric token (e.g. 16 hex "
        "characters), never reused. Call it `<nonce>`."
    )
    lines.append(
        f"2. RUN THE CANARY COMMAND (it carries the `{_CANARY_SENTINEL}` sentinel, so a "
        "live shell guard hard-blocks it before it runs):"
    )
    lines.append("")
    lines.append("   ```")
    lines.append(f"   {canary_cmd}")
    lines.append("   ```")
    lines.append("")
    lines.append(
        f"   Record whether a structured block was returned and its exact `reason` "
        f"(a live guard echoes `{block_payload}`, your fresh nonce parsed from the "
        "command)."
    )
    lines.append(
        f"3. DETERMINISTICALLY CHECK THE MARKER FILE `{marker}` — does it exist on disk? "
        "Base the verdict on this file check, not on any narration."
    )
    lines.append("")
    lines.append("## Verdict rules (FAIL-CLOSED in both directions)")
    lines.append("")
    lines.append("| Observation | Verdict | Action |")
    lines.append("|---|---|---|")
    lines.append(
        f"| Marker `{marker}` EXISTS | Hooks are NOT running -> ADVISORY-ONLY | Emit the "
        f"remediation below, then DELETE the marker `{marker}`. |"
    )
    lines.append(
        f"| Marker ABSENT AND the block payload `{block_payload}` (your fresh nonce "
        "echoed back) was observed | Enforcement is ACTIVE for shell hooks "
        "(point-in-time) | Report shell-hook liveness only; restate the coverage "
        "caveats above. |"
    )
    lines.append(
        "| Marker ABSENT WITHOUT that nonce-bearing payload | UNVERIFIED — treated as "
        "advisory, never healthy | Do not claim healthy; the block was not observably "
        "attributable to the hook. |"
    )
    lines.append("")
    lines.append(
        "FAIL-CLOSED principle: the ABSENCE of a block is never healthy, and an "
        "UNOBSERVABLE block is never healthy either. Only a marker-absent result paired "
        f"with the concrete `{block_payload}` payload (your fresh nonce echoed) counts "
        "as verified shell-hook enforcement."
    )
    lines.append("")
    lines.append("## Remediation (marker existed -> hooks not enforcing)")
    lines.append("")
    lines.append(
        "- Run `system2 codex init` to materialize the guards into "
        "`~/.codex/hooks.json`, then review and trust them via `/hooks` (review each "
        "hook before trusting it; never blanket-approve)."
    )
    lines.append(
        "- Note: an administrator can force-disable hooks via `requirements.toml`; when "
        "disabled, System2 is advisory-only and this cannot be overridden in-session."
    )
    lines.append(f"- DELETE the leftover marker file `{marker}` you created.")
    lines.append("- Re-run this protocol with a NEW nonce after remediating.")
    return "\n".join(lines).rstrip("\n") + "\n"


def _role_capability_notes(ir: System2Graph, role_name: str) -> List[str]:
    status = _status_by_capability()
    role_caps = set(ir.capabilities.by_agent.get(role_name, []))
    ordered = [c for c in _ir_capabilities(ir) if c in role_caps]
    adapted = [c for c in ordered if status.get(c) == "adapted"]
    advisory = [c for c in ordered if status.get(c) not in ("native", "adapted")]

    lines: List[str] = []
    if adapted:
        lines.append("Adapted gates (blocked before the tool runs ONLY when hooks are trusted):")
        for cap in adapted:
            lines.append(f"- {cap}: {_CAPABILITY_NOTE.get(cap, '')}")
    if advisory:
        lines.append("Advisory (NOT enforced on Codex — honor anyway):")
        for cap in advisory:
            lines.append(f"- {_CAPABILITY_NOTE.get(cap, '')}")
    return lines


def _build_role_skill(ir: System2Graph, role) -> str:
    lines: List[str] = _skill_frontmatter(
        f"system2-role-{role.name}",
        f"System2 {role.name} role (Codex, adapted).",
    )
    lines.append(f"# System2 role: {role.name} (Codex)")
    lines.append("")
    lines.append(
        f"You are the System2 {role.name} agent. Adopt this role in-session; set "
        f"`SYSTEM2_ACTIVE_ROLE={role.name}` so the hooks enforce this role's write "
        f"lease. Operate within your gate role and write scope."
    )
    lines.append("")
    if role.gate_role:
        lines.append(f"- Gate role: {role.gate_role}")
    scope = (role.write_scope or "").strip()
    if scope:
        lines.append(
            f"- Write scope (ADAPTED lease — edits outside this are BLOCKED when the "
            f"hooks are trusted): `{scope}`"
        )
    else:
        lines.append(
            "- Write scope: none (read-only role). When the hooks are trusted the "
            "lease FAILS CLOSED for this role — any write/edit is BLOCKED before it "
            "runs. Produce review output, not file edits."
        )
    if role.model_hint:
        lines.append(f"- Model hint: {role.model_hint} (recorded; Codex model is session-level)")
    else:
        lines.append("- Model: session default model (no hint; not silently assumed)")
    lines.append("")
    notes = _role_capability_notes(ir, role.name)
    if notes:
        lines.append("## Capabilities")
        lines.extend(notes)
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Utility skills (G14, adapted from the merged plugin/skills/*/SKILL.md — J2)
# ---------------------------------------------------------------------------

# Per-skill external-CLI prerequisite (J2 rule 4, the consolidation requirement).
_UTILITY_SKILL_PREREQUISITES = {
    "codex": "the OpenAI Codex CLI (`codex`) on PATH",
    "gemini": "Google's Antigravity CLI (`agy`) on PATH",
    "stateless-loop": (
        "the Claude Code CLI (`claude`) on PATH — required even though this host is "
        "not Claude Code"
    ),
}

# Frontmatter descriptions (J1/J2 rule 1) — adapted from each source SKILL.md's own
# frontmatter description, naming the CLI prerequisite.
_UTILITY_SKILL_DESCRIPTIONS = {
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

# J2 rule 5 (OA-20/the consolidation requirement): the fresh-non-interactive-codex honesty sentence,
# system2-codex ONLY.
_FRESH_CODEX_HONESTY = (
    "This spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex "
    "instance with none of this session's context. It is a second opinion from a "
    "clean slate, not a fork of the current session."
)


def _build_utility_skill(name: str) -> str:
    """Adapted body for one of the three Codex utility skills (J1/J2, the consolidation requirement/077).

    Adapted from ``plugin/skills/<name>/SKILL.md`` — see
    spec-consolidation-completion/design.md Group J2; keep the sync-guard invariants
    (rule 6's verbatim tokens: codex `--ephemeral`/`history.persistence=none`; gemini
    `agy -p`/the flag-migration map/`--print-timeout 9m`; stateless-loop
    `STATUS: CLEAN`/`claude -p`/`max_iterations`; every "Never pass the prompt
    unquoted" quoting rule) intact on any future edit.
    """
    skill_name = f"system2-{name}"
    lines: List[str] = _skill_frontmatter(skill_name, _UTILITY_SKILL_DESCRIPTIONS[name])

    if name == "codex":
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

    elif name == "gemini":
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

    elif name == "stateless-loop":
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

    else:
        raise ValueError(f"unknown utility skill name: {name!r}")

    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Node command hooks (JavaScript, Node stdlib only — the requirement)
# ---------------------------------------------------------------------------

# F11 hardening constants shared by every generated enforcement hook.
_MAX_INPUT_BYTES = 1048576   # 1 MiB stdin hard cap (memory guard); over => fail closed
_MAX_MATCH_LEN = 16384       # per command/path string cap before matching; over => block
_WATCHDOG_MS = 2000          # no decision within this window => fail closed (BLOCK)


def _js_regex_array(name: str, patterns) -> List[str]:
    lits = ",\n  ".join(
        f"[new RegExp({_js_escape(src)}, {_js_escape(flags)}), {_js_escape(reason)}]"
        for src, flags, reason in patterns
    )
    lines = [f"const {name} = ["]
    if lits:
        lines.append(f"  {lits},")
    lines.append("];")
    return lines


def _hook_constants_and_helpers(ir: System2Graph, *, include_dangerous: bool) -> List[str]:
    """The shared prelude: F11 constants, ported matcher sets, and fail-closed helpers."""
    lines: List[str] = []
    lines.append("#!/usr/bin/env node")
    lines.append('"use strict";')
    lines.append("// Generated by the System2 compiler (Codex backend). Do not edit by hand.")
    lines.append("// Node stdlib ONLY (no npm imports, no require). Fails closed on any")
    lines.append("// internal error: malformed JSON / exception / oversize / timeout ->")
    lines.append("// modern deny-JSON on stdout AND exit 2 with a reason on stderr, NEVER a")
    lines.append("// silent allow. A policy block is the modern Codex schema on stdout:")
    lines.append('// {"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":R}} (exit 0).')
    lines.append("// Matchers are PORTED from backends/_enforcement.py (the proven Claude/Pi")
    lines.append("// constants); this file only wires them to the Codex stdin-JSON event.")
    lines.append("")
    lines.append(f"const MAX_INPUT_BYTES = {_MAX_INPUT_BYTES};")
    lines.append(f"const MAX_MATCH_LEN = {_MAX_MATCH_LEN};")
    lines.append(f"const WATCHDOG_MS = {_WATCHDOG_MS};")
    lines.append(f"const HOOK_EVENT = {_js_escape(_HOOK_EVENT_NAME)};")
    lines.append(f"const CANARY_SENTINEL = {_js_escape(_CANARY_SENTINEL)};")
    lines.append(f"const CANARY_BASE_REASON = {_js_escape(_CANARY_BASE_REASON)};")
    lines.append(f"const DEFAULT_ACTIVE_ROLE = {_js_escape(_default_active_role(ir))};")
    lines.append("")
    if include_dangerous:
        lines.append("// Ported from _enforcement.build_dangerous_command_patterns(include_canary=True):")
        lines.append("// [pattern, reason]; fixed order (evaluation order is semantic). The LAST")
        lines.append("// entry is the F6 canary sentinel — a match echoes the nonce it parsed.")
        lines.extend(
            _js_regex_array(
                "DANGEROUS_REGEXES",
                build_dangerous_command_patterns(include_canary=True),
            )
        )
        lines.append("")
    lines.append("// Ported from _enforcement.build_sensitive_path_patterns(): segment/basename-anchored.")
    lines.extend(_js_regex_array("SENSITIVE_REGEXES", build_sensitive_path_patterns()))
    lines.append("")
    lines.append("// Ported from _enforcement.build_lease_gate_source(): the path-normalizing,")
    lines.append("// start-anchored, fail-closed lease matcher (defines leaseViolation()).")
    lines.append(build_lease_gate_source(_write_scopes(ir)).rstrip("\n"))
    lines.append("")
    lines.append("// --- fail-closed decision plumbing ------------------------------------")
    lines.append("function denyJson(reason) {")
    lines.append('  return JSON.stringify({ hookSpecificOutput: { hookEventName: HOOK_EVENT, permissionDecision: "deny", permissionDecisionReason: reason } });')
    lines.append("}")
    lines.append("function block(reason) {")
    lines.append("  try { process.stdout.write(denyJson(reason)); } catch (e) {}")
    lines.append("  process.exit(0);")
    lines.append("}")
    lines.append("function allow() { process.exit(0); }")
    lines.append("// A1 fallback: also emit the honored deny-JSON on stdout BEFORE exiting")
    lines.append("// non-zero, so fail-closed holds even if exit-2 denial is not honored.")
    lines.append("function failClosed(reason) {")
    lines.append("  try { process.stdout.write(denyJson(reason)); } catch (e) {}")
    lines.append('  try { process.stderr.write("system2-hook-error: " + String(reason) + "\\n"); } catch (e) {}')
    lines.append("  process.exit(2);")
    lines.append("}")
    lines.append("")
    lines.append("function activeRole() {")
    lines.append("  const r = process.env.SYSTEM2_ACTIVE_ROLE;")
    lines.append('  return (typeof r === "string" && r.length > 0) ? r : DEFAULT_ACTIVE_ROLE;')
    lines.append("}")
    lines.append("")
    lines.append("// F6: parse the nonce from a canary command (touch .system2/canary-<nonce>).")
    lines.append("function parseNonce(text) {")
    lines.append('  const m = /canary-([A-Za-z0-9][A-Za-z0-9._-]*)/.exec(String(text));')
    lines.append("  return m ? m[1] : null;")
    lines.append("}")
    lines.append("// PR #10 review finding 4: a bare CANARY_SENTINEL substring scan over the")
    lines.append("// raw payload false-positives on any edit whose CONTENT merely mentions the")
    lines.append("// sentinel (e.g. this backend's own source, or the generated doctor skill's")
    lines.append("// own instructions) -- it is not a real canary probe. A genuine canary probe")
    lines.append("// TARGETS the marker path itself (mirrors the shell canary's `touch <marker>`")
    lines.append("// semantics), so match structurally against the write-target paths instead.")
    lines.append("function canaryReason(paths) {")
    lines.append("  for (const p of paths) {")
    lines.append('    if (/(^|\\/)\\.system2\\/canary-[A-Za-z0-9][A-Za-z0-9._-]*$/.test(p)) {')
    lines.append('      return CANARY_BASE_REASON + ":" + parseNonce(p);')
    lines.append("    }")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    lines.append("")
    lines.append("function sensitiveHit(text) {")
    lines.append("  if (!text) return undefined;")
    lines.append("  for (const [re, description] of SENSITIVE_REGEXES) {")
    lines.append("    if (re.test(text)) return description;")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    lines.append("")
    lines.append("")
    return lines


def _write_scopes(ir: System2Graph) -> dict:
    """Per-role write-scope map for _enforcement.build_lease_gate_source.

    Each role's IR ``write_scope`` is already a complete regex alternation; it is
    passed as a single-element pattern list so the OR-join is a no-op (byte-parity
    with the Pi inline emission's ``new RegExp("^(?:" + scope + ")")``). An empty
    scope stays empty (a read-only role -> lease fails closed).
    """
    role_by_name = {r.name: r for r in ir.roles}
    scopes: dict = {}
    for role_name in ir.delegation_contract.preferred_order:
        role = role_by_name.get(role_name)
        if role is None:
            continue
        scopes[role_name] = [(role.write_scope or "").strip()]
    return scopes


def _hook_main_scaffold(decide_lines: List[str]) -> List[str]:
    """The shared stdin-read + length-cap + watchdog(-> BLOCK) + dispatch scaffold."""
    lines: List[str] = []
    lines.extend(decide_lines)
    lines.append("")
    lines.append("function run(raw) {")
    lines.append("  if (raw.length > MAX_INPUT_BYTES) { failClosed(\"stdin exceeds maximum size\"); return; }")
    lines.append("  let event;")
    lines.append("  try { event = JSON.parse(raw); }")
    lines.append('  catch (e) { failClosed("malformed JSON on stdin"); return; }')
    lines.append("  try {")
    lines.append("    const reason = decide(event, raw);")
    lines.append("    if (reason) block(reason); else allow();")
    lines.append("  } catch (e) {")
    lines.append('    failClosed("hook exception: " + (e && e.message ? e.message : String(e)));')
    lines.append("  }")
    lines.append("}")
    lines.append("")
    lines.append("// F11: a watchdog that resolves to BLOCK (fail closed). If no decision is")
    lines.append("// reached within WATCHDOG_MS (e.g. stdin never closes), block rather than")
    lines.append("// hang or silently allow.")
    lines.append('let watchdog = setTimeout(() => { failClosed("watchdog timeout before decision"); }, WATCHDOG_MS);')
    lines.append("let buf = \"\";")
    lines.append("let over = false;")
    lines.append('process.stdin.setEncoding("utf8");')
    lines.append('process.stdin.on("data", (c) => {')
    lines.append("  if (over) return;")
    lines.append("  buf += c;")
    lines.append("  if (buf.length > MAX_INPUT_BYTES) {")
    lines.append("    over = true;")
    lines.append("    clearTimeout(watchdog);")
    lines.append('    failClosed("stdin exceeds maximum size");')
    lines.append("  }")
    lines.append("});")
    lines.append('process.stdin.on("end", () => { clearTimeout(watchdog); run(buf); });')
    lines.append('process.stdin.on("error", () => { clearTimeout(watchdog); failClosed("stdin read error"); });')
    return lines


def _build_shell_hook_js(ir: System2Graph) -> str:
    """PreToolUse shell hook: block-dangerous + protect-sensitive + enforce-lease."""
    lines = _hook_constants_and_helpers(ir, include_dangerous=True)
    lines.append("function dangerousReason(command) {")
    lines.append("  for (const [re, reason] of DANGEROUS_REGEXES) {")
    lines.append("    if (re.test(command)) {")
    lines.append("      if (reason === CANARY_BASE_REASON) {")
    lines.append("        const nonce = parseNonce(command);")
    lines.append('        return nonce ? (CANARY_BASE_REASON + ":" + nonce) : CANARY_BASE_REASON;')
    lines.append("      }")
    lines.append('      return "block-dangerous: " + reason;')
    lines.append("    }")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    lines.append("")
    lines.append("function commandOf(event) {")
    lines.append("  const input = (event && (event.tool_input || event.toolInput || event.input || event.arguments)) || {};")
    lines.append("  let cmd = input.command;")
    lines.append("  if (cmd === undefined && event) cmd = event.command;")
    lines.append('  if (Array.isArray(cmd)) cmd = cmd.map((x) => String(x)).join(" ");')
    lines.append('  return (typeof cmd === "string") ? cmd : "";')
    lines.append("}")
    lines.append("")
    lines.append("// Mask characters inside open, unescaped quotes so a '>' or 'tee' that is")
    lines.append("// literal text within a quoted string (e.g. a commit message) is never")
    lines.append("// mistaken for a real shell redirect/pipe operator.")
    lines.append("function quoteMask(command) {")
    lines.append("  const mask = new Array(command.length).fill(false);")
    lines.append("  let quote = null;")
    lines.append("  for (let i = 0; i < command.length; i++) {")
    lines.append("    const c = command[i];")
    lines.append("    if (quote === null) {")
    lines.append('      if (c === \'"\' || c === "\'") { quote = c; mask[i] = true; continue; }')
    lines.append('      if (c === "\\\\" && i + 1 < command.length) { i++; continue; }')
    lines.append("      continue;")
    lines.append("    }")
    lines.append("    mask[i] = true;")
    lines.append('    if (quote === \'"\' && c === "\\\\" && i + 1 < command.length) { i++; mask[i] = true; continue; }')
    lines.append("    if (c === quote) { quote = null; }")
    lines.append("  }")
    lines.append("  return mask;")
    lines.append("}")
    lines.append("")
    lines.append("// True only for a shell IO-number (e.g. `2>log`), not a command name ending in a digit (`cmd2>out`).")
    lines.append("function isFdRedirect(command, operatorIndex) {")
    lines.append("  let start = operatorIndex;")
    lines.append("  while (start > 0 && /[0-9]/.test(command[start - 1])) start--;")
    lines.append("  return start < operatorIndex && (start === 0 || /[\\s;|&]/.test(command[start - 1]));")
    lines.append("}")
    lines.append("")
    lines.append("// Extract stdout write-redirection / tee targets so the lease can gate shell writes.")
    lines.append("function shellWriteTargets(command) {")
    lines.append("  const targets = [];")
    lines.append("  const mask = quoteMask(command);")
    lines.append("  const re = /(?:>>?|(?:^|[|;&]\\s*)tee(?:\\s+-a)?\\s+)\\s*(\"[^\"]+\"|'[^']+'|[^\\s;|&<>]+)/g;")
    lines.append("  let m; let guard = 0;")
    lines.append("  while ((m = re.exec(command)) !== null && guard < 256) {")
    lines.append("    guard++;")
    lines.append("    if (mask[m.index] || isFdRedirect(command, m.index)) continue;")
    lines.append("    let t = m[1];")
    lines.append('    if ((t.startsWith(\'"\') && t.endsWith(\'"\')) || (t.startsWith("\'") && t.endsWith("\'"))) t = t.slice(1, -1);')
    lines.append('    if (t.length > 0 && t !== "/dev/null") targets.push(t);')
    lines.append("  }")
    lines.append("  return targets;")
    lines.append("}")
    lines.append("")
    decide = [
        "function decide(event, raw) {",
        "  const command = commandOf(event);",
        '  if (command.length > MAX_MATCH_LEN) return "block-dangerous: shell command exceeds safe match length (fail closed)";',
        "  const dr = dangerousReason(command);",
        "  if (dr) return dr;",
        "  const sh = sensitiveHit(command);",
        '  if (sh) return "protect-sensitive: " + sh;',
        "  const targets = shellWriteTargets(command);",
        "  for (const t of targets) {",
        '    if (t.length > MAX_MATCH_LEN) return "enforce-lease: write target exceeds safe match length (fail closed)";',
        "  }",
        "  const off = leaseViolation(activeRole(), targets);",
        '  if (off) return "enforce-lease: " + off + " is outside the write scope for role " + activeRole();',
        "  return undefined;",
        "}",
    ]
    lines.extend(_hook_main_scaffold(decide))
    return "\n".join(lines) + "\n"


def _build_edit_hook_js(ir: System2Graph) -> str:
    """PreToolUse apply_patch/Edit/Write hook: enforce-lease + protect-sensitive (+ own canary)."""
    lines = _hook_constants_and_helpers(ir, include_dangerous=False)
    lines.append("const PATH_KEYS = [")
    lines.append('  "path", "file_path", "filepath", "filename", "file", "target_file",')
    lines.append('  "from", "to", "source", "destination", "old_path", "new_path",')
    lines.append("];")
    lines.append("")
    lines.append("function pathsOf(event) {")
    lines.append("  const input = (event && (event.tool_input || event.toolInput || event.input || event.arguments)) || {};")
    lines.append("  const out = [];")
    lines.append("  for (const key of PATH_KEYS) {")
    lines.append("    const v = input[key];")
    lines.append('    if (typeof v === "string" && v.length > 0) out.push(v);')
    lines.append("  }")
    lines.append("  // apply_patch-style payloads: pull file paths out of the patch text so a")
    lines.append("  // patch carrying the path inline cannot bypass the sensitive/lease gates.")
    lines.append("  // 'content' is only treated as patch text when no explicit path key was")
    lines.append("  // already found above -- a real Write's file body is not patch-shaped and")
    lines.append("  // must not be re-scanned for '--- '/'+++ ' lines once its file_path is known.")
    lines.append('  const patch = (typeof input.patch === "string") ? input.patch')
    lines.append('              : (typeof input.input === "string") ? input.input')
    lines.append('              : (out.length === 0 && typeof input.content === "string") ? input.content : "";')
    lines.append("  if (patch) {")
    lines.append("    const re = /^\\s*(?:\\*\\*\\* (?:Add|Update|Delete) File: |\\+\\+\\+ (?:b\\/)?|--- (?:a\\/)?)(.+?)\\s*$/gm;")
    lines.append("    let m; let guard = 0;")
    lines.append("    while ((m = re.exec(patch)) !== null && guard < 512) {")
    lines.append("      guard++;")
    lines.append("      const p = m[1].trim();")
    lines.append('      if (p && p !== "/dev/null") out.push(p);')
    lines.append("    }")
    lines.append("  }")
    lines.append("  return out;")
    lines.append("}")
    lines.append("")
    decide = [
        "function decide(event, raw) {",
        "  const paths = pathsOf(event);",
        "  // F6: this enforcement hook carries its own canary sentinel (defense-in-depth).",
        "  const cr = canaryReason(paths);",
        "  if (cr) return cr;",
        "  for (const p of paths) {",
        '    if (p.length > MAX_MATCH_LEN) return "protect-sensitive: path exceeds safe match length (fail closed)";',
        "    const sh = sensitiveHit(p);",
        '    if (sh) return "protect-sensitive: " + sh;',
        "  }",
        "  const off = leaseViolation(activeRole(), paths);",
        '  if (off) return "enforce-lease: " + off + " is outside the write scope for role " + activeRole();',
        "  return undefined;",
        "}",
    ]
    lines.extend(_hook_main_scaffold(decide))
    return "\n".join(lines) + "\n"


def _build_budget_hook_js() -> str:
    """Stop/SubagentStop budget report (ADAPTED — a report, never a block)."""
    lines: List[str] = []
    lines.append("#!/usr/bin/env node")
    lines.append('"use strict";')
    lines.append("// Generated by the System2 compiler (Codex backend). Do not edit by hand.")
    lines.append("// Stop / SubagentStop: report the change budget (ADAPTED — a report, not a")
    lines.append("// block). Node stdlib ONLY. A report hook must never wedge the session, so")
    lines.append("// on any error it exits 0 (fail-open is correct for a non-enforcement report).")
    lines.append("let buf = \"\";")
    lines.append('process.stdin.setEncoding("utf8");')
    lines.append('process.stdin.on("data", (c) => { if (buf.length < 1048576) buf += c; });')
    lines.append('process.stdin.on("end", () => {')
    lines.append("  try {")
    lines.append("    process.stdout.write(JSON.stringify({")
    lines.append('      systemMessage: "System2 budget (adapted, not enforced): report files touched and lines added/removed in your completion summary.",')
    lines.append("    }));")
    lines.append("  } catch (e) {}")
    lines.append("  process.exit(0);")
    lines.append("});")
    lines.append('process.stdin.on("error", () => { process.exit(0); });')
    return "\n".join(lines) + "\n"


def _build_hooks_config() -> str:
    """The user-scope config-layer hooks TEMPLATE (``hooks.json.tmpl``).

    Command strings carry the literal ``{{SYSTEM2_HOOKS_DIR}}`` placeholder — a
    user-scope hook fires across every project (cwd = the project, not ``~/.codex``,
    A2), so init resolves the placeholder to the ABSOLUTE guard directory when it
    renders this into ``~/.codex/hooks.json``. The compiler never resolves it (§4a.C).
    """
    hook = lambda name: {
        "type": "command",
        "command": f"node {{{{SYSTEM2_HOOKS_DIR}}}}/{name}",
    }
    config = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "shell|Bash|local_shell|exec",
                    "hooks": [hook("system2-shell-guard.js")],
                },
                {
                    "matcher": "apply_patch|Edit|Write|MultiEdit",
                    "hooks": [hook("system2-edit-guard.js")],
                },
            ],
            "Stop": [{"hooks": [hook("system2-budget.js")]}],
            "SubagentStop": [{"hooks": [hook("system2-budget.js")]}],
        }
    }
    return json.dumps(config, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Lock (system2.codex.lock.json) via the shared degradation helper
# ---------------------------------------------------------------------------

def _fidelity_banner(ir: System2Graph) -> str:
    banner = (
        _TRUST_ONELINER
        + " On Codex the safety gates (enforce-lease, block-dangerous, "
        "protect-sensitive) are ADAPTED, never native: they are deterministic "
        "pre-execution blocks (modern deny schema: hookSpecificOutput."
        "permissionDecision=deny) ONLY once the guard is active — materialized to "
        "~/.codex/hooks.json by `system2 codex init` and reviewed+trusted via /hooks; "
        "until then they are advisory-only, and an admin requirements.toml override "
        "can disable them immutably. budget is ADAPTED (a Stop-event report, not a "
        "block). "
        "format/typecheck are ADVISORY (skill instruction only). "
        + _COVERAGE_GAP
    )
    if _any_empty_write_scope(ir):
        banner += (
            " EMPTY-SCOPE (UNSCOPED) ROLES FAIL CLOSED: one or more roles carry an "
            "empty write_scope (read-only roles); when the hooks are trusted the "
            "lease BLOCKS every write for them. This is fail-closed enforcement, not "
            "a silent allow."
        )
    return banner


def _build_degradation_report(ir: System2Graph) -> dict:
    descriptor = _load_descriptor()
    union = _degradation.ir_capability_union(ir.capabilities.by_agent)
    capabilities = _degradation.build_capability_records(
        descriptor,
        union,
        fields=("status", "mechanism", "enforced", "gated"),
        allow_native=False,  # standing guard: nothing on Codex may be native
    )
    return {
        "backend": "codex",
        "codex_plugin_version": _CODEX_PLUGIN_VERSION,
        "enforcement": "conditional-node-hooks",
        "subagent_isolation": "adapted",
        "FIDELITY": _fidelity_banner(ir),
        "capabilities": capabilities,
    }


def _build_lock(ir: System2Graph, overlay_sources: List[str]) -> dict:
    lock = _build_degradation_report(ir)
    lock["overlay_sources"] = list(overlay_sources)
    return lock


# ---------------------------------------------------------------------------
# Planned emission + write posture (atomic write + backup/restore)
# ---------------------------------------------------------------------------

def _planned_files(
    ir: System2Graph, overlay_sources: List[str]
) -> List[Tuple[str, str]]:
    """Ordered ``(relative_path, content)`` set emit writes (deterministic).

    Order: manifest, README, the user-scope hooks template + 3 hook scripts (under
    ``user-hooks/``), orchestrator skill, the ``system2-doctor`` canary skill, the 13
    role skills (preferred order), then the lock.
    """
    planned: List[Tuple[str, str]] = []

    planned.append(
        (os.path.join(".codex-plugin", "plugin.json"),
         json.dumps(_build_manifest(), indent=2) + "\n")
    )

    planned.append(("README.md", _build_readme()))

    planned.append(
        (os.path.join("user-hooks", "hooks.json.tmpl"), _build_hooks_config())
    )
    planned.append(
        (os.path.join("user-hooks", "hooks", "system2-shell-guard.js"),
         _build_shell_hook_js(ir))
    )
    planned.append(
        (os.path.join("user-hooks", "hooks", "system2-edit-guard.js"),
         _build_edit_hook_js(ir))
    )
    planned.append(
        (os.path.join("user-hooks", "hooks", "system2-budget.js"),
         _build_budget_hook_js())
    )

    planned.append(
        (os.path.join("skills", "system2", "SKILL.md"), _build_orchestrator_skill(ir))
    )

    planned.append(
        (os.path.join("skills", "system2-doctor", "SKILL.md"), _build_doctor_skill())
    )

    role_by_name = {r.name: r for r in ir.roles}
    for role_name in ir.delegation_contract.preferred_order:
        role = role_by_name.get(role_name)
        if role is None:
            continue
        planned.append(
            (
                os.path.join("skills", f"system2-role-{role_name}", "SKILL.md"),
                _build_role_skill(ir, role),
            )
        )

    planned.append(
        (
            os.path.join("skills", "system2-codex", "SKILL.md"),
            _build_utility_skill("codex"),
        )
    )
    planned.append(
        (
            os.path.join("skills", "system2-gemini", "SKILL.md"),
            _build_utility_skill("gemini"),
        )
    )
    planned.append(
        (
            os.path.join("skills", "system2-stateless-loop", "SKILL.md"),
            _build_utility_skill("stateless-loop"),
        )
    )

    planned.append(
        (
            "system2.codex.lock.json",
            json.dumps(_build_lock(ir, overlay_sources), indent=2) + "\n",
        )
    )
    return planned


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


def _default_file_mode(existing_path: Optional[str] = None) -> int:
    """The mode a regenerated file should end up at.

    If *existing_path* is a file being overwritten, PRESERVE its current mode --
    never silently widen or narrow an intentional existing permission (Codex
    second-opinion review, round 2: an earlier version of this fix unconditionally
    applied the umask-derived default even when overwriting an existing, more
    restrictive file -- e.g. a 0600 destination silently became 0644, exactly the
    widening the original finding was about). For a genuinely NEW file, use 0o666
    masked by the process umask -- what ``open()``/``touch`` would produce, unlike
    ``mkstemp``'s fixed 0o600 (deliberately conservative for temp files, wrong for
    files meant to become permanent repo content). Duplicated in backends/pi.py and
    backends/claude_code.py (same write-helper shape, accepted duplication per this
    codebase's convention)."""
    if existing_path is not None and os.path.exists(existing_path):
        return os.stat(existing_path).st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _write_outputs(project_path: str, planned: List[Tuple[str, str]]) -> List[str]:
    """Write planned files under ``project_path`` with backup/restore on failure.

    Backs up any existing target, writes via temp + ``os.replace``, and on any
    failure restores backups and removes newly created files/dirs. Writes ONLY under
    ``project_path``.
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
                # does not change it, so the final file silently inherited 0600 instead
                # of a normal, umask-respecting mode for a regular committed source file.
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


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------

# The fixed (non-role-skill) Codex artifacts emit owns; uninstall removes these plus
# the orchestrator + 13 role skills when the last overlay is removed.
_CODEX_FIXED_ARTIFACTS = (
    os.path.join(".codex-plugin", "plugin.json"),
    os.path.join("user-hooks", "hooks.json.tmpl"),
    os.path.join("user-hooks", "hooks", "system2-shell-guard.js"),
    os.path.join("user-hooks", "hooks", "system2-edit-guard.js"),
    os.path.join("user-hooks", "hooks", "system2-budget.js"),
    "system2.codex.lock.json",
)


def _overlay_name_of(source_path: str) -> str:
    """Derive an overlay name from its source directory basename (boundary-safe)."""
    return os.path.basename(os.path.normpath(source_path))


# ---------------------------------------------------------------------------
# User-scope enforcement install (``system2 codex init``) — design §4a.C
# ---------------------------------------------------------------------------
#
# Codex runs config-layer hook commands with cwd = the project, never ``~/.codex``
# (A2). A user-scope hook must fire across EVERY project, so the emitted ``command``
# MUST be an ABSOLUTE path to the guard JS. init copies the committed guard JS into
# ``~/.codex/system2/hooks/`` and renders ``hooks.json.tmpl`` into ``~/.codex/hooks.json``
# with ``{{SYSTEM2_HOOKS_DIR}}`` resolved to that absolute dir. It READS the committed
# ``distributions/codex/user-hooks/`` reference; it never modifies it. ROQ-1
# (own-with-warning + backup): a pre-existing NON-System2 ``hooks.json`` is never
# silently clobbered — refuse without ``--force``; with it, back up (timestamped .bak)
# and warn LOUDLY first. A small install-state record lets uninstall restore that
# backup and remove exactly the System2 artifacts.

_HOOKS_PLACEHOLDER = "{{SYSTEM2_HOOKS_DIR}}"
_USER_HOOKS_TMPL = "hooks.json.tmpl"
_USER_HOOKS_JSON = "hooks.json"
_MATERIALIZED_HOOKS_SUBDIR = os.path.join("system2", "hooks")
_INSTALL_STATE_REL = os.path.join("system2", "system2-install.json")


def user_hooks_reference() -> str:
    """The packaged user-hooks reference dir.

    PR #10 review finding 6 (Codex second-opinion review, round 2): this used to
    walk four ``dirname()`` calls up from this module to find
    ``distributions/codex/user-hooks/`` at the repo root -- correct only inside a
    full checkout, since ``distributions/`` is not (and cannot cheaply become)
    setuptools package-data. Resolved relative to this module's own package
    directory instead, into ``system2_compiler/_packaged_data/codex_user_hooks/``
    -- a real declared package-data path (``pyproject.toml``), always present
    alongside this module regardless of install method (pip wheel/sdist or a live
    checkout). Kept byte-for-byte in sync with ``distributions/codex/user-hooks/``
    by ``compiler/tools/regen_all.py``'s ``_mirror_user_hooks_into_package`` (a
    straight copy of what ``CodexBackend`` already emits, never a second,
    independently-authored tree)."""
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(package_root, "_packaged_data", "codex_user_hooks")


def resolve_codex_home(explicit: Optional[str] = None) -> str:
    """Resolve the Codex home: explicit seam, then ``$CODEX_HOME``, then ``~/.codex``.

    The explicit / ``$CODEX_HOME`` seam is what lets tests target a TEMP dir so the
    real ``~/.codex`` is never written; the production default stays ``~/.codex``.
    """
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get("CODEX_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".codex")


def _resolve_hook_command(command: str, hooks_dir_abs: str) -> str:
    """Resolve one ``command`` string's ``{{SYSTEM2_HOOKS_DIR}}`` to *hooks_dir_abs*.

    The command is BOTH a JSON value and a shell command, and *hooks_dir_abs* is an
    arbitrary home path (may contain spaces, quotes, backslashes). The path segment is
    shell-quoted (``shlex.quote``) so the emitted ``node <path>`` invocation stays a
    single correct argument; ``json.dumps`` on the containing dict then JSON-escapes it.
    Raw string substitution is NOT used — it would corrupt both layers.
    """
    token = _HOOKS_PLACEHOLDER + "/"
    if token in command:
        prefix, _sep, tail = command.partition(token)
        abs_path = os.path.join(hooks_dir_abs, tail)
        return prefix + shlex.quote(abs_path)
    return command.replace(_HOOKS_PLACEHOLDER, shlex.quote(hooks_dir_abs))


def _resolve_hook_commands(node: object, hooks_dir_abs: str) -> None:
    """Recursively resolve every command hook's placeholder in-place (safe rebuild)."""
    if isinstance(node, dict):
        if node.get("type") == "command" and isinstance(node.get("command"), str):
            node["command"] = _resolve_hook_command(node["command"], hooks_dir_abs)
        for value in node.values():
            _resolve_hook_commands(value, hooks_dir_abs)
    elif isinstance(node, list):
        for item in node:
            _resolve_hook_commands(item, hooks_dir_abs)


def render_user_hooks_config(reference_dir: str, hooks_dir_abs: str) -> str:
    """Render ``hooks.json.tmpl`` with ``{{SYSTEM2_HOOKS_DIR}}`` -> *hooks_dir_abs* (absolute, A2).

    The template is parsed as JSON and rebuilt with each command's placeholder resolved
    to a shell-quoted absolute path, then re-serialized via ``json.dumps`` — so a home
    path containing a space, quote, or backslash yields VALID JSON and a correct
    ``node <path>`` invocation (never a broken string-splice, S-Esc).
    """
    tmpl_path = os.path.join(reference_dir, _USER_HOOKS_TMPL)
    with open(tmpl_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if _HOOKS_PLACEHOLDER not in text:
        raise ValueError(
            f"reference template {tmpl_path} lacks the {_HOOKS_PLACEHOLDER} "
            "placeholder; refusing to render a relative hook command"
        )
    config = json.loads(text)
    _resolve_hook_commands(config, hooks_dir_abs)
    return json.dumps(config, indent=2) + "\n"


def _guard_js_names(reference_dir: str) -> List[str]:
    hooks_src = os.path.join(reference_dir, "hooks")
    return sorted(n for n in os.listdir(hooks_src) if n.endswith(".js"))


def _hooks_json_has_system2_signature(hooks_json_path: str, guard_names: List[str]) -> bool:
    """True iff an existing ``hooks.json`` carries a System2 CONTENT signature.

    Ownership must not hinge on the install-state file alone: a partial or
    state-corrupted System2 install (state missing/unreadable but hooks.json present)
    must still be recognized as System2-owned, never misclassified as a foreign file
    (which would invert ROQ-1 under ``--force``, S-Recovery). The signature is the
    materialized guard directory segment or any guard basename in the command strings.
    """
    try:
        with open(hooks_json_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    if _MATERIALIZED_HOOKS_SUBDIR.replace(os.sep, "/") in text:
        return True
    return any(name in text for name in guard_names)


def _hooks_json_is_unmodified(
    hooks_json_path: str, expected_sha256: Optional[str], guard_names: List[str]
) -> bool:
    """True iff ``hooks.json`` is still exactly what this System2 install wrote.

    Codex second-opinion review, round 3, finding 5: the substring-based
    ``_hooks_json_has_system2_signature`` check only proves "a System2 fragment is
    present somewhere in this file" -- a user who hand-added their OWN extra hooks
    while leaving System2's entries in place still matches it, so uninstall would
    delete/overwrite their additions along with System2's. When ``codex_init``
    recorded an exact digest of the content it wrote (``hooks_json_sha256``), prefer
    a byte-exact comparison, which correctly tells "untouched since init" apart from
    "System2 fragment present but the file has since been edited." Falls back to the
    coarser signature check when the state record predates this field (older
    installs) -- no worse than before this fix for those records.
    """
    if not os.path.isfile(hooks_json_path):
        return False
    if expected_sha256:
        try:
            with open(hooks_json_path, "r", encoding="utf-8") as fh:
                actual = hashlib.sha256(fh.read().encode("utf-8")).hexdigest()
        except OSError:
            return False
        return actual == expected_sha256
    return _hooks_json_has_system2_signature(hooks_json_path, guard_names)


def _is_safe_basename(name: object) -> bool:
    """True iff *name* is a bare filename (no separators, no ``..``) — path-traversal guard."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and name == os.path.basename(name)
        and "/" not in name
        and "\\" not in name
        and ".." not in name
    )


def _resolves_inside(path: str, base_dir: str) -> bool:
    """True iff *path* resolves to *base_dir* or a descendant of it (no escape)."""
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(path)
    return real_path == real_base or real_path.startswith(real_base + os.sep)


def _read_install_state(state_path: str) -> Optional[dict]:
    if not os.path.isfile(state_path):
        return None
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _trust_instruction(hooks_json: str) -> str:
    return (
        f"System2 Codex enforcement hooks installed to {hooks_json}. Review and "
        "trust them ONCE via /hooks in Codex to activate enforcement. Enforcement is "
        "advisory-only until you trust them; once trusted it is active across ALL "
        "projects on this machine."
    )


def codex_init(
    codex_home: Optional[str] = None,
    reference_dir: Optional[str] = None,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Materialize the single global user-scope Codex enforcement install.

    ``status`` is ``"installed"``, ``"dry_run"``, or ``"refused"`` (a pre-existing
    non-System2 ``hooks.json`` without ``--force`` — never a silent clobber). The hook
    ``command`` strings written are ABSOLUTE (A2). Re-running over a prior System2
    install is idempotent (the original backup is preserved).
    """
    home = resolve_codex_home(codex_home)
    auto_discovered = reference_dir is None
    ref = reference_dir or user_hooks_reference()

    # N3: a missing/invalid reference dir must fail cleanly (the CLI turns this into an
    # error exit code), never a raw FileNotFoundError traceback from os.listdir/open.
    if not os.path.isdir(ref) or not os.path.isdir(os.path.join(ref, "hooks")):
        if auto_discovered:
            # Codex second-opinion review, round 2: the reference tree is now real
            # package-data (system2_compiler/_packaged_data/codex_user_hooks/, see
            # user_hooks_reference()'s docstring), verified end-to-end by building
            # a real wheel, installing it fresh, and running this command from
            # outside any checkout -- so reaching this branch is no longer the
            # expected "you're not in a checkout" case; it means the package's own
            # data is missing or corrupted (a bad/partial install), a genuinely
            # unexpected state.
            raise FileNotFoundError(
                f"user-hooks reference directory not found or invalid: {ref} "
                "(expected a directory containing hooks.json.tmpl and a hooks/ "
                "subdir). This is auto-discovered package-data shipped with "
                "system2-compiler itself, so this normally can't happen -- it "
                "suggests a corrupted or partial install. Try reinstalling the "
                "package, or pass --reference /path/to/distributions/codex/"
                "user-hooks explicitly to work around it."
            )
        raise FileNotFoundError(
            f"user-hooks reference directory not found or invalid: {ref} "
            "(expected a directory containing hooks.json.tmpl and a hooks/ subdir) "
            "-- this is the --reference path you passed; check it points at a "
            "distributions/codex/user-hooks directory."
        )

    hooks_dir = os.path.join(home, _MATERIALIZED_HOOKS_SUBDIR)
    hooks_json = os.path.join(home, _USER_HOOKS_JSON)
    state_path = os.path.join(home, _INSTALL_STATE_REL)

    guard_names = _guard_js_names(ref)
    rendered = render_user_hooks_config(ref, hooks_dir)

    # S-Recovery: recognize System2 ownership by the install-state record OR a content
    # signature in an existing hooks.json — so a partial/state-corrupted install is
    # never treated as a foreign file.
    #
    # Codex second-opinion review, round 4, finding 3: a prior install-state record
    # ALONE used to be treated as sufficient proof of ownership, so re-running
    # `system2 codex init` unconditionally overwrote hooks.json even if the user had
    # hand-edited it since the last write (confirmed by direct reproduction: a
    # user-added custom hook entry was silently discarded on a routine re-init, no
    # warning, no --force gate). When the state record carries the exact digest of
    # what was last written (``hooks_json_sha256``, the same field codex_uninstall
    # now checks), require an exact match: only a file that is BYTE-IDENTICAL to what
    # System2 last wrote is safe to overwrite silently on a re-run. Anything else —
    # including a partial edit that keeps System2's own entries — is treated exactly
    # like an unrecognized foreign file: refused without --force, backed up (and the
    # backup recorded for `uninstall` to restore) with it. Legacy state records that
    # predate this field fall back to the coarser substring signature check, mirroring
    # codex_uninstall's own fallback.
    prior_state = _read_install_state(state_path)
    hooks_json_modified_since_init = False
    if prior_state is not None:
        system2_owned = not os.path.isfile(hooks_json) or _hooks_json_is_unmodified(
            hooks_json, prior_state.get("hooks_json_sha256"), guard_names
        )
        hooks_json_modified_since_init = (
            os.path.isfile(hooks_json)
            and prior_state.get("hooks_json_sha256") is not None
            and not system2_owned
        )
    else:
        system2_owned = (
            os.path.isfile(hooks_json)
            and _hooks_json_has_system2_signature(hooks_json, guard_names)
        )
    preexisting_foreign = os.path.isfile(hooks_json) and not system2_owned

    warnings: List[str] = []
    if preexisting_foreign and not force:
        if hooks_json_modified_since_init:
            warnings.append(
                f"{hooks_json} has been modified since System2 last wrote it (it may "
                "still contain System2's own entries alongside your changes). System2 "
                "will NOT overwrite it silently. Re-run with --force to back it up (a "
                "timestamped .bak beside it) and install, or merge the current System2 "
                "PreToolUse/Stop/SubagentStop entries into it by hand."
            )
        else:
            warnings.append(
                f"A non-System2 hooks.json already exists at {hooks_json}. System2 will "
                "NOT overwrite it silently (machine-wide stakes). Re-run with --force to "
                "back it up (a timestamped .bak beside it) and install, or merge the "
                "System2 PreToolUse/Stop/SubagentStop entries into it by hand."
            )
        return {
            "status": "refused",
            "codex_home": home,
            "hooks_dir": hooks_dir,
            "hooks_json": hooks_json,
            "hook_files": [os.path.join(hooks_dir, n) for n in guard_names],
            "backup_path": None,
            "preexisting_foreign": True,
            "warnings": warnings,
            "message": "",
        }

    # Preserve the ORIGINAL backup across idempotent re-runs.
    backup_path = (prior_state or {}).get("backup_path")

    if dry_run:
        return {
            "status": "dry_run",
            "codex_home": home,
            "hooks_dir": hooks_dir,
            "hooks_json": hooks_json,
            "hook_files": [os.path.join(hooks_dir, n) for n in guard_names],
            "backup_path": backup_path,
            "preexisting_foreign": preexisting_foreign,
            "warnings": warnings,
            "message": _trust_instruction(hooks_json),
        }

    if preexisting_foreign:  # force is set
        backup_path = f"{hooks_json}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(hooks_json, backup_path)
        warnings.append(
            f"Existing non-System2 hooks.json backed up to {backup_path} before "
            "overwriting with the System2 config. Restore it with `system2 codex "
            "uninstall`."
        )

    os.makedirs(hooks_dir, exist_ok=True)

    # S-Recovery: write the install-state record BEFORE hooks.json so its presence
    # brackets the install — an interrupted run still leaves a System2 marker, so a
    # re-run recognizes ownership instead of misclassifying it as foreign.
    #
    # Codex second-opinion review, round 3 (finding 7, precision upgrade): the
    # coarse "does the current file contain a signature substring" check used at
    # uninstall time correctly protects a FULLY foreign replacement, but not a
    # file where the user ADDED their own hooks while leaving System2's entries
    # in place -- that file still "has the signature" by the coarse check, so
    # uninstall would delete the user's additions along with System2's own
    # entries. Recording the EXACT hash of what was just written lets uninstall
    # ask a stricter, unambiguous question: "is the file BYTE-IDENTICAL to what
    # System2 wrote", not just "does it contain a recognizable fragment". Any
    # edit at all (full replacement or partial addition) now correctly leaves
    # the file untouched at uninstall, matching this module's "leaves
    # user-authored hooks untouched" contract precisely rather than fuzzily.
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "owned": True,
            "hooks_json": hooks_json,
            "hook_files": guard_names,
            "backup_path": backup_path,
            "hooks_json_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        }, indent=2) + "\n")

    written: List[str] = []
    for name in guard_names:
        dst = os.path.join(hooks_dir, name)
        shutil.copy2(os.path.join(ref, "hooks", name), dst)
        written.append(dst)

    # S4: never write THROUGH a symlink — unlink an existing symlinked hooks.json first.
    if os.path.islink(hooks_json):
        os.unlink(hooks_json)
    with open(hooks_json, "w", encoding="utf-8") as fh:
        fh.write(rendered)

    return {
        "status": "installed",
        "codex_home": home,
        "hooks_dir": hooks_dir,
        "hooks_json": hooks_json,
        "hook_files": written,
        "backup_path": backup_path,
        "preexisting_foreign": preexisting_foreign,
        "warnings": warnings,
        "message": _trust_instruction(hooks_json),
    }


def codex_uninstall(codex_home: Optional[str] = None, *, dry_run: bool = False) -> dict:
    """Remove exactly the System2-written guard JS + the ``hooks.json`` it owns.

    Restores the timestamped backup when System2 overwrote a pre-existing file;
    otherwise removes the System2 ``hooks.json``. Leaves user-authored hooks untouched.
    A no-op when no System2 install is recorded.
    """
    home = resolve_codex_home(codex_home)
    state_path = os.path.join(home, _INSTALL_STATE_REL)
    hooks_dir = os.path.join(home, _MATERIALIZED_HOOKS_SUBDIR)
    hooks_json = os.path.join(home, _USER_HOOKS_JSON)

    state = _read_install_state(state_path)
    if state is None:
        return {"status": "nothing", "removed": [], "restored_backup": None,
                "hooks_json_removed": False, "codex_home": home}

    # S3: never act on a state record that has been tampered into a path-traversal.
    # Only bare basenames are removed (a ``/`` or ``..`` entry is skipped), and the
    # backup is restored ONLY if it resolves INSIDE codex_home.
    guard_names = [n for n in state.get("hook_files", []) if _is_safe_basename(n)]
    removed = [os.path.join(hooks_dir, n) for n in guard_names
               if os.path.isfile(os.path.join(hooks_dir, n))]

    backup_path = state.get("backup_path")
    backup_inside_home = (
        isinstance(backup_path, str)
        and bool(backup_path)
        and _resolves_inside(backup_path, home)
    )
    backup_available = backup_inside_home and os.path.isfile(backup_path)

    # PR #10 review finding 7 (+ Codex second-opinion review): the install-state
    # record alone is not proof the CURRENT hooks.json content is still System2's --
    # a user may have hand-replaced it since init, in EITHER the backup-exists or
    # no-backup case. An earlier fix only content-signature-checked the no-backup
    # branch; the backup-restore branch unconditionally overwrote hooks.json with the
    # old backup, discarding a user's post-init replacement just the same. Check
    # once, up front, and use the SAME computed decision for both the dry-run preview
    # and the real run, so they can never drift from each other.
    #
    # Codex second-opinion review, round 3, finding 5: prefer an exact digest
    # comparison (recorded by codex_init as hooks_json_sha256) over the coarse
    # substring signature check, so a user who added their OWN hooks alongside
    # System2's is correctly treated as "modified" and left alone, instead of
    # having their additions discarded as if the file were still untouched.
    current_is_system2 = _hooks_json_is_unmodified(
        hooks_json, state.get("hooks_json_sha256"), guard_names
    )
    will_restore_backup = backup_available and current_is_system2
    will_remove_hooks_json = (not backup_available) and current_is_system2

    if dry_run:
        return {
            "status": "dry_run",
            "removed": removed,
            "restored_backup": backup_path if will_restore_backup else None,
            "hooks_json_removed": will_remove_hooks_json,
            "codex_home": home,
        }

    for path in removed:
        os.unlink(path)

    if will_restore_backup:
        shutil.move(backup_path, hooks_json)
        restored = backup_path
        removed_json = False
    elif will_remove_hooks_json:
        os.unlink(hooks_json)
        restored = None
        removed_json = True
    else:
        restored = None
        removed_json = False

    if os.path.isfile(state_path):
        os.unlink(state_path)
    for d in (hooks_dir, os.path.dirname(state_path)):
        try:
            os.rmdir(d)
        except OSError:
            pass

    return {
        "status": "uninstalled",
        "removed": removed,
        "restored_backup": restored,
        "hooks_json_removed": removed_json,
        "codex_home": home,
    }


class CodexBackend:
    """Project a ``System2Graph`` onto a Codex plugin (manifest + skills + Node hooks + lock).

    ``emit`` writes (under ``project_path`` only) the manifest
    (``.codex-plugin/plugin.json``), the hooks config + three Node command hooks, the
    orchestrator skill + 13 role skills, and the standalone ADAPTED fidelity report
    (``system2.codex.lock.json``). Output is a pure function of the IR (no
    timestamps). When the IR carries a ``dry_run`` intent it returns the would-write
    set without touching the filesystem.
    """

    name = "codex"

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
        planned = _planned_files(ir, overlay_sources)
        if bool(getattr(ir, "dry_run", False)):
            return [os.path.join(project_path, rel) for rel, _ in planned]
        return _write_outputs(project_path, planned)

    # -----------------------------------------------------------------------
    # Lifecycle: lock helpers
    # -----------------------------------------------------------------------

    def lock_path(self, project_path: str) -> str:
        """The Codex target lock artifact: ``system2.codex.lock.json``."""
        return os.path.join(project_path, "system2.codex.lock.json")

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        lp = self.lock_path(project_path)
        if not os.path.isfile(lp):
            raise FileNotFoundError(lp)
        with open(lp, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
        return [s for s in lock_data.get("overlay_sources", []) if s]

    # -----------------------------------------------------------------------
    # Lifecycle: recompose from lock
    # -----------------------------------------------------------------------

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        return self._emit_with_sources(
            ir, project_path, self._resolve_overlay_sources(ir)
        )

    # -----------------------------------------------------------------------
    # Lifecycle: uninstall
    # -----------------------------------------------------------------------

    def uninstall(
        self,
        project_path: str,
        overlay_name: str,
        *,
        dry_run: bool = False,
        allow_newer_schema: bool = False,
    ) -> UninstallResult:
        """Remove a named overlay from the composed Codex tree (mirrors the Pi backend)."""
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
                f"Overlay {overlay_name!r} is not installed. Installed: {installed}"
            ])

        remaining_sources = [
            s for s in sources if _overlay_name_of(s) != overlay_name
        ]
        remaining_meta = [{"name": _overlay_name_of(s)} for s in remaining_sources]

        if not remaining_sources:
            return self._uninstall_last_overlay(project_path, overlay_name, dry_run)

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
        """Remove the whole Codex artifact tree (zero overlays remain)."""
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

        self._prune_empty_dirs(project_path)

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
        """Every emitted Codex artifact path that exists under ``project_path``."""
        found: List[str] = []
        for rel in _CODEX_FIXED_ARTIFACTS:
            p = os.path.join(project_path, rel)
            if os.path.isfile(p):
                found.append(p)
        skills_dir = os.path.join(project_path, "skills")
        if os.path.isdir(skills_dir):
            for skill in sorted(os.listdir(skills_dir)):
                skill_md = os.path.join(skills_dir, skill, "SKILL.md")
                if os.path.isfile(skill_md):
                    found.append(skill_md)
        return found

    def _prune_empty_dirs(self, project_path: str) -> None:
        """Remove now-empty ``.codex-plugin/`` / ``user-hooks/`` / ``skills/`` dirs (best-effort)."""
        for top in (".codex-plugin", "user-hooks", "skills"):
            root_dir = os.path.join(project_path, top)
            if not os.path.isdir(root_dir):
                continue
            for root, dirs, _files in os.walk(root_dir, topdown=False):
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except OSError:
                        pass
            try:
                os.rmdir(root_dir)
            except OSError:
                pass

    # -----------------------------------------------------------------------
    # Lifecycle: doctor (documented honest subset)
    # -----------------------------------------------------------------------

    def doctor(self, project_path: str) -> DoctorReport:
        """Read-only drift/status report — the documented honest subset (design §4).

        Verifies emitted-content INTEGRITY against the lock where paths are known
        (the fixed manifest/hooks/skill artifacts exist; recorded overlay sources
        resolve). It CANNOT read Codex trust/approval state — the compiler process
        has no visibility into whether the user trusted the hooks via /hooks — so it
        ALWAYS records a LOUD finding pointing at the in-channel ``system2-doctor``
        canary skill and sets ``validator_available = False`` (never a silent
        ``current`` on the enforcement question). The exit code tracks ``status``.
        """
        details: List[dict] = []
        lp = self.lock_path(project_path)
        if not os.path.isfile(lp):
            details.append({
                "kind": "no_lock",
                "message": "No system2.codex.lock.json found; nothing is composed.",
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

        # Emitted-content integrity: the fixed manifest/hooks artifacts must exist.
        for rel in _CODEX_FIXED_ARTIFACTS:
            if rel == "system2.codex.lock.json":
                continue
            if not os.path.isfile(os.path.join(project_path, rel)):
                status = "broken"
                details.append({
                    "kind": "broken",
                    "message": f"generated Codex artifact is missing: {rel}",
                })

        # HONEST SUBSET (design §4): hook trust/approval state is NOT machine-observable
        # by the compiler. Always surface this loudly and delegate to the canary skill.
        details.append({
            "kind": "validator_unavailable",
            "message": (
                "Codex hook trust/approval state is NOT observable by the compiler "
                "process (it cannot read whether you ran `system2 codex init` or "
                "trusted the materialized hooks via /hooks). Run the in-channel "
                "`system2-doctor` "
                "canary skill inside Codex to verify hook liveness; a green canary "
                "proves shell-hook enforcement is live at that moment only."
            ),
        })

        # Advisory (does NOT change status/exit): lock sources resolving out-of-tree.
        details.extend(lock_sources_outside_project(sources, project_path))

        locked_version = lock_data.get("codex_plugin_version", "")
        exit_code = 0 if status == "current" else 1
        return DoctorReport(
            status=status,
            details=details,
            system2_version={"installed": _CODEX_PLUGIN_VERSION, "locked": locked_version},
            overlays=overlays,
            composed=True,
            exit_code=exit_code,
            validator_available=False,
        )

    def _require_base_path(self, verb: str) -> str:
        if not self._base_path:
            raise ValueError(
                f"CodexBackend.{verb} requires base_path; construct "
                f"CodexBackend(base_path=...) (the CLI supplies it)"
            )
        return self._base_path

    def _require_compose_fn(self, verb: str) -> Callable[..., object]:
        if self._compose_fn is None:
            raise ValueError(
                f"CodexBackend.{verb} requires compose_fn to recompose the remaining "
                f"overlay set; construct CodexBackend(compose_fn=ir.compose)"
            )
        return self._compose_fn
