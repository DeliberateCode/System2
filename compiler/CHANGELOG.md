# Changelog

All notable changes to the System2 Compiler are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

**Phases 0–5** of the System2 Compiler — **all complete**. The compiler composes
System2 core + overlays + profiles into a harness-neutral IR (`System2Graph`) and
lowers it onto a target via a capability-typed backend with a full per-target
lifecycle. It is now **feature-complete across Claude Code and Pi** (two
backends with `compile` + `uninstall` + `doctor` + `from-lock` parity, plus
harness-neutral profile management), and the **live System2 plugin has converged**
onto a vendored, stdlib-only copy of the compiler.

After the Phase 5 convergence flip, the plugin's `composer.py` is a thin shim over
the vendored `claude-code` bundle and there is **no** change to the Claude
end-user experience: byte-identical output, the identical CLI contract the skills
parse, zero runtime dependency, a `SYSTEM2_USE_BUNDLE=0` escape hatch, and a
one-commit backout (`composer.py.preflip`). The Pi backend remains purely
additive (reached only via `--target pi`).

> Versioning note: `pyproject.toml` declares `version = "0.1.0"`. Distribution is
> **private/internal** (no public PyPI release), so this entry stays under
> `[Unreleased]`; promote to a tagged `[0.1.0]` header if/when a release is cut.

### Changed — pre-publish hardening

- **Namespace refactor.** The product modules moved from top-level (`ir/`,
  `backends/`, `cli.py`, `plugin_adapter.py`) into a single `system2_compiler/`
  package, so an installed wheel no longer drops collision-prone top-level modules
  into a shared `site-packages`. The console entry point is now
  `system2 = system2_compiler.cli:main`. The vendored plugin bundle nests the
  package one level deeper (`_system2_compiler/system2_compiler/…`) and the
  `composer.py` shim imports `from system2_compiler import plugin_adapter`. Composer
  output stays **byte-identical** — goldens (both drivers), the bundle-equivalence
  gate, and the plugin's own suite all prove it.
- **Packaging.** `pyproject.toml` ships the `system2_compiler` package with **zero
  runtime dependencies** (capability JSONs as package data); a clean-venv install
  exposes the `system2` console script and composes against a plugin base.
  Distribution is private/internal — see the README "Installation (private /
  internal)" section.

### Fixed — pre-publish hardening

- **`tools/build_bundle.py` no longer silently drops `_freshness.py` on regen.** The
  plugin-side bundle tamper checker is now a non-hashed *companion*
  (`_BUNDLE_COMPANIONS`) with a canonical source at `tools/_freshness.py`,
  re-emitted on every build — a regen `rmtree` previously left it behind. Added a
  regen guard test (exact bundle file set + companion byte-identity + hash
  exclusion).

### Added

#### Phase 5 — Convergence & Lifecycle Parity

- **Grown `Backend` lifecycle (`backends/base.py`).** The contract grows from a
  single `emit` into a four-method **lifecycle**: `uninstall(project_path,
  overlay_name, *, dry_run)`, `doctor(project_path)`, and `recompose_from_lock(ir,
  project_path, *, dry_run)`, plus the `lock_path` / `read_lock_overlay_sources`
  lock helpers — so Pi reaches feature-completeness alongside Claude, each
  owning its own artifact lifecycle. Two neutral, target-agnostic result
  dataclasses (`UninstallResult`, `DoctorReport`) keep all backends and the CLI on
  one shape. A backend still reads only its **own** target lock + artifacts under
  `project_path` (never manifests / anchor map / profiles / schema directly).
- **Per-target `uninstall`.** Recompose the remaining recorded sources → `emit`;
  on the *last* overlay, revert to base (Claude) or **remove the generated tree**
  (Pi: `.pi/extensions/system2.ts`, `.pi/SYSTEM.md`, `AGENTS.md`, `.pi/prompts/*`,
  `.pi/skills/*`, `system2.pi.lock.json`) and clean empty dirs, with atomic
  backup/restore.
