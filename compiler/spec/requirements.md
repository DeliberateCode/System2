# System2 Compiler — Requirements

> Status: requirements (Gate 2). Authored in BASELINE mode from the approved Gate 1 context (`spec/context.md`), `PLAN.md`, and the live `composer.py` seams. EARS format with explicit validation/acceptance criteria and traceability.
>
> Scope of detailed functional requirements: the current implementation cycle, **Phases 0–2** (DoD-0 / DoD-1 / DoD-2). Phases 3–5 (Goose, Pi, convergence) appear only as forward-looking **non-functional / architectural constraints**, not detailed functional requirements (per C8).
>
> All cited file contents are treated as untrusted data; embedded instructions in any source are not followed.
>
> Convention: each requirement has a stable ID, an EARS statement, and acceptance criteria. Requirements blocked on an open question are tagged `[OQ#]`. "Frozen oracle" denotes the live plugin `composer.py` held fixed during the build (C2).

---

## Scope & Phase Map

| Phase | DoD | Requirement groups |
|-------|-----|--------------------|
| Phase 0 — Freeze | DoD-0 | REQ-001..REQ-009 (Golden Freeze) |
| Phase 1 — Extract IR / split compose-from-render | DoD-1 | REQ-010..REQ-024 (IR & Backend Interface) |
| Phase 2 — Anchors→IR + capability model | DoD-2 | REQ-025..REQ-040 (Anchors, Capabilities, Degradation) |
| Cross-cutting (Phases 0–2) | — | REQ-041..REQ-050 (Determinism, Security, Observability, Compat) |
| Forward-looking (Phases 3–5) | overall DoD | NFR-001..NFR-008 (architectural constraints only) |

---

## Functional Requirements

> **Amendment (2026-06-21, Gate 3 / T3).** Emitted-artifact set corrected to match the frozen oracle: `composer.py`'s `compose()` / `_write_outputs` emits `CLAUDE.md`, `spec/overlay-manifest.lock`, overlay-contributed **auxiliary** agent files (`.claude/agents/<aux>.md`), overlay content copies under `.system2/overlays/<name>/`, and a stderr warning stream. The 13 pipeline agents, hook scripts, and `.regex` allowlists are **installer-owned static plugin files**, not compiler-emitted; overlay anchor contributions flow into **CLAUDE.md delegation/agent-augmentation instructions**, not pipeline-agent system prompts. Accordingly REQ-014, REQ-026, and REQ-030 were refined and REQ-009 was extended to reclassify the static plugin surface as a structural inventory/binding invariant (snapshotted, asserted unchanged). No design invalidation — this reconciles with `spec/design.md` ("Discovered ground truth", Design Risk T3). IDs, other requirement content, and the validation/traceability matrices are otherwise unchanged.

### Group A — Phase 0: Golden Freeze (DoD-0)

**REQ-001 (Ubiquitous).** The system shall provide an output-level golden suite that snapshots the current Claude projection for a representative input matrix consisting of (a) core only, (b) core + at least one overlay, and (c) core + at least one overlay + at least one profile.
- *Acceptance:* The golden suite directory contains snapshot artifacts for ≥3 matrix cells covering cases (a), (b), and (c). A test enumerates the matrix and fails if any declared cell lacks a snapshot.
- *Traceability:* G3, DoD-0, C14.

**REQ-002 (Ubiquitous).** For each matrix cell, the golden snapshot shall capture, at minimum, the composed `CLAUDE.md`, every produced `.claude/agents/*.md`, the produced `spec/overlay-manifest.lock`, and the emitted warnings stream (validation warnings, conflict reports, semantic-tension warnings).
- *Acceptance:* Each snapshot cell contains files/records for all four artifact classes; a schema/manifest check asserts presence. Warning capture includes stderr warning text produced by `_emit_stderr_warnings` for that cell.
- *Traceability:* G3, DoD-0, C14, Observability.

**REQ-003 (Event-driven).** When the golden suite is run against the frozen oracle, the system shall compare each captured artifact to its stored snapshot and report any difference as a failing diff.
- *Acceptance:* A clean run against the unmodified frozen oracle yields an empty diff and a passing result; an injected one-byte change in any captured artifact yields a non-empty diff and a failing result.
- *Traceability:* G3, DoD-0, Observability.

**REQ-004 (State-driven).** While the comparison policy is set to its default, the system shall require **byte-identical** equality between produced artifact and stored snapshot for the pass condition. `[OQ6]`
- *Acceptance:* Default policy is `byte-identical`; a byte-level diff is used; non-equal bytes fail. Policy default is asserted by a configuration test.
- *Traceability:* G3, DoD-0, R5, OQ6.

**REQ-005 (Optional).** Where the comparison policy for a specific artifact class is configured to `semantic-equivalent`, the system shall permit a non-byte-identical match only when an explicit, recorded justification accompanies that artifact class. `[OQ6]`
- *Acceptance:* The policy is a parameter, not a hardcoded constant; selecting `semantic-equivalent` for an artifact class without a recorded justification is rejected; with justification, the normalized comparison is applied and the justification is retained alongside the suite.
- *Traceability:* G3, DoD-0, R5, OQ6. *Blocked on OQ6 (byte vs. semantic policy boundary) — design must resolve which artifact classes, if any, may opt into semantic equivalence.*

