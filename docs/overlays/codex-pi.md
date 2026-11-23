# Overlays and profiles in Codex and Pi

An overlay adds project-specific guidance to System2. A profile is a named set of overlays you can reuse. Both are optional and available after a normal [Codex install](../installation/codex.md) or [Pi install](../installation/pi.md).

The compiler is included. Run `python3 --version` in your terminal to check that Python 3.11 or newer is available. You do not need a separate checkout or a `SYSTEM2_SOURCE` setting. After updating the package, preview a refresh of any active composition before resuming the workflow.

## Preview, approve, apply

Open the project you want to configure. Supply a directory with a `system2.overlay.json` manifest and its referenced files. These are the commands for each host:

| Operation | Codex | Pi |
|---|---|---|
| Preview only | `$system2:system2-compose --dry-run /path/to/overlay` | `/system2-compose --dry-run /path/to/overlay` |
| Apply an overlay | `$system2:system2-compose /path/to/overlay` | `/system2-compose /path/to/overlay` |
| Activate a saved profile | `$system2:system2-compose --profile backend-stack` | `/system2-compose --profile backend-stack` |
| Refresh the current overlay set | `$system2:system2-compose --from-lock` | `/system2-compose --from-lock` |
| Remove one overlay by manifest name | `$system2:system2-compose --uninstall my-overlay` | `/system2-compose --uninstall my-overlay` |
| Check workflow and overlay status | `$system2:system2-doctor` | `/system2-doctor` |

A write request starts with a read-only preview. Review the planned changes, warnings, and deferred items, then approve or cancel. You can also put a list of paths in `.system2/overlays.json` as `{"overlays": ["../my-overlay"]}` and invoke compose with no paths. Relative paths in that list resolve from the project root. The input list is not a lock and does not activate anything by itself.

After applying, start a new conversation and use the normal `$system2:system2` or `/system2 <task>` command. It checks the composition and uses its orchestrator and specialist guidance instead of the installed defaults. It reports missing or stale guidance rather than silently dropping the rules. Removing the last overlay returns that channel to the base workflow.

The compiler keeps each channel's files under `.system2/codex/` or `.system2/pi/`. It does not change `CLAUDE.md`, the specs, or host settings. The locks contain machine-local source paths; do not rely on another user having the same checkout paths. Only use overlays and projects you trust. Host tool, hook, MCP, and permission declarations are deferred, not installed.

## Save and manage profiles

Profiles use the same user-level store (`~/.system2/profiles.json`) as Claude Code. The profile command only lists or inspects; the compose command handles changes and activation. For instance, on Pi:

```text
/system2-compose --save-profile backend-stack
/system2-compose create another-stack /path/to/overlay-a /path/to/overlay-b
/system2-compose edit another-stack --add /path/to/overlay-c --remove overlay-b
/system2-profile list
/system2-profile inspect another-stack
/system2-compose --profile another-stack
/system2-compose delete another-stack
```

On Codex, use `$system2:system2-compose` and `$system2:system2-profile` with the same arguments. Saving, editing, or deleting a profile does not apply it to the project. Preview and activate it separately. A stale profile with a missing source will not be partially applied.

If the harness cannot run the compiler or write the profile store, you can run the same bundled command from a terminal in the project root: `python3 -ISB /absolute/path/to/installed/System2/compiler/run.py …`. Ask the harness for the path it found. Do not change host permissions just to make a System2 command run.