- **Per-target `doctor`.** A read-only drift/status check returning `status ∈
  {current, stale_base, stale_overlay, broken, no_lock}`. Claude ports the oracle's
  `drift_check` byte-for-byte (exit 0 iff `current`); Pi additionally shells
  the **real validator** (the Pi extension load) and surface a **LOUD
  `validator_unavailable`** finding when it is absent — never a silent `current`.
- **Per-target `from-lock` / `recompose_from_lock`.** Reads the lock's recorded
  overlay sources, recomposes via `ir.compose`, and re-emits; refuses on
  missing/empty sources with the parallel message.
- **Additive `overlay_sources[]` lock key (Pi).** The Pi lock gains
  an additive `overlay_sources[]` key (appended last, mirroring the Claude lock's
  additive `degradation_report`) so `uninstall` / `from-lock` can recompose the
  right set. It is byte-additive (a new trailing key) and re-baselines the Pi
  golden exactly once; no existing key's bytes shift.
- **Full CLI parity (`cli.py`).** The CLI becomes a verb dispatcher:
  `compile` · `uninstall` · `doctor` · `from-lock` · `profile`. The `claude-code`
  path of every verb reproduces the frozen oracle's EXACT arg names, exit codes,
  stdout/stderr report bodies, and JSON envelopes (the contract the plugin skills
  parse). A leading `--target` (or no subcommand) still routes to `compile` for
  back-compat with the Phase-0..4 invocation. New compile sub-flags: `--from-lock`
  (xor `--profile` / `--overlays`) and `--allow-injection` (proceed past injection
  warnings in write mode, else exit 4).
