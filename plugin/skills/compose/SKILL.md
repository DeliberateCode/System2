---
name: compose
description: Compose overlay contributions onto the project. Reads overlay manifests, validates, detects conflicts, and materializes a composed CLAUDE.md with overlay-contributed sections. Use when integrating third-party overlays into a System2 project.
argument-hint: "<overlay_path> [overlay_path...] [--dry-run] [--from-lock] [--uninstall <name>]"
---

# /system2:compose -- Compose Overlay Contributions

You are executing the /system2:compose skill. Follow these steps exactly.

## Arguments

Parse the arguments provided after the command name:

1. Collect all arguments that are NOT `--dry-run`, NOT `--allow-injection`, NOT `--allow-newer-schema`, NOT `--from-lock`, and NOT `--uninstall` as overlay directory paths (space-separated, absolute or relative).
2. Check if `--dry-run` is present among the arguments. Store this as a boolean.
3. Check if `--from-lock` is present among the arguments. Store this as a boolean.
4. If `--from-lock` is set, overlay paths are read from the existing lock file (the composer handles this). Skip to step 1 of Steps below.
5. If no overlay paths are provided as arguments, check for a project-local configuration file at `.system2/overlays.json` in the project root. If it exists, read it as JSON — it should contain an `"overlays"` array of path strings. Use those paths.
6. If no overlay paths are provided via arguments, `--from-lock`, or config file, tell the user:
   "Usage: `/system2:compose <overlay_path> [overlay_path...] [--dry-run]`
   Or: `/system2:compose --from-lock` to recompose using locked overlay paths.
   Alternatively, list overlay paths in `.system2/overlays.json`."
   and stop.
7. Check if `--uninstall` is present among the arguments. If so, collect the next argument as the overlay name to uninstall.
8. If `--uninstall` is combined with overlay paths or `--from-lock`, tell the user: "`--uninstall` is mutually exclusive with overlay paths and `--from-lock`. Use `--uninstall` alone with the overlay name." and stop.
9. If `--uninstall` is present without a name argument, tell the user: "Usage: `/system2:compose --uninstall <overlay-name> [--dry-run]`" and stop.
10. If `--uninstall` is present with a valid overlay name, skip to the "Uninstall Steps" section below.

## Steps

### 1. Determine paths

- Set `PROJECT_ROOT` to the current project root directory (the repository root where CLAUDE.md lives or would live).
- Set `PLUGIN_ROOT` to `${CLAUDE_PLUGIN_ROOT}` (the System2 plugin installation directory).

### 2. Validate overlay directories exist

For each overlay path provided:
- Resolve the path (expand relative paths against the current working directory).
- Check that the directory exists and contains a `system2.overlay.json` file.
- If any overlay directory is missing or lacks `system2.overlay.json`, tell the user which path is invalid and stop.

### 3. Run dry-run preview first (always)

**Always** run the composer in dry-run mode first, regardless of whether the user passed `--dry-run`:

If `--from-lock` is set:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --from-lock \
  --dry-run \
  [--allow-newer-schema] \
  --format text
```

Otherwise:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --overlays "<comma_separated_overlay_paths>" \
  --project "${PROJECT_ROOT}" \
  --dry-run \
  [--allow-newer-schema] \
  --format text
```

Include `--allow-newer-schema` if the user passed it. Capture stdout, stderr, and the exit code. If the exit code is not 0, skip to step 4 to handle the error. Otherwise, continue.

### 3a. Present the composition preview

Present the composition report from stdout to the user. The report includes:
- Overlays composed (name and version)
- Contributions applied by type
- Composed CLAUDE.md line count
- Files that would be written

**Deferred contributions:** If the report mentions deferred contributions (hooks or tools declared but not applied in this composition phase), highlight this clearly:

> **Deferred contributions:** The following contributions are declared by overlays but are not applied in the current composition phase. They will become active in a future System2 release that supports hook/tool registration.

List each deferred scope and count.

**Semantic tension warnings:** If stderr contains a "Semantic tensions (warnings):" heading followed by WARNING: lines, present those warnings prominently:

> **Semantic tension warnings:** The following overlays contribute to high-leverage surfaces. Review their combined contributions for coherence before proceeding.

List each warning.

**Size warning:** If stderr contains a warning about composed CLAUDE.md exceeding 500 lines, relay it to the user.

