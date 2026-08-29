# System2 Compiler

A harness-neutral compiler for the System2 workflow, Overlays, and Profiles. It
composes System2 core plus overlays plus a profile into a single intermediate
representation (the **System2 graph** / IR), then lowers that IR onto a concrete
agent harness through a capability-typed **backend**.

There are **two** backends, each now with a **full per-target lifecycle**
(compile + uninstall + doctor + from-lock):

- **`claude-code`** — the privileged, full-fidelity *reference* target. It
  reproduces the System2 plugin's existing `composer.py` output **byte-for-byte**,
  and every safety capability is `native` (enforced — hooks exit non-zero).
- **`pi`** — the second backend (Phase 4), and the **high-fidelity** one. Pi has
  **no** built-in permission system, so the compiler generates a TypeScript
  extension whose `on("tool_call")` handler returns `{ block: true, reason }`
  **before** a tool runs — the handler *is* the gate. This makes
  `enforce-lease`/`block-dangerous`/`protect-sensitive` genuinely **native**
  (deterministic pre-execution blocks), `budget` `adapted` (an `agent_end`
  report, not a block), and `format`/`typecheck` `advisory` (instruction text).
  It is the project's first **MIXED-status** backend.

The core idea: `composer.py` was already a compiler with one hardcoded backend.
This package cuts the seam that already existed — a harness-neutral front-end
(`compose`) on one side, a Claude projection (`emit`) on the other — so that
overlay authors write domain guidance *once* and additional harnesses become
"declare a capability map and write a backend," with no change to overlays,
agents, or the template. Pi proves the seam carries **native
enforcement** onto a second harness; Phase 5 grows that seam from a single `emit`
into a full lifecycle and **converges the live plugin onto the compiler** — all
under `backends/`, with **zero** change to overlays, agents, or the byte output
of the `claude-code` backend.

## Status

This package implements **Phases 0–5** of the rollout. **All phases are
complete**: the compiler is feature-complete across Claude Code and Pi
with full lifecycle parity, and the live System2 plugin has converged onto the
vendored compiler bundle.

| Phase | Deliverable | State |
|-------|-------------|-------|
| **Phase 0 — Freeze** | Output-level golden suite snapshotting the live `composer.py`; the plugin's `composer.py` is the hash-pinned frozen reference oracle. | Done |
| **Phase 1 — Extract IR / split compose-from-render** | Front-end lifted into `ir/` (produces a `System2Graph`); Claude projection lifted behind `Backend.emit(ir, project_path)` as `backends/claude_code.py`; `cli.py` added. Output is byte-identical to the oracle across the golden matrix. | Done |
| **Phase 2 — Anchors → IR + capability model** | Overlay anchors resolved against the IR (not literal-heading matching); agents declare *intent* capabilities; per-backend capability descriptors and a per-capability degradation report appended to the lock file. | Done |
| **Phase 4 — Pi backend** | A second backend (`backends/pi.py`) lowering the same IR to a Pi **extension** plus context/skill/prompt markdown. The generated `.pi/extensions/system2.ts` gate **natively blocks** via `on("tool_call")`; a shared degradation helper drives the honest MIXED report; native blocking is proven by a synthetic-`tool_call` node harness. No `ir/` change to the `claude-code` byte output. | Done |
| **Phase 5 — Convergence & Lifecycle Parity** | The `Backend` contract grows from a single `emit` into a four-method **lifecycle** (`emit` + `uninstall` + `doctor` + `recompose_from_lock`); the CLI reaches **full parity** with `composer.py` (`compile`/`uninstall`/`doctor`/`from-lock`/`profile`); and the **live plugin converges** onto a vendored, stdlib-only compiler bundle behind a thin `composer.py` shim, guarded by a tamper + staleness drift check. Byte-identical Claude output preserved across the flip. | Done |

The Pi backend remains purely additive — selected only via
`--target pi`. After the Phase 5 convergence flip the plugin's
`composer.py` is a thin shim over the vendored `claude-code` bundle; the Claude
end-user experience is **unchanged** (byte-identical output, identical CLI
contract, zero runtime dependency), with a one-environment-variable escape hatch
and a one-commit backout.

## Architecture