- **`profile` verb (harness-neutral).** `list` / `inspect NAME` / `save NAME` /
  `create NAME --paths P` / `edit NAME [--add P]… [--remove OVERLAY]…` /
  `delete NAME`. No `--target` — profiles are harness-neutral (an activated profile
  feeds compose for ANY target) and mutations write only
  `~/.system2/profiles.json`. The mutation sub-flag matrix and refusal text are
  ported verbatim from the oracle (byte-identical for the plugin's dispatch);
  `--dry-run` is rejected for mutations.
- **Deterministic bundler (`tools/build_bundle.py`).** Emits the vendored,
  stdlib-only `_system2_compiler/` subtree — a **pure copy** (no import rewriting)
  of `ir/` + `backends/` + `cli.py` plus a `plugin_adapter.py` entry — and a
  `BUNDLE.json` manifest recording `compiler_source_sha256` (a sha256 over the
  sorted `(relpath, bytes)` of the copied source — the drift anchor),
  `compiler_version`, `generated_from` (`System2-Compiler@<git-rev>`), and
  `bundled_at` (ISO; **excluded** from the hash, so a re-bundle of identical source
  is hash-stable). Byte-identical Claude output is guaranteed by construction (the
  goldens then prove it). Emit-only; writes only under `--dest` (never into
  `System2/plugin/` itself).
- **Vendored bundle + thin-shim convergence (the plugin flip).** The live plugin's
  `scripts/composer.py` becomes a thin shim: as an imported module it execs the
  preserved-verbatim `composer.py.preflip` source into its own namespace (every
  pre-flip symbol present, byte-for-byte the pre-flip behavior — the plugin's own
  `System2/evals/` suite is unaffected); as the CLI it delegates to the vendored
  bundle's `plugin_adapter.main_composer_contract`. `plugin_adapter.py` encodes the
  `composer.py` flag contract once with `--target` **hard-pinned to `claude-code`**
  (the plugin is Claude-only) and delegates to the bundle's `cli.main`, re-using its
  one contract-proven dispatch (the adapter and `cli.py` cannot drift). The switch
  defaults ON (the bundle is the default engine); `SYSTEM2_USE_BUNDLE=0` is the
  escape hatch to the frozen pre-flip engine for in-place A/B checks; the backout is
  one commit (`cp composer.py.preflip composer.py && rm -rf _system2_compiler/`).
- **Bundle drift guard (tamper + staleness).** Two complementary checks, both keyed
  off `compiler_source_sha256`: the **plugin-side tamper check**
  (`_system2_compiler/_freshness.py`, ships with the bundle) recomputes the hash
  over the vendored subtree and compares it to the recorded value, surfacing a
  `bundle_freshness` provenance line plus a LOUD `bundle_tampered` finding through
  `system2:doctor` (report-only; runs with no compiler source present); the
  **CI staleness guard** (`tools/check_bundle_fresh.py`) regenerates the bundle from
  the *current* compiler source and fails non-zero with
  `vendored bundle is stale: regenerate via tools/build_bundle.py` on a stale or
  hand-edited bundle — the machine-enforced merge gate.
- **Phase 5 test surface.** The CLI-contract goldens pin the claude-code lifecycle
  verbs (uninstall / doctor / from-lock / profile) to the frozen oracle's exit codes
  and stdout/stderr; the bundle-equivalence gate
  (`System2-Compiler/evals/test_bundle_equivalence.py`) and the plugin's own evals
  on the bundle (`System2/evals/test_plugin_evals_on_bundle.py`) prove the flip is
  byte-identical; the drift-guard tests exercise both the tamper and staleness legs
  (with negative controls).

#### Phase 4 — Pi backend

- **`pi` backend (`backends/pi.py`).** A second backend, and the first
  **MIXED-status** one, lowering the same `System2Graph` onto a Pi **extension**
  plus Pi context / skill / prompt markdown and a standalone degradation report.
  It honors the same `Backend.emit(ir, project_path)` contract and the same module
  boundary as `claude_code` (imports only `ir.graph`,
  `backends._degradation`, and stdlib; reads its own
  `backends/capabilities/pi.json`). It renders from the **structured** IR only and
  never consumes `base_template` / `OverlayInput`. Output is a pure function of the
  IR plus backend-owned constants (no timestamps; LF endings, single trailing
  newline): identical IR → byte-identical artifacts. `emit` writes only under
  `project_path` (never `~/.pi`). The emitted tree:
  - `.pi/extensions/system2.ts` — the generated TypeScript gate.
  - `.pi/SYSTEM.md` — the orchestrator context + honest MIXED enforcement summary.
  - `AGENTS.md` — the small auto-loaded project context.
  - `.pi/prompts/orchestrator.md` + `.pi/prompts/role-<role>.md` (×13) — the
    orchestrator and 13 role prompt templates (persona, gate-role, write-scope,
    model-hint, per-role native/adapted/advisory capability notes).
  - `.pi/skills/system2-{init,compose,doctor}/SKILL.md` (×3) — the three skills.
  - `system2.pi.lock.json` — the standalone MIXED degradation report.
- **Native TypeScript gate (`.pi/extensions/system2.ts`).** Pi has **no** built-in
  permission system, so the generated extension's `on("tool_call")` handler —
  which fires *before* a tool runs and can `return { block: true, reason }` — **is**
  the gate. This makes `enforce-lease` / `block-dangerous` / `protect-sensitive`
  genuinely **native** (deterministic pre-execution blocks), rather than a merely
  *adapted* gate. An `on("agent_end")` handler reports
  the change budget (`adapted`, not a block); a `before_agent_start` handler injects
  the orchestrator context; and a bounded `/delegate` command
  (`pi.registerCommand`) switches the active role over the 13 roles. The compiler
  emits the `.ts` as **text** — it never runs or transpiles TS (node / pi live only
  in tests). Every IR-derived string is escaped for a TS string literal (no raw
  splice); the dangerous-command and sensitive-path sets are backend-owned
  constants, emitted sorted.
- **`write_scope` IR-enrichment from the Claude allowlists (`ir/build.py`).** Each
  `Role.write_scope` is now sourced **read-only** from the mapped Claude per-agent
  `.regex` path allowlist (the same allowlists `validate-file-paths.py` uses, e.g.
  `executor.regex`). This makes Pi's `enforce-lease` a genuinely-scoped native
  lease rather than a wired-but-unscoped gate. A role with no dedicated allowlist keeps an empty scope
  (no broad fallback that would over-permit), reported loudly. The `claude-code`
  backend never reads `write_scope`, so its emitted bytes are unchanged.
- **Pi capability descriptor (`backends/capabilities/pi.json`).** The first
  **MIXED** descriptor: `enforce-lease` / `block-dangerous` / `protect-sensitive`
  are `native`; `budget` is `adapted`; `format` / `typecheck` are `advisory`. Each
  `mechanism` string is honest about how (and how far) the capability is enforced.
- **Pi degradation report (`system2.pi.lock.json`).** The Pi analogue of the Claude
  lock's `degradation_report`, emitted as its own artifact (no Claude lock to append
  to; keeps the Claude lock byte-untouched). Per capability: `status`, `mechanism`,
  and the derived `enforced` / `gated` flags; plus `backend: "pi"`,
  `pi_version_assumed`, `enforcement: "extension-native-gates"`,
  `subagent_isolation` (honestly `adapted` — `/delegate` is an in-session
  role-switch, not a claimed isolated sub-session), and a top-level `FIDELITY`
  banner making the mixed story explicit. Completeness is asserted — a capability in
  the IR but absent from the descriptor raises (no silent drop).
