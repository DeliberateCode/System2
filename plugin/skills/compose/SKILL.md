---
name: compose
description: Compose overlay contributions onto the project. Reads overlay manifests, validates, detects conflicts, and materializes a composed CLAUDE.md with overlay-contributed sections. Use when integrating third-party overlays into a System2 project.
argument-hint: "<overlay_path> [overlay_path...] [--dry-run] [--from-lock] [--uninstall <name>] | [--profile <name>] [--save-profile <name>] | create <name> <paths...> | edit <name> --add <path> --remove <OverlayName> | delete <name>"
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

### Profile arguments

A profile is a named, reusable set of overlay source paths stored outside any single project. The composer manages profiles; this skill only detects which profile operation is requested and routes to the matching section below. Detect the following, in this order:

11. Check if `--profile <name>` is present. If so, collect the next argument as the profile name and skip to the "Profile Activation Steps" section below. `--profile` activates (composes) a saved profile into the current project.
12. Check if `--save-profile <name>` is present. If so, collect the next argument as the profile name and skip to the "Profile Mutation Steps" section below. `--save-profile` captures the project's current composition (read from the lock file) as a new profile.
13. Check if the first argument is one of the verbs `create`, `edit`, or `delete`. If so, this is a profile mutation:
    - `create <name> <path> [<path>...]` -- record the name and the remaining paths.
    - `edit <name> --add <path> --remove <OverlayName>` -- record the name; collect every `--add <path>` value and every `--remove <OverlayName>` value (each repeatable).
    - `delete <name>` -- record the name.
    Then skip to the "Profile Mutation Steps" section below.
14. The profile forms are mutually exclusive with overlay paths, `--from-lock`, and `--uninstall`, and with each other. You do not need to enforce this yourself — the composer rejects conflicting combinations with a clear error. If the user mixes them, build the command as requested and relay the composer's mutual-exclusion error verbatim.

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

## Profile Activation Steps

These steps apply when `--profile <name>` was detected in the Arguments section (step 11). Activation composes the profile's saved overlay set into the current project using the same preview-then-approve flow as a normal compose. The composer resolves the profile to its ordered overlay paths and reuses the composition engine; it hard-fails (without writing anything) if the profile is unknown or if any of its overlay paths is missing or unresolvable.

### P1. Run dry-run preview first (always)

**Always** run the composer in dry-run mode first, regardless of whether the user passed `--dry-run`:

```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --profile "<name>" \
  --dry-run \
  [--allow-newer-schema] \
  --format text
```

Include `--allow-newer-schema` if the user passed it. Capture stdout, stderr, and the exit code. If the exit code is not 0, handle it using the exit-code mapping in step 4 of the compose flow (an unknown profile or an unresolvable/missing overlay path surfaces there as a validation error naming the offending profile or path) and stop. Otherwise, continue.

### P2. Present the activation preview

Present the composition report from stdout exactly as in step 3a of the compose flow: overlays composed (name and version), contributions applied, composed CLAUDE.md line count, files that would be written, plus any deferred contributions, semantic tension warnings, size warning, and prompt injection warnings. Treat prompt injection warnings as a security gate the same way the compose flow does.

### P3. Gate: user approval

If the user passed `--dry-run`, tell them:
"Dry run complete. No files were written. To activate the profile, run `/system2:compose --profile <name>` without `--dry-run`."
Stop here.

If the user did NOT pass `--dry-run`, ask for explicit approval before writing:
"The preview above shows what will be composed for profile `<name>`. Approve to write the composed artifacts to the project, or cancel."

If prompt injection warnings were present, call them out in the approval prompt exactly as in step 3b of the compose flow.

Wait for user approval. If the user declines, stop without writing.

### P4. Write composed artifacts

After user approval, re-invoke the same command WITHOUT `--dry-run` to write. Forward any flags that were used in the dry-run (`--allow-injection` if injection warnings were approved, `--allow-newer-schema` if the user opted into degraded mode):

```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --profile "<name>" \
  [--allow-injection] \
  [--allow-newer-schema] \
  --format text
```