```
System2-Compiler/
├── system2_compiler/            The installable package (top-level import: `system2_compiler`)
│   ├── ir/                      Harness-neutral front-end + System2Graph schema
│   │   ├── __init__.py          Public: compose() -> CompileResult; System2Graph
│   │   ├── graph.py             System2Graph + node dataclasses (the IR schema)
│   │   ├── build.py             Assembles the System2Graph
│   │   ├── contributions.py     Contribution indexing + topological sort
│   │   ├── conflicts.py         Conflict detection + ConflictReport
│   │   ├── manifest.py          Manifest read/validate + injection scan
│   │   ├── anchors.py           IR-level anchor model (Phase 2)
│   │   ├── capabilities.py      Intent-capability vocabulary (Phase 2)
│   │   ├── profiles.py          Profile resolution (vendored, stdlib-only)
│   │   └── _hook_security.py    Hook-security checks (vendored, stdlib-only)
│   ├── backends/
│   │   ├── base.py              Backend protocol: emit + uninstall + doctor + from-lock lifecycle
│   │   ├── claude_code.py       The Claude projection (byte-identical reference) + lifecycle
│   │   ├── pi.py                The Pi projection (native TS-gate extension, Phase 4) + lifecycle
│   │   ├── _degradation.py      Shared descriptor-driven degradation helper (Phase 4)
│   │   ├── _yaml.py             Internal, stdlib-only block-YAML serializer (retained shared infra)
│   │   └── capabilities/
│   │       ├── claude_code.json Per-capability status descriptor (all native)
│   │       └── pi.json          Per-capability status descriptor (MIXED: native/adapted/advisory)
│   ├── plugin_adapter.py        Bundle ENTRY: the composer.py flag contract, --target pinned to claude-code
│   └── cli.py                   `system2` verb dispatcher: compile/uninstall/doctor/from-lock/profile
├── tools/
│   ├── build_bundle.py          Deterministic bundler -> the vendored _system2_compiler/ subtree + BUNDLE.json (Phase 5)
│   ├── _freshness.py            Canonical plugin-side tamper checker (vendored into the bundle as a companion, Phase 5)
│   └── check_bundle_fresh.py    CI staleness + tamper guard for the vendored bundle (Phase 5)
├── evals/                       Golden suite + oracle + behavioral tests
└── pyproject.toml
```

The convergence as-built lives in the plugin tree:

```
System2/plugin/scripts/
├── composer.py                  Thin shim: CLI delegates to the vendored bundle (default) or the pre-flip engine
├── composer.py.preflip          The frozen pre-flip engine, preserved VERBATIM (equivalence oracle + one-commit backout)
└── _system2_compiler/           The vendored, stdlib-only compiler bundle
    ├── system2_compiler/        Pure copy of the product package (verbatim)
    │   ├── ir/                  (verbatim)
    │   ├── backends/            (verbatim)
    │   ├── plugin_adapter.py    Bundle ENTRY: the composer.py flag contract, --target pinned to claude-code
    │   └── cli.py               The adapter's private, contract-proven dispatch dependency
    ├── _freshness.py            Plugin-side bundle TAMPER check, a non-hashed companion (surfaced via system2:doctor)
    └── BUNDLE.json              Provenance: compiler_source_sha256, compiler_version, generated_from, bundled_at
```

### Compose → render split

1. **`ir.compose(...)`** reads overlay manifests, the anchor map, the schema, and
   (optionally) a profile, then produces a `System2Graph`: the 13 roles, the
   Gate 0 → Gate 5 graph, the delegation contract, post-execution and maintenance
   policy, the `spec/` artifact set, topologically-ordered overlay contributions,
   the active profile, IR-level anchors, and intent capabilities. It refuses
   (returns `graph=None` with errors, emitting nothing) on validation errors,
   known overlay conflicts, ordering cycles, or a `project_path` inside the base.
2. **`Backend.emit(ir, project_path)`** is the sole lowering entry point. Each
   backend consumes only the IR plus the target path:
   - The `claude-code` backend writes `CLAUDE.md`,
     `spec/overlay-manifest.lock`, overlay-contributed auxiliary agent files
     (`.claude/agents/<aux>.md`), and overlay content copies under
     `.system2/overlays/<name>/`, using atomic write with backup/restore.
   - The `pi` backend writes the Pi extension + context/skill/prompt tree (see
     below), also under `project_path` only, with the same backup/restore
     posture. It renders from the **structured** IR fields only and
     never consumes `base_template` / `OverlayInput`.

The IR is the *sole* interface between the front-end and any backend: no backend
reads manifests, the anchor map, profiles, or the schema directly.

### Lifecycle parity (Phase 5)

Phase 1 declared a single `emit` seam. Phase 5 grows the `Backend` contract
(`backends/base.py`) into a **four-method lifecycle** so Pi reaches
feature-completeness alongside Claude — each backend owns its own artifact
lifecycle, exactly the seam Phase 1 cut for `emit`:

- **`emit(ir, project_path)`** — the projection (unchanged from Phase 1).
- **`uninstall(project_path, overlay_name, *, dry_run)`** — remove one overlay.
  Recompose the remaining recorded sources → `emit`; on the *last* overlay, revert
  to base (Claude) or **remove the generated tree** (Pi) and clean empty
  dirs, with atomic backup/restore. Returns a neutral `UninstallResult`.
- **`doctor(project_path)`** — a read-only drift/status check. Returns a neutral
  `DoctorReport` with a `status` of `current | stale_base | stale_overlay |
  broken | no_lock`.
- **`recompose_from_lock(ir, project_path, *, dry_run)`** — re-emit from a
  recomposed IR built off the lock's recorded overlay sources.
- **`lock_path(project_path)`** / **`read_lock_overlay_sources(project_path)`** —
  expose the target's own lock so the CLI stays target-agnostic.

