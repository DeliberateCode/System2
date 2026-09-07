"""Codex backend with trust-gated enforcement."""

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
from .base import (
    DoctorReport,
    UninstallResult,
    build_artifact_ownership,
    lock_sources_outside_project,
    preflight_artifact_write,
    verify_owned_artifacts,
)

__all__ = ["CodexBackend"]

# Overlay-name validation (kebab-case), shared with the other backends' contract.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "codex.json"
)

# Keep in sync with PACKAGE_VERSION; bump when user-visible output changes.
_CODEX_PLUGIN_VERSION = "0.2.2"

# Both enforcement guards are PreToolUse; the modern block schema carries this as
# ``hookSpecificOutput.hookEventName``.
_HOOK_EVENT_NAME = "PreToolUse"

# Default role before an explicit in-session role switch.
_DEFAULT_ACTIVE_ROLE = "executor"

_ADVISORY_LABEL = "ADVISORY — NOT ENFORCED ON CODEX (instruction only)"

# Reuse this trust statement across every user-visible enforcement surface.
_TRUST_ONELINER = (
    "System2 workflows for Codex. NOTE: safety enforcement is INACTIVE until you "
    "review and trust the bundled hooks via /hooks; until then System2 runs "
    "advisory-only."
)

# This coverage statement appears verbatim in the orchestrator preamble and lock
# banner so neither surface can overstate hook coverage.
_COVERAGE_GAP = (
    "Even with hooks trusted, Codex hooks intercept shell commands and "
    "apply_patch-matched edits; they do NOT intercept WebSearch or other "
    "non-shell, non-MCP tools. Enforcement on Codex is therefore ADAPTED, never "
    "total."
)

# Derive the Codex canary from the same matcher set used by its shell hook.
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


# Escaping (untrusted IR strings -> JS / JSON literals; never raw-spliced)

def _js_escape(value: str) -> str:
    """Escape a Python string for a double-quoted JS string literal (json.dumps)."""
    return json.dumps(value)


# Manifest (.codex-plugin/plugin.json)

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


# Skills (orchestrator + 13 role skills)

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
    """Emit frontmatter through the canonical YAML serializer."""
    return ["---", *_yaml.dump({"name": name, "description": description}).rstrip("\n").split("\n"), "---", ""]


def _trust_state_block_lines() -> List[str]:
    """Build the shared enforcement trust and activation guidance."""
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


def _build_doctor_skill() -> str:
    """Build the ``system2-doctor`` hook-liveness canary skill."""
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


# Node command hooks (JavaScript, Node stdlib only)

# Resource limits shared by every generated enforcement hook.
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
    """The shared prelude:  constants, ported matcher sets, and fail-closed helpers."""
    lines: List[str] = []
    lines.append("#!/usr/bin/env node")
    lines.append('"use strict";')
    lines.append("// Generated by the System2 compiler. Do not edit by hand.")
    lines.append("// Invalid input and internal errors fail closed.")
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
        lines.append("// Dangerous-command matchers are ordered; the final entry is the canary.")
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
    lines.append("// Path-normalizing, fail-closed lease matcher.")
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
    lines.append("// Emit a denial before the non-zero fallback exit.")
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
    lines.append("// Parse the nonce from a canary command (touch .system2/canary-<nonce>).")
    lines.append("function parseNonce(text) {")
    lines.append('  const m = /canary-([A-Za-z0-9][A-Za-z0-9._-]*)/.exec(String(text));')
    lines.append("  return m ? m[1] : null;")
    lines.append("}")
    lines.append("// Match canary target paths, not sentinel text in file content.")
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
    """Per-role write-scope map for _enforcement.build_lease_gate_source."""
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
    lines.append("// Block when the watchdog expires.")
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
    lines.append("// Ignore redirect syntax inside quoted text.")
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
    lines.append("// Extract write-redirection / tee targets so the lease can gate shell writes.")
    lines.append("function shellWriteTargets(command) {")
    lines.append("  const targets = [];")
    lines.append("  const mask = quoteMask(command);")
    lines.append("  const re = /(?:>>?|(?:^|[|;&]\\s*)tee(?:\\s+-a)?\\s+)\\s*(\"[^\"]+\"|'[^']+'|[^\\s;|&<>]+)/g;")
    lines.append("  let m; let guard = 0;")
    lines.append("  while ((m = re.exec(command)) !== null && guard < 256) {")
    lines.append("    guard++;")
    lines.append("    if (mask[m.index]) continue;")
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
    lines.append("  // Extract patch paths only when no explicit write path is present.")
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
        "  // This enforcement hook carries its own canary sentinel (defense-in-depth).",
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
    lines.append("// Generated by the System2 compiler. Do not edit by hand.")
    lines.append("// Report the change budget; errors fail open because this hook does not enforce policy.")
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
    """The user-scope config-layer hooks TEMPLATE (``hooks.json.tmpl``)."""
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


