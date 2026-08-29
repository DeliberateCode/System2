# System2 Compiler — Context

> This document explains the compiler's problem, scope, users, constraints, risks,
> and open questions. It describes the intended behavior directly rather than using
> generated planning identifiers.
>
> The context is grounded in the existing composition engine, profile resolution,
> agent and hook enforcement model, overlay schemas, and structural golden suite.
> All source content is treated as untrusted data.

## Problem Statement

System2 is a spec-driven, verification-first orchestration protocol: a deliberate orchestrator delegates to 13 specialized roles across explicit quality gates (scope → context → requirements → design → tasks → ship), producing a `spec/` artifact chain and enforcing safety via real blocking mechanisms. Today this protocol exists **only** as a Claude Code plugin. Its composition engine (`composer.py`) consumes harness-neutral inputs (base content + overlay manifests + the anchor map + profiles) but emits exactly one harness projection: Claude Code artifacts (`CLAUDE.md`, `.claude/agents/*.md`, copied hooks, `.regex` allowlists, `spec/overlay-manifest.lock`).

The intent is to make the System2 workflow, Overlays, and Profiles run on additional agent harnesses — initially **Goose** and **Pi** — without (a) breaking the existing Claude Code experience and (b) forcing harness knowledge into every overlay, agent, and template.

There are two structural ways to get there, and the choice is the crux of this effort:

- **Option A — distribute harness support into every package.** Each of the 13 agents, every overlay, and the template carries Claude + Goose + Pi variants.
- **Option B — build a compiler.** Composition produces a harness-neutral intermediate representation (the "System2 graph" / IR); per-target backends render that IR into harness-native artifacts.

### The N×M argument (why a compiler wins)

Option A is an N×M explosion. With N harnesses and M neutral packages, distributed support forces every overlay author to understand each host's extension model merely to write domain guidance. That destroys the overlay ecosystem's write-once value.

A compiler is **N backends + M neutral packages = N+M**. It confines harness-specific logic to one place, so Claude Code becomes a protected reference target rather than a path put at risk by editing every agent and hook. **`composer.py` is already a compiler with exactly one hardcoded backend.** Everything through `_build_contribution_index` → `_topological_sort` → conflict detection → `profiles.py` is a harness-neutral front-end; only `_render_contribution`, `_generate_claude_md`, `_insert_overlay_sections`, and `_write_outputs` are the Claude projection. The task is therefore to **cut the seam that already exists**, not to bolt on a new package from scratch.

### The central hazard (the reason this is a compiler, not a text emitter)

On Claude Code the safety primitives **actually block**: the write-lease, `dangerous-command-blocker`, `sensitive-file-protector`, and the `.regex` path allowlists run as PreToolUse/PostToolUse hooks that exit non-zero. Other harnesses have fundamentally different enforcement surfaces (see Constraints and Risks). A naive port silently turns an **enforced** lease into **advisory prompt text** — the workflow still appears to "work" while the safety guarantee evaporates. This is why capabilities must be first-class typed objects in the IR with explicit per-target degradation reporting, not metadata. It is the #1 risk of the whole effort and the key open question for the design gate (it is *surfaced* here, not *resolved* here).

## Goals

- **Single-source, harness-neutral authoring.** Overlay authors and the 13 core agents write System2 concepts once; no overlay is required to carry Claude/Goose/Pi variants. Measurable: zero harness-specific content required in a baseline overlay to target all backends (escape hatch `targets.{claude,pi,goose}` remains rare and optional).
- **Compose-then-render split.** A `compose(core + overlays + profile) → System2 IR` phase distinct from `render(IR, target) → harness artifacts`. Measurable: the IR is the sole interface between front-end and every backend; no backend reads overlay manifests or the anchor map directly.
- **Byte-identical Claude reference fidelity.** The `claude-code` backend reproduces the current plugin output (`CLAUDE.md`, `.claude/agents/*.md`, copied hooks, `.regex` allowlists, `spec/overlay-manifest.lock`, warnings) **byte-for-byte**, verified by goldens diffed against the live `composer.py`. Measurable: golden diff is empty across the covered input matrix.
- **Capability-typed, never-silently-lossy backends.** Each backend declares, per capability, one of `native | adapted | advisory | unsupported`, emitted into the lock file as a degradation report. Measurable: every IR capability has an explicit per-target status; no capability is dropped without a corresponding lock-file entry.
- **Intent-declaring agents.** Agents declare *intent capabilities* (`enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`, plus write-scope, model hint, gate role) rather than Claude mechanisms (`tools:`, `hooks:`, `permissionMode`). The `claude-code` backend lowers intent back to today's hooks/allowlists/frontmatter. Measurable: agent definitions in the IR contain no Claude-specific mechanism fields.
- **Preserve Claude end-user UX exactly.** `/system2:init`, `/system2:compose`, `/system2:doctor`, and plugin install behave identically for end-users throughout the build. Measurable: no change to slash-command surface, command outputs, or installed file layout for Claude users.
- **Zero-dependency end-user path preserved.** At convergence, the plugin consumes a **vendored, stdlib-only** bundle of the `claude-code` backend — never a `pip install` for end-users. Measurable: the plugin's runtime dependency set stays empty (stdlib only), as `composer.py` is today.
- **Machine-enforced cross-repo freshness.** Vendored-bundle staleness between `System2-Compiler` and the `System2` plugin is caught by tooling (extended `system2:doctor` drift checks + a CI hash/staleness guard), not by manual discipline. Measurable: a stale vendored bundle fails CI and is reported by `doctor`.
- **Future-harness extensibility.** Adding harness #4 is "declare its capability map and write a backend," with no change to overlays, agents, or the template. Measurable: a new backend touches only `backends/` and `backends/capabilities/`.

