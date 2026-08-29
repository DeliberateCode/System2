# Installation and Updating (Claude Code)

Claude Code is the reference channel for System2, installed as a plugin from the System2
marketplace. For the other harnesses, see the [Installation and Updating](../installation.md)
index.

## Installation

### Step 1: Add the System2 Marketplace

```
/plugin marketplace add DeliberateCode/System2
```

This only **registers** the System2 catalog — you'll see a confirmation that the
marketplace was added. It does **not** install the plugin yet; that's Step 2.

### Step 2: Install the Plugin

Now install the `system2` plugin from the catalog you just added:

```
/plugin install system2@system2-marketplace
```

This installs all 13 agents, hooks, allowlists, and seven skills — `/system2:init`,
`/system2:compose`, `/system2:doctor`, `/system2:profile`, plus the utility skills
`/system2:codex`, `/system2:gemini`, and `/system2:stateless-loop`.

```
System2 Plugin
├── agents/              # 13 subagent definitions
├── skills/              # Skills (/system2:init, /system2:compose, /system2:doctor, /system2:profile, /system2:codex, /system2:gemini, /system2:stateless-loop)
├── hooks/               # Validation and quality hook scripts
├── allowlists/          # Per-agent file restriction patterns
├── schemas/             # Overlay manifest schema and anchor map
├── scripts/             # Overlay composer and shared validation helpers
└── .claude-plugin/      # Plugin identity and marketplace metadata
```

### Utility skills

Three additional skills, orthogonal to the core spec-driven pipeline, ship in the same plugin.

#### `/system2:stateless-loop`

Runs an instruction in a stateless subprocess loop. Each iteration invokes `claude -p` with no
LLM context from prior runs — the only continuity between iterations is the file system on disk.
Each iteration runs as a separate Bash call, so every iteration gets its own 10-minute timeout
window rather than the entire loop sharing one. The sub-agent outputs `STATUS: CLEAN` when the
task is fully resolved; the loop exits on that signal or when the iteration cap is reached.
**Requires the `claude` CLI on PATH.**

```
/system2:stateless-loop fix all type errors in src/
```

```
/system2:stateless-loop "run the test suite and fix any failures" --max_iterations 20
```

| Flag | Default | Description |
|------|---------|-------------|
| `--max_iterations N` | 10 | Hard cap on loop iterations |

#### `/system2:gemini`

Runs a prompt through Google's Antigravity CLI (`agy`, which replaced the older standalone
`gemini` CLI) non-interactively via `agy -p`. Useful for a second opinion, code review, or any
task where you want Gemini's perspective on the current project. **Requires the `agy` CLI on
PATH.**

```
/system2:gemini check my unstaged changes and perform a code review
```

```
/system2:gemini "explain the architecture of this project" --model "Gemini 3.1 Pro (High)"
```

Any flags supported by `agy` (e.g. `--model`, `--sandbox`, `--dangerously-skip-permissions`,
`--add-dir`) are passed through. Old `gemini` flags map across: `--yolo` →
`--dangerously-skip-permissions`, `--include-directories` → `--add-dir`, `--resume` →
`--continue`/`--conversation`.

#### `/system2:codex`

Runs a prompt through OpenAI's Codex CLI non-interactively via `codex exec`. Useful for a second
opinion, code review, or any task where you want Codex's perspective on the current project.
**Requires the `codex` CLI on PATH.**

`/system2:codex` runs the OpenAI Codex CLI for a second opinion from inside Claude Code — not to
be confused with the **Codex install channel** (`distributions/codex/`), which is System2
compiled *for* the Codex harness.

```
/system2:codex review the recent changes for security issues
```

```
/system2:codex "find and fix type errors in src/" --model o3
```

Any flags supported by `codex exec` (e.g. `--model`, `--sandbox`, `--config`) are passed through.

### Step 3: Restart Claude Code

After installing, restart Claude Code so the new agents, hooks, and skills are loaded.

### Step 4: Initialize CLAUDE.md

In your project directory, run:

```
/system2:init
```

This writes the System2 orchestrator instructions to `CLAUDE.md` in your project root.

To overwrite an existing CLAUDE.md:

```
/system2:init --force
```

### Step 5: Restart Claude Code

Restart Claude Code again so the new `CLAUDE.md` orchestrator instructions take effect.

### Optional: Overlays and profiles

System2 includes an opt-in overlay mechanism for extending the base workflow without
forking the plugin, plus reusable overlay **profiles**. These are documented separately —
see [Overlays (optional extensions) — Claude Code](../overlays/claude-code.md).

## Migrating from the sys2 utility skills

The `codex`, `gemini`, and `stateless-loop` utility skills are now part of the `system2` plugin
under the `/system2:` namespace — there is no separate `sys2` plugin.

If you previously installed `sys2` from the standalone `System2-UtilitySkills` marketplace:

1. Uninstall the old `sys2` plugin (or remove the standalone marketplace).
2. Add this marketplace and install `system2` as described in [Installation](#installation)
   above:

   ```
   /plugin marketplace add DeliberateCode/System2
   /plugin install system2@system2-marketplace
   ```

Old invocations map to the new namespace one-for-one: `sys2:codex`, `sys2:gemini`, and
`sys2:stateless-loop` become `/system2:codex`, `/system2:gemini`, and `/system2:stateless-loop`.

This supersedes an earlier plan to host `sys2` as a second plugin alongside `system2` in this
marketplace — that layout never shipped. The full release note, including the exact invocation
mapping, is in [`CHANGELOG.md`](../../CHANGELOG.md) under `[Unreleased]`.

## Updating

System2 updates are handled by the Claude Code plugin system. No manual update commands are needed.

To check plugin status:

```
/plugin list
```

Plugin updates do not automatically rewrite composed overlay artifacts in your project. After a plugin update, check whether your composed overlays need refreshing:

```
/system2:doctor
```

If the doctor reports drift (stale base or stale overlay), recompose using the locked overlay paths:

```
/system2:compose --from-lock
```

This reads the overlay source paths recorded in `spec/overlay-manifest.lock` and runs the normal compose flow (dry-run preview, approval, write). See [Overlays (optional extensions) — Claude Code](../overlays/claude-code.md) for the full overlay and profile workflow.

## Backout / rollback

This channel is independently reversible; nothing here couples to another channel.

Remove the marketplace (or uninstall the `system2` plugin) to return to the prior state.
The composed-output engine keeps its deep escape hatch: set `SYSTEM2_USE_BUNDLE=0` to run the
plugin off the frozen `composer.py.preflip` baseline instead of the vendored bundle. Composed
output is byte-identical across both paths.
