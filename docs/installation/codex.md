# Installation and Updating (Codex)

The Codex channel is **live from this repo**. It carries the **base System2 workflow**
compiled for Codex. For the other harnesses, see the
[Installation and Updating](../installation.md) index.

## Installation

Add the System2 marketplace from this repository, then install the `system2` plugin:

```
codex plugin marketplace add DeliberateCode/System2
```

This resolves the root `.agents/plugins/marketplace.json`, which points at
`./distributions/codex`. (The exact marketplace-resolution behavior is verified
end-to-end against the `codex` CLI in the codex smoke runbook; see
[../runbooks/codex-smoke.md](../runbooks/codex-smoke.md).)

**What Codex gets, stated plainly.** The Codex plugin is an *adapted* port, not a
native re-implementation:

| System2 component | On Codex | Fidelity |
|-------------------|----------|----------|
| 13 pipeline agents + orchestrator | Lowered to role skills + an orchestrator skill with in-session role switching | **adapted** (no native subagent isolation) |
| `block-dangerous` / `protect-sensitive` / `enforce-lease` | Node command hooks (`PreToolUse`) generated from the same proven matcher constants the Pi backend uses | **adapted** — `enforced: false`, `gated: true` at rest |
| `budget` | Stop-event report | **adapted** (a report, not a block) |
| `format` / `typecheck` | Skill instruction only | **advisory** |

Read this before you rely on enforcement:

> System2 workflows for Codex. NOTE: safety enforcement is INACTIVE until you review
> and trust the bundled hooks via `/hooks`; until then System2 runs advisory-only.

The safety gates are **advisory until you review and trust the bundled hooks via
`/hooks`** (and `features.hooks` is enabled). Once trusted they are *adapted* — a
deterministic pre-execution block — but **never native**, and coverage is partial:

> Even with hooks trusted, Codex hooks intercept shell commands and apply_patch-matched
> edits; they do NOT intercept WebSearch or other non-shell, non-MCP tools. Enforcement
> on Codex is therefore ADAPTED, never total.

No capability is classified `native`, none is `enforced` at rest, and nothing
auto-enables hooks or asks for blanket approval — review each hook before trusting it.
After trusting, run the `system2-doctor` skill to confirm hook liveness (the compiler
cannot read Codex trust state). The same statements appear in
`distributions/codex/README.md` and the `system2.codex.lock.json` FIDELITY banner,
which are machine-checked for agreement on every change.

**Enforcement init (one-time, global).** On current Codex builds the safety hooks are
delivered through the user-level `~/.codex/hooks.json` config layer, not plugin-bundled.
After installing the plugin, materialize the hooks once for your machine:

```
system2 codex init
```

`system2 codex init` is the `system2-compiler` Python package's own CLI (see the
compiler's README for install options), separate from the plugin. Its user-hooks
reference is packaged as real package-data and ships with the installed package
regardless of install method — a `pip install`ed wheel works the same as running
from a repo checkout (verified by building a wheel, installing it fresh, and
running this command from outside any checkout). If it ever fails to find its
reference tree, that indicates a corrupted or partial install; `--reference
/path/to/distributions/codex/user-hooks` is available as a manual override.

This copies the System2 guard scripts into `~/.codex/system2/hooks/` and writes
`~/.codex/hooks.json` with absolute hook commands (so they fire from any project). A
pre-existing non-System2 `~/.codex/hooks.json` is backed up (timestamped `.bak`) and
never silently overwritten — re-run with `--force` to install over it. Then review and
trust the hooks once via `/hooks`: enforcement is advisory-only until you trust them,
and active across all projects once you do. Undo with `system2 codex uninstall` (it
restores any backup and removes only the System2 artifacts).

## Utility skills

Three adapted second-opinion skills ship alongside the 13-role workflow, each
requiring its own external CLI on PATH. If the CLI is missing, the skill stops and
reports the missing prerequisite rather than improvising a substitute.

| Skill | Purpose | Prerequisite |
|-------|---------|---------------|
| `system2-codex` | Run a prompt through OpenAI's Codex CLI (`codex exec`) non-interactively for a second opinion or code review from a fresh Codex instance. | the OpenAI Codex CLI (`codex`) on PATH |
| `system2-gemini` | Run a prompt through Google's Antigravity CLI (`agy`) non-interactively for a second opinion or code review from a fresh instance. | Google's Antigravity CLI (`agy`) on PATH |
| `system2-stateless-loop` | Run an instruction in a stateless subprocess loop using `claude -p` until the task reports STATUS: CLEAN or max iterations are reached. | the Claude Code CLI (`claude`) on PATH — required even though this host is not Claude Code |

`system2-codex` spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex
instance with none of this session's context. It is a second opinion from a clean
slate, not a fork of the current session.

See [Installation and Updating (Claude Code)](claude-code.md#utility-skills) for the
`/system2:codex`-skill-vs-Codex-install-channel disambiguation — referenced here, not
restated.

## Updating

Codex plugin updates follow Codex's own plugin update flow for a marketplace-installed
plugin; re-running `codex plugin marketplace add DeliberateCode/System2` re-resolves this
repository. The safety hooks are installed separately and globally: if the bundled hooks
change, re-run `system2 codex init` to re-materialize them (it backs up any existing
`~/.codex/hooks.json` as a timestamped `.bak` and never silently overwrites — add `--force`
to install over a pre-existing config). After any hook change, re-review and trust the hooks
via `/hooks`, then run the `system2-doctor` skill to confirm hook liveness.

## Backout / rollback

This channel is independently reversible; nothing here couples to another channel.

The plugin is additive — uninstall it from the harness, or remove the marketplace, to back
out. No project files are modified by installing it. The globally-installed hooks are removed
with `system2 codex uninstall`, which restores any backup and removes only the System2
artifacts. If `~/.codex/hooks.json` changed after installation, uninstall refuses and leaves
the referenced scripts and install state intact rather than creating dangling global hooks.