**Prompt injection warnings:** If stderr contains lines mentioning "prompt injection", present them with high visibility and tell the user which overlay content files are flagged. This is a security gate — the user must explicitly acknowledge these warnings before composition can proceed in write mode.

### 3b. Gate: user approval

If the user passed `--dry-run`, tell them:
"Dry run complete. No files were written. To apply the composition, run `/system2:compose` without `--dry-run`."
Stop here.

If the user did NOT pass `--dry-run`, ask for explicit approval before writing:
"The preview above shows what will be composed. Approve to write the composed artifacts to the project, or cancel."

If prompt injection warnings were present, explicitly call them out in the approval prompt:
"**Security notice:** Prompt injection patterns were detected in overlay content files (see warnings above). Confirm that you trust this overlay content before proceeding."

Wait for user approval. If the user declines, stop without writing.

### 3c. Write composed artifacts

After user approval, run the composer in write mode. Forward any flags that were used in the dry-run (`--allow-injection` if injection warnings were approved, `--allow-newer-schema` if the user opted into degraded mode, `--from-lock` if that was the source):

If `--from-lock` is set:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --from-lock \
  [--allow-injection] \
  [--allow-newer-schema] \
  --format text
```

Otherwise:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --overlays "<comma_separated_overlay_paths>" \
  --project "${PROJECT_ROOT}" \
  [--allow-injection] \
  [--allow-newer-schema] \
  --format text
```

Capture stdout, stderr, and the exit code. If exit code is 0, tell the user:
"Composition complete. The composed artifacts have been written to the project."

If the exit code is not 0, handle it per step 4.

### 4. Handle errors based on exit code

#### Exit 1 -- Validation errors

One or more overlay manifests failed validation. The composer printed error details to stderr.

Present the validation errors to the user. For each error, suggest a fix:
- "missing required top-level field: X" -- Add the field to system2.overlay.json
- "must be kebab-case" -- Rename using only lowercase letters, numbers, and hyphens
- "content_file not found" -- Create the referenced file or fix the path in the manifest
- "path traversal rejected" -- Use relative paths without `..` components
- "symlink resolves outside overlay directory" -- Remove the symlink or point it inside the overlay
- "unknown pipeline agent" -- Use one of the 13 pipeline agent names: executor, code-reviewer, design-architect, spec-coordinator, requirements-engineer, task-planner, test-engineer, security-sentinel, eval-engineer, docs-release, repo-governor, postmortem-scribe, mcp-toolsmith
- "unknown anchor" -- Check the anchor map at `plugin/schemas/anchor-map.json` for valid anchor names
- "hook security violation" -- Hooks must use Python 3.8+ stdlib only with no network calls
- "when inline is false (or omitted), summary is required" -- Add a `summary` field to the prompt section contribution
- "schema_version ... is not supported" -- The overlay uses a newer schema version. Ask the user if they want to attempt degraded composition; if yes, re-run the composer with `--allow-newer-schema` (unknown contribution types will be skipped)

Tell the user: "Fix the errors above in the overlay manifest(s) and re-run `/system2:compose`."

#### Exit 2 -- Structural conflicts

Structural conflicts prevent composition. The composer printed conflict details to stderr.

Present all conflicts to the user. For each conflict type, suggest a resolution:
- "auxiliary agent name collision" -- Rename one of the conflicting auxiliary agents so names are unique across overlays
- "known_conflicts" -- One overlay explicitly declares incompatibility with the other. Remove the conflicting overlay or resolve the declared conflict
- "ordering cycle" -- Remove or adjust `after` declarations in the overlay manifests to break the cycle

Tell the user: "Structural conflicts block composition. Resolve the conflicts above and re-run `/system2:compose`."

#### Exit 3 -- I/O error

A filesystem error occurred during composition.

Present the error to the user and suggest:
- Check that the project directory is writable
- Check that overlay directories are readable
- Check disk space
- Re-run `/system2:compose` after resolving the I/O issue

#### Exit 4 -- Prompt injection blocked

Prompt injection patterns were detected in overlay content files and the composer blocked write mode. This exit code only occurs in write mode without `--allow-injection`.

Present the injection warnings from the output and tell the user:
"Prompt injection patterns were detected. Review the flagged content files. If you trust the overlay content, the skill will re-run with `--allow-injection` after your explicit approval."

#### Any other exit code

Tell the user: "The composition engine exited with unexpected code N. Check the output above for details."