**REQ-006 (Ubiquitous).** The system shall designate the plugin's live `composer.py` as the frozen reference oracle for the duration of the Phase 0–2 build and record this designation in a discoverable artifact.
- *Acceptance:* A recorded oracle reference (path + content hash/version) exists in the suite; the suite reads the oracle from that recorded reference rather than an arbitrary copy.
- *Traceability:* C2, DoD-0.

**REQ-007 (Unwanted behavior).** If the frozen oracle's source content changes (its recorded hash no longer matches), then the system shall fail the golden suite with a message identifying the oracle drift, rather than silently re-baselining.
- *Acceptance:* Mutating the oracle source and re-running produces an explicit "oracle changed / re-baseline required" failure; snapshots are not auto-regenerated by a normal test run.
- *Traceability:* C2, DoD-0, R2.

**REQ-008 (Ubiquitous).** The golden suite shall extend the existing `System2/evals/goldens/` structural golden infrastructure rather than replace it, and shall run under the same stdlib-only harness constraints.
- *Acceptance:* Output-level goldens are added alongside the existing structural goldens (agent inventory, allowlist bindings, hook inventory, delegation map, manifest schemas); the existing structural goldens continue to pass unchanged; the new suite imports no third-party package.
- *Traceability:* C14, C9, G3.

**REQ-009 (Ubiquitous).** The representative matrix shall include the 13-agent / 6-gate invariant artifacts so that any change to role inventory, gate graph, delegation map, or spec artifact set is caught by a golden diff. The static plugin surface — the 13 pipeline agents, the hook scripts, and the `.regex` allowlists — shall be locked as a **structural inventory / binding invariant**: snapshotted and asserted unchanged byte-for-byte, and explicitly classified as **installer-owned static plugin files, NOT compiler-emitted/composed artifacts** (see Amendment 2026-06-21).
- *Acceptance:* The captured 13 pipeline agents (`expected_count = 13`), the hook-script inventory, and the `.regex` allowlist bindings are snapshotted via the existing structural goldens (`System2/evals/goldens/`); a removed/renamed pipeline agent, altered hook inventory, altered allowlist binding, or altered delegation map produces a failing diff. These inventories are asserted as unchanged static files — they are not produced by `compose → claude-code.emit` (REQ-014) and are excluded from the emitted-artifact set.
- *Traceability:* C13, G3, DoD-0; Amendment T3 (static-plugin-surface inventory invariant).

### Group B — Phase 1: Extract IR & Backend Interface (DoD-1)

**REQ-010 (Ubiquitous).** The system shall provide a front-end `compose(core + overlays + profile) → System2Graph` that produces a harness-neutral intermediate representation (the System2 graph / IR).
- *Acceptance:* A callable front-end returns an IR object/structure given core, an ordered overlay set, and a profile; the IR is produced without invoking any backend.
- *Traceability:* G2, DoD-1, OQ5.

