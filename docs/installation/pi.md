# Installation and Updating (Pi)

The Pi channel is **publishing soon** — its install command is withheld pending publish. It
will carry the **base System2 workflow** compiled for
Pi. For the other harnesses, see the [Installation and Updating](../installation.md) index.

## Installation

The Pi channel is an npm package under the `@deliberatecode` scope, auto-discovered by
the [Pi coding agent](https://github.com/earendil-works/pi-coding-agent).

**The install command is intentionally withheld here.** The `@deliberatecode` npm scope was
claimed and secured on 2026-07-04 (recorded in the consolidation cycle's authorization ledger;
not independently verifiable from an unauthenticated host). The
install one-liner remains withheld until the package is actually published with build
provenance: publishing a runnable command before the package exists would still direct users
at an unservable name. See the Pi package README at
[../../distributions/pi/README.md](../../distributions/pi/README.md) for the eventual form.

## Utility skills

Three adapted second-opinion skills ship alongside the base workflow, each requiring its own
external CLI on PATH. If the CLI is missing, the skill stops and reports the missing
prerequisite rather than improvising a substitute.

| Skill | Purpose | Prerequisite |
|-------|---------|---------------|
| `system2-codex` | Run a prompt through OpenAI's Codex CLI (`codex exec`) non-interactively for a second opinion or code review from a fresh Codex instance. | the OpenAI Codex CLI (`codex`) on PATH |
| `system2-gemini` | Run a prompt through Google's Antigravity CLI (`agy`) non-interactively for a second opinion or code review from a fresh instance. | Google's Antigravity CLI (`agy`) on PATH |
| `system2-stateless-loop` | Run an instruction in a stateless subprocess loop using `claude -p` until the task reports STATUS: CLEAN or max iterations are reached. | the Claude Code CLI (`claude`) on PATH — required even though this host is not Claude Code |

`system2-codex` spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex instance
with none of this session's context. It is a second opinion from a clean slate, not a fork of
the current session.

**Known Pi limitation.** The System2 Pi extension's protect-sensitive gate scans the ENTIRE
bash command, with no override, and Pi has no permission prompt to bypass it — so a prompt
containing a token like `credentials` or `secrets` is hard-blocked before the CLI runs.
Workaround: reword the prompt to avoid those tokens. This is the gate doing its declared job,
not a bug — stated here so a block is never a surprise.

See [Installation and Updating (Claude Code)](claude-code.md#utility-skills) for the
`/system2:codex`-skill-vs-Codex-install-channel disambiguation — referenced here, not restated.

## Updating

Not applicable yet — the Pi channel is unpublished (see above). Once published, updates will
follow the Pi agent's own package update flow.

## Backout / rollback

This channel is independently reversible; nothing here couples to another channel.

Not yet published (see above). Once available, the package is removed with the Pi agent's own
uninstall path; project files materialized by `/system2-init` are managed and skip-if-identical,
so they are safe to remove by hand.