## Uninstall Steps

These steps apply when `--uninstall` was detected in the Arguments section (step 10).

### U1. Run dry-run preview first (always)

**Always** run the composer in dry-run mode first, regardless of whether the user passed `--dry-run`:

```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --uninstall "<overlay-name>" \
  --dry-run \
  [--allow-newer-schema] \
  --format text
```

Include `--allow-newer-schema` if the user passed it. Capture stdout, stderr, and the exit code. If the exit code is not 0, skip to step U5 to handle the error. Otherwise, continue.

### U2. Present the uninstall preview

Present the uninstall report from stdout to the user. The report includes:
- Overlay being removed (name and version)
- Remaining overlays after removal (names and versions), or "none" if this is the last overlay
- Files and directories to be removed (stale artifacts from the uninstalled overlay)
- CLAUDE.md preview (first 20 lines of the resulting CLAUDE.md)

### U3. Gate: user approval

If the user passed `--dry-run`, tell them:
"Dry run complete. No files were written. To apply the uninstall, run `/system2:compose --uninstall <overlay-name>` without `--dry-run`."
Stop here.

If the user did NOT pass `--dry-run`, ask for explicit approval before writing:
"The preview above shows what will change when the overlay is removed. Approve to proceed with the uninstall, or cancel."

Wait for user approval. If the user declines, stop without writing.

### U4. Execute uninstall in write mode

After user approval, run the composer in write mode (same command without `--dry-run`). Forward any flags that were used in the dry-run (`--allow-injection` if injection warnings were approved, `--allow-newer-schema` if the user opted into degraded mode):

```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --uninstall "<overlay-name>" \
  [--allow-injection] \
  [--allow-newer-schema] \
  --format text
```

Capture stdout, stderr, and the exit code. If exit code is 0, tell the user:
"Uninstall complete. The overlay has been removed and the project artifacts have been updated."

If the exit code is not 0, handle it per step U5.

### U5. Handle errors

Handle errors using the same exit code mapping as step 4 of the compose flow:

- **Exit 1** -- Validation errors (e.g., invalid overlay name, overlay not found in lock file, no lock file). Present the error details and suggest fixes.
- **Exit 2** -- Structural conflicts during recomposition of remaining overlays. Present conflicts and suggest resolutions.
- **Exit 3** -- I/O error during file operations. Present the error and suggest checking permissions and disk space.
- **Exit 4** -- Prompt injection blocked during recomposition of remaining overlays. Present warnings and offer to re-run with `--allow-injection` after explicit approval.
- **Any other exit code** -- Tell the user: "The composition engine exited with unexpected code N. Check the output above for details."

## Usage Examples

Initialize a project with a single overlay:
```
/system2:compose /path/to/my-overlay
```

Compose multiple overlays:
```
/system2:compose /path/to/overlay-a /path/to/overlay-b
```

Preview composition without writing files:
```
/system2:compose --dry-run /path/to/my-overlay
```

Preview with multiple overlays:
```
/system2:compose --dry-run /path/to/overlay-a /path/to/overlay-b
```

Recompose using overlay paths from the lock file (after plugin or overlay updates):
```
/system2:compose --from-lock
```

Preview a lock-based recomposition:
```
/system2:compose --from-lock --dry-run
```

Remove an overlay:
```
/system2:compose --uninstall overlay-a
```

Preview overlay removal without writing files:
```
/system2:compose --uninstall overlay-a --dry-run
```

## Notes

- This skill invokes `composer.py` for all validation, conflict detection, and composition logic. Do not reimplement any of that logic.
- Overlay contributions are materialized only by this command. `/system2:init` produces base-only output regardless of installed overlays.
- The composed `CLAUDE.md` replaces the project-root `CLAUDE.md`. The base System2 `CLAUDE.md` (in the plugin directory) is never modified.
- Content files from overlays are copied to `.system2/overlays/<overlay-name>/` in the project so the project is self-contained.
- A lock file is written to `spec/overlay-manifest.lock` recording the composition state.
- Auxiliary agent files are copied to `.claude/agents/` in the project.
- Re-running `/system2:compose` with the same overlays produces identical output (idempotent).
- To update after changing an overlay, re-run `/system2:compose` with the same arguments.
- When the last overlay is uninstalled, the project reverts to base System2 (same as `/system2:init` output) and the lock file is removed.
