# System2 Compiler — Architecture

> This document describes the compiler as implemented. It uses behavioral and symbol
> names instead of generated requirement, decision, risk, or task identifiers.

## Overview

System2 composition has two stages:

1. `ir.compose(...)` validates core and overlay inputs, resolves profiles, orders
   contributions, detects conflicts, and builds a harness-neutral `System2Graph`.
2. A backend lowers that graph into target-native artifacts and owns those artifacts'
   complete lifecycle.

The separation keeps overlays and role definitions single-source. Target-specific
hooks, extensions, file formats, and trust models remain inside backends.

The implemented targets are:

- **Claude Code:** the full-fidelity reference projection. Its composed output remains
  byte-compatible with the frozen plugin composer.
- **Pi:** emits a TypeScript extension plus context, prompts, skills, and a lock. Its
  pre-tool handler natively blocks lease, dangerous-command, and sensitive-path
  violations; budget reporting is adapted; formatting and type checking are advisory.
- **Codex:** emits a plugin, role skills, user-scope Node hooks, and a lock. Enforcement
  is conditional on users materializing and trusting hooks, so safety is reported as
  adapted rather than native.

## Components and Boundaries

```text
validated plugin data + overlays + optional profile
                       |
                       v
              system2_compiler.ir
  manifest -> conflicts -> contributions -> anchors
                       -> capabilities -> System2Graph
                                      |
              +-----------------------+-----------------------+
              |                       |                       |
              v                       v                       v
       ClaudeCodeBackend          PiBackend              CodexBackend
              |                       |                       |
       Claude artifacts          Pi artifacts           Codex artifacts
```

### Neutral front end

`system2_compiler/ir/` owns:

- manifest and content validation;
- path containment and prompt-injection warnings;
- profile resolution;
- contribution indexing and deterministic topological ordering;
- structural conflict and semantic-tension detection;
- anchor identity and unknown-anchor exclusion;
- the fixed intent-capability vocabulary and blocking semantics;
- construction of frozen dataclasses in `ir.graph`.

The IR package imports no backend and contains no target prompt rendering, hook wiring,
frontmatter emission, lock formatting, or target configuration.

### Backend layer

`system2_compiler/backends/` owns:

- target-specific rendering and escaping;
- capability descriptors and fidelity mechanism text;
- lock formats;
- project-local atomic writes and rollback;
- uninstall, doctor, and from-lock behavior;
- target validators and honest handling when a validator is unavailable.

A backend receives structured graph data and a target project path. It does not load
manifests, profiles, schemas, or the anchor map directly.

### CLI and plugin adapter

`system2_compiler/cli.py` parses commands, selects a backend, invokes composition or a
backend lifecycle method, and renders neutral results. Profiles remain target-neutral.

The plugin's thin `composer.py` shim reaches the vendored compiler through
`plugin_adapter.py`, which pins the target to Claude Code and preserves the plugin's
existing flag, output, and exit-code contract.

## Public Interfaces

### Composition

```python
compose(
    base_path: str,
    overlay_paths: list[str],
    project_path: str,
    *,
    profile: str | None = None,
    dry_run: bool = False,
    allow_newer_schema: bool = False,
) -> CompileResult
```

`CompileResult` carries either a graph or errors, plus warnings, intended writes, and a
report. Validation errors and structural conflicts return no graph and write nothing.

### Backend lifecycle

Each backend provides:

```python
emit(ir, project_path) -> list[str]
uninstall(project_path, overlay_name, *, dry_run=False) -> UninstallResult
doctor(project_path) -> DoctorReport
recompose_from_lock(ir, project_path, *, dry_run=False) -> list[str]
lock_path(project_path) -> str
read_lock_overlay_sources(project_path) -> list[str]
```

`UninstallResult` and `DoctorReport` are neutral dataclasses so the CLI can remain
independent of target lock and artifact formats.

## Intermediate Representation

`System2Graph` is frozen, serializable data containing:

- system and schema versions;
- the 13 pipeline roles with gate role, write scope, model hint, and capabilities;
- the ordered gate graph;
- the delegation contract;
- post-execution and maintenance behavior;
- known project artifact types;
- ordered overlay contributions;
- the active profile;
- per-agent anchors;
- per-agent intent capabilities and blocking semantics;
- warnings;
- quarantined Claude byte-fidelity carriers used only by the Claude backend.

Roles and contributions never carry Claude mechanism fields such as `tools`, `hooks`,
or `permissionMode`.

## Contribution and Anchor Resolution

Contributions are grouped by semantic scope and sorted deterministically. Ordering uses
explicit `after` dependencies where present and a stable `(overlay_name,
contribution_id)` key otherwise. Cycles produce a structural refusal.

Anchors are keyed by `(agent, anchor_name)`. The front end filters unknown anchors before
content loading, which means excluded content cannot trigger injection warnings or appear
in output. Each backend decides where a valid anchor renders in its own artifact model.

## Capability and Fidelity Model

The intent vocabulary is:

- `enforce-lease`
- `block-dangerous`
- `protect-sensitive`
- `format`
- `typecheck`
- `budget`

Each target descriptor assigns every capability one status:

| Status | Meaning |
|---|---|
| `native` | The target provides a real first-class or deterministic blocking mechanism. |
| `adapted` | The target provides a partial gate, emulation, or report with weaker effect. |
| `advisory` | The behavior is instruction text only. |
| `unsupported` | The target does not represent the capability. |

`backends/_degradation.py` assembles records from the descriptor and the capabilities
actually present in the graph. It raises if a present capability has no descriptor
entry. Status flags are total and centralized: native is enforced, adapted is gated,
and advisory or unsupported is neither.

Mechanism text must say what actually happens. In particular, a report is not a block,
a trusted-hook requirement is not native enforcement, and prompt guidance is not a gate.

## Target Projections

### Claude Code

The Claude backend writes:

- `CLAUDE.md`;
- `spec/overlay-manifest.lock`;
- overlay-contributed auxiliary agents;
- project-local overlay content copies.

It preserves formatting, key insertion order, warning text, timestamps, fingerprints,
and atomic-write behavior from the frozen composer. The degradation report is an
additive final lock key so preceding lock bytes remain stable.

### Pi

The Pi backend writes:

- `.pi/extensions/system2.ts`;
- `.pi/SYSTEM.md` and `AGENTS.md`;
- an orchestrator prompt and one prompt per role;
- System2 workflow and utility skills;
- `system2.pi.lock.json`.

The generated extension is emitted as text. Its `tool_call` handler normalizes paths and
commands, blocks dangerous commands and sensitive paths, and applies the active role's
write scope before a tool runs. A bounded `/delegate` command changes the active role in
the current session; the lock therefore reports adapted isolation rather than claiming a
separate subagent. An `agent_end` handler reports budget usage but does not block.

All IR-derived strings are escaped before entering TypeScript or Markdown. Emission
never writes to the user's real Pi configuration or home directory.

### Codex

The Codex backend writes:

- `.codex-plugin/plugin.json` and a README;
- an orchestrator skill, doctor skill, role skills, and utility skills;
- user-hook templates and generated Node guards;
- `system2.codex.lock.json`.

Current Codex does not dispatch plugin-bundled hooks as active enforcement. The
`system2 codex init` lifecycle command therefore materializes guards into the user's
Codex hook configuration with absolute command paths. A pre-existing non-System2 hook
configuration is refused unless `--force` creates a backup first.

The manifest, orchestrator, README, and lock share exact trust and coverage statements.
At rest, no capability claims native or enforced behavior. Safety capabilities are
adapted and gated because they become active only after explicit user review and trust,
and their tool coverage remains partial.

The doctor command reports artifact integrity but cannot observe hook trust. It always
surfaces that limitation and directs the user to the nonce-bearing marker-file canary for
a point-in-time liveness check.

## Atomic Writes and Lifecycle

All backends plan paths relative to `project_path`, reject writes into protected source
or overlay trees, and use temporary files plus `os.replace`. Existing files and generated
directories are backed up before mutation. Any exception restores prior files, restores
removed directories, and removes newly created paths.