## Non-Goals / Out of Scope

- **Reducing Claude Code to a lowest-common-denominator abstraction.** Claude stays the privileged, full-fidelity reference target; it is never degraded to match weaker harnesses.
- **Bash as the abstraction or translation layer.** Bash is only a thin generated installer or launcher per target. It does not represent role semantics, gate state, delegation, or enforcement.
- **`pip install` distribution for hobby end-users.** Out of scope by Gate 0; convergence uses a vendored stdlib-only bundle.
- **Per-target repositories as the initial topology.** The compiler is one package with a `backends/` directory. Per-target repositories are deliberately not adopted.
- **Requirements, design, tasks, or implementation.** This is context only. No EARS, no architecture, no code.
- **Resolving the enforced-vs-advisory enforcement policy per target.** Surfaced as an open question for the design gate; deliberately not decided here.
- **Changing System2 core, OverlayTemplate, or profile semantics.** They stay essentially as-is and harness-neutral.

## Users & Use-Cases

- **Hobby-programmer Claude end-users (primary, protected).** Install the plugin, run `/system2:init` / `/system2:compose` / `/system2:doctor`. They must see **no change** and must never need a Python dependency install. Their experience is the invariant the whole effort protects.
- **Overlay authors.** Write one harness-neutral overlay (domain principles, per-agent prompt sections at named anchors, required spec sections, auxiliary agents, intent capabilities). It compiles to every backend with no per-harness work. Rare advanced overlays may use the `targets.{…}` escape hatch.
- **Power users targeting Goose or Pi.** Run `system2 compile --profile X --target {goose,pi}` to get harness-native artifacts (Goose recipes + launcher; Pi context files, skills, prompt templates, and a generated TypeScript gate extension). They receive an honest degradation report describing exactly which safety capabilities are enforced vs. advisory on their chosen harness.
- **The maintainer (you).** Develops and protects Claude at full velocity against a frozen reference oracle; adds backends additively without regressing Claude; relies on machine-enforced drift checks to keep the vendored bundle fresh.

## Constraints & Invariants

The following decisions and discovered platform constraints are binding.

### Locked Gate 0 decisions (binding)

- **Standalone topology, all the way through.** The engine and all target backends live in one compiler package with `ir/`, `backends/`, capability descriptors, and `cli.py`; they are not colocated in the plugin.
- **Claude Code is the privileged, full-fidelity reference target; its UX must not change.** During the build, the plugin's `composer.py` stays a **FROZEN reference oracle**. The compiler's `claude-code` backend must reproduce its output byte-for-byte (goldens diffed against the live `composer.py`). The plugin keeps running its own `composer.py` until convergence.
- **Convergence (later) uses a vendored, stdlib-only bundle.** The plugin consumes a **vendored**, stdlib-only bundle of the `claude-code` backend — never `pip install` for end-users. The plugin's zero-dependency, just-works property is preserved. Cross-repo freshness is **machine-enforced** (extended `system2:doctor` drift checks + CI hash/staleness guard), never hand-maintained.
- **Backends are capability-typed and never silently lossy.** Each backend declares, per capability, `native | adapted | advisory | unsupported`, emitted into the lock file as a degradation report. Agents declare **intent** capabilities (`enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`, plus write-scope / model-hint / gate-role) rather than Claude mechanisms (`tools:`, `hooks:`, `permissionMode`).
- **Enforcement fidelity is the central risk (decision deferred to design).** Pi can reach **higher** fidelity than Goose because the compiler generates a TypeScript gate extension it owns (`on("tool_call")`, protected paths). Goose's safety layer is **built-in** (prompt-injection detection, permission controls, sandbox mode); arbitrary PreToolUse/PostToolUse scripts cannot be installed, so some hook semantics map only partially. The hazard is enforced leases silently becoming advisory text. The enforced-vs-advisory policy per target is **not** decided here.
- **Bash is not the abstraction layer.** Only a thin generated installer/launcher per target.
- **Overlays and Profiles stay harness-neutral and single-source**, with a rare optional escape hatch `targets.{claude,pi,goose}`.
- **Milestone-gated execution.** This context describes the full vision (Phases 0–5). The immediate implementation milestone after the spec chain is **Phases 0–2**. Phases 3–5 are in-scope for the vision and planned as reached.