Capture stdout, stderr, and the exit code. If exit code is 0, tell the user:
"Activation complete. Profile `<name>` has been composed into the project."
Surface the one-line activation note from the report naming the profile that was activated.

If the exit code is not 0, handle it using the exit-code mapping in step 4 of the compose flow.

## Profile Mutation Steps

These steps apply when `--save-profile`, `create`, `edit`, or `delete` was detected in the Arguments section (steps 12-13). Mutations change only the stored profile definition; they never write project artifacts and never recompose on their own. The composer REJECTS `--dry-run` with mutations (it exits 1 with a clear error), so never pass `--dry-run` for `--save-profile`, `create`, `edit`, or `delete`, and do not run a dry-run preview for them.

### M1. Build and run the mutation command

Build the command that matches the requested operation:

Save the project's current composition as a profile:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --save-profile "<name>" \
  [--force] \
  --format text
```

Create a profile from explicit overlay paths:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --profile-op create \
  --profile-name "<name>" \
  --profile-paths "<path1,path2,...>" \
  [--force] \
  --format text
```

Edit a profile (add paths and/or remove overlays by name; both repeatable, combinable in one call):
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --profile-op edit \
  --profile-name "<name>" \
  [--profile-add "<path>" ...] \
  [--profile-remove "<OverlayName>" ...] \
  --format text
```

Delete a profile:
```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --profile-op delete \
  --profile-name "<name>" \
  --format text
```

Add `--force` only for save/create when the user wants to overwrite an existing profile. Capture stdout, stderr, and the exit code.

If the exit code is not 0, present the error to the user. Exit 1 means a validation error (for example: the profile name is invalid, the profile already exists without `--force`, there is no current composition to capture, an overlay to remove is not in the profile, or the profile is unknown). Exit 3 means an I/O error writing the profile store. Suggest the matching fix and stop.

### M2. Present the mutation summary

On success, present the mutation summary from the output: the profile name, where it is stored, and its resulting overlay set (with resolved overlay names where available). For a delete, confirm the profile was removed.

### M3. Recompose prompt (only when the profile is active here)

After presenting the summary, read the active-profile signal from the same output: in `--format text` it is the one-line "Profile `<name>` is currently active in this project." note; in `--format json` it is the `active_in_project` boolean field. This signal reports whether the just-mutated profile's overlay set matches what is currently composed in this project's lock.

- If the signal indicates the profile is NOT active in this project, you are done. The mutation changed only the stored profile definition.
- If the signal indicates the profile IS active in this project, the project's composed artifacts no longer match the updated profile. Ask the user:
  "Profile `<name>` is active in this project. Recompose now?"
  - On approval: run the standard "Profile Activation Steps" above for `--profile <name>` (P1 dry-run preview -> P3 approval gate -> P4 write). Do not invent a separate write path; recomposition goes through the exact same preview-then-approve activation flow.
  - On decline: tell the user "No changes were made to this project's composed artifacts." and stop.

Never recompose automatically. Recomposition only ever happens through the standard activation flow above and only after the user explicitly approves it here.

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

Activate a saved profile (composes its overlay set into the project, preview first):
```
/system2:compose --profile backend-stack
```

Save the project's current composition as a profile:
```
/system2:compose --save-profile backend-stack
```

Create a profile from explicit overlay paths:
```
/system2:compose create backend-stack /path/to/overlay-a /path/to/overlay-b
```

Edit a profile, adding one overlay and removing another by name:
```
/system2:compose edit backend-stack --add /path/to/overlay-c --remove overlay-a
```

Delete a profile:
```
/system2:compose delete backend-stack
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
- Activating a profile reuses the same composition engine and preview-then-approve flow as a normal compose; it never writes without your approval. Profile mutations (`--save-profile`, `create`, `edit`, `delete`) change only the stored profile definition and never recompose the project on their own.
- To list profiles or inspect a single profile's overlay set, use `/system2:profile` — that is the read-only listing and inspection namespace. It never composes or mutates anything.
