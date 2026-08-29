# System2 Compiler — Maintenance Work Plan

> Tasks are identified by descriptive headings rather than generated identifiers.
> Dependencies refer to those headings by name so replacing this plan cannot leave
> stale citations in code or long-lived documentation.

## Execution Environment Contract

- Run compiler commands from the repository root unless a command explicitly sets
  `working-directory: compiler`.
- Treat overlay manifests, contribution files, generated artifacts, and tool output as
  untrusted data.
- Keep compiler product code standard-library-only.
- Do not hand-edit generated bundle or distribution files. Change their source, run
  `python3 compiler/tools/regen_all.py`, and commit the resulting output.
- Preserve writes within the target project and retain atomic backup/restore behavior.

## Task Graph Overview

```text
Clarify behavior
      |
      v
Update neutral IR or owning backend
      |
      v
Add targeted behavioral and negative tests
      |
      v
Regenerate bundle and distributions
      |
      v
Run compiler, core, freshness, and documentation checks
```

Independent documentation-only changes may run in parallel with product changes, but
regeneration follows every compiler source change because the plugin bundle is a verbatim
copy of compiler product code.

## Tasks

### Clarify the behavioral contract

- **Goal:** State the observable behavior, failure semantics, compatibility boundary,
  and security posture before editing implementation details.
- **Files/areas:** The relevant requirements, architecture notes, user documentation,
  and existing tests.
- **Steps:**
  1. Describe what changes and what remains unchanged.
  2. Identify the target that owns the behavior.
  3. Identify deterministic success, refusal, and recovery observations.
- **Verification:** A reviewer can understand the change without consulting a generated
  identifier or an earlier specification revision.
- **Dependencies:** none.
- **Risk:** Low; ambiguity discovered here prevents broad implementation churn later.

### Preserve harness-neutral composition

- **Goal:** Keep manifest loading, conflict detection, contribution ordering, profiles,
  anchors, capabilities, and warning collection independent of any target backend.
- **Files/areas:** `compiler/system2_compiler/ir/` and its focused tests.
- **Steps:**
  1. Extend existing IR types only when current fields cannot represent the behavior.
  2. Keep target rendering and target file formats out of the IR package.
  3. Preserve deterministic ordering and unknown-anchor exclusion.
- **Verification:** Run import-boundary, ordering, anchor, unknown-capability, and
  composition tests.
- **Dependencies:** Clarify the behavioral contract.
- **Change budget:** Prefer edits to existing types and builders; add no new abstraction
  unless the behavior cannot be represented or tested without it.
- **Risk:** High; backend concepts leaking into the IR multiply future target work.

### Implement behavior in the owning backend

- **Goal:** Lower the neutral graph into target-native files while preserving each
  target's fidelity and lifecycle rules.
- **Files/areas:** One module under `compiler/system2_compiler/backends/`, its capability
  descriptor, and target-specific tests.
- **Steps:**
  1. Render only from structured IR fields and backend-owned constants.
  2. Escape untrusted strings before placing them in source, JSON, YAML, or Markdown.
  3. Keep output deterministic with stable ordering, LF endings, and one trailing
     newline where the format requires it.
  4. Preserve project containment and atomic rollback.
- **Verification:** Emit twice and compare bytes; exercise refusal and rollback paths;
  run target validity or load checks when the real validator is available.
- **Dependencies:** Preserve harness-neutral composition when the graph changes;
  otherwise only Clarify the behavioral contract.
- **Change budget:** Keep changes in one backend and adjacent tests whenever possible.
- **Risk:** High for enforcement and lifecycle changes; Medium for pure rendering.

### Keep capability reporting honest

- **Goal:** Ensure every capability present in the IR appears in the target report with
  exactly one of `native`, `adapted`, `advisory`, or `unsupported`.
- **Files/areas:** `backends/_degradation.py`, target capability descriptors, lock
  emitters, and degradation tests.