### Discovered / platform constraints

- **Stdlib-only engine.** The lifted IR and engine preserve the current composition engine's standard-library-only runtime so the vendored Claude bundle introduces no end-user dependency.
- **Deterministic, byte-identical Claude output.** The `claude-code` backend reproduces ordering, whitespace, anchor insertion, lock shape, and path-safety behavior exactly. In particular, composition refuses a project path inside the plugin directory.
- **Anchor map is currently Claude-shaped.** `anchor-map.json` inserts overlay content by string-matching literal headings in Claude agent prompts (`after_section`, e.g. `"Safety rules:"`, `"## Anti-additive bias"`). Multi-target requires anchors to resolve against the **IR agent definition**, with each backend deciding how to render an anchored contribution into its representation. This is the genuine engineering of Phase 2.
- **Claude enforcement is real and blocking.** Agent frontmatter wires PreToolUse/PostToolUse/SubagentStop hooks (`dangerous-command-blocker.py`, `sensitive-file-protector.py`, `validate-file-paths.py` against per-agent `.regex` allowlists, `boundary-check.py`, `auto-formatter.py`, `type-checker.py`, `change-budget-reporter.py`). The write-lease lifecycle is orchestrated by `CLAUDE.md` (writing `.task-lease.regex` / `.task-budget.json`). Any IR capability model must faithfully capture this blocking semantics so backends can report honestly whether they reproduce it.
- **13-agent / 6-gate invariant.** The IR must represent the 13 roles (golden `agent_inventory.json`, expected_count 13), the gate graph (Gate 0 scope → Gate 5 ship), the delegation contract, post-execution trigger rules, the regression/maintenance loop, and the `spec/` artifact set — all currently encoded in `CLAUDE.md` and agent prompts.
- **Golden infrastructure exists and is extended, not replaced.** `System2/evals/goldens/` already holds structural goldens (agent inventory, allowlist bindings, hook inventory, delegation map, manifest schemas). Phase 0 extends this with output-level goldens.
- **Standalone package topology.** The compiler is independently releasable and keeps all target backends in one package.

## Success Metrics & Acceptance Criteria

### Definition of done — current cycle (Phases 0–2)

- **Freeze.** A golden suite snapshots current Claude output for a representative matrix (core + ≥1 overlay + ≥1 profile) covering `CLAUDE.md`, `.claude/agents/*.md`, `spec/overlay-manifest.lock`, and warnings. Re-running the live `composer.py` produces a byte-identical (or explicitly-justified semantically-identical) match. The plugin's `composer.py` is designated the frozen reference oracle.
- **Extract IR / split compose from render.** `composer.py` is cut along its existing seam: the front-end (through `_topological_sort` + `profiles.py`) builds a `System2Graph` IR; `_generate_claude_md` + `_insert_overlay_sections` + `_write_outputs` live behind a `Backend.emit(ir, project_path) -> written_files` interface as `backends/claude_code.py` in `System2-Compiler`. Phase 0 goldens are green; **no user-visible change**; the plugin still runs its own frozen `composer.py`.
- **Anchors to IR and capability model.** Anchor resolution is lifted from literal-heading matching to IR-level anchors. Agents declare intent capabilities; the `claude-code` backend lowers them back to today's hooks/allowlists/frontmatter with goldens still byte-identical. `backends/capabilities/*.json` exist and every backend reports `native | adapted | advisory | unsupported` per capability into the lock file. **No silent dropping.**

### Definition of done — overall effort

- All of Phases 0–2 plus: at least one non-Claude backend (Goose) emitting validated harness-native artifacts with an honest degradation report (Phase 3); a Pi backend including a generated TypeScript gate extension that buys real enforcement where chosen (Phase 4); and convergence — the plugin consumes the vendored stdlib-only `claude-code` backend bundle, with `system2:doctor` + CI drift guards green, and published "System2 for Goose / Pi" example packages (Phase 5). Claude end-user UX is unchanged throughout; no backend can regress the Claude path.

