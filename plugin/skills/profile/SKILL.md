---
name: profile
description: STRICTLY READ-ONLY listing and inspection of saved overlay profiles. Lists every profile by name, or inspects one profile to show its ordered overlay set with resolved names/versions and stale annotations. Makes no changes to anything -- it never composes, never writes project artifacts, and never mutates the profile store. Use when you want to see which profiles exist or what a profile contains.
argument-hint: "list [--profile <name>]"
---

# /system2:profile -- Inspect Overlay Profiles (read-only)

You are executing the /system2:profile skill. This namespace is **strictly read-only**. It only lists and inspects saved overlay profiles. It performs no writes of any kind. Follow these steps exactly.

## Arguments

Parse the arguments provided after the command name:

1. Check if `--profile <name>` is present among the arguments. If so, collect the next argument as the profile name to inspect.
2. Anything else (including a bare `list` with no `--profile`) means "list all profiles".

## Steps

### 1. Determine the plugin root

Set `PLUGIN_ROOT` to `${CLAUDE_PLUGIN_ROOT}` (the System2 plugin installation directory).

### 2. Run the read-only profile command

If the user did NOT pass `--profile <name>` (a bare `list`), list every saved profile by name:

```
python3 "${PLUGIN_ROOT}/scripts/profiles.py" --list --format text
```

If the user passed `--profile <name>`, inspect that single profile:

```
python3 "${PLUGIN_ROOT}/scripts/profiles.py" --inspect "<name>" --format text
```

Capture stdout, stderr, and the exit code.

### 3. Present the output

Relay the command's output to the user verbatim, including any stale annotations exactly as printed (for example, a source path annotated "unresolvable (path missing / no valid manifest)"). Do not summarize away or rewrite those annotations.

Exit-code handling:

- **Exit 0** -- Success. For a bare `list`, the output is the profile names, or the single line "No profiles are defined yet." when the store is empty or absent. For an inspect, the output is the profile's ordered overlay set with resolved name/version per valid path and a stale annotation per unresolvable path. Present it as-is.
- **Exit 1** -- The named profile is unknown, or the profile store is corrupt. The script prints a single clean error line (no Python stack trace). Relay that error line to the user as-is, then stop. For an unknown profile, the message names the requested profile and may list the available profiles.
- **Any other exit code** -- Tell the user: "The profile reader exited with unexpected code N. Check the output above for details." and stop.

## Usage Examples

List every saved profile by name:
```
/system2:profile list
```

Inspect a single profile's overlay set:
```
/system2:profile list --profile backend-stack
```

## Notes

- This namespace is **strictly read-only**. It invokes only the read-only profile reader (`profiles.py --list` / `--inspect`). It has no write step, no mutation step, and never changes the profile store, the project, or any composed artifact.
- It never composes anything and never writes project files. Listing or inspecting a profile leaves `~/.system2/profiles.json` and every project artifact untouched.
- Profiles are stored at `~/.system2/profiles.json`, outside any single project, so the same profile is visible from every project on this machine.
- Stale entries (an overlay path that is missing or has no valid manifest) are shown with an annotation and never abort the listing; the rest of the profile is still displayed.
- To manage or activate profiles (saving, building, renaming, removing, or activating them), use the `/system2:compose` skill -- that is the namespace where profile changes and activation happen. This `/system2:profile` namespace only reads.