# Lock (system2.codex.lock.json) via the shared degradation helper

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


_CODEX_LOCK = "system2.codex.lock.json"


def _build_lock(
    ir: System2Graph, overlay_sources: List[str], ownership: dict
) -> dict:
    lock = _build_degradation_report(ir)
    lock["ownership"] = ownership
    lock["overlay_sources"] = list(overlay_sources)
    return lock


# Planned emission + write posture (atomic write + backup/restore)

def _planned_files(
    ir: System2Graph, overlay_sources: List[str]
) -> List[Tuple[str, str]]:
    """Ordered ``(relative_path, content)`` set emit writes (deterministic)."""
    planned: List[Tuple[str, str]] = []

    planned.append(
        (os.path.join(".codex-plugin", "plugin.json"),
         json.dumps(_build_manifest(), indent=2) + "\n")
    )

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

    ownership = build_artifact_ownership(planned, _CODEX_LOCK)
    planned.append(
        (
            _CODEX_LOCK,
            json.dumps(_build_lock(ir, overlay_sources, ownership), indent=2) + "\n",
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
    """The mode a regenerated file should end up at."""
    if existing_path is not None and os.path.exists(existing_path):
        return os.stat(existing_path).st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def _write_outputs(project_path: str, planned: List[Tuple[str, str]]) -> List[str]:
    """Write planned files under ``project_path`` with backup/restore on failure."""
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
                # Apply the final mode because mkstemp creates files as 0600.
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


# Lifecycle helpers

# Fixed Codex artifacts required by doctor (variable skills live in the lock inventory).
_CODEX_FIXED_ARTIFACTS = (
    os.path.join(".codex-plugin", "plugin.json"),
    os.path.join("user-hooks", "hooks.json.tmpl"),
    os.path.join("user-hooks", "hooks", "system2-shell-guard.js"),
    os.path.join("user-hooks", "hooks", "system2-edit-guard.js"),
    os.path.join("user-hooks", "hooks", "system2-budget.js"),
    _CODEX_LOCK,
)


def _overlay_name_of(source_path: str) -> str:
    """Derive an overlay name from its source directory basename (boundary-safe)."""
    return os.path.basename(os.path.normpath(source_path))


# User-scope enforcement install
# Hooks need absolute paths because Codex invokes them from each project directory.

_HOOKS_PLACEHOLDER = "{{SYSTEM2_HOOKS_DIR}}"
_USER_HOOKS_TMPL = "hooks.json.tmpl"
_USER_HOOKS_JSON = "hooks.json"
_MATERIALIZED_HOOKS_SUBDIR = os.path.join("system2", "hooks")
_INSTALL_STATE_REL = os.path.join("system2", "system2-install.json")


def user_hooks_reference() -> str:
    """The packaged user-hooks reference dir."""
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(package_root, "_packaged_data", "codex_user_hooks")


def resolve_codex_home(explicit: Optional[str] = None) -> str:
    """Resolve the Codex home: explicit seam, then ``$CODEX_HOME``, then ``~/.codex``."""
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    env = os.environ.get("CODEX_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".codex")


def _resolve_hook_command(command: str, hooks_dir_abs: str) -> str:
    """Resolve one ``command`` string's ``{{SYSTEM2_HOOKS_DIR}}`` to *hooks_dir_abs*."""
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
    """Render ``hooks.json.tmpl`` with an absolute *hooks_dir_abs*."""
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
    """True iff an existing ``hooks.json`` carries a System2 CONTENT signature."""
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
    """True iff ``hooks.json`` is still exactly what this System2 install wrote."""
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
    """Materialize the single global user-scope Codex enforcement install."""
    home = resolve_codex_home(codex_home)
    auto_discovered = reference_dir is None
    ref = reference_dir or user_hooks_reference()

    # A missing or invalid reference directory becomes a clean CLI error, never a
    # raw FileNotFoundError traceback from os.listdir/open.
    if not os.path.isdir(ref) or not os.path.isdir(os.path.join(ref, "hooks")):
        if auto_discovered:
            # Auto-discovered package data should exist in every valid installation.
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

    # Only overwrite hooks.json silently when it still matches the recorded digest.
    # Legacy state falls back to the content signature.
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

    # Record state first so interrupted installs remain recognizable.
    # The digest prevents uninstall from deleting later user edits.
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

    # never write THROUGH a symlink — unlink an existing symlinked hooks.json first.
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
    """Remove exactly the System2-written guard JS + the ``hooks.json`` it owns."""
    home = resolve_codex_home(codex_home)
    state_path = os.path.join(home, _INSTALL_STATE_REL)
    hooks_dir = os.path.join(home, _MATERIALIZED_HOOKS_SUBDIR)
    hooks_json = os.path.join(home, _USER_HOOKS_JSON)

    state = _read_install_state(state_path)
    if state is None:
        return {"status": "nothing", "removed": [], "restored_backup": None,
                "hooks_json_removed": False, "codex_home": home}

    # Ignore state entries that escape codex_home.
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

    # Never remove or replace hooks.json after the user has modified it.
    current_is_system2 = _hooks_json_is_unmodified(
        hooks_json, state.get("hooks_json_sha256"), guard_names
    )
    if os.path.lexists(hooks_json) and not current_is_system2:
        return {
            "status": "refused",
            "removed": [],
            "restored_backup": None,
            "hooks_json_removed": False,
            "codex_home": home,
            "message": (
                "hooks.json changed since System2 installed it; refusing to remove "
                "the referenced hook scripts or install state"
            ),
        }

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
    """Project a ``System2Graph`` onto a Codex plugin (manifest + skills + Node hooks + lock)."""

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
            ir,
            project_path,
            self._resolve_overlay_sources(ir),
            recompose=os.path.lexists(self.lock_path(project_path)),
        )

    def _emit_with_sources(
        self,
        ir: System2Graph,
        project_path: str,
        overlay_sources: List[str],
        *,
        dry_run: bool = False,
        recompose: bool = False,
    ) -> List[str]:
        planned = _planned_files(ir, overlay_sources)
        planned_paths = preflight_artifact_write(
            project_path, planned, _CODEX_LOCK, recompose=recompose
        )
        if dry_run or bool(getattr(ir, "dry_run", False)):
            return planned_paths
        return _write_outputs(project_path, planned)

    # Lifecycle: lock helpers

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

    # Lifecycle: recompose from lock

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        return self._emit_with_sources(
            ir,
            project_path,
            self._resolve_overlay_sources(ir),
            dry_run=dry_run,
            recompose=True,
        )

    # Lifecycle: uninstall

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

        try:
            owned_artifacts = verify_owned_artifacts(
                project_path, lock_data, _CODEX_LOCK, require_all=False
            )
        except ValueError as exc:
            return _err([str(exc)])

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
            return self._uninstall_last_overlay(
                project_path, overlay_name, dry_run, owned_artifacts
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
        try:
            files_written = self._emit_with_sources(
                result.graph,
                project_path,
                remaining_sources,
                dry_run=dry_run,
                recompose=True,
            )
        except (FileExistsError, OSError, ValueError, json.JSONDecodeError) as exc:
            return _err([f"Cannot safely recompose owned artifacts: {exc}"])

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
        self,
        project_path: str,
        overlay_name: str,
        dry_run: bool,
        owned_artifacts: List[str],
    ) -> UninstallResult:
        """Remove the validated Codex artifacts when zero overlays remain."""
        artifacts = list(owned_artifacts) + [self.lock_path(project_path)]

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

    # Lifecycle: doctor (documented honest subset)

    def doctor(self, project_path: str) -> DoctorReport:
        """Return a read-only drift report without claiming unobservable hook state."""
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

        # Hook trust and approval are not observable by the compiler. Always surface
        # that limitation loudly and delegate the liveness check to the canary skill.
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
