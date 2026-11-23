# Installation and Updating (Codex)

System2 is available as a Codex plugin. It installs the workflow, overlay and profile commands, and 13 specialist role skills inside the `system2` plugin namespace. Overlay and profile commands need Python 3.11 or newer on your `PATH`.

## Install

In your terminal:

```sh
codex plugin marketplace add DeliberateCode/System2
codex plugin add system2@system2-marketplace
```

Restart Codex. System2 uses [native Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), enabled by default in current releases. If you disabled them, re-enable `[agents]` → `enabled` in Codex settings and start a new session. No separate System2 configuration or custom agent installation is required.

Verify the installation with:

```sh
codex plugin list --json
```

## Run

Open your project and invoke `$system2:system2` with your task. For an existing System2 project, ask it to read the current `spec/` files and diff, then continue the next unfinished task.

Use `$system2:system2-doctor` for a read-only summary of the current workflow state. `$system2:system2-compose` and `$system2:system2-profile` work with the bundled compiler. See [examples for overlays and profiles](../overlays/codex-pi.md).

## Runtime boundary

The orchestrator delegates each specialist step to a fresh Codex subagent thread with the corresponding System2 role skill. Use `/subagents` in Codex to inspect the threads. The parent handles approvals and waits for the required evidence. Separate threads are not separate permission sandboxes: System2 does not install hooks or modify Codex configuration.

Pause any other agent before allowing Codex to edit the same checkout.

## Migrating from the hook-based preview

If you previously ran `system2 codex init`, remove those global hooks before updating or removing the native plugin. A native plugin operation does not touch the old hooks. The preview install record is `system2/system2-install.json` under `$CODEX_HOME` (default: `~/.codex`). Skip this section if you only installed the native plugin.

Close Codex. From the System2 source checkout, fetch the `consolidation` branch if the revision below is missing, then run the previous revision's ownership-checking uninstaller in a separate worktree:

```sh
scratch=$(mktemp -d)
legacy="$scratch/System2"
git worktree add --detach "$legacy" cd009dd4f6506eae45767a1becf49a444dddd09f

codex_home="${CODEX_HOME:-$HOME/.codex}" # Set to the original --codex-home if different.
(cd "$legacy/compiler" && python3 -m system2_compiler.cli codex uninstall --codex-home "$codex_home" --dry-run)
```

After reviewing the dry run, run the same command without `--dry-run`:

```sh
(cd "$legacy/compiler" && python3 -m system2_compiler.cli codex uninstall --codex-home "$codex_home")
git worktree remove "$legacy"
rmdir "$scratch"
```

The uninstaller verifies installed files and restores the original `hooks.json` from its verified backup if one existed. If it refuses (for example, if you edited the hooks or the backup is missing), stop and reconcile the files manually, preserving your changes. Do not delete `hooks.json`, the backup, or the install record to bypass a refusal.

## Update

```sh
codex plugin marketplace upgrade system2-marketplace
codex plugin remove system2@system2-marketplace
codex plugin add system2@system2-marketplace
```

## Remove

```sh
codex plugin remove system2@system2-marketplace
```

To remove the marketplace as well:

```sh
codex plugin marketplace remove system2-marketplace
```

Existing project specs remain project files and are not deleted automatically.
