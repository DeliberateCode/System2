---
name: doctor
description: Read-only drift and status check for composed System2 projects. Reports whether base plugin and overlay compositions are current, stale, or broken relative to the lock file.
argument-hint: ""
---

# /system2:doctor -- Composition Drift Check

You are executing the /system2:doctor skill. Follow these steps exactly.

## Purpose

This is a **read-only** diagnostic command. It inspects the project's `spec/overlay-manifest.lock` and reports whether the current composition is up to date, stale, or broken. It never writes any files.

## Steps

### 1. Determine paths

- Set `PROJECT_ROOT` to the current project root directory (the repository root where CLAUDE.md lives).
- Set `PLUGIN_ROOT` to `${CLAUDE_PLUGIN_ROOT}` (the System2 plugin installation directory).

### 2. Run the drift check

```
python3 "${PLUGIN_ROOT}/scripts/composer.py" \
  --base "${PLUGIN_ROOT}" \
  --project "${PROJECT_ROOT}" \
  --doctor \
  --format text
```

Capture stdout, stderr, and the exit code.

### 2b. Run the vendored-bundle integrity (tamper) check

The plugin runs a vendored copy of the compiler engine
(`${PLUGIN_ROOT}/scripts/_system2_compiler/`, recorded in `BUNDLE.json`). This
step verifies that vendored copy has not been hand-edited. It is **report-only**:
it never blocks composition.

```
python3 "${PLUGIN_ROOT}/scripts/_system2_compiler/_freshness.py"
```

Capture stdout and the exit code (0 = untampered, 1 = tampered). This check
recomputes the hash over the vendored subtree's bytes and compares it to the
`compiler_source_sha256` recorded in `BUNDLE.json`. It needs no compiler source —
it is the plugin half of the drift guard (internal-integrity / **tamper**). The
cross-repo **staleness** half — "is the vendored bundle still current vs the
compiler source?" — requires the compiler source and is the CI guard
(`System2-Compiler/tools/check_bundle_fresh.py`), not this command.

This step is independent of step 2; it does not change the `composer.py --doctor`
report.

### 3. Present the results

Present the output to the user. The report covers:

- **Status**: one of:
  - **Current**: base and overlays match the lock file. No action needed.
  - **Stale base**: the installed System2 plugin version differs from the version recorded in the lock file. The user should recompose.
  - **Stale overlay**: one or more overlay manifests or content files have changed since the last composition.
  - **Broken**: an overlay source path or project-local overlay copy is missing.
  - **No lock file**: no `spec/overlay-manifest.lock` exists. The project has not been composed with overlays.

- **System2 version**: installed vs. locked version.
- **CLAUDE.md composed**: whether the project CLAUDE.md has a `<!-- COMPOSED: ... -->` header.
- **Per-overlay status**: source path, local copy, manifest hash, and content hash compared to lock.
- **Findings**: specific details about any drift or breakage.

Then present the bundle-integrity findings from step 2b:

- **`bundle_freshness`**: the recorded `compiler_source_sha256`, `compiler_version`, and `generated_from` provenance of the vendored engine. Informational.
- **`bundle_tampered`**: `ok` when the vendored subtree matches its recorded hash; a **loud warning** when it does not — the vendored `_system2_compiler/` bundle was hand-edited and must be regenerated via the compiler's `tools/build_bundle.py`.

### 4. Suggest next steps

Based on the status:

- **Current**: Tell the user everything is up to date.
- **Stale base** or **Stale overlay**: Suggest running `/system2:compose --from-lock` to refresh the composition using the overlay paths recorded in the lock file.
- **Broken**: Tell the user which overlay source paths or local copies are missing. They need to fix the paths first, then run `/system2:compose --from-lock`.
- **No lock file**: Tell the user this project has not been composed with overlays. If they want to add overlays, use `/system2:compose <overlay_path>`.

For `bundle_tampered`:

- **`ok`**: No action needed; the vendored engine is intact.
- **tampered**: Warn the user that the vendored `_system2_compiler/` bundle has been hand-edited and may behave unpredictably. They should reinstall the plugin or regenerate the bundle via the compiler's `tools/build_bundle.py`. This is informational and does not block composition.

## Notes

- This command is strictly read-only. It does not modify any files.
- It does not re-validate overlay manifests or check for conflicts — those checks happen during `/system2:compose`.
- The exit code is 0 when status is "current", 1 otherwise. This is informational, not an error.