- **Shared degradation helper (`backends/_degradation.py`, PG6).** A backend-agnostic,
  stdlib-only, descriptor-driven helper that lifts the per-capability report-record
  assembly and the total `status → (enforced, gated)` flag rule out of each
  backend's own builder so all backends share one source of
  truth. It does no I/O and imports no `ir/*` (a backend hands it
  `ir.capabilities.by_agent` as plain data plus its own parsed descriptor). It is
  **byte-preserving**: each backend keeps its own envelope and `fields` selection,
  so the Claude lock `degradation_report` is byte-identical across the refactor,
  while Pi's MIXED status (native + non-native in one backend) is correct by
  construction.
- **`--target pi` (`cli.py`).** The CLI now accepts `pi` in addition to
  `claude-code`; the front-end refusal path stays backend-independent
  (a refused composition emits nothing for any target).
- **Proven-blocking test (`evals/test_pi_proven_blocking.py`).** A node harness
  loads the **emitted** `.pi/extensions/system2.ts` through Pi's own
  `discoverAndLoadExtensions`, captures the registered `on("tool_call")` handler,
  and fires **synthetic** `tool_call` events at it (no LLM in the loop): an
  off-`write_scope` write, a dangerous `rm -rf /`, and a `.env` read each return
  `{ block: true }`; an in-scope `src/...` write, a benign `ls -la`, and an ordinary
  `README.md` read are each **not** blocked (the negative control proving the gate
  discriminates rather than blocking everything). It also exercises the bounded
  `/delegate` dispatcher (valid role accepted, unknown role rejected) and confirms
  the `subagent_isolation` aspect is honestly `adapted`. node/pi present → MUST run
  and pass; absent → LOUD SKIP under a hermetic temp `HOME`/`.pi` (the real `~/.pi`
  is asserted untouched).
- **Pi test suite.** `evals/test_pi_goldens.py` (structure / determinism / emit
  purity + the emitted extension loads under `discoverAndLoadExtensions` with
  `errors: []`), `evals/test_pi_proven_blocking.py` (above), and
  `evals/test_pi_degradation.py` (the MIXED report, descriptor parity, and the
  shared-helper byte-identity gate).

#### Folded hardening