A backend reads only its **own** target lock + artifacts under `project_path`
(never manifests / the anchor map / profiles / the schema directly); the from-lock
recompose arrives as an already-recomposed IR.

**Per-target lifecycle via additive lock keys.** Claude already records its
overlay set in `spec/overlay-manifest.lock`. Pi gains an **additive
`overlay_sources[]`** key (appended last to
`system2.pi.lock.json`, mirroring the Claude lock's additive `degradation_report`)
so `uninstall` / `from-lock` can recompose the right set. The key is byte-additive
— it is a new trailing key — and re-baselines the Pi golden exactly once.

**Honest doctor on Pi.** The Claude `doctor` ports the oracle's drift check
byte-for-byte (exit 0 iff `current`). Pi's `doctor` runs the same structural
checks and additionally shells the **real validator** (
the Pi extension load); when that validator is absent it surfaces a **LOUD**
`validator_unavailable` finding rather than a silent `current`.

The CLI verbs (below) are thin contract-faithful wrappers over this lifecycle;
the claude-code path of every verb reproduces the frozen oracle's exact arg names,
exit codes, stdout/stderr bodies, and JSON envelopes.

### The Pi backend (Phase 4) — native enforcement

`backends/pi.py` lowers the same `System2Graph` to a Pi **extension** plus Pi
context/skill/prompt markdown. The headline: **Pi has no built-in permission
system**, so the generated `.pi/extensions/system2.ts` extension's
`on("tool_call")` handler — which fires *before* a tool runs and can
`return { block: true, reason }` — **is** the gate. That handler is what makes
the safety capabilities genuinely `native` (a deterministic pre-execution block).

`emit` writes the following deterministic tree under `project_path` (output is a
pure function of the IR plus backend-owned constants — no timestamps; identical IR
→ byte-identical artifacts; LF endings, single trailing newline):

| Artifact | Contents |
|----------|----------|
| `.pi/extensions/system2.ts` | The generated TypeScript gate (emitted as **text** — the compiler never runs or transpiles TS). Its `on("tool_call")` handler **hard-blocks** off-scope writes/edits (enforce-lease), dangerous bash (block-dangerous), and sensitive-path reads/writes/edits/bash (protect-sensitive) before the tool runs; an `on("agent_end")` handler **reports** the change budget (adapted, not a block); a `before_agent_start` handler injects the orchestrator context; and a bounded `/delegate` command (registered via `pi.registerCommand`) switches the active role over the 13 roles. |
| `.pi/SYSTEM.md` | The orchestrator context rendered from the *structured* IR: the gate graph 0→5, the delegation contract, the post-execution and maintenance policy, overlay-contributed orchestrator material, and an honest, labelled MIXED enforcement summary (native / adapted / advisory). |
| `AGENTS.md` | The small auto-loaded project context: the System2 one-liner, the 13-role inventory, the gate pipeline, and pointers to `.pi/SYSTEM.md`, the skills, and `/delegate`. |
| `.pi/prompts/role-<role>.md` (×13) | One prompt template per pipeline role: persona, gate-role, write-scope (rendered as the native lease note), model-hint (recorded; Pi model selection is session-level), and per-role native/adapted/advisory capability notes. The `/delegate <role>` dispatcher targets these. |
| `.pi/prompts/orchestrator.md` | The orchestrator prompt template (drive the gate graph 0→5, delegate via `/delegate <role>`). |
| `.pi/skills/system2-{init,compose,doctor}/SKILL.md` (×3) | Three skills: `init` (set up the workflow / what the extension provides), `compose` (run the gate pipeline + delegation), `doctor` (verify the extension loads and the gates are live — the operator analogue of the proven-blocking test). |
| `system2.pi.lock.json` | The standalone MIXED degradation report: every IR capability with its `status`, `mechanism`, `enforced`/`gated` flags, a top-level `FIDELITY` banner making the mixed story explicit, and (Phase 5) the additive `overlay_sources[]` key for the lifecycle verbs. |

**Auto-discovery, no install step.** The extension is auto-discovered by Pi from
the project-local `.pi/extensions/` directory; there is no separate install or
registration step. **Validity is Pi's own `discoverAndLoadExtensions`**: the test
suite loads the emitted `.pi/extensions/system2.ts` through Pi's own discovery and
asserts it loads with `errors: []` and registers the expected handlers. **Native
blocking is proven** by a synthetic-`tool_call` node harness (no LLM in the loop):
it captures the registered `on("tool_call")` handler and fires synthetic events at
it — an off-scope write, a dangerous `rm -rf /`, and a `.env` read each return
`{ block: true }`; while an in-scope write, a benign `ls`, and an ordinary read
are **not** blocked (the negative control proving the gate discriminates rather
than blocking everything).

**`write_scope` is real and scoped.** Each role's `write_scope` is sourced
read-only from the same Claude per-agent `.regex` path allowlists that
`validate-file-paths.py` uses (e.g. `executor.regex`), carried on the IR `Role`.
The Pi lease gate compiles that scope into its per-path block, so `enforce-lease`
is a genuinely-scoped native lease on Pi. A role with no dedicated allowlist keeps an empty scope (no
broad fallback that would over-permit); the report says so loudly rather than
making a vacuous native claim. The `claude-code` backend never reads
`write_scope`, so its bytes are unchanged.

### Capability / degradation model (Phase 2)

Agents declare **intent** capabilities, not harness mechanisms. The vocabulary is
fixed for this cycle:

- Intent capabilities: `enforce-lease`, `block-dangerous`, `protect-sensitive`,
  `format`, `typecheck`, `budget`.
- Role attributes: `write-scope`, `model-hint`, `gate-role`.

Each backend ships a descriptor at `backends/capabilities/<backend>.json` that
assigns every capability exactly one **status**:

| Status | Meaning |
|--------|---------|
| `native` | Real, enforced / first-class on the harness (blocks the action). |
| `adapted` | Emulated with equivalent *intent* but best-effort effect (a gate or a report, not a hard block). |
| `advisory` | Prompt text only — described, not enforced. |
| `unsupported` | Not represented. |

Per-backend status (verified against the descriptors) — **native** (Claude & Pi)
versus Pi's own **adapted** capability:

| Capability | `claude-code` | `pi` |
|------------|---------------|------|
| `enforce-lease` | native (write-lease lifecycle + path allowlists) | **native** — `on("tool_call")` blocks an off-`write_scope` write before it runs (scope sourced from the Claude `.regex` allowlists) |
| `block-dangerous` | native (`dangerous-command-blocker.py`) | **native** — `on("tool_call")` hard-blocks a dangerous bash command before it runs |
| `protect-sensitive` | native (`sensitive-file-protector.py` + `boundary-check.py`) | **native** — `on("tool_call")` hard-blocks any read/write/edit/bash touching a sensitive path |
| `format` | native (`auto-formatter.py`, PostToolUse) | advisory — instruction text only (no post-edit formatter seam) |
| `typecheck` | native (`type-checker.py`, PostToolUse) | advisory — instruction text only (no post-edit type-check seam) |
| `budget` | native (`change-budget-reporter.py`, SubagentStop) | **adapted** — `on("agent_end")` reports the change budget (a report, not a block) |

The active backend emits a **degradation report** enumerating every IR capability
with its status. No capability is ever dropped silently: if it is present in the
IR but absent from the descriptor, emit raises rather than dropping it. For
`claude-code` the report is the single additive `degradation_report` key in
`spec/overlay-manifest.lock`; for `pi` it is the standalone `system2.pi.lock.json`
(Pi has no lock to append to, and a separate file keeps the
Claude lock byte-untouched).

Both backends derive that report through one shared, descriptor-driven
helper, **`backends/_degradation.py`**. It owns the per-capability
report-record assembly and the total `status → (enforced, gated)` flag rule over
the four-value enum (`native` → enforced; `adapted` → gated; `advisory`/
`unsupported` → neither). Each backend keeps its own report envelope and `fields`
selection, so the serialized bytes of the existing Claude report are
unchanged, while Pi's MIXED status is correct by construction. This is what lets a
single backend honestly report **native and non-native capabilities together**.

> The enforced-vs-advisory distinction is the central safety property of this
> project. On Claude and Pi the safety primitives actually block (Claude
> hooks exit non-zero; Pi's `on("tool_call")` handler returns `{ block: true }`
> before the tool runs), except for Pi's `budget` (adapted) and `format`/
> `typecheck` (advisory instruction text). Every backend's degradation report and
> its headline banner exist so a user can always read which capabilities are
> *enforced* versus merely *adapted* or *described* on their harness. `adapted` is
> not `enforced`; `advisory` is not `enforced`.

The Pi extension needs no delivery dance for its enforcement: it is
auto-discovered from the project-local `.pi/extensions/` directory, so the native
gates are active simply by running Pi from the project root.

## Convergence: the live plugin runs on the vendored compiler bundle (Phase 5)

Through Phases 0–4 the System2 plugin kept running its own frozen `composer.py`;
the compiler was *not* wired into the plugin's compose path. Phase 5 performs the
**convergence flip**: the compiler is now the **single source of truth**, and the
plugin's `composer.py` is a thin shim over a vendored, stdlib-only copy of it.

**The vendored bundle.** `tools/build_bundle.py` is a deterministic bundler that
emits `_system2_compiler/` — a **pure copy** (no import rewriting) of the
compiler's product modules (`ir/` + `backends/` + `cli.py`) plus a
`plugin_adapter.py` entry and a `BUNDLE.json` manifest. Because it is a verbatim
subtree copy, the module-boundary and stdlib-only properties are *exactly* the
compiler's, and byte-identical Claude output is guaranteed by construction (the
goldens then prove it). `BUNDLE.json` records `compiler_source_sha256` (a sha256
over the sorted `(relpath, bytes)` of the copied source — the drift anchor),
`compiler_version`, `generated_from` (`System2-Compiler@<git-rev>`), and
`bundled_at` (ISO; **excluded** from the hash, so a re-bundle of identical source
is hash-stable).

