# Changelog

All notable changes to System2 are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Replaced generated planning references throughout documentation, comments, docstrings,
  test names, diagnostics, and archived compiler notes with direct behavioral descriptions.
  Agent guidance now produces descriptive requirement, task, and eval headings, and a
  repository-wide structural check prevents generated specification identifiers from
  returning.

## [1.2.0] - 2026-08-04

### Added

- **Monorepo consolidation.** The `System2-Compiler` and `System2-UtilitySkills` repositories are being consolidated into this repo (compiler at `compiler/`, the utility skills merged into `plugin/skills/`), preserving full git history via subtree merges. The Claude Code install experience is unchanged (byte-identical composed output; the vendored bundle and `composer.py` shim are untouched).
- **Breaking (for standalone-marketplace `sys2` users): utility skills merged into `system2`.** The `sys2` plugin's three skills are now part of the `system2` plugin: `/sys2:codex` → `/system2:codex`, `/sys2:gemini` → `/system2:gemini`, `/sys2:stateless-loop` → `/system2:stateless-loop`. The planned two-plugin marketplace hosting and its version-ripple note are superseded and never shipped. Migration: uninstall the old `sys2` plugin, install `system2` from this marketplace. sys2 0.1.x history: versions 0.1.0–0.1.4 shipped from the standalone System2-UtilitySkills repository; that history is preserved in this repo's imported subtree commits (`git log --follow plugin/skills/codex/SKILL.md`) and in the old repository until its archival.
- **Codex install channel.** A generated Codex plugin at `distributions/codex/` (installable via `codex plugin marketplace add DeliberateCode/System2`, resolved through the root `.agents/plugins/marketplace.json`). Agents are lowered to role skills plus an orchestrator skill (adapted; no native subagent isolation). Safety gates (`block-dangerous`, `protect-sensitive`, `enforce-lease`) are Node command hooks ported from the same proven matcher constants as the Pi backend; they are **advisory until the user reviews and trusts the hooks via `/hooks`**, then adapted (`enforced: false`, `gated: true` at rest, never `native`) with partial tool coverage (shell + apply_patch/Edit/Write only, not WebSearch/other). A three-surface honesty invariant (manifest `description`, README preamble, lock FIDELITY banner) is machine-checked on every change.
- **Pi install channel (pending publish).** A generated npm package at `distributions/pi/` (`@deliberatecode/pi-system2`) carrying a native safety gate, the 13-role `/delegate` orchestrator, and the System2 skills and prompts; no install scripts and no dependencies. **Not yet published:** the install command is deliberately withheld from user docs until the package is published (the scope was claimed and secured 2026-07-04; publish is Phase 3, user-gated), so the docs cannot direct users at an unservable name.
- **Repo docs for the three channels.** `README.md` now documents all three install channels with per-channel one-line commands (the Pi command withheld with justification), the utility-skills migration note, the clone-and-run posture for overlays on non-Claude harnesses, and per-channel backout notes. A new local runbook `docs/runbooks/codex-smoke.md` covers the one path CI does not exercise (Codex marketplace resolution + hook-trust canary).

### Changed

- **Version bump: 1.1.1 → 1.2.0.** Absorbing the three utility skills into the `system2` plugin is a minor version bump per semver (new user-facing functionality, backward compatible for existing `system2` installs).

### Fixed

Everything below predates this release (present on the prior 1.1.1 `system2` plugin
and its test suite) and is fixed by this version. Bugs found and fixed *within* the
development of this release itself (in code that has never shipped — the new Codex
and Pi channels, the namespace merge) are not listed separately here; they simply
don't exist in what ships as 1.2.0.

- The `profile` skill's description contained invalid YAML (an unquoted `: ` inside
  the frontmatter value), which a strict YAML parser rejects outright — silently
  breaking that skill on any harness that parses frontmatter strictly.