- **Vendored-pin drift guard (`evals/test_vendored_pin.py`, F-03).** Pins the
  vendored `ir/profiles.py` / `ir/_hook_security.py` byte-for-byte against their
  plugin originals (`System2/plugin/scripts/`), failing loudly ("vendored copy
  drifted / re-vendor required") on any non-sanctioned diff, with a negative
  control proving the guard has teeth.
- **Eval-breadth tests (`evals/test_breadth.py`).** Asserts argument-ordering
  determinism (the requirement — composition is independent of `--overlays` order) and
  anchor-exclusion (the requirement/027 — a contribution to a non-existent
  `(agent, anchor)` is excluded exactly as the oracle excludes it) **directly**,
  not only transitively via the golden byte-diff.

#### Phases 0–2 (prior, unchanged)

- **Golden-freeze harness (Phase 0).** Output-level golden suite under `evals/`
  that snapshots the current Claude projection across a representative matrix
  (`core`, `core+overlay`, `core+overlay+profile`, `core+conflict`,
  `core+tension`). The plugin's live `composer.py` is designated the frozen
  reference oracle, located and **hash-pinned** (including its `profiles.py` and
  `hook_security.py` dependencies); the oracle is invoked only as an isolated
  subprocess. A matrix-completeness check fails if any declared cell lacks a
  snapshot. Snapshots are never auto-rewritten; re-baselining requires an explicit
  `--rebaseline` flag.
- **Comparison-policy parameter.** Per-artifact-class comparison policy
  (`CLAUDE.md`, `agents`, `lock`, `warnings`) loaded from `comparison_policy.json`,
  defaulting to `byte-identical`. Selecting `semantic-equivalent` without a
  recorded justification is rejected; this cycle ships every class
  `byte-identical`.
- **Harness-neutral IR (`ir/`, Phase 1).** `ir.compose(...) -> CompileResult`
  builds a `System2Graph` from core + overlays + an optional profile: the 13
  roles, the Gate 0 → Gate 5 graph, the delegation contract, post-execution and
  maintenance policy, the `spec/` artifact set, topologically-ordered overlay
  contributions, and the active profile. The front-end lifts the existing
  contribution indexing, topological sort, conflict detection, manifest
  validation, injection scan, and profile resolution without semantic change.
- **Backend interface (`backends/base.py`, Phase 1).** `Backend.emit(ir,
  project_path) -> written_files` as the sole lowering entry point. The IR is the
  only interface between the front-end and any backend.
- **`claude-code` backend (`backends/claude_code.py`, Phase 1).** Lowers the IR to
  the artifacts the oracle writes — `CLAUDE.md`, `spec/overlay-manifest.lock`,
  overlay-contributed auxiliary agents (`.claude/agents/<aux>.md`), and overlay
  content copies under `.system2/overlays/<name>/` — with atomic write and
  backup/restore. Preserves the lock shape, key ordering, `json.dumps(indent=2)`
  formatting, content fingerprint, and idempotent `composed_at` reuse.
- **`system2 compile` CLI (`cli.py`, Phase 1).** Additive, opt-in command:
  `--target`, `--profile` xor `--overlays`, `--base`, `--project`, `--dry-run`,
  `--allow-newer-schema`, and `--format text|json`. Renders the stderr warning
  stream byte-identically to the oracle and classifies refusal exit codes
  (1 validation, 2 structural conflict, 3 I/O).
- **IR-level anchors (`ir/anchors.py`, Phase 2).** Overlay anchor contributions
  are resolved against the IR agent definition by `(agent, anchor_name)` identity
  rather than by literal-heading string matching, representing every anchor in
  `anchor-map.json` for all 13 agents and excluding contributions to non-existent
  anchors exactly as the oracle does.
- **Intent-capability model (`ir/capabilities.py`, Phase 2).** Agents declare
  intent capabilities (`enforce-lease`, `block-dangerous`, `protect-sensitive`,
  `format`, `typecheck`, `budget`) plus role attributes (`write-scope`,
  `model-hint`, `gate-role`) instead of Claude mechanisms (`tools`, `hooks`,
  `permissionMode`). Blocking-semantics descriptors capture, per capability,
  whether the action is actually blocked, so fidelity can be reported honestly.
- **Per-backend capability descriptor (`backends/capabilities/claude_code.json`,
  Phase 2).** Maps every capability to one of `native | adapted | advisory |
  unsupported`; for `claude-code` all enforced safety capabilities are `native`.
- **Degradation report (Phase 2).** The active backend appends a per-capability
  `degradation_report` to the lock file enumerating every IR capability with its
  status — the primary, machine-readable observability surface for which safety
  capabilities are enforced versus advisory on a harness.
- **Eval test suite (`evals/test_*.py`).** Stdlib `unittest` (also `pytest`-runnable)
  coverage for path safety, atomic-write/restore, dry-run, refusal text, module
  boundaries, the mechanism → capability mapping, degradation-report completeness
  and no-silent-drop, lowering invariance, and unknown-capability warnings.

### Changed

- **Compose and render are split (Phase 1).** What was a single `composer.py` with
  one hardcoded Claude projection is now a harness-neutral `compose` front-end and
  a separate `claude-code` `emit` backend, joined only by the `System2Graph` IR.
  This is a seam cut and relocation, not a rewrite; output remains byte-identical
  to the frozen oracle across the golden matrix.
- **The seam now carries a second backend (Phase 4).** Pi was
  added entirely under `backends/` — overlays, agents, and
  `backends/claude_code.py` byte output are unchanged, validating the "declare a
  capability map and write a backend" extension model. The CLI's `--target` gained
  `pi`.
- **The `Backend` seam grows from `emit` into a full lifecycle (Phase 5).** The
  single `emit` contract becomes `emit` + `uninstall` + `doctor` +
  `recompose_from_lock` (+ lock helpers), with neutral `UninstallResult` /
  `DoctorReport` result shapes — so both backends own their own artifact
  lifecycle, not just composition. The `claude-code` lifecycle ports the oracle
  byte-for-byte; Pi gets the same verbs, target-aware.
- **The CLI becomes a verb dispatcher (Phase 5).** `cli.py` grows from a single
  implicit `compile` verb into `compile` / `uninstall` / `doctor` / `from-lock` /
  `profile`, reaching full parity with `composer.py`'s `main()` contract. A leading
  `--target` (or no subcommand) still routes to `compile` for back-compat.
- **The live plugin converges onto the compiler (Phase 5 flip).** The plugin's
  `scripts/composer.py` changes from the engine itself to a thin shim that
  delegates (as a CLI) to the vendored, stdlib-only bundle and (as a module) execs
  the preserved-verbatim `composer.py.preflip`. The compiler is now the single
  source of truth; the Claude end-user experience is unchanged (byte-identical
  output, identical CLI contract, zero dependency).
- **The degradation report is now a shared helper (Phase 4, PG6).** The
  per-capability record assembly and the `status → (enforced, gated)` flag rule
  moved into `backends/_degradation.py`, shared by both backends. The refactor
  is byte-preserving: the Claude lock `degradation_report` is byte-identical
  before and after, asserted by a byte-identity gate.
- **`Role.write_scope` is now populated (Phase 4).** It is sourced read-only from
  the mapped Claude per-agent `.regex` path allowlist so Pi's `enforce-lease` is a
  genuinely-scoped native lease. The `claude-code` backend does not read
  `write_scope`, so its emitted bytes are unchanged (goldens stay empty-diff).
- **Pi's lock gains an additive `overlay_sources[]` key (Phase 5).** Appended
  last to enable the lifecycle verbs; re-baselines the Pi golden once, with
  no existing key's bytes shifting.
- **Anchor resolution moved from literal-heading matching to the IR (Phase 2).**
  The `claude-code` backend still renders anchored contributions into the same
  `CLAUDE.md` delegation / agent-augmentation locations and ordering as before, so
  the goldens stay empty-diff.
- **Lock file gains one additive key (Phase 2).** `degradation_report` is appended
  last; stripping it reproduces the prior lock byte-for-byte, so existing keys'
  bytes are unperturbed.

### Security

- Overlay manifests, contribution content, anchor data, agent definitions, and any
  cited pi/schema text are treated as untrusted; no code path evaluates or
  obeys instructions embedded in them. The injection scan runs in the front-end and
  produces warnings only. Every IR-derived string interpolated into the generated
  Pi `.ts` is escaped for a TS string literal (no raw splice); the dangerous-command
  and sensitive-path sets are backend-owned constants, not overlay-sourced.
- The `project_path`-not-inside-`base` invariant is preserved: composition refuses
  to write into the installed plugin directory.
- **Pi `emit` is home-dir-free.** It writes only under `project_path`;
  Pi never touches `~/.pi`. Tests prove
  the real discovery dir is unmodified by emit and by the hermetic
  `discoverAndLoadExtensions` leg — project-local auto-discovery from
  `.pi/extensions/` needs no delivery step.
- The Pi backend reads the plugin's `.regex` path allowlists **read-only** to source
  each role's `write_scope`; it never writes to that installer-owned static surface.
- **The vendored bundle cannot silently diverge (Phase 5 drift guard).** A
  plugin-side tamper check (`_freshness.py`, surfaced via `system2:doctor`) catches
  a hand-edited vendored byte, and a CI staleness check (`tools/check_bundle_fresh.py`)
  fails the merge when the committed bundle predates the current compiler source —
  both keyed off `compiler_source_sha256`. The bundle is stdlib-only by construction
  (a pure copy), so no new runtime dependency is introduced; the convergence flip is
  reversible (`SYSTEM2_USE_BUNDLE=0` escape hatch + one-commit backout via
  `composer.py.preflip`).
- No new network calls or runtime telemetry; observability is compile-time only
  (golden diffs, the lock-file degradation reports, the
  Pi extension load + proven-blocking harness, and the bundle freshness/tamper
  surfaces).
- **The enforced-vs-advisory distinction is never blurred.** For `claude-code`
  every enforced capability is `native`; for
  `pi` the status is honestly MIXED (`enforce-lease`/`block-dangerous`/
  `protect-sensitive` `native` via the `on("tool_call")` hard block,
  `budget` `adapted`, `format`/`typecheck` `advisory`). A headline banner plus a
  completeness assertion guarantee no capability is silently downgraded, and the Pi
  native blocks are *proven* by a synthetic-`tool_call` harness rather than merely
  asserted.
- **Vendored-pin drift guard (F-03).** The vendored `profiles.py` /
  `_hook_security.py` are pinned byte-for-byte to the plugin originals so a
  tightened hook-security ban cannot silently lag the vendored copy.

### Notes

- **Stdlib-only / zero end-user dependency.** `ir/`, `backends/`, `cli.py`, and
  `tools/` import only the Python standard library; `profiles.py` and
  `_hook_security.py` are vendored stdlib-only copies; the Pi backend emits
  TypeScript/markdown/JSON as text and never runs or transpiles TS. No `pip install`
  is introduced for end-users (`node`/`pi` are required only by the Pi eval tests),
  and the plugin's vendored bundle is stdlib-only by construction.
- **Validate-as-oracle for new backends.** Pi output is new (not a byte
  relocation), so it has no frozen byte oracle; validity is gated by Pi's own
  `discoverAndLoadExtensions` loading the emitted extension, and native blocking
  by a synthetic-`tool_call` node harness. When the relevant tool is absent the
  suites record a LOUD skip, never a silent pass.
- **Convergence is reversible and byte-proven.** The flip defaults the plugin to the
  vendored bundle but preserves the engine verbatim as `composer.py.preflip`; the
  bundle-equivalence gate and the plugin's own evals-on-the-bundle prove the default
  reproduces the pre-flip behavior byte-for-byte. `SYSTEM2_USE_BUNDLE=0` runs the
  frozen engine in-place; the backout is one commit.
- **Structured-policy-only gap on Pi (T5/OQ-G3).** Policy that exists only
  as Claude-targeted `base_template` prose has no structured IR representation and is
  not re-expressed on Pi; this is recorded as a future IR-enrichment
  question, not implied parity.
- **Pi `/delegate` isolation honesty.** The bounded dispatcher switches the active
  role in-session, so the report's `subagent_isolation` is recorded `adapted`, not a
  claimed isolated sub-session.
- **Static plugin surface.** The 13 pipeline agents, hook scripts, and `.regex`
  allowlists are installer-owned static plugin files, not compiler-emitted; the
  golden suite locks them as a structural inventory/binding invariant. Pi reads the
  `.regex` allowlists read-only to source `write_scope`.
- **Plugin is Claude-only.** The vendored bundle's `plugin_adapter.py` entry pins
  `--target` to `claude-code`; the multi-target `cli.main` surface ships only as the
  adapter's private dispatch dependency and is never reachable from the plugin.