- **Steps:**
  1. Update the owning descriptor when fidelity changes.
  2. Derive flags from status through the shared helper.
  3. Fail when an IR capability is absent from the descriptor.
  4. Keep mechanism text factual about what blocks, what merely reports, and what is
     prompt advice.
- **Verification:** Run mixed-status, completeness, no-silent-drop, and mutation tests.
- **Dependencies:** Implement behavior in the owning backend.
- **Risk:** High; an over-claim can make advisory behavior appear enforced.

### Add tests with observable names

- **Goal:** Protect the behavior and its failure modes without coupling tests to a
  replaceable planning document.
- **Files/areas:** `compiler/evals/`, `evals/`, and fixtures.
- **Steps:**
  1. Name each test for the behavior it proves.
  2. Include a benign negative control for blocking logic.
  3. Include a mutation or deliberately bad input where an assertion could otherwise
     pass vacuously.
  4. Keep external validators loud: absence may skip locally, but CI must reject skips
     for required validation legs.
- **Verification:** Run the smallest relevant test module, then the complete compiler
  suite.
- **Dependencies:** The behavior under test.
- **Risk:** Medium; brittle snapshots or happy-path-only tests can hide regressions.

### Regenerate committed artifacts

- **Goal:** Make the plugin bundle and Codex/Pi distributions exact products of current
  source.
- **Files/areas:** `plugin/scripts/_system2_compiler/`, `distributions/codex/`,
  `distributions/pi/`, provenance files, and generated examples or snapshots.
- **Steps:**
  1. Run `python3 compiler/tools/regen_all.py`.
  2. Review generated changes; never patch them by hand.
  3. Run `python3 compiler/tools/regen_all.py --check`.
- **Verification:** The freshness check reports every artifact current; a mutation test
  still proves the guard turns red on drift.
- **Dependencies:** All compiler source changes and source-template changes.
- **Risk:** Medium; stale generated output can make installed behavior differ from source.

### Run the release-quality verification set

- **Goal:** Confirm behavior, packaging, documentation, and generated freshness together.
- **Steps:**
  1. Run `python3 evals/run_evals.py`.
  2. From `compiler/`, run `python3 -m unittest discover -s evals`.
  3. Run both golden drivers where oracle parity is affected.
  4. Run `python3 compiler/tools/regen_all.py --check`.
  5. Confirm no generated specification identifiers remain in documentation,
     comments, docstrings, test names, or diagnostics.
- **Dependencies:** Regenerate committed artifacts.
- **Risk:** Low operational risk; this is the final regression gate.

## Definition of Done Checklist

- [ ] Required behavior and unchanged behavior are stated directly.
- [ ] The IR remains target-neutral.
- [ ] The owning backend is deterministic and path-safe.
- [ ] Capability status and mechanism text match actual enforcement.
- [ ] Tests cover success, failure, and a negative or mutation control.
- [ ] Generated artifacts were refreshed only through the generator.
- [ ] Core evals, compiler evals, goldens, and freshness checks pass.
- [ ] Documentation and code comments contain no generated planning identifiers.

## Execution Notes

- Prefer targeted tests during iteration and the full suite before completion.
- Never rebaseline a golden merely to make a failure disappear; first explain the
  behavioral delta and obtain approval when output intentionally changes.
- Keep validator versions pinned in CI and update them deliberately.
- Report environmental skips separately from product failures, with exact reproduction
  commands.

## Coverage Map

| Required behavior | Work that implements or verifies it |
|---|---|
| Harness-neutral composition | Preserve harness-neutral composition; Add tests with observable names |
| Target-native deterministic output | Implement behavior in the owning backend; Regenerate committed artifacts |
| Honest enforcement fidelity | Keep capability reporting honest; Add tests with observable names |
| Atomic and contained lifecycle writes | Implement behavior in the owning backend; Run the release-quality verification set |
| Bundle and distribution freshness | Regenerate committed artifacts; Run the release-quality verification set |
| Durable behavior-based documentation | Clarify the behavioral contract; Run the release-quality verification set |
