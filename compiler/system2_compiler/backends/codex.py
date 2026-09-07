"""Codex backend with advisory, unverified candidate hooks."""

import hashlib
import json
import os
import re
import shlex
import shutil
import tempfile
from typing import Callable, List, Optional, Tuple

from system2_compiler.channel_version import CHANNEL_VERSION
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
    render_workflow_contract,
    validate_project_target,
    verify_owned_artifacts,
)

__all__ = ["CodexBackend"]

# Overlay-name validation (kebab-case), shared with the other backends' contract.
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "capabilities", "codex.json"
)

_CODEX_PLUGIN_VERSION = CHANNEL_VERSION

# Both enforcement guards are PreToolUse; the modern block schema carries this as
# ``hookSpecificOutput.hookEventName``.
_HOOK_EVENT_NAME = "PreToolUse"

# Candidate guards cannot observe same-session prompt/skill role adoption. They use
# this fixed fallback; role-aware hook authorization is unsupported without a native seam.
_DEFAULT_ACTIVE_ROLE = "executor"

_ADVISORY_LABEL = "ADVISORY — NOT ENFORCED ON CODEX (instruction only)"

# Reuse this trust statement across every user-visible enforcement surface.
_TRUST_ONELINER = (
    "System2 workflows for Codex. NOTE: bundled hooks are unverified candidate "
    "artifacts, not a release enforcement guarantee; Codex safety capabilities "
    "remain advisory-only pending native acceptance."
)