**The thin shim** (`plugin/scripts/composer.py`). The original engine is preserved
**verbatim** as `composer.py.preflip` (the immutable equivalence oracle and the
backout). The shim has two facets:

- **As an imported module** (`import composer`), it execs the `composer.py.preflip`
  source into its own namespace, so every public/internal symbol the engine
  exposed (`compose`, `main`, `_activate_profile`, …) is present and byte-for-byte
  the pre-flip behavior. The plugin's own `System2/evals/` suite imports those
  symbols directly and is unaffected by the flip.
- **As the CLI** (`python3 composer.py …`), it routes the process to the vendored
  bundle's `plugin_adapter.main_composer_contract`. The adapter encodes the
  `composer.py` flag contract once (`--base`/`--overlays`/`--project`/`--dry-run`/
  `--format`/`--allow-injection`/`--allow-newer-schema`/`--doctor`/`--uninstall`/
  `--from-lock`/`--profile`/`--save-profile`/`--profile-op`/`--profile-*`/
  `--force`), with `--target` **hard-pinned to `claude-code`** (the plugin is
  Claude-only), and delegates to the bundle's `cli.main`. The adapter does *not*
  re-implement dispatch — `cli.py` ships as its private dependency so the adapter
  and `cli.py` cannot drift; `cli`'s multi-target surface is never reachable
  because the adapter pins the target.

