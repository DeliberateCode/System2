# Installation and Updating (Claude Code)

System2 is available as a Claude Code plugin. It installs the orchestrator, 13 specialist roles, workflow skills, and supporting hooks.

## Install

```text
/plugin marketplace add DeliberateCode/System2
/plugin install system2@system2-marketplace
```

Restart Claude Code, open your project, and initialize the workflow:

```text
/system2:init
```

This writes the System2 orchestrator instructions to `CLAUDE.md`. If the project already has that file and you intend to replace it, use:

```text
/system2:init --force
```

Restart Claude Code after initialization so the project instructions are loaded.

## Run

Give the orchestrator your task in the project conversation. For an existing System2 project, ask it to read the current `spec/` files and diff, then continue the next unfinished task.

Use `/system2:doctor` for a read-only summary of the current workflow and overlay state.

## Runtime boundary

Claude Code runs System2 specialist roles as native subagents. The plugin also supplies hooks and role-specific write restrictions, while Claude Code's permissions and user configuration remain authoritative.

Pause any other agent before allowing Claude Code to edit the same checkout.

## Optional overlays and profiles

Claude Code exposes `/system2:compose` and `/system2:profile` for optional workflow extensions. See [Overlays and profiles](../overlays/claude-code.md).

## Update

Claude Code handles installed plugin updates. Check installation status with:

```text
/plugin list
```

After an update, use `/system2:doctor` if the project uses overlays. If it reports drift, refresh the composition with:

```text
/system2:compose --from-lock
```

## Remove

Uninstall the plugin with Claude Code's plugin manager:

```text
/plugin uninstall system2@system2-marketplace
```

You may also remove the `system2-marketplace` marketplace after uninstalling the plugin. Existing project specs and `CLAUDE.md` remain project files and are not deleted automatically.