**REQ-011 (Ubiquitous).** The IR shall be constructed by lifting the existing harness-neutral front-end logic — contribution indexing (`_build_contribution_index`), within-scope topological ordering (`_topological_sort`), conflict detection, and profile resolution (`profiles.py`) — without semantic change to ordering or conflict outcomes.
- *Acceptance:* For every matrix cell, the IR-derived ordering of contributions within each scope and the set of detected conflicts equal those of the frozen oracle (verified transitively via REQ-014's byte-identical output).
- *Traceability:* G2, DoD-1, R2, "seam cut not rewrite" (Minimal Change Intent).

**REQ-012 (Ubiquitous).** The IR shall represent the System2 graph contents required to render any backend: the 13 roles, the Gate 0→5 gate graph, the delegation contract, post-execution trigger rules, the regression/maintenance loop, the `spec/` artifact set, ordered overlay contributions, the active profile, and (Phase 2) intent capabilities. `[OQ5]`
- *Acceptance:* The IR exposes named fields for each listed element; a structural test asserts presence of all listed elements for the core-only cell. Precise schema is finalized at the design gate.
- *Traceability:* C13, G2, R2, OQ5. *Blocked on OQ5 (formal IR boundary) — design formalizes the schema; this requirement fixes the required content set.*

**REQ-013 (Ubiquitous).** The system shall define a backend interface `Backend.emit(ir, project_path) -> written_files` as the sole entry point by which an IR is lowered to harness artifacts.
- *Acceptance:* A `base` backend interface declares `emit(ir, project_path)` returning the list of written file paths; the `claude-code` backend implements it; the CLI/front-end invokes backends only through this interface.
- *Traceability:* G2, DoD-1.

**REQ-014 (Event-driven).** When the `claude-code` backend emits from an IR produced for any matrix cell, the system shall produce exactly the artifact set the frozen oracle writes — `CLAUDE.md`, `spec/overlay-manifest.lock`, overlay-contributed **auxiliary** agent files (`.claude/agents/<aux>.md`), and overlay content copies under `.system2/overlays/<name>/` — plus the stderr warning stream, all **byte-identical** to the frozen oracle's output for the same inputs (subject to the REQ-004/REQ-005 policy). This is the keystone fidelity requirement. (The 13 pipeline agents, hook scripts, and `.regex` allowlists are NOT emitted here; they are installer-owned static files asserted unchanged under REQ-009.) `[OQ6]`
- *Acceptance:* The Phase 0 golden suite passes with an empty diff when driven through `compose → claude-code.emit` instead of the oracle, across the full matrix, for `CLAUDE.md`, `spec/overlay-manifest.lock`, every produced auxiliary `.claude/agents/<aux>.md`, every overlay content copy under `.system2/overlays/<name>/`, and the stderr warning stream; no artifact outside this emitted set is produced by the backend.
- *Traceability:* G3 (highest priority), DoD-1, C10, OQ6; Amendment T3 (emitted-set correction).

**REQ-015 (Ubiquitous).** The IR shall be the sole interface between the front-end and every backend; no backend shall read overlay manifests, the anchor map, profiles, or the schema directly.
- *Acceptance:* Static inspection / dependency check shows backend modules import no manifest/anchor-map/profile loader and receive all needed data via the IR argument; a test fails if a backend references those source files.
- *Traceability:* G2, DoD-1.

**REQ-016 (Ubiquitous).** The lifted IR builder and the `claude-code` backend shall use Python standard library only, introducing no third-party runtime dependency.
- *Acceptance:* A dependency scan (e.g., reuse of `check_no_external_deps`) over `ir/` and `backends/claude_code.py` reports zero external imports.
- *Traceability:* C9, G7, DoD-1.

**REQ-017 (Ubiquitous).** Throughout Phase 1, the plugin shall continue to run its own frozen `composer.py` as its runtime engine; the compiler shall not be wired into the plugin's compose path.
- *Acceptance:* No change to the plugin's invocation of `composer.py`; the plugin's runtime behavior is unaffected by the existence of `System2-Compiler`.
- *Traceability:* C2, DoD-1, G6.

**REQ-018 (Ubiquitous).** Phase 1 shall produce no user-visible change to the Claude end-user surface (`/system2:init`, `/system2:compose`, `/system2:doctor`, plugin install) or installed file layout.
- *Acceptance:* Slash-command surface, command outputs, and installed file layout are unchanged; verified by REQ-014 byte-identity plus absence of plugin-side edits.
- *Traceability:* G6, DoD-1.

**REQ-019 (Ubiquitous).** The `claude-code` backend shall reproduce the lock file (`spec/overlay-manifest.lock`) shape, key ordering, and JSON formatting exactly as the frozen oracle emits it.
- *Acceptance:* Lock files match byte-for-byte across the matrix (`json.dumps(..., indent=2) + "\n"` formatting preserved).
- *Traceability:* G3, C10, DoD-1.

**REQ-020 (Ubiquitous).** The system shall preserve the existing composition safety invariant that `project_path` must not be inside or equal to the plugin/base directory.
- *Acceptance:* Composing with a `project_path` inside the base/plugin directory is rejected with the existing error semantics; a test exercises the rejected case.
- *Traceability:* C10, Security.

**REQ-021 (Event-driven).** When the front-end detects a known structural conflict between overlays, the system shall refuse to produce an IR/output for that combination, matching the frozen oracle's refusal behavior.
- *Acceptance:* A `known_conflicts` pair produces the same refusal and message text as the oracle; verified via golden warning/refusal capture.
- *Traceability:* G3, C10, DoD-1.

**REQ-022 (Event-driven).** When overlays share a tag listed in `compatibility.review_when_combined_with_tags`, the system shall emit the same semantic-tension warning text as the frozen oracle.
- *Acceptance:* Semantic-tension warnings appear identically (byte-level) in the captured warning stream across the matrix.
- *Traceability:* G3, Observability, DoD-1.

**REQ-023 (Unwanted behavior).** If the front-end encounters a `dry_run` request, then the system shall compute the IR and intended outputs without writing content files to project-local paths, matching the oracle's dry-run semantics.
- *Acceptance:* A dry-run produces the same reported `files_to_write` set as the oracle and writes nothing to disk.
- *Traceability:* C10, DoD-1, G6.

**REQ-024 (Ubiquitous).** The compiler shall reside in the standalone `System2-Compiler` package with the layout `ir/`, `backends/{base,claude_code}.py`, `backends/capabilities/*.json` (Phase 2), and `cli.py`, in its own git repository.
- *Acceptance:* The package contains the specified directories/modules; `claude-code` logic is not colocated in the System2 plugin; the package is an independent git repo.
- *Traceability:* C1, C15, DoD-1.

### Group C — Phase 2: Anchors→IR & Capability Model (DoD-2)

**REQ-025 (Ubiquitous).** The system shall resolve overlay anchor contributions against the IR agent definition rather than by literal-heading string matching in Claude prompt text.
- *Acceptance:* Anchors are represented as named IR-level insertion points keyed per agent; the `after_section` literal-heading match is no longer the resolution mechanism in the IR; resolution is driven by IR anchor identity.
- *Traceability:* C11, R4, DoD-2, G2.

**REQ-026 (Ubiquitous).** Each backend shall decide how to render an IR-anchored contribution into its own representation; the `claude-code` backend shall render anchored contributions into the **`CLAUDE.md` delegation / agent-augmentation instructions** (not into pipeline-agent system prompts) at the same placement and ordering as the frozen oracle.
- *Acceptance:* For every matrix cell that exercises anchors, the composed `CLAUDE.md` remains byte-identical to the oracle (golden diff empty), confirming that anchored-contribution placement and ordering within the `CLAUDE.md` delegation/agent-augmentation sections are preserved through the IR-level anchor lift. The pipeline-agent system prompts (installer-owned static files, REQ-009) are unaffected by anchor rendering.
- *Traceability:* C11, R4, G3, DoD-2; Amendment T3 (anchor rendering target = CLAUDE.md delegation instructions).

**REQ-027 (Ubiquitous).** The IR shall represent the full set of agent anchors currently defined in `anchor-map.json` for all 13 agents, preserving anchor identity and per-agent scoping.
- *Acceptance:* Every `(agent, anchor)` pair present in `anchor-map.json` and `valid_anchor_names_by_agent` is representable and resolvable in the IR; a contribution to a non-existent anchor is excluded exactly as the oracle excludes it.
- *Traceability:* C11, C13, DoD-2.

**REQ-028 (Ubiquitous).** Agents in the IR shall declare **intent capabilities** drawn from the vocabulary `enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`, plus role attributes `write-scope`, `model-hint`, and `gate-role` — rather than Claude mechanisms (`tools:`, `hooks:`, `permissionMode`).
- *Acceptance:* IR agent definitions contain no Claude-specific mechanism fields; capability fields use the declared intent vocabulary; a test asserts absence of `tools:`/`hooks:`/`permissionMode` in IR agent objects.
- *Traceability:* G5, C4, DoD-2.

**REQ-029 (Ubiquitous).** The capability model shall faithfully capture the blocking semantics of the current Claude enforcement surface (write-lease lifecycle, `dangerous-command-blocker`, `sensitive-file-protector`, `.regex` path allowlists via `validate-file-paths`, `boundary-check`, `auto-formatter`, `type-checker`, `change-budget-reporter`) so that fidelity can be reported honestly per backend.
- *Acceptance:* Each enforced Claude mechanism maps to exactly one intent capability in the IR; a mapping table exists and is asserted; no enforced mechanism is left unrepresented.
- *Traceability:* C12, C4, R1, DoD-2.

**REQ-030 (Event-driven).** When the `claude-code` backend lowers intent capabilities, the system shall reproduce today's static enforcement surface — the hooks, `.regex` allowlists, and pipeline-agent frontmatter — where that surface is **installer-owned static plugin files asserted unchanged byte-for-byte** (per REQ-009), not artifacts emitted by the backend. The intent-capability lowering shall not alter the composed outputs the backend does emit (REQ-014), keeping all Phase 0 goldens byte-identical.
- *Acceptance:* After the intent-capability lowering, the golden suite remains empty-diff across the matrix: the emitted composed outputs (REQ-014) are unchanged byte-for-byte, and the static hook wiring, `.regex` allowlist bindings, and pipeline-agent frontmatter remain unchanged byte-for-byte as asserted via the structural goldens (REQ-009). No enforced capability is dropped from the degradation report (REQ-033).
- *Traceability:* G5, G3, C12, DoD-2; Amendment T3 (static enforcement surface asserted-unchanged, not emitted).

**REQ-031 (Ubiquitous).** The system shall provide per-backend capability descriptors at `backends/capabilities/*.json` (starting with `claude_code.json`) declaring, for each capability, a status of `native`, `adapted`, `advisory`, or `unsupported`.
- *Acceptance:* `backends/capabilities/claude_code.json` exists and assigns one of the four statuses to every capability in the IR vocabulary; a schema check validates the enum and completeness.
- *Traceability:* C4, G4, DoD-2.

**REQ-032 (Ubiquitous).** Every backend shall emit a per-capability degradation report into its lock file, recording the `native | adapted | advisory | unsupported` status of each capability present in the IR.
- *Acceptance:* The produced lock file contains a degradation-report section enumerating every IR capability with its status for that backend; a lock-file assertion verifies completeness.
- *Traceability:* C4, G4, DoD-2, Observability.

**REQ-033 (Unwanted behavior).** If a capability present in the IR is not rendered natively by a backend, then the system shall record its degraded status (`adapted`, `advisory`, or `unsupported`) in the degradation report and shall not drop it silently.
- *Acceptance:* No IR capability is absent from the degradation report; a test that removes a capability's report entry fails; there is no code path that discards a capability without a corresponding report entry.
- *Traceability:* C4, G4, R1, DoD-2 ("No silent dropping").

**REQ-034 (State-driven).** While the active backend is `claude-code`, the degradation report shall reflect that all enforced safety capabilities are `native` (real, blocking), consistent with Claude being the full-fidelity reference target.
- *Acceptance:* The `claude-code` lock file reports `native` for `enforce-lease`, `block-dangerous`, `protect-sensitive` and the other enforced capabilities; verified against `claude_code.json`.
- *Traceability:* C12, G4, Non-Goals (Claude never degraded).

**REQ-035 (Ubiquitous).** Adding a new intent capability to the vocabulary shall not change the `claude-code` byte-level output unless that capability is explicitly lowered into Claude artifacts.
- *Acceptance:* Introducing a new capability with no Claude lowering leaves the golden diff empty; only an explicit lowering changes output.
- *Traceability:* R6, G3, DoD-2.

**REQ-036 (Ubiquitous).** The degradation report's per-capability status vocabulary shall be exactly `native`, `adapted`, `advisory`, `unsupported` (no synonyms or additional values).
- *Acceptance:* A schema/enum check rejects any status outside the four-value set.
- *Traceability:* C4, Glossary (capability status), DoD-2.

**REQ-037 (Ubiquitous).** The lock file shall remain machine-readable and the primary observability surface for which safety capabilities are enforced versus advisory on a given harness.
- *Acceptance:* The degradation report is parseable JSON within the lock file; a reader can determine enforced-vs-advisory status per capability without consulting any other artifact.
- *Traceability:* Observability, C4, G4.

**REQ-038 (Ubiquitous).** Phase 2 changes shall preserve the harness-neutral, single-source authoring property of overlays: a baseline overlay shall require zero harness-specific content to target all backends.
- *Acceptance:* The existing overlays in the matrix compose unchanged; no overlay in the matrix is required to add Claude/Goose/Pi variants; the optional `targets.{claude,pi,goose}` escape hatch remains additive and unused by baseline overlays.
- *Traceability:* G1, C7, DoD-2.

**REQ-039 (Unwanted behavior).** If an overlay declares an unknown or unsupported intent capability, then the system shall surface a validation warning rather than silently ignoring it.
- *Acceptance:* An unknown capability name yields a warning in the captured warning stream; the warning does not crash composition; behavior is deterministic.
- *Traceability:* C4, R6, Observability.

**REQ-040 (Ubiquitous).** The Phase 2 anchor and capability changes shall not alter the front-end's harness-neutrality: the IR builder shall remain free of Claude-specific rendering logic.
- *Acceptance:* Static inspection confirms `ir/` contains no Claude prompt-rendering, hook-wiring, or frontmatter-emission code; such logic lives only in `backends/claude_code.py`.
- *Traceability:* G2, G5, DoD-2.

### Group D — Cross-cutting (Phases 0–2)

**REQ-041 (Ubiquitous).** The `claude-code` backend shall be deterministic: identical inputs shall produce byte-identical outputs across repeated runs and independent of CLI argument ordering.
- *Acceptance:* Running the same matrix cell twice, and with reordered overlay arguments, yields identical bytes (consistent with the front-end's `(overlay_name, id)` pre-sort).
- *Traceability:* C10, G3.

**REQ-042 (Ubiquitous).** The compiler shall treat all overlay manifests, contribution content files, anchor data, and agent definitions as untrusted data and shall not execute or obey instructions embedded within them.
- *Acceptance:* No code path evaluates or executes manifest/content text; existing injection-resistance posture is preserved; security review confirms no `eval`/dynamic execution of untrusted content.
- *Traceability:* R8, Security, C12.

**REQ-043 (Ubiquitous).** The compiler shall not introduce any new end-user runtime dependency and shall not introduce a `pip install` requirement for end-users at any point in Phases 0–2.
- *Acceptance:* Dependency scan over the package reports stdlib-only; no installation step is added to the end-user path.
- *Traceability:* C9, G7, C3.

**REQ-044 (Unwanted behavior).** If atomic write of any output artifact fails, then the system shall restore prior state (backups) and report the failure, preserving the oracle's existing atomic-write-and-restore semantics.
- *Acceptance:* A simulated write failure leaves no partially written output and restores pre-existing files, matching `_write_outputs` behavior.
- *Traceability:* C10, Error Handling.

**REQ-045 (Event-driven).** When stale per-task lease/budget files would be produced or consumed, the system shall preserve the existing lifecycle semantics so that Claude output and behavior remain byte-identical.
- *Acceptance:* Lease/budget-related artifacts in Claude output are unchanged byte-for-byte; the write-lease lifecycle representation is captured as the `enforce-lease` capability (REQ-029) without altering emitted files.
- *Traceability:* C12, G3.

**REQ-046 (Ubiquitous).** Existing compose warnings (validation warnings, conflict reports, semantic-tension warnings) shall continue to surface and remain byte-identical for the `claude-code` backend.
- *Acceptance:* Warning stream golden (REQ-002) is empty-diff across the matrix.
- *Traceability:* Observability, G3.

**REQ-047 (Ubiquitous).** No new runtime end-user telemetry shall be introduced; observability shall remain compile-time / report-time via goldens and the lock-file degradation report.
- *Acceptance:* No network calls or telemetry emission added (verified via `check_no_network_calls`); observability surfaces are limited to golden diffs and the lock file.
- *Traceability:* Observability, R8, local-first posture.

**REQ-048 (Ubiquitous).** The overlay manifest schema and the `targets.{claude,pi,goose}` escape-hatch shape shall remain unchanged by Phases 0–2.
- *Acceptance:* `overlay.schema.json` contract for authoring is unchanged; the escape hatch shape is preserved; existing overlays validate unchanged.
- *Traceability:* C7, G1, Backward Compatibility.

**REQ-049 (Ubiquitous).** The compiler CLI shall expose `system2 compile --profile X --target claude-code` as an additive, opt-in capability that does not alter the Claude end-user command surface.
- *Acceptance:* The CLI accepts `--profile` and `--target claude-code`; invoking it produces the byte-identical Claude output; the plugin's `/system2:*` commands are unaffected.
- *Traceability:* G6, DoD-1, Rollout.

**REQ-050 (Ubiquitous).** The system shall represent the 13-role inventory, the 6-gate graph (Gate 0 scope → Gate 5 ship), the delegation contract, post-execution trigger rules, the regression/maintenance loop, and the `spec/` artifact set in the IR such that any backend can reproduce them. `[OQ5]`
- *Acceptance:* The IR exposes these elements (cross-references REQ-012); the `claude-code` backend reproduces them byte-identically; structural goldens (agent inventory = 13, delegation map) remain green.
- *Traceability:* C13, G2, OQ5.

---

## Open Requirements

These are intentionally under-specified pending an open question; the design gate must resolve them.

- **OPEN-1 (from OQ6).** The exact set of artifact classes (if any) permitted to use `semantic-equivalent` comparison, and the normalization rules, are unresolved. Default remains byte-identical (REQ-004). Governs REQ-005.
- **OPEN-2 (from OQ5).** The formal IR schema (precise representation of blocking semantics, write-scope, gate-role, and anchors) is discovered during Phase 1 and formalized at the design gate. Governs REQ-012, REQ-050.
- **OPEN-3 (from OQ1).** The enforced-vs-advisory policy per non-Claude target is **not** decided in this cycle. It does not block Phases 0–2 (Claude is fully `native`) but the capability model (REQ-028..REQ-037) must be expressive enough to record either outcome for future backends. Governs NFR-003.
- **OPEN-4 (from OQ3).** Vendor-vs-install mechanics at convergence (bundle layout, hash/staleness guard, `doctor` drift semantics) are deferred to Phase 5. Governs NFR-005, NFR-006.

---

## Data & Interface Contracts

- **IR (System2Graph).** Harness-neutral structure produced by `compose(core, overlays, profile)`. Required content: 13 roles; Gate 0→5 graph; delegation contract; post-execution trigger rules; regression/maintenance loop; `spec/` artifact set; ordered overlay contributions; active profile; (Phase 2) per-agent intent capabilities and role attributes; IR-level anchors. Formal schema deferred to design (OPEN-2). No Claude mechanism fields permitted (REQ-028, REQ-040).
- **Backend interface.** `Backend.emit(ir, project_path) -> written_files` (list of written file paths). Sole lowering entry point (REQ-013, REQ-015). Backends receive only the IR and the target path; they do not read manifests/anchor-map/profiles/schema.
- **Capability descriptor.** `backends/capabilities/<backend>.json`: maps each capability in the IR vocabulary to one of `{native, adapted, advisory, unsupported}` (REQ-031, REQ-036).
- **Lock file.** `spec/overlay-manifest.lock` (Claude) / per-target equivalent: JSON, `json.dumps(indent=2) + "\n"` formatting preserved (REQ-019); contains the per-capability degradation report (REQ-032, REQ-037). Machine-readable; primary observability surface.
- **Capability vocabulary (fixed for this cycle).** Intent: `enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`. Role attributes: `write-scope`, `model-hint`, `gate-role`. Status enum: `native | adapted | advisory | unsupported`.
- **Idempotency / determinism.** Identical inputs → byte-identical outputs; argument-order independent (REQ-041).
- **Persistence.** Atomic write with backup/restore semantics preserved (REQ-044).

## Error Handling & Recovery

- Schema/anchor-map load failure → composition returns an error result without writing (preserve oracle behavior). (REQ-010 context, C10)
- `project_path` inside/equal to base/plugin dir → rejected with explicit error. (REQ-020)
- Known overlay conflict → refusal with oracle-identical message. (REQ-021)
- Atomic write failure → restore backups, report failure, no partial output. (REQ-044)
- Oracle drift detected → fail golden suite, do not auto-rebaseline. (REQ-007)
- Unknown intent capability declared by an overlay → validation warning, no crash. (REQ-039)
- No new retry/timeout surfaces are introduced (compile-time, local, deterministic; no network).

## Performance & Scalability

- No explicit latency budget beyond "comparable to the frozen oracle"; compose is a local, deterministic, single-shot operation over a bounded overlay set.
- Acceptance threshold: the `compose → claude-code.emit` path shall not increase end-to-end composition wall-clock time by more than a small constant factor over the oracle for the matrix cells (qualitative; no regression to user-perceived `/system2:compose` latency, which in this cycle still runs the oracle anyway per REQ-017).
- Memory/footprint: stdlib-only, in-process; no new persistent services.

## Security & Privacy

- All overlay/agent/manifest content treated as untrusted; no execution of embedded instructions (REQ-042).
- No new network calls or telemetry (REQ-047).
- Path-safety invariant preserved: never write into the installed plugin directory (REQ-020).
- Logging hygiene: no secrets/credentials emitted; degradation report and warnings contain only capability/status/identity metadata.
- Enforcement honesty: the safety distinction enforced-vs-advisory must never be blurred; for Claude it remains `native` (REQ-034); for future backends it must be reported, never silently downgraded (REQ-033, cross-cutting per R1).

## Observability

- Golden diffs are the Claude-fidelity regression signal (empty diff = pass) for Phases 0–2 (REQ-003, REQ-014, REQ-046).
- The lock-file degradation report is the primary capability observability surface (REQ-032, REQ-037).
- Existing compose warnings continue to surface byte-identically (REQ-022, REQ-046).
- No runtime end-user telemetry (REQ-047). Drift checks (`system2:doctor` + CI guard) are a Phase 5 concern (NFR-006), not built in this cycle.

## Backward Compatibility & Migration

- Claude end-users: zero change throughout Phases 0–2; plugin runs its own frozen `composer.py` (REQ-017, REQ-018). Convergence swap to a vendored bundle is Phase 5 (out of scope here; NFR-005).
- Overlay authors: existing overlays compose unchanged; harness-neutrality preserved; escape hatch additive/optional (REQ-038, REQ-048).
- New CLI `--target claude-code` is additive/opt-in (REQ-049).
- No `pip install` for end-users at any point (REQ-043).
- The byte-identical golden net (REQ-014) is the migration safety guarantee for the seam cut.

## Compliance / Policy Constraints

- Locked Gate 0 constraints C1–C15 are binding; this document does not contradict them. Tensions, where surfaced by source reviews, are recorded in "Assumptions & Risks," not silently resolved.
- No policy/regulatory compliance regime applies beyond the project's own injection-resistance and zero-dependency posture.

## Forward-Looking Non-Functional / Architectural Constraints (Phases 3–5 — NOT detailed functional requirements)

> Recorded per C8 as architectural constraints the Phase 0–2 design must not foreclose. These are deliberately not decomposed into testable functional requirements in this cycle.

- **NFR-001 — Capability-typed extensibility.** The IR, backend interface, and capability-descriptor model shall be shaped so that adding a future harness (Goose, Pi) touches only `backends/` and `backends/capabilities/`, with no change to overlays, agents, or the template. *(G9)*
- **NFR-002 — Goose first.** The first non-Claude backend is Goose (committed at Gate 1); the architecture must support recipe-YAML role→artifact mapping and a thin generated launcher (bash only as installer/launcher, never abstraction). *(OQ2 resolved, C6)*
- **NFR-003 — Enforcement-fidelity honesty (cross-cutting).** No capability may be silently downgraded on any target; degradation must be reported. The enforced-vs-advisory policy per target is an explicit design-gate decision before Phase 3. *(R1, OQ1; the Phase 0–2 capability model REQ-028..REQ-037 is the substrate.)*
- **NFR-004 — Pi higher fidelity.** The model must allow Pi to reach higher enforcement fidelity than Goose via an owned generated TypeScript gate extension. *(C5, Phase 4)*
- **NFR-005 — Vendored, stdlib-only convergence.** At convergence the plugin consumes a vendored stdlib-only `claude-code` bundle, never `pip install`. *(C3, G7, OQ3)*
- **NFR-006 — Machine-enforced cross-repo freshness.** Vendored-bundle staleness is caught by `system2:doctor` drift checks + CI hash/staleness guard. *(G8, R3, OQ3)*
- **NFR-007 — Bash is not the abstraction layer.** Only a thin generated installer/launcher per target. *(C6)*
- **NFR-008 — Single standalone topology.** No per-target repos / per-target compiler packages as day-one topology. *(C1, deferred reviews B/C.)*

## Validation Plan

| Mechanism | Requirements validated |
|-----------|------------------------|
| Output golden byte-diff vs. frozen oracle (full matrix) | REQ-002, REQ-003, REQ-004, REQ-014, REQ-019, REQ-022, REQ-026, REQ-030, REQ-035, REQ-041, REQ-045, REQ-046, REQ-050 |
| Structural-inventory goldens — static plugin surface asserted unchanged (13 pipeline agents, hook inventory, `.regex` allowlist bindings, delegation map) | REQ-009, REQ-030 |
| Matrix-completeness / suite structure test | REQ-001, REQ-008 |
| Comparison-policy parameter test | REQ-004, REQ-005 |
| Oracle-pinning / drift test | REQ-006, REQ-007 |
| Backend-interface & IR structural tests | REQ-010, REQ-011, REQ-012, REQ-013, REQ-024, REQ-050 |
| Dependency-isolation / no-backend-reads-source check | REQ-015, REQ-016, REQ-040, REQ-043 |
| Stdlib-only / no-network scan | REQ-016, REQ-043, REQ-047 |
| Path-safety / atomic-write tests | REQ-020, REQ-044 |
| Conflict / dry-run behavioral tests | REQ-021, REQ-023 |
| Anchor-resolution IR tests | REQ-025, REQ-027 |
| Capability-vocabulary / mechanism-absence tests | REQ-028, REQ-029 |
| Capability-descriptor schema/enum check | REQ-031, REQ-036 |
| Lock-file degradation-report assertion (completeness, no-drop) | REQ-032, REQ-033, REQ-034, REQ-037 |
| Schema-stability / overlay-compat test | REQ-038, REQ-048 |
| Unknown-capability warning test | REQ-039 |
| CLI surface test | REQ-049 |
| Injection-resistance / security review | REQ-042 |
| No-user-visible-change inspection | REQ-017, REQ-018 |

## Traceability Matrix

| Requirement | Context Goal(s) | Constraint(s) | DoD |
|-------------|-----------------|---------------|-----|
| REQ-001 | G3 | C14 | DoD-0 |
| REQ-002 | G3 | C14 | DoD-0 |
| REQ-003 | G3 | — | DoD-0 |
| REQ-004 `[OQ6]` | G3 | — | DoD-0 |
| REQ-005 `[OQ6]` | G3 | — | DoD-0 |
| REQ-006 | — | C2 | DoD-0 |
| REQ-007 | — | C2 | DoD-0 |
| REQ-008 | G3 | C9, C14 | DoD-0 |
| REQ-009 | G3 | C13 (+Amendment T3: static-plugin-surface inventory invariant) | DoD-0 |
| REQ-010 | G2 | — | DoD-1 |
| REQ-011 | G2 | — | DoD-1 |
| REQ-012 `[OQ5]` | G2 | C13 | DoD-1 |
| REQ-013 | G2 | — | DoD-1 |
| REQ-014 `[OQ6]` | G3 | C10 (+Amendment T3: emitted-set correction) | DoD-1 |
| REQ-015 | G2 | — | DoD-1 |
| REQ-016 | G7 | C9 | DoD-1 |
| REQ-017 | G6 | C2 | DoD-1 |
| REQ-018 | G6 | — | DoD-1 |
| REQ-019 | G3 | C10 | DoD-1 |
| REQ-020 | — | C10 | DoD-1 |
| REQ-021 | G3 | C10 | DoD-1 |
| REQ-022 | G3 | — | DoD-1 |
| REQ-023 | G6 | C10 | DoD-1 |
| REQ-024 | — | C1, C15 | DoD-1 |
| REQ-025 | G2 | C11 | DoD-2 |
| REQ-026 | G3 | C11 (+Amendment T3: anchors→CLAUDE.md delegation) | DoD-2 |
| REQ-027 | — | C11, C13 | DoD-2 |
| REQ-028 | G5 | C4 | DoD-2 |
| REQ-029 | G4 | C12 | DoD-2 |
| REQ-030 | G3, G5 | C12 (+Amendment T3: static surface asserted-unchanged, not emitted) | DoD-2 |
| REQ-031 | G4 | C4 | DoD-2 |
| REQ-032 | G4 | C4 | DoD-2 |
| REQ-033 | G4 | C4 | DoD-2 |
| REQ-034 | G4 | C12 | DoD-2 |
| REQ-035 | G3 | — | DoD-2 |
| REQ-036 | G4 | C4 | DoD-2 |
| REQ-037 | G4 | C4 | DoD-2 |
| REQ-038 | G1 | C7 | DoD-2 |
| REQ-039 | G4 | C4 | DoD-2 |
| REQ-040 | G2, G5 | — | DoD-2 |
| REQ-041 | G3 | C10 | DoD-1 |
| REQ-042 | — | C12 | all |
| REQ-043 | G7 | C3, C9 | all |
| REQ-044 | — | C10 | DoD-1 |
| REQ-045 | G3 | C12 | DoD-2 |
| REQ-046 | G3 | — | DoD-1 |
| REQ-047 | — | — | all |
| REQ-048 | G1 | C7 | all |
| REQ-049 | G6 | — | DoD-1 |
| REQ-050 `[OQ5]` | G2 | C13 | DoD-1 |
| NFR-001 | G9 | C1 | overall |
| NFR-002 | — | C6 (OQ2) | Phase 3 |
| NFR-003 | G4 | C4, C5 (OQ1) | Phase 3+ |
| NFR-004 | — | C5 | Phase 4 |
| NFR-005 | G7 | C3 (OQ3) | Phase 5 |
| NFR-006 | G8 | C3 (OQ3) | Phase 5 |
| NFR-007 | — | C6 | Phase 3+ |
| NFR-008 | — | C1 | all |

### Goal coverage check (G1–G9)

| Goal | Covered by | Status |
|------|-----------|--------|
| G1 — single-source harness-neutral authoring | REQ-038, REQ-048 | Covered (Phase 2) |
| G2 — compose-then-render split | REQ-010..REQ-015, REQ-040, REQ-050 | Covered (Phase 1) |
| G3 — byte-identical Claude fidelity | REQ-014 (+ REQ-002/003/004/019/026/030/041/046) | Covered (high-priority) |
| G4 — capability-typed, never-silently-lossy | REQ-029, REQ-031..REQ-037, REQ-039 | Covered (Phase 2) |
| G5 — intent-declaring agents | REQ-028, REQ-030, REQ-040 | Covered (Phase 2) |
| G6 — preserve Claude UX exactly | REQ-017, REQ-018, REQ-023, REQ-049 | Covered |
| G7 — zero-dependency end-user path | REQ-016, REQ-043 | Covered (this cycle); convergence via NFR-005 |
| G8 — machine-enforced cross-repo freshness | NFR-006 | **Not covered in Phases 0–2 (Phase 5); intentional gap per C8** |
| G9 — future-harness extensibility | NFR-001 | **Architectural constraint only (Phase 3+); intentional gap per C8** |

> Gaps G8 and G9 are deliberate: they are full-vision goals whose functional realization is Phase 3–5 work, out of scope for the current cycle (C8). They are preserved as non-functional constraints (NFR-001, NFR-006) so Phase 0–2 design does not foreclose them.

## Assumptions & Risks

- **A1.** `compose → claude-code.emit` is a seam cut + relocation of existing logic, not a rewrite (per Minimal Change Intent). Risk: mis-cutting leaks Claude-isms into the IR or under-captures blocking semantics (R2); mitigated by REQ-014/REQ-040 and the golden net.
- **A2.** The representative matrix (REQ-001) is assumed sufficient to exercise anchors, conflicts, semantic-tension warnings, and ≥1 profile. Risk: an un-exercised code path differs from the oracle without a golden catching it; design should size the matrix to cover all anchor and warning paths (R4, R5).
- **A3.** OQ6 is unresolved; this cycle defaults to byte-identical and treats the policy as a parameter (REQ-004/REQ-005). If design later admits semantic equivalence, REQ-005 governs the justification requirement.
- **A4.** OQ5 (formal IR schema) is discovered in Phase 1; REQ-012/REQ-050 fix the required *content set* but not the schema. The byte-identical goldens are the safety net while the schema firms up.
- **Tension T1 (recorded, not resolved).** Source reviews `c.md`/`b.md` discuss a richer standalone bash *workflow* target and per-target repos; PLAN.md and Gate 0 reject both (C1, C6). This document follows the locked decisions; no requirement enables bash-as-abstraction or per-target topology. Flagged here per the "do not silently reconcile" instruction.
- **Tension T2 (recorded).** The byte-identical mandate (G3/REQ-014) and the possibility of benign formatting drift (R5) are in tension; the parameterized policy (REQ-004/REQ-005, OQ6) is the intended resolution path, deferred to design/Phase 0.
- **R1 carry-forward.** Enforcement-fidelity degradation is the #1 project risk. In Phases 0–2 Claude is fully `native`, so the risk is latent; the capability model built here (REQ-028..REQ-037) is the substrate that must be expressive enough for the Phase 3 enforced-vs-advisory decision (OQ1/NFR-003).