**The switch (default ON) and the backout.** This revision defaults to the bundle:

- unset / anything except `0` → the CLI delegates to the **vendored bundle**.
- `SYSTEM2_USE_BUNDLE=0` → an **escape hatch** that runs the frozen
  `composer.py.preflip` engine in-place (for A/B checks).
- **One-commit backout** (zero residue):
  `cp composer.py.preflip composer.py && rm -rf _system2_compiler/`.

The flip is proven byte-identical: the bundle-equivalence gate
(`System2-Compiler/evals/test_bundle_equivalence.py`) and the plugin's own
`System2/evals/` suite (`test_plugin_evals_on_bundle.py`) both pass with the
bundle as the default engine. **The Claude end-user UX is unchanged**, and the
plugin stays **zero-dependency** (the bundle is stdlib-only by construction).

### The drift guard (tamper + staleness)

A vendored copy invites silent divergence; two complementary checks prevent it,
both keyed off `compiler_source_sha256`:

- **Tamper (plugin-side, ships with the bundle)** — `_system2_compiler/_freshness.py`
  recomputes the sha256 over the vendored subtree's bytes (the same
  `(relpath, bytes)` algorithm the bundler uses) and compares it to the recorded
  `compiler_source_sha256`. A mismatch means a vendored file was hand-edited
  without re-running the bundler. It runs with **no compiler source present**, so
  `system2:doctor` surfaces it (a `bundle_freshness` provenance line plus a LOUD
  `bundle_tampered` finding); it is report-only and never blocks compose.
- **Staleness (CI-side, needs the compiler source)** —
  `tools/check_bundle_fresh.py` regenerates the bundle from the *current* compiler
  source into a temp dir, recomputes the source hash, and compares it to the
  target bundle's recorded **and** recomputed hashes. It catches both a stale
  bundle (the committed copy predates the current compiler source) and a tampered
  one, exiting non-zero with `vendored bundle is stale: regenerate via
  tools/build_bundle.py`. This is the machine-enforced merge gate: a stale
  vendored bundle cannot merge.

## Usage

The compiler ships as the `system2_compiler` package and is invoked through the
`system2` console entry point (`system2_compiler.cli:main`) as a **verb
dispatcher**; from a source checkout without installing, use
`python3 -m system2_compiler.cli`. Verbs:
`compile` · `uninstall` · `doctor` · `from-lock` · `profile`. For back-compat, a
leading `--target` (or no subcommand) dispatches to `compile`, so the historical
`--target …` invocation is unchanged.

```
system2 compile   --target {claude-code|pi}
                  ( --profile NAME | --overlays P1,P2,… | --from-lock )
                  --base PATH --project PATH
                  [--dry-run] [--allow-newer-schema] [--allow-injection]
                  [--format text|json]

system2 uninstall --target {claude-code|pi}
                  --base PATH --project PATH --name OVERLAY
                  [--dry-run] [--allow-newer-schema] [--allow-injection]
                  [--format text|json]

system2 doctor    --target {claude-code|pi}
                  --base PATH --project PATH [--format text|json]

system2 from-lock --target {claude-code|pi}
                  --base PATH --project PATH
                  [--dry-run] [--allow-newer-schema] [--allow-injection]
                  [--format text|json]

system2 profile   { list | inspect NAME | save NAME | create NAME --paths P |
                    edit NAME [--add P]… [--remove OVERLAY]… | delete NAME }
                  [--project PATH] [--force] [--format text|json]
                  # harness-neutral; no --target
```