### Scope: full phased rollout (0–5)

- **Phase 0 — Freeze Claude behavior (current cycle).** Output-level goldens; designate frozen oracle.
- **Phase 1 — Extract IR / split compose from render (current cycle).** Cut the seam; `claude-code` backend behind `Backend.emit`.
- **Phase 2 — Lift anchors to IR + add capability model (current cycle).** IR-level anchors; intent capabilities; degradation reporting.
- **Phase 3 — First non-Claude backend (planned).** Committed: Goose first (recipe YAML gives a clean role→artifact mapping and forces the degradation path immediately). The throwaway bash "driver" alternative is rejected (resolved at Gate 1); bash stays a thin generated installer/launcher only.
- **Phase 4 — Pi backend (planned).** Context files, skills, prompt templates, plus a generated TypeScript gate/dispatcher extension for real enforcement.
- **Phase 5 — Convergence + publish (planned).** Optionally wire `/system2:compose` to call `system2 compile --target claude-code` internally (non-breaking); plugin consumes the vendored bundle; doctor/CI drift guards; publish example packages.

## Risks & Edge Cases

- **Highest risk — Enforcement-fidelity degradation.** Enforced write-leases / command-blocking silently becoming advisory prompt text on Goose (and on Pi if the gate extension is not generated). Mitigation direction: first-class typed capabilities + loud per-target degradation report; the enforced-vs-advisory policy is an explicit design-gate decision (see Open Questions). Goose's built-in safety layer cannot host arbitrary hooks; Pi can reach higher fidelity via an owned TS extension.
- **IR shape still being discovered.** The "System2 graph" boundary (roles, gate graph, delegation contract, spec artifact set, trigger rules, ordered overlay contributions, profile, intent capabilities) is inferred from the existing `composer.py` seam and `CLAUDE.md`, not yet formally specified. Mis-cutting the seam risks leaking Claude-isms into the IR or under-capturing blocking semantics. Phase 0 goldens are the safety net.
- **Cross-repo sync / drift.** The vendored stdlib-only `claude-code` bundle in the plugin can diverge from `System2-Compiler`. Mitigation: machine-enforced (`system2:doctor` drift checks + CI hash/staleness guard); never hand-maintained.
- **Anchor-map lift.** Moving from literal-heading `after_section` matching to IR-level anchors risks breaking insertion points or changing output ordering, which would fail byte-identical goldens. Bounded but genuine.
- **Byte-identical brittleness.** Strict byte-equality goldens may flag benign formatting differences; the spec must decide where semantic equivalence is acceptable and justified.
- **Capability taxonomy drift.** The intent-capability vocabulary (`enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`, write-scope, model-hint, gate-role) may prove incomplete as backends are written; adding a capability must not silently change Claude output.
- **Escape-hatch overuse.** `targets.{claude,pi,goose}` could erode the single-source property if it becomes a common path rather than rare.
- **Untrusted overlay/agent content.** Manifests and contribution files are untrusted; the IR builder and backends must not execute or trust embedded instructions (consistent with System2's own injection-resistance posture in `CLAUDE.md`).

## Observability / Telemetry expectations

- **The lock file is the primary observability surface.** The degradation report (per-capability `native | adapted | advisory | unsupported` per target) is emitted into the lock file (`spec/overlay-manifest.lock` for Claude; the per-target equivalent for Goose/Pi). A user must always be able to read which safety capabilities are enforced vs. merely described on their harness.
- **Golden diffs are the regression signal** for Claude fidelity (Phases 0–2): an empty diff against the frozen `composer.py` oracle is the pass condition.
- **Drift checks** (`system2:doctor` + CI hash/staleness guard) report vendored-bundle freshness at convergence (Phase 5).
- **Existing compose warnings** (validation warnings, conflict reports, semantic-tension warnings from `compatibility.review_when_combined_with_tags`) continue to surface and must remain byte-identical for Claude.
- No new runtime end-user telemetry is required; observability is compile-time/report-time, consistent with the local-first, zero-dependency posture.

## Rollout & Backward Compatibility

- **Claude end-users:** zero-change throughout. The plugin runs its own frozen `composer.py` until convergence; at convergence it swaps to a vendored stdlib-only bundle with identical behavior (goldens enforce this).
- **Overlay authors:** existing overlays continue to work unchanged; harness-neutrality is preserved; the escape hatch is additive and optional.
- **New capability for power users:** `system2 compile --profile X --target {claude-code,goose,pi}` is additive and opt-in.
- **Convergence is non-breaking:** wiring `/system2:compose` to call the compiler internally (Phase 5) changes nothing user-visible.
- **No `pip install` is ever introduced for end-users**.

## Open Questions

- **Enforced vs. advisory behavior per target.** Each non-Claude target must state whether write leases and command blocking are real gates or prompt-only guidance.
- **Convergence mechanics.** Bundle layout, freshness hashing, and doctor semantics must preserve a zero-dependency install.
- **Formal IR boundary.** Blocking semantics, write scope, gate roles, and anchors need a neutral representation that does not leak host mechanisms.
- **Golden comparison policy.** Byte equality remains the default; semantic equivalence needs explicit normalization and justification.

### Resolved Direction

- The first recipe-based backend validates the degradation path, while bash remains only generated launcher glue.
- The compiler remains one independently releasable package with all backends under a shared directory.

## Minimal Change Intent

- **Existing modules expected to absorb the change.** The `composer.py` front-end (`_build_contribution_index`, `_topological_sort`, conflict detection) and `profiles.py` are **lifted** into `System2-Compiler/ir/` as the IR builder. The Claude projection (`_render_contribution`, `_generate_claude_md`, `_insert_overlay_sections`, `_write_outputs`) is **moved behind** `Backend.emit(ir, project_path)` as `backends/claude_code.py`. The change is a *seam cut + relocation*, not a rewrite. The plugin's `composer.py` is **frozen** as the reference oracle and remains the plugin's runtime engine until convergence.
- **Abstractions explicitly out of scope unless later approved.** Per-target repositories, a standalone bash workflow, `pip`-based end-user distribution, changes to core overlay/profile semantics, and new end-user runtime dependencies.
- **API / surface that must remain unchanged unless explicitly required.** The Claude end-user command surface (`/system2:init`, `/system2:compose`, `/system2:doctor`, plugin install) and its installed file layout (`CLAUDE.md`, `.claude/agents/*.md`, hooks, `.regex` allowlists, `spec/overlay-manifest.lock`); the byte-level Claude output; the overlay manifest schema and `targets.{…}` escape-hatch shape (single-source authoring); the stdlib-only property of the engine.

## Glossary

- **System2** — the spec-driven, verification-first orchestration protocol (13 roles, 6 gates, `spec/` artifact chain), currently shipped as a Claude Code plugin.
- **System2 Compiler / `System2-Compiler`** — the standalone package built by this effort: IR builder (front-end) + capability-typed backends + CLI.
- **IR / System2 graph** — the harness-neutral intermediate representation produced by `compose`: roles, gate graph, delegation contract, spec artifact set, trigger rules, ordered overlay contributions, profile, and intent capabilities.
- **Backend / target / renderer** — a module that lowers the IR into one harness's native artifacts (`claude-code`, `goose`, `pi`). This document uses **backend** consistently.
- **Capability** — a typed, first-class IR object describing an *intent* (e.g., `enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`) plus role attributes (write-scope, model-hint, gate-role). Distinct from a Claude *mechanism* (`tools:`, `hooks:`, `permissionMode`).
- **Capability status** — per-backend, per-capability rendering fidelity: `native` (real, enforced/first-class) / `adapted` (emulated with equivalent effect) / `advisory` (prompt text only, not enforced) / `unsupported` (not represented).
- **Degradation report** — the per-target capability-status table emitted into the lock file; the honest record of what is enforced vs. advisory on a harness.
- **Enforced vs. advisory** — *enforced* = the harness actually blocks the disallowed action (Claude hooks exit non-zero); *advisory* = the constraint is only described in a prompt and may be ignored. The core safety distinction this project must not blur.
- **Reference oracle (frozen `composer.py`)** — the live plugin composer, held fixed during the build; the `claude-code` backend must reproduce its output byte-for-byte.
- **Anchor / anchor map** — named insertion points for overlay contributions. Today literal-heading `after_section` matches in Claude prompts (`anchor-map.json`); to become IR-level anchors resolved per backend.
- **Overlay** — an additive, harness-neutral package of domain contributions (principles, per-agent prompt sections, required spec sections, auxiliary agents, intent capabilities).
- **Profile** — a named, ordered set of overlays on a given core version; harness-neutral (compiles to any target).
- **Vendored bundle** — a copied, stdlib-only snapshot of the `claude-code` backend embedded in the plugin at convergence, preserving zero end-user dependencies (never `pip install`).
- **Escape hatch (`targets.{claude,pi,goose}`)** — rare, optional per-overlay harness-specific affordance; the exception to single-source authoring.
- **Harness** — an agent runtime (Claude Code, Goose, Pi, …) with its own extension/enforcement model.
