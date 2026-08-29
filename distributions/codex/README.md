# System2 for Codex

GENERATED plugin — do not hand-edit. Regenerate via `python3 compiler/tools/regen_all.py`.

## Trust state (READ THIS FIRST — enforcement is CONDITIONAL on Codex)

System2 workflows for Codex. NOTE: safety enforcement is INACTIVE until you review and trust the bundled hooks via /hooks; until then System2 runs advisory-only.

| Trust state | Enforcement |
|---|---|
| Hooks not reviewed / untrusted | ADVISORY ONLY — nothing is blocked; the hooks do not run. |
| Hooks materialized to `~/.codex/hooks.json` and trusted via `/hooks` | CONDITIONAL ENFORCEMENT — dangerous shell commands, sensitive-path access, and off-lease edits are blocked before they run, with the coverage gap below. |
| Admin-disabled (`requirements.toml`) | ADVISORY ONLY — immutable; hooks cannot run and this cannot be overridden. |

To activate enforcement: run `system2 codex init` to materialize the guards into `~/.codex/hooks.json`, then review and trust them via `/hooks`. An administrator may disable hooks via `requirements.toml`; when disabled, System2 is advisory-only and this cannot be overridden. Nothing here auto-enables hooks or instructs blanket approval — review each hook before trusting it.

Coverage gap: Even with hooks trusted, Codex hooks intercept shell commands and apply_patch-matched edits; they do NOT intercept WebSearch or other non-shell, non-MCP tools. Enforcement on Codex is therefore ADAPTED, never total.

## Activating enforcement

1. Run `system2 codex init` to materialize the guards into `~/.codex/hooks.json` (the hook `command` is written as an absolute path).
2. Review and trust the materialized hooks via `/hooks` — read each hook before trusting it; never blanket-approve.
3. An administrator may force-disable hooks via `requirements.toml`; when disabled, System2 is advisory-only and this cannot be overridden in-session.
4. Run the `system2-doctor` skill to verify hook liveness (the compiler cannot read Codex trust state).

## Utility skills

Three adapted second-opinion skills ship alongside the 13-role workflow, each requiring its own external CLI on PATH:

- `system2-codex` — run a prompt through OpenAI's Codex CLI (`codex exec`) non-interactively for a second opinion or code review from a fresh Codex instance. This spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex instance with none of this session's context. It is a second opinion from a clean slate, not a fork of the current session. Requires the OpenAI Codex CLI (`codex`) on PATH.
- `system2-gemini` — run a prompt through Google's Antigravity CLI (`agy`) non-interactively for a second opinion or code review from a fresh instance. Requires Google's Antigravity CLI (`agy`) on PATH.
- `system2-stateless-loop` — run an instruction in a stateless subprocess loop using `claude -p` until the task reports STATUS: CLEAN or max iterations are reached. Requires the Claude Code CLI (`claude`) on PATH — required even though this host is not Claude Code.

See `docs/installation/claude-code.md`'s utility-skill disambiguation for how the Claude-channel `codex` skill relates to this Codex install channel — not restated here.

See `system2.codex.lock.json` for the per-capability fidelity report and the FIDELITY banner.