### Installation (private / internal)

The compiler is **not published to PyPI** — it is distributed privately (this
repository, or an internal package index). It is pure-Python and **zero
runtime dependencies**, so a wheel installs anywhere with Python ≥ 3.8:

```bash
# Build the wheel + sdist (requires the `build` frontend):
python3 -m build                       # -> dist/system2_compiler-<ver>-py3-none-any.whl

# Install from the built wheel (or from a private index / git):
pip install dist/system2_compiler-*.whl
#   pip install --index-url https://<your-internal-index>/simple system2-compiler
#   pip install "system2-compiler @ git+ssh://…/System2-Compiler.git"
```

This puts the `system2` console script on `PATH`. The "System2 for Pi"
example is **not** separately packaged — they live in `examples/` in this repo.

Once installed, run the `system2` console script; or, from a source checkout
without installing, run `python3 -m system2_compiler.cli` from the repo root:

```bash
# Compose core + one or more overlays onto a project (Claude artifacts):
system2 compile --target claude-code \
  --base /path/to/System2 \
  --project /path/to/my-project \
  --overlays /path/to/overlay-a,/path/to/overlay-b

# Compose by profile name (resolved from the profile store, e.g. ~/.system2/profiles.json):
system2 compile --target claude-code \
  --base /path/to/System2 \
  --project /path/to/my-project \
  --profile my-profile

# Recompose from the existing lock's recorded overlay sources:
system2 from-lock --target claude-code \
  --base /path/to/System2 \
  --project /path/to/my-project

# Remove a single overlay (recompose the remaining set, or revert to base on the last):
system2 uninstall --target claude-code \
  --base /path/to/System2 \
  --project /path/to/my-project \
  --name overlay-a

# Read-only drift/status check:
system2 doctor --target claude-code \
  --base /path/to/System2 \
  --project /path/to/my-project

# Lower the SAME composition onto Pi (native TS-gate extension):
system2 compile --target pi \
  --base /path/to/System2 --project /path/to/my-project --overlays /path/to/overlay-a

# Preview the write set without touching disk, as JSON:
system2 compile --target pi \
  --base /path/to/System2 --project /path/to/my-project --overlays /path/to/overlay-a \
  --dry-run --format json

# Harness-neutral profile management (writes only ~/.system2/profiles.json):
system2 profile list
system2 profile inspect my-profile
system2 profile create my-profile --paths /path/to/overlay-a,/path/to/overlay-b
system2 profile edit my-profile --add /path/to/overlay-c --remove overlay-a
system2 profile save my-profile --project /path/to/my-project --force
system2 profile delete my-profile
```

Flag reference for the write/lifecycle verbs (verified against `cli.py`):

| Flag | Verbs | Required | Purpose |
|------|-------|----------|---------|
| `--target` | compile/uninstall/doctor/from-lock | yes | Backend to lower onto: `claude-code` or `pi`. |
| `--base PATH` | compile/uninstall/doctor/from-lock | yes | Path to the System2 plugin root (schema, anchor map, base template, the per-agent `.regex` allowlists). |
| `--project PATH` | all | yes (write/lifecycle); optional for `profile` | Target project root. Must not be inside or equal to `--base`. |
| `--profile NAME` | compile | xor `--overlays` / `--from-lock` | Activate the named profile's overlay set. |
| `--overlays PATHS` | compile | xor `--profile` / `--from-lock` | Comma-separated overlay source paths. |
| `--from-lock` | compile (or the `from-lock` verb) | xor `--profile` / `--overlays` | Recompose from the lock's recorded overlay sources. |
| `--name OVERLAY` | uninstall | yes | Overlay to remove. |
| `--dry-run` | compile/uninstall/from-lock | no | Compute the would-write set; write nothing. (Rejected for profile mutations.) |
| `--allow-newer-schema` | compile/uninstall/from-lock | no | Accept overlays with an unsupported `schema_version` (degraded mode). |
| `--allow-injection` | compile/uninstall/from-lock | no | Proceed in write mode despite prompt-injection warnings (otherwise exit 4). |
| `--force` | profile (save/create) | no | Overwrite an existing profile. |
| `--format text\|json` | all | no | Output format (default `text`). |

`--profile`, `--overlays`, and `--from-lock` are mutually exclusive on `compile`.
Output is deterministic and independent of overlay argument order. The front-end
refusal path is backend-independent: a known conflict/tension or path-safety
violation refuses *before* any backend runs, so Pi (like Claude) emits
nothing for a refused composition. On refusal the CLI exits non-zero (1 validation,
2 structural conflict, 3 I/O; 4 = injection blocked in write mode) and the
`claude-code` path prints the oracle-identical error text. **Profiles are
harness-neutral** — an activated profile feeds compose for *any* `--target`, and
mutations write only `~/.system2/profiles.json`.

### Running the Pi workflow

