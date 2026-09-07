# Overlays (optional extensions) — Claude Code

Overlays are System2's opt-in mechanism for extending the base workflow without forking the
plugin. This page covers composing overlays and saving reusable overlay **profiles** on
Claude Code, the only harness with end-user overlay/profile UX. See the
[Overlays index](../overlays.md) for channel availability, and
[Installation and Updating (Claude Code)](../installation/claude-code.md) for setup.

Overlays are entirely optional: `/system2:init` remains base-only and produces the same
orchestrator instructions regardless of installed or available overlays.

## Composing overlays

System2 includes an opt-in overlay mechanism for extending the base workflow without forking the plugin. Overlays are local directories with a `system2.overlay.json` manifest and referenced content files.

Preview an overlay composition without writing files:

```
/system2:compose --dry-run /path/to/my-overlay
```

Apply an overlay after preview and approval:

```
/system2:compose /path/to/my-overlay
```

`/system2:compose` validates manifests, detects structural conflicts, reports warnings, then writes project-local composed artifacts:

- `CLAUDE.md` with base System2 instructions plus overlay-contributed sections
- `.system2/overlays/<overlay-name>/` with local copies of overlay content
- `.claude/agents/<auxiliary-agent>.md` for overlay-contributed auxiliary agents
- `spec/overlay-manifest.lock` with versions, hashes, and applied contributions

The lock file records the overlay source paths used during composition. On subsequent updates, `--from-lock` reads those paths so you don't need to retype them:

```
/system2:compose --from-lock
```

To remove an overlay without affecting others, use `--uninstall` with the overlay's name (not its path):

```
/system2:compose --uninstall my-overlay
```

This recomposes the project with the remaining overlays and cleans up the removed overlay's cached content, auxiliary agents, and lock file entries. If the removed overlay was the last one, the project reverts to base System2. A dry-run preview is always shown before any files are modified.

Overlay composition is explicit. `/system2:init` remains base-only and produces the same orchestrator instructions regardless of installed or available overlays.

## Overlay profiles

A profile is a named, reusable set of overlays. Once you have settled on a useful combination of overlays, you can save it as a profile and activate it by name in any project instead of retyping overlay paths. Profiles are stored at `~/.system2/profiles.json` (user-level, shared across every project on your machine) and are independent of the per-project `.system2/overlays.json`.

The `/system2:compose` namespace creates, activates, and changes profiles. Activate a profile to compose its overlay set in the current project (a dry-run preview is shown before any files are written):

```
/system2:compose --profile backend-stack
```

Capture the project's current composed overlay set as a profile, with no paths typed:

```
/system2:compose --save-profile backend-stack
```

Define a profile explicitly from overlay paths:

```
/system2:compose create backend-stack /path/to/overlay-a /path/to/overlay-b
```

Adjust a profile by adding paths or removing overlays by name (flags are repeatable):

```
/system2:compose edit backend-stack --add /path/to/overlay-c --remove OverlayA
```

Remove a profile:

```
/system2:compose delete backend-stack
```

The `/system2:profile` namespace is strictly read-only. List every saved profile, or inspect one to see its ordered overlay set with resolved names and stale annotations:

```
/system2:profile list
/system2:profile list --profile backend-stack
```

Activation never composes a partial set. If a profile references an overlay path that is missing or no longer valid, activation fails cleanly, names the offending path, and writes nothing.