# This coverage statement appears verbatim in the orchestrator preamble and lock
# banner so neither surface can overstate hook coverage.
_COVERAGE_GAP = (
    "Candidate guards inspect command strings from recognized command keys, shell "
    "redirection and limited tee targets, and explicit edit paths or apply-patch "
    "headers. Other shell writes are not inspected. Synthetic corpus tests are not "
    "native Codex acceptance."
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
        "ADVISORY on Codex: unverified candidate edit/shell guards project-normalize "
        "explicit edit paths, apply-patch headers, redirection targets, and limited "
        "tee targets. Other shell writes are not inspected. Same-session role-aware "
        "hook authorization is unsupported. This is not a release guarantee."
    ),
    "block-dangerous": (
        "ADVISORY on Codex: an unverified candidate shell guard corpus-tests regex "
        "matching over recognized command strings. Native routing, trust, and deny "
        "semantics are unaccepted; this is not a release guarantee."
    ),
    "protect-sensitive": (
        "ADVISORY on Codex: unverified candidate guards corpus-test explicit edit "
        "paths, patch headers, and recognized shell command text. They do not parse "
        "all shell paths or writes. This is not a release guarantee."
    ),
    "budget": (
        "ADVISORY on Codex: the candidate turn-end hook emits an instruction to "
        "report budget data; it does not calculate a budget."
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
        "| Hooks not reviewed / untrusted | ADVISORY ONLY — candidate hooks do not run. |",
        "| Hooks materialized and reviewed via `/hooks` | UNVERIFIED CANDIDATE "
        "BEHAVIOR — native routing, trust, and deny semantics have not been accepted; "
        "this is not a release guarantee. |",
        "| Admin-disabled (`requirements.toml`) | ADVISORY ONLY — candidate hooks cannot run. |",
        "",
        "To inspect candidate behavior: run `system2 codex init` to materialize the "
        "guards into `~/.codex/hooks.json`, then review them via `/hooks`. Do not treat "
        "installation or trust as proof of enforcement. Nothing here auto-enables hooks "
        "or instructs blanket approval.",
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
    lines.append("## Trust state (READ THIS FIRST — PENDING NATIVE ACCEPTANCE)")
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
    if ir.gate_graph.approval_rule:
        lines.append(f"- Approval rule: {ir.gate_graph.approval_rule}")
    lines.append("")

    lines.append("## Delegation (same-session prompt/skill adoption)")
    lines.append(
        "No Codex subagent component exists. Adopt the target role's skill in the "
        "same session; this is prompt behavior, not an isolated sub-agent. A child-shell "
        "export cannot update later hook processes, so role-aware hook authorization is "
        "unsupported pending a native state seam. Do not claim role-specific lease "
        "enforcement."
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

    lines.extend(render_workflow_contract(ir))
    lines.append("")
    lines.append(
        "See `system2.codex.lock.json` for the per-capability fidelity report and the "
        "FIDELITY banner. The `system2-doctor` skill is a candidate diagnostic only; "
        "the compiler cannot validate native Codex routing, trust, or deny behavior."
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def _build_doctor_skill() -> str:
    """Build the ``system2-doctor`` hook-liveness canary skill."""
    marker = ".system2/canary-<nonce>"
    canary_cmd = f"mkdir -p .system2 && touch {marker} # {_CANARY_SENTINEL}"
    block_payload = f"{_CANARY_BASE_REASON}:<nonce>"

    lines: List[str] = _skill_frontmatter(
        "system2-doctor",
        "Run an unverified System2 Codex candidate-hook canary (not native acceptance).",
    )
    lines.append("# System2 doctor (Codex hook-liveness canary)")
    lines.append("")
    lines.append(
        "UNVERIFIED CANDIDATE ONLY: native Codex event routing, trust, and deny "
        "semantics have not been accepted. This marker protocol can distinguish local "
        "candidate observations from narration, but it is not release evidence or a "
        "release enforcement guarantee."
    )
    lines.append("")
    lines.append("## What the candidate canary observes (and what it does NOT prove)")
    lines.append("")
    lines.append(
        "- A candidate-positive result observes only the expected shell-guard output "
        "and marker behavior at that moment. It does NOT prove native liveness or "
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
        f"2. RUN THE CANARY COMMAND (it carries the `{_CANARY_SENTINEL}` sentinel; "
        "candidate logic is expected to emit a deny, but native behavior is unverified):"
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
        "echoed back) was observed | CANDIDATE-POSITIVE, NATIVE STATUS UNVALIDATED | "
        "Report only the observation; restate the native-evidence limits above. |"
    )
    lines.append(
        "| Marker ABSENT WITHOUT that nonce-bearing payload | UNVERIFIED — treated as "
        "advisory, never healthy | Do not claim healthy; the block was not observably "
        "attributable to the hook. |"
    )
    lines.append("")
    lines.append(
        "FAIL-CLOSED principle: the ABSENCE of a block is never healthy, and an "
        "UNOBSERVABLE block is never healthy either. A marker-absent result paired "
        f"with the concrete `{block_payload}` payload (your fresh nonce echoed) is "
        "only candidate-positive; it is not native acceptance."
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
        f"System2 {role.name} role (Codex, advisory).",
    )
    lines.append(f"# System2 role: {role.name} (Codex)")
    lines.append("")
    lines.append(
        f"You are the System2 {role.name} agent. Adopt this role's prompt and skill "
        f"in the same session. Role-aware hook authorization is unsupported pending a "
        f"native state seam; honor the write scope as an advisory instruction."
    )
    lines.append("")
    if role.gate_role:
        lines.append(f"- Gate role: {role.gate_role}")
    scope = (role.write_scope or "").strip()
    if scope:
        lines.append(
            f"- Write scope (ADVISORY — role-aware hook authorization is unsupported): "
            f"`{scope}`"
        )
    else:
        lines.append(
            "- Write scope: none (read-only role, advisory). Produce review output, "
            "not file edits; Codex has no supported role-aware hook state seam."
        )
    if role.model_hint:
        lines.append(f"- Model hint: {role.model_hint} (recorded; Codex model is session-level)")
    else:
        lines.append("- Model: session default model (no hint; not silently assumed)")
    lines.append("")
    lines.append("## Canonical role contract")
    lines.append("")
    lines.extend(role.contract_text.splitlines())
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
    lines.append("  if (!event || typeof event !== \"object\" || Array.isArray(event)) return null;")
    lines.append("  const input = (event.tool_input || event.toolInput || event.input || event.arguments) || {};")
    lines.append("  if (!input || typeof input !== \"object\" || Array.isArray(input)) return null;")
    lines.append("  let cmd = input.command;")
    lines.append("  if (cmd === undefined) cmd = event.command;")
    lines.append('  if (Array.isArray(cmd) && cmd.every((x) => typeof x === "string")) cmd = cmd.join(" ");')
    lines.append('  return (typeof cmd === "string" && cmd.length > 0) ? cmd : null;')
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
    lines.append("  while ((m = re.exec(command)) !== null) {")
    lines.append("    if (guard >= 256) return { targets, overflow: true };")
    lines.append("    guard++;")
    lines.append("    if (mask[m.index]) continue;")
    lines.append("    let t = m[1];")
    lines.append('    if ((t.startsWith(\'"\') && t.endsWith(\'"\')) || (t.startsWith("\'") && t.endsWith("\'"))) t = t.slice(1, -1);')
    lines.append('    if (t.length > 0 && t !== "/dev/null") targets.push(t);')
    lines.append("  }")
    lines.append("  return { targets, overflow: false };")
    lines.append("}")
    lines.append("")
    decide = [
        "function decide(event, raw) {",
        "  const command = commandOf(event);",
        '  if (command === null) return "block-dangerous: uninspectable routed shell event (candidate fail closed)";',
        '  if (command.length > MAX_MATCH_LEN) return "block-dangerous: shell command exceeds safe match length (fail closed)";',
        "  const dr = dangerousReason(command);",
        "  if (dr) return dr;",
        "  const sh = sensitiveHit(command);",
        '  if (sh) return "protect-sensitive: " + sh;',
        "  const extracted = shellWriteTargets(command);",
        '  if (extracted.overflow) return "enforce-lease: shell target extraction limit exceeded (candidate fail closed)";',
        "  const targets = extracted.targets;",
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
    lines.append("  if (!event || typeof event !== \"object\" || Array.isArray(event)) return null;")
    lines.append("  const input = (event.tool_input || event.toolInput || event.input || event.arguments) || {};")
    lines.append("  if (!input || typeof input !== \"object\" || Array.isArray(input)) return null;")
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
    lines.append("    while ((m = re.exec(patch)) !== null) {")
    lines.append("      if (guard >= 512) return { paths: out, overflow: true };")
    lines.append("      guard++;")
    lines.append("      const p = m[1].trim();")
    lines.append('      if (p && p !== "/dev/null") out.push(p);')
    lines.append("    }")
    lines.append("  }")
    lines.append("  if (out.length === 0) return null;")
    lines.append("  return { paths: out, overflow: false };")
    lines.append("}")
    lines.append("")
    decide = [
        "function decide(event, raw) {",
        "  const extracted = pathsOf(event);",
        '  if (extracted === null) return "enforce-lease: uninspectable routed edit event (candidate fail closed)";',
        '  if (extracted.overflow) return "enforce-lease: patch path extraction limit exceeded (candidate fail closed)";',
        "  const paths = extracted.paths;",
        "  // This candidate hook carries its own canary sentinel (defense-in-depth).",
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
    """Stop/SubagentStop advisory instruction (does not calculate a budget)."""
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
    lines.append('      systemMessage: "System2 budget (advisory instruction only): report files touched and lines added/removed in your completion summary.",')
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
    return (
        _TRUST_ONELINER
        + " PENDING NATIVE ACCEPTANCE: enforce-lease, block-dangerous, "
        "protect-sensitive, and budget are ADVISORY with enforced:false and "
        "gated:false. Candidate guards emit hookSpecificOutput."
        "permissionDecision=deny for corpus-tested inputs, but native event routing, "
        "trust, and deny semantics are unverified and not a release guarantee. "
        "Same-session role adoption does not provide role-aware hook authorization. "
        + _COVERAGE_GAP
    )


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
        "enforcement": "unverified-candidate-hooks",
        "subagent_isolation": "adapted",
        "FIDELITY": _fidelity_banner(ir),
        "capabilities": capabilities,
    }


_CODEX_LOCK = "system2.codex.lock.json"


def _build_lock(ir: System2Graph, ownership: dict) -> dict:
    lock = _build_degradation_report(ir)
    lock["ownership"] = ownership
    lock["overlay_sources"] = list(ir.overlay_sources)
    return lock


# Planned emission + write posture (atomic write + backup/restore)

def _planned_files(ir: System2Graph) -> List[Tuple[str, str]]:
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
            json.dumps(_build_lock(ir, ownership), indent=2) + "\n",
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


def _write_outputs(
    project_path: str,
    planned: List[Tuple[str, str]],
    stale_paths: Optional[List[str]] = None,
) -> List[str]:
    """Write planned files and transactionally remove validated stale files."""
    project_root = os.path.abspath(project_path)

    def relative(path: str) -> str:
        return os.path.relpath(path, project_root).replace(os.sep, "/")

    stale_rels = [relative(path) for path in stale_paths or []]
    for rel in stale_rels:
        validate_project_target(project_path, rel)
    for rel, _content in planned:
        validate_project_target(project_path, rel.replace(os.sep, "/"))

    backups: List[Tuple[str, str]] = []
    newly_created: List[str] = []
    dirs_created: List[str] = []
    written: List[str] = []
    try:
        for rel in stale_rels:
            path = validate_project_target(project_path, rel)
            if not os.path.isfile(path):
                raise ValueError(f"owned artifact is no longer a regular file: {rel}")
            dir_name = os.path.dirname(path)
            fd, bak = tempfile.mkstemp(
                prefix=f".{os.path.basename(path)}.", suffix=".bak", dir=dir_name
            )
            os.close(fd)
            try:
                path = validate_project_target(project_path, rel)
                validate_project_target(project_path, relative(bak))
                shutil.copy2(path, bak)
            except Exception:
                os.unlink(bak)
                raise
            backups.append((path, bak))
            path = validate_project_target(project_path, rel)
            os.unlink(path)
        for rel, content in planned:
            canonical_rel = rel.replace(os.sep, "/")
            dst = validate_project_target(project_path, canonical_rel)
            dir_name = os.path.dirname(dst)
            _makedirs_tracked(dir_name, dirs_created)
            dst = validate_project_target(project_path, canonical_rel)
            if os.path.lexists(dst):
                if not os.path.isfile(dst):
                    raise ValueError(
                        f"owned artifact is no longer a regular file: {canonical_rel}"
                    )
                fd, bak = tempfile.mkstemp(
                    prefix=f".{os.path.basename(dst)}.", suffix=".bak", dir=dir_name
                )
                os.close(fd)
                try:
                    dst = validate_project_target(project_path, canonical_rel)
                    validate_project_target(project_path, relative(bak))
                    shutil.copy2(dst, bak)
                except Exception:
                    os.unlink(bak)
                    raise
                backups.append((dst, bak))
            dst = validate_project_target(project_path, canonical_rel)
            fd, tmp = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(content)
                dst = validate_project_target(project_path, canonical_rel)
                os.chmod(tmp, _default_file_mode(dst))
                dst = validate_project_target(project_path, canonical_rel)
                os.replace(tmp, dst)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            if dst not in [orig for orig, _ in backups]:
                newly_created.append(dst)
            written.append(dst)
    except Exception:
        for orig, bak in reversed(backups):
            try:
                if os.path.exists(bak):
                    validate_project_target(project_path, relative(orig))
                    validate_project_target(project_path, relative(bak))
                    shutil.copy2(bak, orig)
                    os.unlink(bak)
            except Exception:
                pass
        for created in newly_created:
            try:
                if os.path.exists(created):
                    validate_project_target(project_path, relative(created))
                    os.unlink(created)
            except Exception:
                pass
        for d in dirs_created:
            try:
                validate_project_target(project_path, relative(d))
                os.rmdir(d)
            except Exception:
                pass
        raise
    for _orig, bak in backups:
        try:
            if os.path.exists(bak):
                validate_project_target(project_path, relative(bak))
                os.unlink(bak)
        except Exception:
            pass
    return written


# Lifecycle helpers

def _validated_lock_overlay_sources(lock_data: object) -> List[str]:
    """Return validated Codex overlay source paths from a decoded lock."""
    if not isinstance(lock_data, dict):
        raise ValueError("Lock file is malformed: expected a JSON object")
    sources = lock_data.get("overlay_sources")
    if not isinstance(sources, list):
        raise ValueError("Lock file is malformed: 'overlay_sources' is not a list")
    if any(not isinstance(source, str) or not source for source in sources):
        raise ValueError(
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths"
        )
    return list(sources)


def _overlay_name_of(source_path: str) -> str:
    """Derive an overlay name from its source directory basename (boundary-safe)."""
    return os.path.basename(os.path.normpath(source_path))


# User-scope candidate-hook install
# Commands need absolute paths because invocation may occur from project directories.

_HOOKS_PLACEHOLDER = "{{SYSTEM2_HOOKS_DIR}}"
_USER_HOOKS_TMPL = "hooks.json.tmpl"
_USER_HOOKS_JSON = "hooks.json"
_MATERIALIZED_HOOKS_SUBDIR = os.path.join("system2", "hooks")
_INSTALL_STATE_REL = os.path.join("system2", "system2-install.json")
_USER_GUARD_NAMES = (
    "system2-budget.js", "system2-edit-guard.js", "system2-shell-guard.js",
)


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


_INSTALL_STATE_SCHEMA_VERSION = 1
_INSTALL_STATE_FIELDS = {
    "schema_version", "owned", "hooks_json", "hooks_json_sha256",
    "hooks_dir", "hook_files", "restore_backup", "restore_backup_sha256",
}
_HOOK_STATE_FIELDS = {
    "name", "path", "sha256", "restore_backup", "restore_backup_sha256",
}


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _file_matches(path: str, expected: bytes) -> bool:
    actual = _read_bytes(path)
    return actual is not None and actual == expected


def _is_safe_basename(name: object) -> bool:
    """True iff *name* is a bare filename (no separators or traversal)."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and name == os.path.basename(name)
        and "/" not in name
        and "\\" not in name
        and ".." not in name
    )


def _resolves_inside(path: str, base_dir: str) -> bool:
    """True iff *path* resolves to *base_dir* or a descendant of it."""
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(path)
    return real_path == real_base or real_path.startswith(real_base + os.sep)


def _managed_tree_is_safe(home: str) -> bool:
    """Reject managed-directory symlinks before any state read or write."""
    for path in (os.path.join(home, "system2"),
                 os.path.join(home, _MATERIALIZED_HOOKS_SUBDIR)):
        if os.path.islink(path):
            return False
        if os.path.lexists(path) and not os.path.isdir(path):
            return False
    return True


def _validate_backup_fields(
    backup: object, digest: object, home: str, label: str, target: str,
) -> Optional[str]:
    if backup is None and digest is None:
        return None
    if not isinstance(backup, str) or not backup or not _valid_sha256(digest):
        return f"{label} backup path/digest pair is malformed"
    if not os.path.isabs(backup) or not _resolves_inside(backup, home):
        return f"{label} backup path escapes the Codex home"
    prefix = re.escape(target + ".system2-original")
    if re.fullmatch(prefix + r"(?:\.\d+)?\.bak", backup) is None:
        return f"{label} backup path is not an exact System2 restore path"
    return None


def _read_install_state(
    state_path: str, home: str, expected_guard_names: Optional[List[str]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """Read and fully validate untrusted install state."""
    if not os.path.lexists(state_path):
        return None, None
    if os.path.islink(state_path) or not os.path.isfile(state_path):
        return None, "install state is not a regular file"
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"install state is unreadable: {exc}"
    if not isinstance(state, dict):
        return None, "install state is malformed: expected a JSON object"
    if set(state) != _INSTALL_STATE_FIELDS:
        return None, "install state schema fields are missing or unexpected"
    if state.get("schema_version") != _INSTALL_STATE_SCHEMA_VERSION:
        return None, "install state schema version is unsupported"
    if state.get("owned") is not True:
        return None, "install state ownership marker is invalid"

    hooks_json = os.path.join(home, _USER_HOOKS_JSON)
    hooks_dir = os.path.join(home, _MATERIALIZED_HOOKS_SUBDIR)
    if state.get("hooks_json") != hooks_json or state.get("hooks_dir") != hooks_dir:
        return None, "install state managed paths do not match this Codex home"
    if not _valid_sha256(state.get("hooks_json_sha256")):
        return None, "install state hooks.json digest is malformed"
    error = _validate_backup_fields(
        state.get("restore_backup"), state.get("restore_backup_sha256"),
        home, "hooks.json", hooks_json,
    )
    if error:
        return None, error

    entries = state.get("hook_files")
    if not isinstance(entries, list) or not entries:
        return None, "install state hook_files must be a non-empty list"
    names = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _HOOK_STATE_FIELDS:
            return None, "install state hook file entry is malformed"
        name = entry.get("name")
        if not _is_safe_basename(name) or not name.endswith(".js") or name in names:
            return None, "install state hook file name is invalid or duplicated"
        names.append(name)
        if entry.get("path") != os.path.join(hooks_dir, name):
            return None, "install state hook file path is invalid"
        if not _valid_sha256(entry.get("sha256")):
            return None, "install state hook file digest is malformed"
        error = _validate_backup_fields(
            entry.get("restore_backup"), entry.get("restore_backup_sha256"),
            home, f"hook {name}", os.path.join(hooks_dir, name),
        )
        if error:
            return None, error
    if expected_guard_names is not None and names != expected_guard_names:
        return None, "install state hook inventory does not match the current reference"
    return state, None


def _next_backup_path(path: str, purpose: str) -> str:
    """Return a deterministic collision-safe backup path (also usable by dry-run)."""
    base = f"{path}.system2-{purpose}.bak"
    candidate = base
    index = 1
    while os.path.lexists(candidate):
        candidate = f"{path}.system2-{purpose}.{index}.bak"
        index += 1
    return candidate


def _snapshot_path(path: str) -> tuple:
    if os.path.islink(path):
        return ("link", os.readlink(path), os.lstat(path).st_mode & 0o7777)
    if not os.path.lexists(path):
        return ("absent",)
    if not os.path.isfile(path):
        raise OSError(f"transaction target is not a regular file: {path}")
    with open(path, "rb") as fh:
        return ("file", fh.read(), os.stat(path).st_mode & 0o7777)


def _stage_bytes(path: str, content: bytes, mode: int) -> str:
    fd, staged = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                                  dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(staged, mode)
        return staged
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        if os.path.exists(staged):
            os.unlink(staged)
        raise


def _atomic_replace(src: str, dst: str) -> None:
    """Injection seam for commit-boundary rollback tests."""
    os.replace(src, dst)


def _restore_snapshot(path: str, snapshot: tuple) -> None:
    if os.path.lexists(path):
        if os.path.isdir(path) and not os.path.islink(path):
            raise OSError(f"cannot roll back over directory: {path}")
        os.unlink(path)
    if snapshot[0] == "absent":
        return
    if snapshot[0] == "link":
        os.symlink(snapshot[1], path)
        return
    staged = _stage_bytes(path, snapshot[1], snapshot[2])
    os.replace(staged, path)


def _apply_file_transaction(
    changes: List[Tuple[str, Optional[bytes], int]], dirs_created: List[str],
) -> None:
    """Atomically commit ordered file replacements/removals and roll back exactly."""
    snapshots = {path: _snapshot_path(path) for path, _content, _mode in changes}
    staged = {}
    tombstones: List[str] = []
    try:
        for path, content, mode in changes:
            if content is not None:
                staged[path] = _stage_bytes(path, content, mode)
        for path, content, _mode in changes:
            if content is None:
                fd, tomb = tempfile.mkstemp(
                    prefix=f".{os.path.basename(path)}.", suffix=".remove",
                    dir=os.path.dirname(path),
                )
                os.close(fd)
                os.unlink(tomb)
                tombstones.append(tomb)
                _atomic_replace(path, tomb)
            else:
                _atomic_replace(staged[path], path)
                staged.pop(path, None)
        for tomb in tombstones:
            if os.path.lexists(tomb):
                os.unlink(tomb)
    except Exception as commit_error:
        rollback_errors = []
        for path, _content, _mode in reversed(changes):
            try:
                _restore_snapshot(path, snapshots[path])
            except Exception as exc:
                rollback_errors.append(f"{path}: {exc}")
        for temp_path in list(staged.values()) + tombstones:
            try:
                if os.path.lexists(temp_path):
                    os.unlink(temp_path)
            except OSError as exc:
                rollback_errors.append(f"{temp_path}: {exc}")
        for directory in dirs_created:
            try:
                os.rmdir(directory)
            except OSError:
                pass
        if rollback_errors:
            raise RuntimeError(
                "Codex global lifecycle rollback failed: " + "; ".join(rollback_errors)
            ) from commit_error
        raise


def _mode_for_reference(path: str) -> int:
    return os.stat(path).st_mode & 0o7777


def _state_script_entries(state: Optional[dict]) -> dict:
    if state is None:
        return {}
    return {entry["name"]: entry for entry in state["hook_files"]}


def _trust_instruction(hooks_json: str) -> str:
    return (
        f"System2 Codex candidate hooks installed to {hooks_json}. Review each via "
        "/hooks. Native event, trust, and deny behavior is unverified; installation "
        "is not a release enforcement guarantee, so capabilities remain advisory."
    )


def codex_init(
    codex_home: Optional[str] = None,
    reference_dir: Optional[str] = None,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Transactionally materialize the global candidate hook installation."""
    home = resolve_codex_home(codex_home)
    auto_discovered = reference_dir is None
    ref = reference_dir or user_hooks_reference()

    if not os.path.isdir(ref) or not os.path.isdir(os.path.join(ref, "hooks")):
        if auto_discovered:
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
    if tuple(guard_names) != _USER_GUARD_NAMES:
        raise ValueError(
            f"reference hook inventory must be exactly {_USER_GUARD_NAMES!r}; "
            f"got {tuple(guard_names)!r}"
        )
    rendered_bytes = render_user_hooks_config(ref, hooks_dir).encode("utf-8")
    guard_bytes = {
        name: _read_bytes(os.path.join(ref, "hooks", name)) for name in guard_names
    }
    if not guard_names or any(content is None for content in guard_bytes.values()):
        raise FileNotFoundError(f"reference hook inventory is incomplete: {ref}")

    hook_paths = {name: os.path.join(hooks_dir, name) for name in guard_names}
    warnings: List[str] = []
    if not _managed_tree_is_safe(home):
        warnings.append(
            "System2 managed directories are symlinked or not directories; refusing "
            "to read state or mutate paths outside the Codex home."
        )
        return {
            "status": "refused", "codex_home": home, "hooks_dir": hooks_dir,
            "hooks_json": hooks_json, "hook_files": list(hook_paths.values()),
            "backup_path": None, "preexisting_foreign": True,
            "warnings": warnings, "message": warnings[0],
        }

    prior_state, state_error = _read_install_state(
        state_path, home, expected_guard_names=guard_names,
    )
    if state_error:
        warnings.append(
            f"Existing System2 install state is invalid ({state_error}); ownership "
            "will be accepted only if the complete live config/script bundle exactly "
            "matches the current rendered bytes."
        )

    if prior_state is not None:
        restore_pairs = []
        if prior_state["restore_backup"] is not None:
            restore_pairs.append((prior_state["restore_backup"],
                                  prior_state["restore_backup_sha256"]))
        restore_pairs.extend(
            (entry["restore_backup"], entry["restore_backup_sha256"])
            for entry in prior_state["hook_files"]
            if entry["restore_backup"] is not None
        )
        damaged_restore = [
            path for path, digest in restore_pairs
            if _read_bytes(path) is None
            or _sha256_bytes(_read_bytes(path) or b"") != digest
        ]
        if damaged_restore:
            message = (
                "Original foreign restore point is missing or modified; refusing "
                "reinstall so it is not silently replaced: " + ", ".join(damaged_restore)
            )
            warnings.append(message)
            return {
                "status": "refused", "codex_home": home, "hooks_dir": hooks_dir,
                "hooks_json": hooks_json, "hook_files": list(hook_paths.values()),
                "backup_path": None, "preexisting_foreign": True,
                "warnings": warnings, "message": message,
            }

    prior_scripts = _state_script_entries(prior_state)
    if prior_state is not None:
        config_owned = (
            (_read_bytes(hooks_json) is not None)
            and _sha256_bytes(_read_bytes(hooks_json) or b"")
            == prior_state["hooks_json_sha256"]
        )
        scripts_owned = all(
            _read_bytes(hook_paths[name]) is not None
            and _sha256_bytes(_read_bytes(hook_paths[name]) or b"")
            == prior_scripts[name]["sha256"]
            for name in guard_names
        )
        system2_owned = config_owned and scripts_owned
    else:
        config_owned = _file_matches(hooks_json, rendered_bytes)
        scripts_owned = all(
            _file_matches(hook_paths[name], guard_bytes[name] or b"")
            for name in guard_names
        )
        system2_owned = config_owned and scripts_owned

    managed_paths = [hooks_json] + list(hook_paths.values())
    has_managed_artifacts = any(os.path.lexists(path) for path in managed_paths)
    preexisting_foreign = has_managed_artifacts and not system2_owned
    invalid_targets = [
        path for path in managed_paths
        if os.path.lexists(path) and not os.path.isfile(path) and not os.path.islink(path)
    ]
    if invalid_targets:
        preexisting_foreign = True
        warnings.append(
            "Managed target is not a regular file; refusing even with --force: "
            + ", ".join(invalid_targets)
        )
        force = False

    if preexisting_foreign and not force:
        warnings.append(
            "The live hooks config/scripts are not exactly owned by a valid versioned "
            "state or the current rendered bundle. Substring signatures are not "
            "ownership. Refusing to overwrite; use --force only after reviewing the "
            "reported global collisions."
        )
        return {
            "status": "refused", "codex_home": home, "hooks_dir": hooks_dir,
            "hooks_json": hooks_json, "hook_files": list(hook_paths.values()),
            "backup_path": None, "preexisting_foreign": True,
            "warnings": warnings, "message": warnings[-1],
        }

    restore_config = prior_state.get("restore_backup") if prior_state else None
    restore_config_digest = (
        prior_state.get("restore_backup_sha256") if prior_state else None
    )
    restore_scripts = {
        name: (
            prior_scripts[name].get("restore_backup"),
            prior_scripts[name].get("restore_backup_sha256"),
        ) if name in prior_scripts else (None, None)
        for name in guard_names
    }

    backup_changes: List[Tuple[str, Optional[bytes], int]] = []
    created_config_backup: Optional[str] = None
    if preexisting_foreign:
        for name, path in [("hooks.json", hooks_json)] + list(hook_paths.items()):
            content = _read_bytes(path)
            if content is None:
                continue
            if prior_state is None:
                purpose = "original"
            else:
                expected = (
                    prior_state["hooks_json_sha256"] if name == "hooks.json"
                    else prior_scripts[name]["sha256"]
                )
                if _sha256_bytes(content) == expected:
                    continue
                purpose = "safety"
            backup_path = _next_backup_path(path, purpose)
            backup_changes.append((backup_path, content, os.stat(path).st_mode & 0o7777))
            if name == "hooks.json":
                created_config_backup = backup_path
                if prior_state is None:
                    restore_config = backup_path
                    restore_config_digest = _sha256_bytes(content)
            elif prior_state is None:
                restore_scripts[name] = (backup_path, _sha256_bytes(content))
        warnings.append(
            "Reviewed foreign/modified managed files will be copied to collision-safe "
            "backups before any candidate hook file is replaced."
        )

    hook_entries = []
    for name in guard_names:
        restore_backup, restore_digest = restore_scripts[name]
        hook_entries.append({
            "name": name,
            "path": hook_paths[name],
            "sha256": _sha256_bytes(guard_bytes[name] or b""),
            "restore_backup": restore_backup,
            "restore_backup_sha256": restore_digest,
        })
    state = {
        "schema_version": _INSTALL_STATE_SCHEMA_VERSION,
        "owned": True,
        "hooks_json": hooks_json,
        "hooks_json_sha256": _sha256_bytes(rendered_bytes),
        "hooks_dir": hooks_dir,
        "hook_files": hook_entries,
        "restore_backup": restore_config,
        "restore_backup_sha256": restore_config_digest,
    }
    state_bytes = (json.dumps(state, indent=2) + "\n").encode("utf-8")

    if dry_run:
        return {
            "status": "dry_run", "codex_home": home, "hooks_dir": hooks_dir,
            "hooks_json": hooks_json, "hook_files": list(hook_paths.values()),
            "backup_path": created_config_backup,
            "preexisting_foreign": preexisting_foreign,
            "warnings": warnings, "message": _trust_instruction(hooks_json),
        }

    dirs_created: List[str] = []
    _makedirs_tracked(home, dirs_created)
    _makedirs_tracked(hooks_dir, dirs_created)
    changes = list(backup_changes)
    for name in guard_names:
        source_path = os.path.join(ref, "hooks", name)
        changes.append((hook_paths[name], guard_bytes[name], _mode_for_reference(source_path)))
    changes.append((hooks_json, rendered_bytes, _default_file_mode(hooks_json)))
    changes.append((state_path, state_bytes, 0o600))  # state commits last
    _apply_file_transaction(changes, dirs_created)

    return {
        "status": "installed", "codex_home": home, "hooks_dir": hooks_dir,
        "hooks_json": hooks_json, "hook_files": list(hook_paths.values()),
        "backup_path": created_config_backup,
        "preexisting_foreign": preexisting_foreign,
        "warnings": warnings, "message": _trust_instruction(hooks_json),
    }


def codex_uninstall(codex_home: Optional[str] = None, *, dry_run: bool = False) -> dict:
    """Transactionally restore global config before removing exact-owned scripts."""
    home = resolve_codex_home(codex_home)
    state_path = os.path.join(home, _INSTALL_STATE_REL)
    hooks_dir = os.path.join(home, _MATERIALIZED_HOOKS_SUBDIR)
    hooks_json = os.path.join(home, _USER_HOOKS_JSON)

    if not os.path.lexists(state_path):
        return {"status": "nothing", "removed": [], "restored_backup": None,
                "hooks_json_removed": False, "codex_home": home}
    if not _managed_tree_is_safe(home):
        message = "System2 managed directories are unsafe; refusing uninstall"
        return {"status": "refused", "removed": [], "restored_backup": None,
                "hooks_json_removed": False, "codex_home": home, "message": message}

    state, state_error = _read_install_state(
        state_path, home, expected_guard_names=list(_USER_GUARD_NAMES),
    )
    if state is None:
        message = f"Invalid System2 install state; refusing uninstall: {state_error}"
        return {"status": "refused", "removed": [], "restored_backup": None,
                "hooks_json_removed": False, "codex_home": home, "message": message}

    config_bytes = _read_bytes(hooks_json)
    if (
        config_bytes is None
        or _sha256_bytes(config_bytes) != state["hooks_json_sha256"]
    ):
        message = (
            "hooks.json is missing or changed since System2 installed it; refusing "
            "to remove scripts or install state"
        )
        return {"status": "refused", "removed": [], "restored_backup": None,
                "hooks_json_removed": False, "codex_home": home, "message": message}

    for entry in state["hook_files"]:
        content = _read_bytes(entry["path"])
        if content is None or _sha256_bytes(content) != entry["sha256"]:
            message = (
                f"Installed script is missing or modified; refusing uninstall: "
                f"{entry['path']}"
            )
            return {"status": "refused", "removed": [], "restored_backup": None,
                    "hooks_json_removed": False, "codex_home": home, "message": message}

    backup_pairs = []
    if state["restore_backup"] is not None:
        backup_pairs.append((state["restore_backup"],
                             state["restore_backup_sha256"], "hooks.json"))
    for entry in state["hook_files"]:
        if entry["restore_backup"] is not None:
            backup_pairs.append((entry["restore_backup"],
                                 entry["restore_backup_sha256"], entry["name"]))
    for backup_path, expected_digest, label in backup_pairs:
        content = _read_bytes(backup_path)
        if content is None or _sha256_bytes(content) != expected_digest:
            message = f"Original {label} restore backup is missing or modified; refusing uninstall"
            return {"status": "refused", "removed": [], "restored_backup": None,
                    "hooks_json_removed": False, "codex_home": home, "message": message}

    removed = [entry["path"] for entry in state["hook_files"]]
    restored = state["restore_backup"]
    removed_json = restored is None
    if dry_run:
        return {
            "status": "dry_run", "removed": removed,
            "restored_backup": restored, "hooks_json_removed": removed_json,
            "codex_home": home,
        }

    # Config restoration/removal is first, so script removals never expose dangling
    # System2 command references. The state file is removed last.
    changes: List[Tuple[str, Optional[bytes], int]] = []
    if restored is None:
        changes.append((hooks_json, None, 0))
    else:
        restore_bytes = _read_bytes(restored) or b""
        changes.append((hooks_json, restore_bytes, os.stat(restored).st_mode & 0o7777))
    for entry in state["hook_files"]:
        backup_path = entry["restore_backup"]
        if backup_path is None:
            changes.append((entry["path"], None, 0))
        else:
            restore_bytes = _read_bytes(backup_path) or b""
            changes.append((entry["path"], restore_bytes,
                            os.stat(backup_path).st_mode & 0o7777))
    changes.append((state_path, None, 0))  # state removal commits last
    _apply_file_transaction(changes, [])

    for backup_path, _digest, _label in backup_pairs:
        try:
            os.unlink(backup_path)
        except OSError:
            pass
    for directory in (hooks_dir, os.path.dirname(state_path)):
        try:
            os.rmdir(directory)
        except OSError:
            pass

    return {
        "status": "uninstalled", "removed": removed,
        "restored_backup": restored, "hooks_json_removed": removed_json,
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
        # Deprecated compatibility input: graph provenance is authoritative.
        self._overlay_sources = list(overlay_sources) if overlay_sources else None

    def _validate_legacy_overlay_sources(self, ir: System2Graph) -> None:
        if (
            self._overlay_sources is not None
            and tuple(self._overlay_sources) != ir.overlay_sources
        ):
            raise ValueError(
                "constructor overlay_sources do not match authoritative graph "
                "provenance"
            )

    def emit(self, ir: System2Graph, project_path: str) -> List[str]:
        return self._emit_graph(
            ir,
            project_path,
            recompose=os.path.lexists(self.lock_path(project_path)),
        )

    def plan(self, ir: System2Graph, project_path: str) -> List[str]:
        """Return the target-native write plan without mutating the project."""
        return self._emit_graph(
            ir,
            project_path,
            dry_run=True,
            recompose=os.path.lexists(self.lock_path(project_path)),
        )

    def _emit_graph(
        self,
        ir: System2Graph,
        project_path: str,
        *,
        dry_run: bool = False,
        recompose: bool = False,
    ) -> List[str]:
        self._validate_legacy_overlay_sources(ir)
        planned = _planned_files(ir)
        planned_paths, stale_paths = preflight_artifact_write(
            project_path, planned, _CODEX_LOCK, recompose=recompose
        )
        if dry_run or bool(getattr(ir, "dry_run", False)):
            return planned_paths + ["(remove) " + path for path in stale_paths]
        return _write_outputs(project_path, planned, stale_paths)

    # Lifecycle: lock helpers

    def lock_path(self, project_path: str) -> str:
        """The Codex target lock artifact: ``system2.codex.lock.json``."""
        return os.path.join(project_path, "system2.codex.lock.json")

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        lp = validate_project_target(project_path, _CODEX_LOCK)
        if not os.path.isfile(lp):
            raise FileNotFoundError(lp)
        lp = validate_project_target(project_path, _CODEX_LOCK)
        with open(lp, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
        return _validated_lock_overlay_sources(lock_data)

    # Lifecycle: recompose from lock

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        return self._emit_graph(
            ir,
            project_path,
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

        try:
            lp = validate_project_target(project_path, _CODEX_LOCK)
        except ValueError as exc:
            return _err([str(exc)])
        if not os.path.isfile(lp):
            return _err(["No lock file found; no overlays are composed"])
        try:
            lp = validate_project_target(project_path, _CODEX_LOCK)
            with open(lp, "r", encoding="utf-8") as fh:
                lock_data = json.load(fh)
        except json.JSONDecodeError:
            return _err(["Lock file is malformed (invalid JSON)"])
        except OSError as exc:
            return _err([f"Cannot read lock file: {exc}"])

        try:
            sources = _validated_lock_overlay_sources(lock_data)
        except ValueError as exc:
            return _err([str(exc)])

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
            files_written = self._emit_graph(
                result.graph,
                project_path,
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
        artifacts = list(owned_artifacts) + [
            validate_project_target(project_path, _CODEX_LOCK)
        ]
        artifact_rels = [
            os.path.relpath(path, os.path.abspath(project_path)).replace(os.sep, "/")
            for path in artifacts
        ]
        artifacts = [
            validate_project_target(project_path, rel) for rel in artifact_rels
        ]

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
            for rel in artifact_rels:
                path = validate_project_target(project_path, rel)
                if not os.path.isfile(path):
                    raise ValueError(
                        f"owned artifact is no longer a regular file: {rel}"
                    )
                dir_name = os.path.dirname(path)
                fd, bak = tempfile.mkstemp(
                    prefix=f".{os.path.basename(path)}.", suffix=".bak", dir=dir_name
                )
                os.close(fd)
                try:
                    path = validate_project_target(project_path, rel)
                    validate_project_target(
                        project_path,
                        os.path.relpath(
                            bak, os.path.abspath(project_path)
                        ).replace(os.sep, "/"),
                    )
                    shutil.copy2(path, bak)
                except Exception:
                    os.unlink(bak)
                    raise
                backups.append((path, bak))
            for rel in artifact_rels:
                path = validate_project_target(project_path, rel)
                os.unlink(path)
        except Exception:
            for orig, bak in reversed(backups):
                try:
                    if os.path.exists(bak):
                        validate_project_target(
                            project_path,
                            os.path.relpath(
                                orig, os.path.abspath(project_path)
                            ).replace(os.sep, "/"),
                        )
                        validate_project_target(
                            project_path,
                            os.path.relpath(
                                bak, os.path.abspath(project_path)
                            ).replace(os.sep, "/"),
                        )
                        shutil.copy2(bak, orig)
                        os.unlink(bak)
                except Exception:
                    pass
            raise

        for _orig, bak in backups:
            try:
                if os.path.exists(bak):
                    validate_project_target(
                        project_path,
                        os.path.relpath(
                            bak, os.path.abspath(project_path)
                        ).replace(os.sep, "/"),
                    )
                    os.unlink(bak)
            except Exception:
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

    # Lifecycle: doctor (documented honest subset)

    def doctor(self, project_path: str) -> DoctorReport:
        """Return a read-only drift report without claiming unobservable hook state."""
        details: List[dict] = []
        unchecked_lock = self.lock_path(project_path)
        if not os.path.lexists(unchecked_lock):
            details.append({
                "kind": "no_lock",
                "message": "No system2.codex.lock.json found; nothing is composed.",
            })
            return DoctorReport(
                status="no_lock", details=details,
                system2_version={"installed": "", "locked": ""},
                overlays=[], composed=False, exit_code=1,
                validator_available=False,
            )
        try:
            lp = validate_project_target(project_path, _CODEX_LOCK)
        except ValueError as exc:
            details.append({"kind": "broken", "message": f"Unsafe lock path: {exc}"})
            return DoctorReport(
                status="broken", details=details,
                system2_version={"installed": "", "locked": ""},
                overlays=[], composed=True, exit_code=1,
                validator_available=False,
            )
        if not os.path.isfile(lp):
            details.append({"kind": "broken", "message": "Lock path is not a regular file."})
            return DoctorReport(
                status="broken", details=details,
                system2_version={"installed": "", "locked": ""},
                overlays=[], composed=True, exit_code=1,
                validator_available=False,
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
                validator_available=False,
            )

        if not isinstance(lock_data, dict):
            details.append({
                "kind": "broken",
                "message": "Lock file is malformed: expected a JSON object.",
            })
            return DoctorReport(
                status="broken", details=details,
                system2_version={"installed": _CODEX_PLUGIN_VERSION, "locked": ""},
                overlays=[], composed=True, exit_code=1,
                validator_available=False,
            )
        raw_sources = lock_data.get("overlay_sources")
        if (
            not isinstance(raw_sources, list)
            or any(not isinstance(source, str) or not source for source in raw_sources)
        ):
            details.append({
                "kind": "broken",
                "message": "Lock file is malformed: overlay_sources must be non-empty strings.",
            })
            return DoctorReport(
                status="broken", details=details,
                system2_version={"installed": _CODEX_PLUGIN_VERSION,
                                 "locked": lock_data.get("codex_plugin_version", "")},
                overlays=[], composed=True, exit_code=1,
                validator_available=False,
            )

        sources = list(raw_sources)
        overlays = [{"name": _overlay_name_of(s), "source_present": os.path.isdir(s)}
                    for s in sources]
        status = "pending_validation"

        missing_sources = [s for s in sources if not os.path.isdir(s)]
        if missing_sources:
            status = "stale_overlay"
            for s in missing_sources:
                details.append({
                    "kind": "stale_overlay",
                    "message": f"recorded overlay source is missing: {s}",
                })

        # The lock owns the complete variable inventory, including orchestrator,
        # roles, guards, manifest, and hook template. Validate every byte digest.
        try:
            verify_owned_artifacts(
                project_path, lock_data, _CODEX_LOCK, require_all=True,
            )
        except ValueError as exc:
            status = "broken"
            details.append({
                "kind": "broken",
                "message": f"Generated Codex artifact integrity failure: {exc}",
            })

        # Hook trust and approval are not observable by the compiler. Always surface
        # that limitation loudly and delegate the liveness check to the canary skill.
        details.append({
            "kind": "validator_unavailable",
            "message": (
                "PENDING NATIVE ACCEPTANCE: Codex hook event routing, trust/approval, "
                "and deny behavior are NOT observable or validated by the compiler. "
                "The in-channel `system2-doctor` canary is only a candidate diagnostic; "
                "it is not native release evidence or a release enforcement guarantee."
            ),
        })

        # Advisory (does NOT change status/exit): lock sources resolving out-of-tree.
        details.extend(lock_sources_outside_project(sources, project_path))

        locked_version = lock_data.get("codex_plugin_version", "")
        exit_code = 1
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