After `compile --target pi` writes the extension + context tree, just run Pi from
the project root — the extension is auto-discovered from the project-local
`.pi/extensions/` directory (no install step, no launcher):

```bash
cd /path/to/my-project

# Pi auto-discovers .pi/extensions/system2.ts; the native gates are live.
pi
```

Read `.pi/SYSTEM.md` for the orchestrator context and the honest MIXED enforcement
summary, use `/delegate <role>` to dispatch to one of the 13 roles, and read
`system2.pi.lock.json` for the per-capability degradation report and the
`FIDELITY` banner.

### Regenerating + checking the vendored bundle

The bundle is regenerated from the compiler source and the freshness guard is run
from the package root:

```bash
cd System2-Compiler

# Regenerate the vendored bundle into a destination dir (writes <dest>/_system2_compiler/ + BUNDLE.json):
python3 tools/build_bundle.py --dest /path/to/dest

# CI staleness + tamper guard: fail if the committed vendored bundle is stale or hand-edited.
python3 tools/check_bundle_fresh.py --target /path/to/System2/plugin/scripts
```

`build_bundle.py` never writes into `System2/plugin/` itself (that is the flip
task's job); it writes only under `--dest`.

### Running the goldens

The Claude golden suite byte-diffs produced artifacts against captured snapshots.
The **compiler driver** is the keystone fidelity check — it runs the in-process
`compose → ClaudeCodeBackend().emit` path and requires an empty diff against the
frozen baseline:

```bash
cd System2-Compiler

# Keystone: compiler (compose -> emit) must be byte-identical to the baseline.
python3 -m evals.run_goldens --driver compiler

# Cross-check: re-run the frozen composer.py oracle as a subprocess (default driver).
python3 -m evals.run_goldens --driver oracle
```

Snapshots are never rewritten by a normal run; re-baselining requires the
explicit `--rebaseline` flag. If the oracle's source content changes, the suite
fails with an "oracle changed / re-baseline required" message rather than
silently re-baselining.

Pi has no byte oracle: its validity is gated by **Pi's own
`discoverAndLoadExtensions`** loading the emitted extension, and its native
blocking is gated by a synthetic-`tool_call` node harness. With `node`/`pi`
present these MUST run and pass; absent, they record a **LOUD skip**, never a
silent pass, under a hermetic temp `HOME`/`.pi`:

```bash
cd System2-Compiler

# Pi structure/determinism/emit-purity goldens (+ extension loads under Pi):
python3 -m unittest evals.test_pi_goldens

# Proven native blocking (synthetic tool_call; off-scope write / dangerous bash /
# sensitive read are BLOCKED, in-scope/benign cases are NOT — the negative control):
python3 -m unittest evals.test_pi_proven_blocking

# Point at specific node / pi binaries if they are not on PATH:
NODE_BIN=/path/to/node PI_BIN=/path/to/pi python3 -m unittest evals.test_pi_proven_blocking
```

### Running the test suite

The behavioral and structural tests are stdlib `unittest` and also run under
`pytest`:

```bash
cd System2-Compiler

# Run all eval tests:
python3 -m unittest discover -s evals -p 'test_*.py'

# Or with pytest:
pytest evals
```

These cover path safety, atomic-write/restore, dry-run, refusal text, module
boundaries, the mechanism → capability mapping, the degradation report
(completeness + no-silent-drop) for both backends, lowering invariance,
unknown-capability warnings, the Pi goldens (`discoverAndLoadExtensions` load +
the proven-blocking node harness), the shared `_degradation` helper and its
byte-identity gate, the stdlib YAML serializer, the
**Phase 5 lifecycle verbs** (uninstall / doctor / from-lock parity across both
targets; the CLI-contract goldens pinning the claude-code path to the frozen
oracle's exit codes + stdout/stderr; the additive `overlay_sources[]` lock), the
**convergence flip** (the bundle-equivalence gate and the plugin's own evals on
the bundle), the **bundle drift guard** (tamper + staleness), no-regression (Claude
goldens stay empty-diff; the Claude lock stays byte-identical across the
 refactor; `ir/` and `backends/claude_code.py` byte output unchanged), the
vendored-pin drift guard, and the eval-breadth gaps (argument-ordering determinism
+ anchor-exclusion).

## Design invariants

- **Stdlib-only.** `ir/`, `backends/`, `cli.py`, and `tools/` import only the
  Python standard library; no third-party runtime dependency, and never a
  `pip install` requirement for end-users (`profiles.py` and `_hook_security.py`
  are vendored stdlib-only copies; the Pi backend emits TypeScript/markdown/JSON as
  **text** and never runs or transpiles TS — `node`/`pi` live only in the eval
  tests). The vendored plugin bundle is stdlib-only **by construction** (a pure
  copy of the stdlib-only compiler).
- **Frozen-oracle reference.** The plugin's pre-flip `composer.py` (preserved
  verbatim as `composer.py.preflip`) is the hash-pinned reference oracle. The
  Claude golden suite verifies the compiler against it and refuses to
  auto-re-baseline on oracle drift.
- **Byte-identical Claude output across the flip.** The `claude-code` backend
  reproduces the oracle's emitted artifacts (`CLAUDE.md`, the lock, auxiliary
  agents, overlay content copies, and the stderr warning stream) byte-for-byte,
  and the lifecycle verbs (uninstall / doctor / from-lock) reproduce the oracle's
  output, exit codes, and parsed stdout/stderr byte-for-byte — which is what makes
  the convergence flip safe (the plugin now runs the bundle for those paths). The
  Phase 2 lock `degradation_report` is the single additive Claude key; stripping it
  yields the baseline lock byte-for-byte. Adding the Pi backend and
  growing the lifecycle did not perturb any of this; the shared degradation
  refactor preserves the Claude report bytes.
