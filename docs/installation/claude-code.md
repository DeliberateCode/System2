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

This installs all 13 agents, hooks, allowlists, and the `/system2:init`,
`/system2:compose`, `/system2:doctor`, and `/system2:profile` skills.

```
System2 Plugin
├── agents/              # 13 subagent definitions
├── skills/              # Skills (/system2:init, /system2:compose, /system2:doctor, /system2:profile)
├── hooks/               # Validation and quality hook scripts
├── allowlists/          # Per-agent file restriction patterns
├── schemas/             # Overlay manifest schema and anchor map
├── scripts/             # Overlay composer and shared validation helpers
└── .claude-plugin/      # Plugin identity and marketplace metadata
```

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