Dry-run returns intended paths without mutation. Uninstall removes one overlay by
recomposing remaining recorded sources; removing the final overlay restores the base
Claude state or removes the generated target tree. From-lock reads source paths through
the backend that owns the lock format and then performs normal composition and emission.

## Determinism and Consistency

- Contributions use stable ordering independent of CLI overlay argument order.
- Backends control insertion order for serialized mappings.
- Generated text uses LF endings and a single trailing newline where applicable.
- Output is a pure function of graph data and backend constants except documented lock
  timestamps, which are reused when content fingerprints match.
- Generated distributions carry source fingerprints; timestamps are excluded only from
  comparisons where they are documented provenance breadcrumbs.

## Security Model

- All manifests, contribution text, event payloads, profile paths, and lock contents are
  untrusted input.
- JSON and emitted source are parsed or escaped; untrusted text is never evaluated.
- Content and hook paths undergo lexical and realpath containment checks.
- Overlay hooks receive static checks for process execution, dynamic imports, code
  execution, and network modules.
- Compiler product code uses only the Python standard library and performs no telemetry
  or network calls.
- Generated blocking hooks cap input sizes, use watchdogs, and fail closed on malformed
  input or internal errors.
- Trust limitations and unsupported coverage remain visible in target locks and user
  instructions.

## Generated Bundle and Distribution Freshness

`compiler/tools/regen_all.py` is the only supported regeneration entry point. It builds
Codex and Pi distributions, mirrors packaged hook data, then builds the plugin bundle
last so the bundle includes all source-side generated mirrors.

The plugin bundle is a verbatim copy of compiler product modules plus a freshness
companion and `BUNDLE.json`. CI regenerates and compares source hashes to catch stale
bundles. The plugin can recompute its own vendored-subtree hash to catch hand edits when
compiler source is unavailable. Distribution provenance records generator inputs,
versions, source hashes, and a timestamp; only documented timestamp and revision
breadcrumbs are excluded from deterministic comparisons.

## Failure Modes and Recovery

| Failure | Behavior |
|---|---|
| Invalid manifest, schema, anchor, or profile data | Return errors and write nothing. |
| Known conflict or ordering cycle | Refuse composition deterministically. |
| Unknown capability | Emit a validation warning; never silently ignore it. |
| Missing capability descriptor entry | Raise before emission. |
| Project path inside protected source | Refuse before writing. |
| Mid-write error | Restore backups and remove partial output. |
| Missing external validator | Report `validator_unavailable` loudly; never claim current based on a check that did not run. |
| Codex hook trust not observable | Report the limitation and require the canary skill. |
| Stale or tampered generated artifact | Fail freshness checks and name the regeneration command. |

## Simplicity Budget

- Keep the IR and backend lifecycle as the principal abstractions.
- Prefer backend-owned constants over widening the neutral graph for target-only policy.
- Keep serialization helpers limited to the formats actually emitted.
- Keep the plugin adapter thin and target-pinned.
- Add no runtime dependency, service, telemetry pipeline, or target-specific overlay fork.

## Rejected Abstractions

- Per-target overlay or compiler repositories.
- Bash as a semantic workflow engine.
- A lowest-common-denominator capability model that weakens Claude or Pi.
- Runtime reads from source skills during package emission.
- Silent fallback from missing validators or inactive hooks to a healthy status.
- Hand-maintained generated bundle or distribution trees.

## Verification Strategy

- Byte-level Claude goldens compare both the in-process compiler and frozen subprocess
  oracle against the same baseline.
- Structural tests enforce import boundaries, standard-library-only code, and no network
  calls.
- Anchor and ordering tests cover identity, exclusion, cycles, and argument-order
  independence.
- Capability tests cover descriptor completeness, mixed statuses, flag derivation, and
  no-silent-drop mutation controls.
- Pi loads the shipped extension through Pi's own loader and fires synthetic tool events
  to prove both blocking and benign negative controls.
- Codex launches the generated Node hooks with realistic event envelopes and exercises
  bypass, malformed-input, timeout, and oversized-input cases.
- Lifecycle tests cover dry-run, from-lock, uninstall, doctor, rollback, and validator
  absence.
- Freshness tests mutate generated bytes to prove both CI and plugin-side guards turn red.