- **Validate-as-oracle for new backends.** Pi has no byte oracle; the
  emitted extension must load under Pi's own `discoverAndLoadExtensions` and its
  native blocks must be proven by the synthetic-`tool_call` harness. The backend
  emits byte-deterministically (identical IR → identical tree, no timestamps).
- **Per-target lifecycle is additive.** Pi gained `uninstall`/`doctor`/
  `from-lock` and an additive `overlay_sources[]` lock key (appended last); a
  re-baseline of the Pi golden happens exactly once and no existing key's
  bytes shift. Pi's `doctor` never reports a silent `current` when its real
  validator is unavailable — it surfaces a LOUD `validator_unavailable` finding.
- **No silent capability dropping.** Every capability present in the IR appears in
  the active backend's degradation report with one of `native | adapted |
  advisory | unsupported`. A capability present in the IR but absent from the
  descriptor makes emit raise. On Pi the status is
  honestly MIXED.
- **Pure, home-dir-free `emit`.** Both backends write only under
  `project_path`. The Pi backend never touches `~/.pi` (project-local
  auto-discovery, no delivery step needed).
- **Harness-neutral front-end.** `ir/` contains no Claude- or
  Pi-specific rendering, hook-wiring, frontmatter, YAML-emission, or
  TypeScript-emission logic; backends may import the IR graph type but not the
  manifest/anchor-map/profile/schema loaders. The Pi backend renders
  only structured IR fields and never reads `base_template` / `OverlayInput`.
- **Harness-neutral profiles.** Profile management (`profile` verb) is shared
  across all backends — an activated profile feeds compose for any `--target` —
  and mutates only `~/.system2/profiles.json`, never a project artifact.
- **Single dispatch, no drift.** The plugin's `plugin_adapter.py` re-uses the
  bundle's `cli.py` for the one contract-proven verb dispatch (with `--target`
  pinned to `claude-code`); it never re-implements dispatch, so the adapter and
  `cli.py` cannot drift. The vendored bundle cannot silently diverge from the
  compiler source: the plugin-side tamper check and the CI staleness check both key
  off `compiler_source_sha256`.
- **Untrusted input.** Overlay manifests, contribution content, anchor data, agent
  definitions, and any cited schema text are treated as untrusted data; no code
  path executes or obeys instructions embedded within them. Every IR-derived
  string interpolated into the generated Pi `.ts` is escaped for a TS string
  literal (no raw splice; the pattern sets are backend-owned constants).
- **Single standalone package.** Everything lives in `System2-Compiler` with a
  `backends/` directory; there are no per-target repos or per-target compiler
  packages. The plugin ships only a vendored *copy* of it.

## Scope notes

- The 13 pipeline agents, the hook scripts, and the `.regex` path allowlists are
  **installer-owned static plugin files**, not artifacts the compiler emits. The
  golden suite locks them as a structural inventory/binding invariant (asserted
  unchanged), and capability *lowering* targets that same static surface without
  altering it. The Pi backend reads the `.regex` allowlists **read-only** to
  source each role's `write_scope` — it never writes them.
- Overlay anchor contributions are rendered into `CLAUDE.md` delegation /
  agent-augmentation instructions (Claude), and into `.pi/SYSTEM.md` / role
  prompt templates (Pi), not into pipeline-agent system prompts.
- **Structured-policy-only gap on Pi.** The Pi
  backend faithfully renders the *structured* policy (roles, gate graph,
  delegation contract, capabilities). Any policy that exists only as
  Claude-targeted `base_template` prose has no structured IR representation and is
  **not** re-expressed on that backend; this is recorded (not silently dropped)
  and flagged as a future IR-enrichment question, not implied parity.
- **Pi `/delegate` isolation honesty.** The bounded `/delegate` dispatcher
  switches the active role in-session (it mutates `activeRole`); the report's
  `subagent_isolation` is therefore recorded `adapted`, not a silently-claimed
  native isolated sub-session.
- **Plugin is Claude-only.** The vendored bundle's entry pins `--target` to
  `claude-code`; the multi-target `cli.main` surface ships only as the adapter's
  private dependency and is never reachable from the plugin. The Pi target is
  reached only through the compiler package's own `cli.py`.