- `evals/test_composer.py`'s `test_backup_and_restore_on_failure` gave a
  false pass/fail signal when the test suite is run as root, since root bypasses the
  POSIX permission check the test relies on to force a write failure; it now skips
  under root instead of asserting a premise that doesn't hold there.
- Bare `pytest` at the repo root failed to collect at all (`import file mismatch`)
  because the `anti-slop-sequence` MAINT-eval fixtures deliberately reuse the
  filename `test_calculator.py` across four snapshot directories (by design, so the
  eval can diff the same logical file's evolution) — a pattern pytest's default
  collection can't disambiguate. Added `pytest.ini` scoping collection away from
  fixture directories.

## [1.1.1] - 2026-06-26

### Changed

- **Vendored compiler bundle restructured to the `system2_compiler/` namespace.** The product modules in `plugin/scripts/_system2_compiler/` now live under a nested `system2_compiler/` package (previously top-level `ir/`, `backends/`, `cli.py`, `plugin_adapter.py`), tracking the upstream compiler's pre-publish namespace refactor. The `composer.py` shim's bundle entry was updated to `from system2_compiler import plugin_adapter`. **Composer output is byte-identical** — proven by the bundle-equivalence gate and the plugin's own 55-test suite, which pass against both the bundle and the frozen `composer.py.preflip` baseline. The one-commit backout (`cp composer.py.preflip composer.py && rm -rf _system2_compiler/`) is unchanged.

### Fixed

- **Bundle regeneration can no longer silently drop the tamper check.** `_freshness.py` (the plugin-side bundle integrity check surfaced via `system2:doctor`) is now a re-emitted bundle *companion* with a canonical source in the compiler, so regenerating the vendored bundle always restores it instead of leaving it behind.

## [1.1.0] - 2026-06-20

### Added

- **Overlay Profiles**: named, reusable, user-level sets of overlays. A domain-specific set of overlays can now be activated by name instead of by remembering and retyping overlay paths. Profiles are stored at `~/.system2/profiles.json` (user-level, shared across projects) and are independent of the per-project `.system2/overlays.json`.
- `/system2:compose --profile <name>` activates a profile, composing its overlay set through the existing composition engine. Includes the standard dry-run preview then approval flow, and produces artifacts byte-identical to composing those overlay paths directly.
- `/system2:compose --save-profile <name>` captures a project's currently composed overlay set as a profile, with no paths typed.
- `/system2:compose create <name> <pathA> <pathB> …` defines a profile explicitly from overlay paths.
- `/system2:compose edit <name> --add <path> --remove <OverlayName>` incrementally adds or removes overlays; flags are repeatable and combinable in a single call.
- `/system2:compose delete <name>` removes a profile.
- `/system2:profile list` (and `/system2:profile list --profile <name>`) read-only inspection namespace for listing and examining profiles. It is strictly read-only and never mutates a profile or composes a project.
- Hard-fail-on-stale activation: activating a profile whose overlay path is missing or invalid refuses the entire activation, names the offending path, and writes nothing.
- When a mutation targets the profile currently active in a project, the compose skill prompts whether to recompose now; recomposition is never automatic.
- New `profiles.py` module (stdlib-only) implementing the profile store, path resolution, read-only CLI, and importable mutation API; new read-only `profile` `SKILL.md`; new profile flags in `composer.py` (`--profile`, `--save-profile`, `--profile-op`, `--profile-name`, `--profile-paths`, `--profile-add`, `--profile-remove`, `--force`); and additive profile sections in the compose `SKILL.md`. Existing compose, `--from-lock`, `--uninstall`, and `.system2/overlays.json` behavior is unchanged.
- New unit suite `evals/test_profiles.py` and integration suite `evals/test_profile_activation.py` covering profile storage, resolution, mutation, activation byte-identity, hard-fail-on-stale, and read-only/independence guarantees.

## [1.0.2] - 2026-06-12

### Added

- `/system2:compose --uninstall <name>` for removing a single named overlay from a composed project. Recomposes with remaining overlays (multi-overlay case) or reverts to base System2 (last-overlay case). Includes dry-run preview, atomic rollback on failure, and stale artifact cleanup.
- Four new internal functions in `composer.py`: `_read_base_template`, `_compute_stale_artifacts`, `_uninstall_last_overlay`, `_uninstall`.
- Uninstall argument handling and UX flow section in compose `SKILL.md`.
- 38 tests in `evals/test_uninstall.py` covering argument validation, multi-overlay uninstall, last-overlay uninstall, rollback, security (path traversal rejection), and output format compliance.

## [1.0.1] - 2026-06-07

### Changed

- `executor`: added an explicit prohibition against replacing behavioral explanations with generated specification identifiers in code, comments, docstrings, tests, or documentation. Durable history belongs in version control.
- `code-reviewer`: minimality checks now flag generated identifiers leaking into code or documentation. Simplification mode treats those references as removable comments.

## [1.0.0] - 2026-06-05

### Added

- `/system2:compose` skill for opt-in overlay composition. The command validates overlay manifests, previews applied/deferred contributions, detects structural conflicts and semantic tensions, asks for approval, and writes composed project-local artifacts.
- `/system2:compose --from-lock` mode for recomposing using overlay source paths recorded in `spec/overlay-manifest.lock`, enabling graceful updates after plugin or overlay changes without requiring users to remember overlay paths.
- `/system2:doctor` skill for read-only drift and status checking of composed projects. Reports whether base plugin and overlay compositions are current, stale, or broken relative to the lock file. Detects version drift, manifest changes, source content changes, local copy mutations, and missing files.
- Overlay manifest schema at `plugin/schemas/overlay.schema.json` and anchor map at `plugin/schemas/anchor-map.json`.
- Overlay composition engine at `plugin/scripts/composer.py` with stdlib-only manifest validation, deterministic additive ordering, conflict detection, prompt-injection warnings, content copying, lock generation, and atomic write/rollback behavior.
- Shared hook security helper at `plugin/scripts/hook_security.py`, reused by evals and overlay hook validation.
- Overlay eval coverage for schema presence, anchor-map integrity, dry-run composition, composed `CLAUDE.md` preservation, skipped unknown anchors, conflict detection, deterministic ordering, semantic tensions, hook security, rollback cleanup, and drift checking.
- Test overlay fixtures and unit tests for lock generation, content copying, output writing, idempotency, CLI behavior, and doctor drift detection.

### Changed

- Plugin version is now `1.0.0`.
- Installed command surface now includes `/system2:compose`, `/system2:doctor`, and `/system2:compose --from-lock` in addition to `/system2:init`.
- Base System2 behavior remains unchanged unless `/system2:compose` is explicitly invoked. `/system2:init` remains base-only and the 13 pipeline agents, hooks, allowlists, and orchestrator template are preserved.

## [0.4.1] - 2026-04-11

### Fixed

- `repo-governor` agent template for `.claude/settings.json` now uses correct `Read(pattern)`/`Edit(pattern)` syntax for `permissions.deny` rules. Bare glob patterns were silently ignored or produced warnings at startup.

## [0.4.0] - 2026-04-11

Anti-additive bias and simplification pass across agents, hooks, and evals to reduce generated slop.

### Added

- Simplification step in post-execution workflow: `code-reviewer` runs in a new simplification mode when diffs exceed 50 lines or touch more than 2 files, identifying removable abstractions, wrappers, comments, and dead code.
- Slop catalog integration: `code-reviewer` reads `.claude/slop-catalog.md` for project-specific anti-patterns and suggests new entries; `executor` treats catalog entries as local convention.
- Write-lease lifecycle in orchestrator: per-task file path constraints written to `.task-lease.regex` before execution, enforced by `validate-file-paths.py`, cleaned up after completion.
- Change budget reporting: `change-budget-reporter.py` SubagentStop hook reads `.task-budget.json` and reports surface-area metrics (files changed, symbols added, lines delta).
- Module boundary enforcement: `boundary-check.py` PreToolUse hook validates imports against `spec/module-boundaries.json`.
- Boundary artifact outputs for `design-architect`: emits `spec/interfaces.json` (public exports per module) and `spec/module-boundaries.json` (allowed/forbidden import paths) alongside `spec/design.md`.
- Anti-slop sequence eval suite (`evals/fixtures/anti-slop-sequence/`): 4-task progressive coding sequence with golden files testing whether the executor avoids unnecessary abstractions across sequential changes.
- `` eval validating all allowlist `.regex` files contain compilable patterns.
- Stale task-lease/budget file cleanup during session bootstrap.

### Changed

- `executor`: added anti-additive bias rules (prefer deletion over addition, justify every new symbol, removal pass after green tests), assumptions-first protocol for non-trivial tasks.
- `code-reviewer`: expanded review checklist with minimality and adaptation cost criteria; surface-area delta reporting; future-change probe now covers two requirements instead of one.
- `design-architect`: new required sections in `spec/design.md` — "Simplicity Budget" (caps on new modules/interfaces, mandatory do-nothing alternative) and "Rejected Abstractions".
- `spec-coordinator`: new required section "Minimal Change Intent" in `spec/context.md`.
- `task-planner`: tasks now include `change_budget` (max files, max new symbols, interface policy) and `write_lease` (file path regex patterns) fields.
- Delegation contract in orchestrator CLAUDE.md: added "Non-goals" and "Change shape" fields.
- Hook utilities (`_hook_utils.py`): trimmed verbose docstrings, removed unused `check_command_exists`, `get_tool_input()` now returns None on parse failure.
- `dangerous-command-blocker.py`, `sensitive-file-protector.py`, `auto-formatter.py`, `type-checker.py`, `tts-notify.py`, `validate-file-paths.py`: reduced to minimal implementations, removed narrative docstrings and dead code.

### Removed

- `dangerous-commands-allowlist.regex` and `sensitive-patterns.regex` — patterns now embedded directly in their respective hooks.
- `example-hooks-config.md` — removed in favor of `HOOKS.md`.
- Unused helper functions and verbose docstring boilerplate across all hooks.

## [0.3.0] - 2026-03-15

Add a bounded corrective path for non-local regressions while preserving the existing fast path for routine implementation.

### Added

- Maintenance / Regression Loop in `CLAUDE.md` with local-vs-non-local failure classification, regression ledger recording, amendment-vs-invalidation routing, and a 3-cycle corrective iteration cap.
- Corrective mode for `requirements-engineer` — bimodal operation (baseline for initial spec work, corrective for post-verification failure analysis) producing bounded requirement deltas with design-impact classification.
- Maintenance execution rules for `executor` now keep corrective cycles within scope and use approved behavioral statements directly as authority for test updates.
- Structured verification summary for `test-engineer` — baseline/regressed/flaky/changed-file breakdown required in completion output, plus a test mutation policy with edit classification and assertion-weakening guards.
- Future-change probe for `code-reviewer` — assesses whether each diff makes plausible next changes easier, neutral, or harder, and identifies new rigidities.
- Maintenance evals for `eval-engineer` — sequential change-sequence authoring with metrics for regression-free completion, diff size growth, interface churn, and corrective cycle count.
- `spec/regression-ledger.md` as a formal artifact with `allowlists/regression-ledger.regex` and tracked in `agent_allowlist_bindings.json` as an unbound allowlist.
- `spec/regression-ledger.md` listed in `design-architect` inputs for context when refreshing design after corrective requirements.
- `` — validates all allowlist `.regex` files contain compilable regex patterns.
- `Maintenance / Regression Loop` added to `template_sections.json` required headings.

### Changed

- `requirements-engineer` description and inputs updated to reflect bimodal operation.
- `test-engineer` completion summary now requires the structured verification summary.
- `allowlist_inventory.json` expected count updated from 12 to 13.
- `skills/init/SKILL.md` template synced with updated `CLAUDE.md`.

## [0.2.0] - 2026-02-16

Remove Roo Code support and convert to Claude Code plugin with marketplace distribution.

### Added

- Plugin manifest (`.claude-plugin/plugin.json`) declaring name, version, author, and description.
- Marketplace manifest (`.claude-plugin/marketplace.json`) for distribution via `/plugin marketplace`.
- `/system2:init` skill that writes the orchestrator CLAUDE.md into the consuming project.

### Changed

- All System2 files relocated from `.claude/` to plugin-standard directories (`agents/`, `hooks/`, `allowlists/`, `skills/`).
- Agent frontmatter hook paths migrated from `$CLAUDE_PROJECT_DIR/.claude/hooks/` to `${CLAUDE_PLUGIN_ROOT}/hooks/` (and likewise for allowlists).
- `README.md` rewritten for plugin installation workflow; manual copy instructions removed.
- `CLAUDE.md` updated to remove `.claude/agents/` path references.
- Consolidated `README.md` and `README-CLAUDE.md` into a single README.

### Removed (BREAKING)

- **Installation method changed from manual file copy to the Claude Code plugin system.** Users must reinstall via `/plugin marketplace add` and `/plugin install`.
- **Roo Code platform support has been fully removed.** Roo Code users should pin to the `v0.1.0` tag for continued support, or maintain a fork.
- 14 Roo Code mode definition files (`roo/01-orchestrator-system2.yml` through `roo/14-code-reviewer.yml`).
- `roo/system2-pack.yml` -- combined Roo Code mode pack.
- `.roo/commands/update-system2.md` -- Roo Code slash command.
- `README-ROO.md` -- Roo Code documentation.
- `README-CLAUDE.md` -- consolidated into `README.md`.
- `manifest.json` -- declarative file list for the update script.
- `.system2/` directory -- update infrastructure (backup, lock, log, version cache).
- `scripts/` directory -- `update-system2.sh`, `generate-manifest.py`, `update-system2-yaml.py`, and Claude hook scripts (moved to `hooks/`).
- `tests/` directory -- shell and Python test suites for removed infrastructure.
- `/update-system2` slash command (replaced by native plugin update mechanism).

## [0.1.0] - 2026-02-01

### Added

- `/update-system2` slash command for both Claude Code and Roo Code platforms.
- `scripts/update-system2.sh` -- manifest-driven update script with backup, validation, lock file, and self-update detection.
  - Flags: `--dry-run`, `--force`, `--scope project|global|both`, `--repo-url <url>`, `--branch <branch>`.
  - Exit codes: 0 (success/up-to-date), 1 (general error), 2 (network failure), 3 (validation failure), 4 (missing dependency).
- `scripts/update-system2-yaml.py` -- YAML merge helper for Roo Code mode updates. Preserves non-System2 custom modes during merge.
- `manifest.json` -- declarative file list with platform, scope, and validation metadata consumed by the update script.
- `VERSION` file (0.1.0) used for upstream version comparison.
- Timestamped backups in `.system2-backup/` before any file overwrites.
- Update audit log written to `.system2/update.log`.
- Safety and quality hooks in `scripts/claude-hooks/`: dangerous-command-blocker, sensitive-file-protector, auto-formatter, type-checker, tts-notify, validate-file-paths.
- 13 Claude Code subagent definitions in `.claude/agents/` with file-access allowlists.
- 14 Roo Code mode definitions in `roo/system2-pack.yml`.
- Orchestrator instructions in `CLAUDE.md` with session bootstrap, gate workflow, and post-execution agent chaining.
- Platform-specific documentation: `README-CLAUDE.md`, `README-ROO.md`.
