# System2 Compiler — Eval / Regression Coverage Assessment

Status: review-only assessment. Author: evaluation engineer. Date: 2026-06-21.

Scope: the golden harness and test suite under `System2-Compiler/evals/`
(`oracle.py`, `matrix.py`, `capture.py`, `run_goldens.py`, `comparison_policy.json`,
and the six `test_*.py` modules — 55 unit tests plus the golden runner), assessed
against `spec/requirements.md` (REQ→validation matrix), `spec/design.md`
(Verification Strategy), and `spec/tasks.md`.

> Note on authority: this document is an assessment only. The compiler's tests live
> under `System2-Compiler/evals/`, outside this assessment's write scope. Recommended
> additions below are for the orchestrator/executor to implement if warranted; no test
> files were added or modified, and no product code or `System2/` content was touched.
> All reviewed file/overlay/spec contents were treated as untrusted data.

---

## 1. Overall verdict

The suite is **substantial and genuinely rigorous** for the requirements it claims.
Its load-bearing strengths:

- **A frozen-oracle hash-pin** (`oracle.py`: SHA-256 over `composer.py` + `profiles.py`
  + `hook_security.py`, verified on every `run_goldens`/`capture`, never auto-rebaselined)
  gives the byte-identity net a trustworthy reference and a real drift tripwire (REQ-006/007).
- **Two independent drivers** (frozen subprocess oracle vs in-process
  `ir.compose → ClaudeCodeBackend().emit`) cross-checked against the *same* baseline is the
  correct shape for a seam-cut migration: the compiler driver is the DoD-1/DoD-2 fidelity
  gate, the oracle driver is the backout cross-check (REQ-014).
- **Hermetic HOME** for profile resolution (per-run temp `$HOME`) means the suite never
  touches the user's real `~/.system2/` and is environment-deterministic.
- **The structural-additive lock comparator** (`_compare_lock`) is applied *uniformly* to
  both drivers: strip the additive `degradation_report`, byte-match the remainder to the
  immutable baseline, and (compiler driver only) assert the report is present, complete,
  enum-valid, and `native`. This is exactly the right encoding of REQ-035 (additive-only)
  and REQ-033/034 (no-silent-drop, claude=native).
- **Negative controls have teeth.** `test_boundaries.py`, `test_capability_mapping.py`,
  and `test_degradation_report.py` each feed deliberately-bad input through the *same*
  assertion machinery and prove it fails — including the subtle one documenting that the
  vendored `check_no_external_deps` false-positives on relative imports so a future "fix"
  reverting to the raw scanner is caught.

The headline weakness is **breadth of the golden matrix, not depth of the checks**: the
five cells exercise only one composed-overlay shape, so several distinct byte-output and
refusal paths are asserted only transitively (or not at all). Nothing found rises to a
ship blocker for the current phase; the gaps are concentrated where Phase 3 (Goose) will
first stress the harness.

**Ship verdict:** the suite is sufficient to gate the Phase 0–2 DoDs. No gap below is
severe enough to block ship *for the claude-code reference backend*. The highest-value
gaps (G1, G2) are cheap, close real determinism/composition holes, and should land before
Phase 3 rather than after.

---

## 2. Coverage vs requirements

### Well-covered (high confidence)

| REQ | Mechanism | Assessment |
|---|---|---|
| REQ-006/007 | `oracle.verify_pin` hash-pin + `DRIFT_MESSAGE`; never auto-rebaselines | Strong. Pin covers all three oracle sources; rebaseline is an explicit flag only. |
| REQ-014 | Compiler-driver byte-diff across the matrix (`test_lowering_invariance.CompilerDriverGreenGate`) | Strong **for the cells present**. CLAUDE.md / aux agents / warnings byte-identical; lock additive-only. See matrix-breadth gaps below. |
| REQ-033 | `_compare_lock(require_report=True)` + `test_no_silent_drop_negative_control` + `test_no_enforced_capability_dropped_from_report` (report set == IR capability union per cell) | Strong, with a real negative control. |
| REQ-034 | every report/descriptor status asserted `native` | Strong. |
| REQ-035 | strip-report-then-byte-match on every composed cell, both drivers | Strong; this is the cleanest invariant in the suite. |
| REQ-009 / T3 | `StaticSurfaceInventoryInvariantGate`: SHA-256 of the 10 read-only structural goldens pinned via `core/structural_goldens.json`, plus explicit 13-agent count and base-template pin | Strong. Correctly models the static surface as asserted-unchanged, not emitted. |
| REQ-015/040/016/043/047/017 | AST import-boundary + stdlib-only + no-network + no-plugin-import scans, with synthetic negative controls | Strong. Level-aware wrapper is a notable correctness detail. |
| REQ-020/021/023/044 | path-safety (incl. sibling-prefix guard), conflict-refusal byte-parity vs oracle, dry-run writes-nothing, atomic-restore with leftover scan + positive control | Strong; the failure/recovery matrix the goldens don't reach is covered here. |
| REQ-029/031/036/037 | mechanism→capability completeness, descriptor enum/completeness, report self-sufficiency, with negative controls | Strong. |
| REQ-039 | unknown-capability warning at both the validator seam and the full compose path, including a determinism check | Strong. |
| REQ-004/005 | comparison-policy load/validate; under-justified `semantic-equivalent` rejected | Strong (policy is byte-identical this cycle but the gate is live). |

### Weak / transitive-only / missing

| REQ | Current coverage | Gap |
|---|---|---|
| **REQ-025/027** (anchor resolution by IR identity; per-agent scoping; **contribution to a non-existent anchor excluded exactly as the oracle excludes it**) | **Transitive only.** `ir/anchors.py` has a dedicated `AnchorTable` with explicit non-existent-anchor filtering and a per-agent valid-anchor scan, but **no test exercises it directly.** The only anchor exercise is the single `executor.implementation_discipline` contribution in `core+overlay`, validated solely via CLAUDE.md byte-identity. The design's Verification Strategy *names* an "Anchor-resolution IR test (identity-keyed; non-existent anchor excluded as oracle does)" — that test does not exist. | **G2 below.** The most under-covered claimed requirement. The exclusion path (contribution to an unknown anchor) and per-agent scoping (same anchor name on a different agent) are wholly untested. |
| **REQ-041** (deterministic; **independent of CLI argument ordering**) | **Partial.** `composed_at` reuse is exercised by lock byte-identity, and `test_unknown_capability` checks run-to-run determinism on one path. But the *argument-ordering* clause — reordered `--overlays` yields identical bytes — has **no test at all.** | **G1 below.** |
| **REQ-028** (mechanism-absence: no `tools`/`hooks`/`permissionMode` in the IR) | **Missing as an explicit assertion.** Covered only implicitly by the import-boundary tests (which prove the *builder* imports no backend) and the capability-vocabulary tests. No test inspects an IR/graph instance and asserts the absence of Claude-mechanism fields. | G6 below (low value; boundary tests largely subsume it). |
| **REQ-046** (warning parity) | Covered for the single composed-overlay and the conflict/tension cells. The warning *renderer* parity (`cli._emit_stderr_warnings`) is exercised, but only on the warning shapes those four cells produce. | Folds into matrix-breadth (G3). |
| **REQ-038/048** (schema-stability / `--allow-newer-schema` escape hatch unused) | **Missing.** No eval references `allow_newer_schema` / `schema_version` mismatch. The design lists "schema-stability / overlay-compat (escape hatch unused)" as a mechanism. | G4 below. |
| REQ-042 | By security-review only (acceptable per its own acceptance text); no `eval`/dynamic-exec exists to test. No automated assertion that untrusted manifest text never reaches an executor. | Low value; acceptable as-is. |

---

## 3. Matrix adequacy

The five declared cells (`core`, `core+overlay`, `core+overlay+profile`, `core+conflict`,
`core+tension`) and the artifacts present under `goldens/` cover:

- base template + 13-agent/6-gate static inventory (`core`);
- a *single* composed overlay (`core+overlay` / `core+overlay+profile`) exercising
  principles, gate-3 consultation, an advisory source, **one** anchored `prompt_sections`
  contribution, **one** spec required-section, and **one** auxiliary agent;
- known-conflict refusal (`core+conflict`);
- semantic-tension warning that proceeds (`core+tension`).

**Composition paths NOT exercised by any golden cell:**

1. **`after`-ordering between overlays.** The `after` field exists in the test-overlay
   manifest but is `null`. No cell has two overlays whose contributions must be
   topologically ordered relative to each other, so the IR's within-scope `after`
   ordering (REQ-011) is only ever exercised on a single-contributor scope.
2. **Multiple overlays contributing to the same anchor / high-leverage surface.** The
   tension cell's two overlays have **empty `contributions: {}`** — they exercise only the
   shared-review-tag warning, not co-contribution. No cell stacks two overlays onto the
   same `(agent, anchor)`, the same gate, or the same principle list. Ordering, dedup, and
   merge at a shared insertion point are untested end-to-end.
3. **Contribution to a non-existent anchor (exclusion path).** No overlay contributes to
   an unknown anchor, so the oracle-parity exclusion behavior (REQ-027 acceptance) is never
   diffed.
4. **`--allow-newer-schema` / newer-schema manifest.** No cell carries a forward
   `schema_version`; the only documented degraded mode is untested (REQ-038/048).
5. **Dry-run goldens.** Dry-run is unit-tested behaviorally (`test_behavior.DryRunTest`:
   writes nothing, `files_to_write` populated) but there is **no golden** asserting the
   dry-run `files_to_write` *plan* is byte-stable across runs / matches the oracle's
   dry-run plan.
6. **Ordering-cycle refusal.** The design's error table lists "Ordering cycle in `after`
   declarations → structural conflict; refuse (oracle-identical)" (REQ-021), but the only
   refusal cell is a `known_conflicts` pair. The cycle-refusal branch is untested.
7. **Multiple profiles / a profile resolving >1 overlay.** The profile cell resolves the
   single test-overlay; profile composition with several overlays is untested.

The matrix is adequate to prove the *seam cut preserves byte-identity for one
representative shape*. It is **not** broad enough to prove composition algebra (ordering,
merge, exclusion) is preserved, because every composed cell is effectively single-source.

---

## 4. Determinism / idempotency

- **`composed_at` reuse + fingerprint stability is structurally enforced, not directly
  asserted.** Both `capture.py` and `run_goldens.py` *seed* the prior golden lock into the
  temp project before composing, so a matching `content_fingerprint` reuses the frozen
  `composed_at`; the byte-identical lock diff then passes *as a consequence*. There is no
  test that (a) composes twice without seeding and asserts the second run reuses the first
  run's `composed_at`, nor (b) mutates the fingerprint and asserts `composed_at` is
  *regenerated*. The reuse mechanism is therefore validated only inline via the diff, and a
  regression that ignored a seeded lock could be masked if it happened to reproduce the same
  timestamp. (See G5.)
- **Run-to-run determinism** is directly tested only on the unknown-capability validation
  stream (`test_warning_is_deterministic_across_repeated_runs`). Good but narrow.
- **CLI argument-ordering independence (REQ-041)** is not tested at all (G1).

## 5. Negative controls

Strong across the board. Every assertion family that could silently rot has a paired
"prove it fails on bad input" test: forbidden-import detection, third-party-import
detection, network-pattern detection, plugin-import detection, unmapped-mechanism /
dropped-capability completeness breakage, out-of-enum status rejection, dropped-report-entry
no-silent-drop, and the sibling-prefix path-safety guard (a positive control against a naive
`startswith`). The `test_level_aware_scanner_fixes_vendored_false_positive` test is a
particularly good guard against a future simplification reintroducing a false positive.
The refusal cells also assert non-zero exit explicitly, so a refusal silently degrading to
success would fail.

The one gap in negative controls: there is **no negative control proving the byte-identity
golden diff itself has teeth** — i.e. no test that perturbs an artifact and confirms
`run_goldens` reports a failure. The comparator logic is straightforward, but a fixture-level
"mutate one byte → expect one failure" smoke test would lock the comparator's own
sensitivity.

## 6. Future-proofing for Phase 3 (Goose)

The capability/degradation harness is **shaped correctly** for a non-`native` backend:

- The four-value status enum (`native`/`adapted`/`advisory`/`unsupported`) and the
  enforced-vs-advisory classification (`test_report_is_self_sufficient_for_enforced_vs_advisory`)
  are already in place and computable from the lock alone.
- `_compare_lock` already takes a `require_report` flag and validates *every* entry's status
  against the enum and presence of a `mechanism` — so a Goose report with `advisory`/
  `unsupported` entries would be structurally validated today.
- The descriptor model (`backends/capabilities/claude_code.json`) is parameterized per
  backend and completeness-checked against the IR vocabulary.

**But the enforced-vs-advisory *degradation* path is asserted-absent, not asserted-correct.**
Every current test asserts the claude-code report is *all* `native`. There is no fixture or
backend stub that produces a `degraded` report, so the assertion machinery for "this
capability is `advisory`, here is its honest mechanism string, and the enforced-vs-advisory
classification flips accordingly" has **never executed its non-native branch**. The
comparator's `status != "native"` failure arm and the classification's `advisory` arm are
unexercised. This is the single most important Phase-3-readiness gap: the harness is *shaped*
for degradation but its degradation logic is dead code until a non-native descriptor exists.
(See G2-Phase3 below.)

---

## 7. Gaps ranked by value

### G1 — CLI argument-ordering determinism (REQ-041) — **HIGH, cheap**
No test reorders `--overlays` and asserts byte-identical output. REQ-041 explicitly
requires order-independence and cites the front-end `(overlay_name, id)` pre-sort.
*Recommended:* a test that composes a two-overlay input in both argument orders and asserts
identical CLAUDE.md + lock bytes. **Why it matters:** order-dependence is a classic
composition regression that the current single-overlay matrix structurally cannot catch, and
this is the cheapest high-value addition.

### G2 — Direct anchor-resolution + exclusion test (REQ-025/027) — **HIGH**
The design's Verification Strategy promises this test; it is absent. `ir/anchors.py`'s
non-existent-anchor filter and per-agent scoping are untested except transitively.
*Recommended:* a unit test over `build_anchor_table` / the anchor-filter path that asserts
(a) a contribution to an unknown `(agent, anchor)` is excluded and surfaces the same warning
the oracle emits, and (b) the same anchor name on two different agents resolves independently.
**Why it matters:** REQ-025/027 are core Phase-2 requirements claimed as "Covered" in the
traceability matrix but only validated by a single happy-path byte diff; the exclusion branch
(the actual risk surface) is dark.

### G3 — Multi-overlay composition cell(s) (REQ-011/014/041/046) — **HIGH**
Every composed golden is single-source. Add a matrix cell with two overlays that
co-contribute to the same anchor and the same gate, with non-null `after` ordering between
them, captured as a new golden. **Why it matters:** this is the only way to put the
ordering/merge/dedup algebra under the byte-identity net; today a merge regression would pass
the suite. This is the largest *breadth* gap and the one most likely to hide a real seam-cut
defect.

### G4 — `--allow-newer-schema` / newer-schema refusal + escape-hatch (REQ-038/048) — **MEDIUM**
The only degraded mode in the design is untested. *Recommended:* a behavioral test that a
newer `schema_version` is refused without the flag and accepted (verbatim, oracle-parity) with
it. **Why it matters:** named in the Verification Strategy; the refusal-vs-accept branch is a
distinct exit-code/behavior path with zero coverage.

### G5 — Explicit `composed_at` reuse/regeneration test (REQ-014 determinism) — **MEDIUM**
Make the idempotency mechanism a first-class assertion rather than an inline consequence of
seeding. *Recommended:* compose into a seeded project and assert `composed_at` is reused
byte-for-byte; mutate the fingerprint and assert it is regenerated. **Why it matters:** locks
the determinism mechanism against a regression that the diff alone could mask.

### G6 — Ordering-cycle refusal + REQ-028 mechanism-absence — **LOW**
A cycle-in-`after` refusal cell (oracle-parity) closes the second refusal branch listed in
the design's error table. A direct "no `tools`/`hooks`/`permissionMode` field on any IR
node" assertion makes REQ-028 explicit rather than implied by the import boundaries.
**Why it matters:** completeness; both are largely subsumed by existing checks, hence low.

### G2-Phase3 — A degraded (non-native) backend/descriptor fixture — **HIGH for Phase 3, not now**
Add a synthetic descriptor (or backend stub) that reports at least one `advisory`/`unsupported`
capability, and assert: the report records the degraded status + mechanism (no silent drop,
REQ-033), the enforced-vs-advisory classification flips, and the lock comparator's non-native
arm fires/passes appropriately. **Why it matters:** until this exists, the degradation machinery
the whole project hinges on (the #1 latent risk, R1) has never run its degraded branch. This is
not a current-phase blocker (claude-code is fully native) but should be the *first* Phase-3 test.

### G7 — Comparator self-teeth smoke test — **LOW**
One "mutate one golden byte → expect exactly one `run_goldens` failure" test to lock the
byte-diff comparator's own sensitivity. Cheap insurance.

---

## 8. How to run (for reference)

From `System2-Compiler/` (package root):

- Unit suite: `python3 -m pytest evals/` (or `python3 -m unittest discover -s evals`).
- Golden cross-check (frozen oracle): `python3 -m evals.run_goldens --driver oracle`.
- Golden fidelity gate (in-process compiler): `python3 -m evals.run_goldens --driver compiler`.
- Re-baseline (explicit only): `python3 -m evals.run_goldens --rebaseline`.

Exact invocation/CI wiring is **unknown: requires user confirmation** (no CI config or
CLAUDE.md run-contract was in scope for this assessment). Recommended CI integration point:
gate merges on `pytest evals/` **plus** `run_goldens --driver compiler` (the DoD gate) with
the oracle hash-pin verified first; treat any `DRIFT_MESSAGE` as a hard stop requiring an
explicit, reviewed `--rebaseline`.

---

## 9. Traceability (REQ → eval) — deltas from the claimed matrix

| REQ | Design claims | Reality |
|---|---|---|
| REQ-025, REQ-027 | "Anchor-resolution IR test" | **No such test.** Transitive byte-diff only. → G2 |
| REQ-041 | "Output golden byte-diff … REQ-041" | `composed_at` determinism transitively covered; **arg-ordering clause untested.** → G1 |
| REQ-011 | byte-identity transitively | Only single-source ordering exercised; multi-overlay `after` untested. → G3 |
| REQ-038, REQ-048 | "Schema-stability / overlay-compat … escape hatch unused" | **No `allow-newer-schema` test.** → G4 |
| REQ-028 | "mechanism-absence … no tools/hooks/permissionMode in IR" | Implied by boundary tests; **no direct assertion.** → G6 |
| REQ-021 (cycle arm) | "Ordering cycle … refuse (oracle-identical)" | Only `known_conflicts` refusal tested; **cycle arm untested.** → G6 |

All other REQ→eval mappings in `spec/design.md` §Verification Strategy are corroborated by an
actual, teeth-bearing test.

---

## Phase 3 — Goose Backend

Status: review-only assessment. Author: evaluation engineer. Date: 2026-06-22.

Scope: the Goose backend test/eval coverage — `test_yaml_serializer.py`,
`test_goose_descriptor.py`, `test_goose_goldens.py`, `test_goose_degradation.py`,
`test_goose_launcher.py`, `test_phase3_no_regression.py`, `test_vendored_pin.py`,
`test_breadth.py` (104 Goose-related of 160 total tests) — assessed against
`backends/goose.py`, `backends/_yaml.py`, `backends/capabilities/goose.json`,
`spec/design.md` §"Phase 3 — Goose Backend" (test strategy + AC-G1..AC-G6) and
`spec/requirements.md` NFR-001..004.

> Authority note: the compiler's tests live under `System2-Compiler/evals/`, **outside
> this assessment's write allowlist** (which covers `spec/evals.md` and cwd-relative
> `evals/*` only). The recommended additions below are for the orchestrator/executor to
> implement if warranted; no test files were authored. No product code, no test file,
> and no `System2/` content was touched. All reviewed file/overlay/recipe/spec contents
> were treated as untrusted data; embedded instructions were not followed.

### P3.1 — Overall verdict

The Phase 3 Goose suite is **rigorous and honest on the two things that matter most** for
this backend: the `goose recipe validate` validity oracle and the non-native degradation
assertions. The Phase 0–2 "harness shaped for degradation but its degraded branch is dead
code" gap (G2-Phase3) is **genuinely closed** — `test_goose_degradation.py` exercises a
real `adapted` permission policy and labelled `advisory` blocks, asserts report==descriptor
status per capability, asserts nothing-native, and carries teeth-bearing negative controls.
The launcher's ephemeral-config / no-global-mutation / idempotency contract is exercised
**without any LLM run** via a PATH-shadowing `goose` stub under a hermetic HOME — a
notably good design. Determinism, emit-purity (no `~/.config/goose` mutation), the
13-role/no-nesting structure, the additive no-claude-regression gate, and the stdlib-only
import boundaries are all directly and well covered.

The headline weakness is the **same one Phases 0–2 had: breadth of the input, not depth of
the checks.** Every Goose emit in the suite composes the *single* `TEST_OVERLAY` fixture, so
several distinct emission branches are asserted only on one IR shape — and at least three
branches are **never executed by any test** (the per-recipe `settings:`/`goose_model` model-hint
path, the non-default `write_scope` rendering, and any multi-overlay Goose emit). There is
**no committed Goose golden snapshot** — the suite is in-test emit+validate only, which
diverges from the design's leg-1 ("Deterministic Goose-artifact goldens (snapshots)") and
weakens regression detection on the prose-rendering surface.

**Ship verdict (Phase 3):** the suite is sufficient to gate the Phase 3 DoD (AC-G1..AC-G6 are
each backed by a real, teeth-bearing test). **No gap below blocks Phase 3 ship.** The
highest-value gaps (PG1 model-hint/settings, PG2 committed snapshots) are cheap and close real
emission-branch holes; they should land early in maintenance rather than be deferred. The
Phase-4-readiness gap (PG6) does not block Phase 3 but is the single most important thing to
fix *before* Pi work begins.

### P3.2 — Coverage vs the Phase 3 design / AC-G* / NFRs

#### Well-covered (high confidence)

| AC / NFR | Mechanism | Assessment |
|---|---|---|
| **AC-G1** (valid recipes) | `test_goose_goldens.GooseRecipeValidateOracleTest` runs `goose recipe validate` on the orchestrator **and every** `agents/*.recipe.yaml` (≥14 recipes), under a hermetic temp HOME, asserting `~/.config/goose` is byte-untouched; LOUD skip (never silent pass) when goose absent, via `shutil.which`/`GOOSE_BIN`. `test_yaml_serializer` adds a minimal-recipe validate smoke leg with the same gating. `_assert_parameters_referenced` pre-checks referenced⊇declared at emit time. | **Strong** — the oracle is exercised on **all** emitted recipes, exactly per the design's leg 2. (Caveat: see PG1 — no emitted recipe under test carries a `settings:` block, so that schema corner is never validated.) |
| **AC-G3 / NFR-003** (OQ1-correct degradation, no silent downgrade) | `test_goose_descriptor` (descriptor enum/completeness/nothing-native/OQ1 map exact) + `test_goose_degradation` (report status == descriptor status per cap; nothing-native; completeness == IR cap union; LOUD `DEGRADATION` banner; `enforced:false`/`gated:true|false` flags). Negative controls: dropped entry breaks completeness, native-flip trips the invariant, report/descriptor status drift detected. | **Strong, and this is the real win.** The Phase-0–2 dead-degradation-branch gap is closed: the `adapted` and `advisory` arms both execute on a real emit. |
| **AC-G6** (non-native exercised, not just claimed) | `test_goose_degradation`: the **adapted** path asserts a real `goose/permission.yaml` with `user:` entries for Bash/shell/Read/Write/Edit + `ask_before` + `never_allow_commands`; the **advisory** path asserts a labelled `NOT ENFORCED ON GOOSE: <cap>` block in the recipe text for each of the four advisory caps. | **Strong** — adapted ≠ enforced (T6) is encoded as `enforced:false, gated:true` and asserted. |
| **AC-G2** (faithful representation) | `test_goose_goldens.GooseEmitStructureTest`: exactly 13 role sub-recipes + orchestrator referencing all 13; role recipes declare **no** `sub_recipes` (no-nesting); 17-file emit set; launcher shebang + `bash -n` clean + `GOOSE_MODE=smart_approve`. | **Strong on structure.** (Caveat: the *content* of the gate-graph / delegation-contract / post-exec rendering is asserted only indirectly via `goose recipe validate` passing + a committed-snapshot-free emit — see PG2; a prose-drift that still validates would not be caught.) |
| Launcher behavior (ephemeral XDG config, no global mutation, idempotency) | `test_goose_launcher`: a PATH-shadowing `goose` stub (validate→0; run→records `XDG_CONFIG_HOME` + copies the config it saw) under a hermetic HOME drives the **real** launcher shell logic with **no LLM/network**. Asserts: ephemeral dir built (≠ user config), permission.yaml delivered verbatim, user `config.yaml` carried, real `~/.config/goose` byte-untouched, **run-twice idempotent**, trap-cleanup (+ `KEEP_CONFIG`), and `SYSTEM2_NO_PERMISSIONS` runs against the user config with a loud notice (with a teeth negative control that default≠NO_PERMISSIONS XDG paths). | **Strong** — directly answers the "ephemeral-config + no-global-mutation + idempotency tested without a real LLM run" question: **yes, and well.** |
| **AC-G4 / NFR-001** (additive, no regression) | `test_phase3_no_regression`: both backends registered + `--target` accepts/rejects; claude-code goldens empty-diff under both drivers (`run_goldens`); Goose emit does not perturb claude bytes; Goose writes only its own surface (no `CLAUDE.md`/`overlay-manifest.lock`). | **Strong.** |
| **AC-G5 / NFR-001** (stdlib-only, IR-only, no carrier leak) | `test_phase3_no_regression.GooseBoundaryTest`: AST import scan — `goose.py` imports only `ir.graph` + `backends._yaml`/`base` + stdlib; `_yaml.py` stdlib-only with no IR/backend knowledge; no-network scan; **static check that `goose.py` never references `base_template`/`overlay_inputs`** (the Claude-targeted carriers, OQ-G3). | **Strong** — the "render from structured IR only" invariant is statically enforced. |
| Serializer correctness (AC-G1/AC-G5 substrate) | `test_yaml_serializer` (27 tests): conservative quoting (colon/hash/leading-dash/bool-lookalike/numeric-lookalike), scalar typing (bool≠int), block-literal indent, insertion-order-preserved, LF + single trailing newline, JSON-flow fallback, byte-determinism, + the validate smoke leg. | **Strong on the enumerated cases.** (Caveat: happy-path-ish — see PG5 on adversarial fuzzing.) |
| F-03 vendored-pin drift guard | `test_vendored_pin`: byte-identity of `ir/profiles.py` / `ir/_hook_security.py` vs the plugin originals, with logic-line + unsanctioned-import negative controls. | **Strong** (a security-relevant carry-forward, not Goose-specific but in the Phase 3 batch). |

#### Direct answers to the assessment questions

- **Is `goose recipe validate` exercised on ALL emitted recipes?** **Yes** — orchestrator +
  all 13 role sub-recipes (`GooseRecipeValidateOracleTest`), plus a standalone minimal-recipe
  smoke leg, both with loud-skip-when-absent. The one un-validated *shape* is a recipe carrying
  a `settings:`/`goose_model` block (PG1).
- **Is the non-native degradation path (adapted/advisory) actually asserted** (the Phase 0–2
  gap)? **Yes** — this is the strongest part of the Phase 3 suite. Real `permission.yaml`
  (adapted) + labelled NOT-ENFORCED blocks (advisory) + report flags, with negative controls.
- **Is the launcher's ephemeral-config + no-global-mutation + idempotency tested without a real
  LLM run?** **Yes** — via the `goose` stub under a hermetic HOME; the real `~/.config/goose`
  is snapshotted before/after and asserted byte-identical, and run-twice idempotency is a
  dedicated test.

### P3.3 — Gaps ranked by value (each with the AC/NFR it covers and why it matters)

#### PG1 — Model-hint `settings:` / non-default `write_scope` emission is never exercised (AC-G1/AC-G2/OQ-G1) — **HIGH, cheap; the most important non-Phase-4 gap**

`backends/goose.py:395-396` emits `recipe["settings"] = {"goose_model": role.model_hint}`
when a role carries a `model_hint`, and `_role_instructions` renders a non-default
`write_scope`. **Verified empirically:** the `TEST_OVERLAY` IR carries **no** role with a
`model_hint` (all 13 are empty) and **all** `write_scope` values are empty. Therefore:
- The `settings:`/`goose_model` branch is **dead under test** — and crucially, **`goose recipe
  validate` never validates a recipe with a `settings:` block.** The design explicitly flags
  the exact `settings` key/shape as an empirical open question (OQ-G1) that *must* be pinned
  against the validator. The suite does not pin it, so a wrong `settings` shape would ship
  un-caught by the very oracle the design relies on.
- The non-default `write_scope` rendering branch in `_role_instructions` is likewise unexercised.

*Recommended:* add a small fixture overlay (or a synthetic IR) where ≥1 role carries a
`model_hint` and a non-empty `write_scope`, then (a) run `goose recipe validate` on that role
recipe (pins the `settings` shape against the real oracle — closes OQ-G1), and (b) assert the
emitted `settings.goose_model` value and the scoped advisory line. **Why it matters:** this is
the one place the validity-oracle coverage has a real hole, on the exact schema corner the
design called out as needing empirical validation.

#### PG2 — No committed Goose golden snapshot; emit-then-validate only (AC-G2; design leg 1) — **HIGH**

The design's **leg 1** is "Deterministic Goose-artifact goldens (snapshots) … snapshot every
emitted artifact … byte-identical comparison." The suite implements leg 2 (validate) and leg 3
(degradation) but **not leg 1**: there is no `evals/goldens/<cell>/goose/` snapshot; the
structure/degradation tests emit into a temp dir and inspect in-process. Consequence: the large
**instruction-prose surface** (the rendered gate graph, delegation contract, post-exec/
maintenance policy, advisory blocks) is regression-checked only by "does `goose recipe validate`
still pass" + a handful of `assertIn` substring checks. A prose reordering, a dropped gate
consultation, or a silently changed advisory wording that **still validates** would pass the
suite. Emit-determinism is tested (emit-twice byte-identical), but determinism ≠ correctness:
two identical-but-wrong emits are still wrong.

*Recommended:* commit a byte-snapshot of the full Goose artifact tree for `core` and
`core+overlay` (reuse the Phase 0 comparator/policy, default `byte-identical`) and diff on every
run; pair with a "mutate one snapshot byte → exactly one failure" teeth test (the comparator-
self-teeth gap noted in §5 for the Claude side, here applied to Goose). **Why it matters:** it
puts the prose-rendering surface under the byte-identity net, which is the only thing that
catches a render regression that still happens to be valid YAML.

#### PG3 — Single-overlay Goose input; multi-overlay / anchorfile Goose emit untested (AC-G2; NFR-001 breadth) — **MEDIUM-HIGH**

Every Goose emit composes the single `TEST_OVERLAY`. This is the **same single-source breadth
gap** flagged for Phases 0–2 (§3, G3), now inherited by the Goose backend. The orchestrator's
`_orchestrator_scoped_lines` renders overlay-contributed principles, gate consultations,
advisory sources, spec sections, and auxiliary agents — but only for one overlay's worth of
each, in one order. Multi-overlay co-contribution (two overlays onto the same gate/principle
list, with `after`-ordering between them) is **never** rendered to a Goose recipe, so the
ordering/merge of scoped lines into instruction prose is untested for Goose. `test_breadth`
exercises arg-ordering determinism **only for the Claude CLAUDE.md body**, not for Goose recipes.

*Recommended:* add a two-overlay Goose emit (reuse `ANCHORFILE` + `TEST_OVERLAY`, already used
by `test_breadth`) and assert (a) `goose recipe validate` passes, (b) emit is order-independent
modulo nothing (Goose has no provenance/timestamp header, so it should be *fully* byte-identical
under overlay reorder — a stronger guarantee than Claude). **Why it matters:** order-independence
is a classic composition regression; the Goose render path has its own ordering logic
(`_orchestrator_scoped_lines`, `_gate_order`) that the single-overlay cell cannot stress.

#### PG4 — YAML serializer not fuzzed beyond curated cases (AC-G5; security-relevant robustness) — **MEDIUM**

`test_yaml_serializer` covers a strong **curated** set, but is not adversarial. The serializer
is the one component turning **untrusted overlay text** (principle descriptions, advisory-source
names, gate checklist text, spec headings — all flowing from overlay manifests into recipe
prose) into YAML, gated only by `goose recipe validate` *if goose is installed* (else loud-skip
— so on a goose-absent CI the serializer's correctness on hostile input is **unchecked**).
Unexercised adversarial inputs: a value containing `: |` / a leading `|`/`>` / `---` document
markers / `\t` tabs / a key (not just a value) needing quoting / a multi-line value whose lines
themselves start with `#` or `-` / non-ASCII / a string that is exactly `"{}"`/`"[]"`. The
`_dump_key` path quotes keys but no test feeds a hostile **key**; the block-literal path is only
tested with benign interior lines.

*Recommended:* a property/fuzz-style test that, for a corpus of adversarial strings, asserts the
serializer's output **round-trips through a reference YAML parser back to the original dict** (a
parser may be used *in the test only* as a checker — the product stays PyYAML-free), and — when
goose is present — that an emitted recipe carrying such a string in `instructions`/`title` still
passes `goose recipe validate`. **Why it matters:** this is the injection-adjacent robustness
surface; a quoting miss here is both a correctness bug and a path for untrusted text to change
recipe structure. Today the only backstop is an oracle that may be skipped.

#### PG5 — `--allow-newer-schema` / refusal behavior not covered for the goose target (REQ-038/048; design G4) — **MEDIUM**

The Phase 0–2 assessment flagged the missing `--allow-newer-schema` / newer-schema refusal test
(§7, G4). For Goose this matters specifically because the **refusal precedes backend selection**
(front-end), so a newer-schema overlay should refuse identically regardless of `--target goose`.
`test_goose_goldens.GooseRefusalParityTest` covers only the **conflict** refusal, not the
**schema-version** refusal/accept branch. No Goose test references `allow_newer_schema`.

*Recommended:* fold a `--target goose` case into the (still-missing) schema-version test: a
newer `schema_version` refuses (no Goose emit) without the flag and emits verbatim with it.
**Why it matters:** closes the only documented degraded-input mode for the goose path; it is a
distinct exit-code branch with zero coverage. Low-ish because the refusal is front-end-shared
and the conflict-refusal-parity test already proves "front-end refusal ⇒ no Goose emit."

#### PG6 — Degradation harness is goose-private; not yet shaped for Pi's *mixed* native/adapted/advisory (NFR-004; Phase 4 readiness) — **HIGH for Phase 4, does NOT block Phase 3**

This is the Phase-4 readiness verdict. Goose is the *first* exercise of the non-native path, but
it is **all non-native** (`adapted` ×2 + `advisory` ×4, nothing native). Pi (NFR-004) will be a
**mix**: native-via-owned-TS-extension for some capabilities, adapted/advisory for others. The
current degradation-assertion machinery is hard-wired to Goose's all-non-native shape:
- `test_goose_degradation` hard-codes `_ADAPTED`/`_ADVISORY` sets and a **nothing-native**
  invariant; `backends/goose.py:_build_degradation_report` **raises** if any descriptor status
  is `native` (AC-G3 honesty for Goose). For Pi, `native` is *correct* for some caps — so the
  per-capability `native|adapted|advisory|unsupported` assertion logic is **not yet expressed as
  a backend-parameterized check** that can validate a backend whose report legitimately mixes all
  four statuses.
- The `enforced`/`gated` flag semantics (`adapted ⇒ enforced:false,gated:true`;
  `advisory ⇒ both false`) are asserted only for Goose's two-way split. There is no test that a
  `native` cap reports `enforced:true`, nor a parameterized table-driven degradation check that
  takes `{backend, expected status-per-cap, expected enforced/gated-per-status}` and runs against
  any backend's descriptor+report.

So: **can the degradation-report tests express per-capability native/adapted/advisory/unsupported
for a backend that isn't all-advisory?** **Not yet, as written** — the *report format* (the
four-value enum + flags) is expressive enough (it was designed for this in Phase 0), but the
*test harness* encodes Goose's specific split rather than a backend-parameterized contract.

*Recommended (do before Pi work starts, not now):* refactor the degradation assertions into a
**backend-parameterized fixture** — a table `{capability → expected status}` per backend plus a
status→flags rule (`native⇒enforced:true,gated:false`; `adapted⇒enforced:false,gated:true`;
`advisory⇒both false`; `unsupported⇒both false`) — and drive both the Goose report and a
**synthetic mixed-status descriptor** (≥1 native, ≥1 adapted, ≥1 advisory, ≥1 unsupported)
through it, asserting the report mirrors the descriptor and the flags follow the rule. This is the
direct analogue of the Phase-0–2 "add a non-native fixture" recommendation, raised one level: Pi
needs a *mixed*-status fixture, and the assertion code must stop assuming nothing is native.
**Why it matters:** the project's central honesty apparatus (NFR-003) must validate Pi's mixed
fidelity; today it can only validate an all-or-nothing backend. This is the single most important
Phase-4-readiness item.

#### PG7 — `permission.yaml` is fixed/neutral; not derived from IR `blocking_semantics` (AC-G6; low) — **LOW**

`_build_permission_policy` and `_DANGEROUS_COMMANDS` are **constants** — the adapted policy is
the same regardless of IR content, and `test_goose_degradation` asserts the fixed tool set. The
design (§"IR → Goose artifact mapping") describes the adapted path as derived from `capabilities`
+ `blocking_semantics`. The current emit is a fixed neutral policy, which is defensible (the
dangerous set is a fixed System2 policy), but it means the permission policy is not actually a
function of the per-cell IR, and no test would catch a future divergence between the descriptor's
`adapted` claim and an IR cell that *lacks* `block-dangerous`. **Why it matters (low):** the
emit is honest and deterministic today; this is a "the test pins the constant, not the mapping"
note for when the policy becomes IR-derived.

### P3.4 — Determinism / negative controls (Phase 3)

- **Determinism:** directly tested — `GooseEmitDeterminismTest` (emit-twice byte-identical
  trees), serializer byte-stability, no-timestamp-in-lock, and the design's "pure function of the
  IR" claim is asserted. **Good.**
- **Negative controls:** strong and teeth-bearing — descriptor bad-enum/native-flip/dropped-cap;
  report dropped-entry/native-flip/status-drift; launcher default≠NO_PERMISSIONS XDG paths;
  vendored-pin logic/import drift. The **one missing** negative control is the same as the Claude
  side: **no "mutate one emitted Goose artifact byte → exactly one failure"** comparator-self-teeth
  test (folds into PG2, since there is no committed snapshot to mutate yet).

### P3.5 — Traceability (AC-G* / NFR → eval) — Phase 3 deltas

| AC / NFR | Design claims | Reality |
|---|---|---|
| AC-G1 | "Every emitted recipe passes `goose recipe validate`" | **Met** for all 14+ emitted recipes; **but** no recipe-with-`settings:` is ever validated (the OQ-G1 schema corner). → PG1 |
| AC-G1 leg 1 | "Deterministic Goose-artifact goldens (snapshots)" | **Not implemented** — emit+validate in-test only; no committed snapshot. → PG2 |
| AC-G2 | "13 roles render; gate graph/contract render from structured IR" | Structure **met**; rendered **prose** asserted only by validate-pass + substring checks (no byte-snapshot). → PG2 |
| AC-G3 / NFR-003 | "report-status == descriptor-status; nothing native; completeness; LOUD banner" | **Met**, with negative controls. **Strong.** |
| AC-G6 | "adapted path + advisory path exercised, not just the happy recipe" | **Met** — real `permission.yaml` + labelled NOT-ENFORCED blocks. **Strong.** |
| AC-G4 / NFR-001 | "claude goldens empty-diff; IR read-only; carriers not consumed" | **Met** (registry, both-driver empty-diff, no-perturb, AST + static-carrier scans). **Strong.** |
| AC-G5 | "stdlib-only; no-network; internal serializer" | **Met.** Serializer correctness curated, not fuzzed. → PG4 |
| NFR-002 (model_hint→settings) | "`settings.goose_model` from `model_hint` when present" | **Branch never executed by any test** (no fixture role carries a model_hint). → PG1 |
| NFR-004 (Pi mixed fidelity) | degradation model must record native+adapted+advisory+unsupported for one backend | report *format* expressive; **test harness assumes nothing-native** → can't yet validate a mixed-status backend. → PG6 |
| REQ-038/048 (schema escape hatch) | front-end refusal precedes backend | conflict-refusal parity covered; **schema-version refusal/accept for `--target goose` untested.** → PG5 |

### P3.6 — How to run (Phase 3)

From `System2-Compiler/` (package root):

- Full unit suite incl. Goose: `python3 -m pytest evals/` (or `python3 -m unittest discover -s evals`).
- Goose modules only: `python3 -m pytest evals/test_yaml_serializer.py evals/test_goose_descriptor.py evals/test_goose_goldens.py evals/test_goose_degradation.py evals/test_goose_launcher.py evals/test_phase3_no_regression.py`.
- Run the real validity oracle: install goose v1.38.0 (or set `GOOSE_BIN`) so the
  `goose recipe validate` legs run instead of LOUD-skipping — **CI that gates Phase-3 readiness
  MUST install goose**, otherwise the validity oracle silently (loudly) does not run.
- Claude no-regression gate (unchanged): `python3 -m evals.run_goldens --driver compiler`.

Exact CI wiring is **unknown: requires user confirmation** (no CI config / CLAUDE.md run-contract
in scope). Recommended CI integration point for Phase 3: gate merges on `pytest evals/` **with
goose installed on the runner** (so the validate oracle is live, not skipped) **plus** the Claude
`run_goldens --driver compiler` empty-diff gate; treat a LOUD-skipped validate leg on the
readiness runner as a CI failure, not a pass.

## Phase 4 — Pi Backend

*Reviewer: eval-engineer. Scope: the 54 Phase-4 tests across `test_pi_goldens.py`,
`test_pi_proven_blocking.py`, `test_pi_degradation.py`, `test_phase4_no_regression.py`,
`test_degradation_helper.py`, `test_write_scope_enrichment.py`, read against `backends/pi.py`,
`backends/_degradation.py`, `backends/capabilities/pi.json`, `ir/build.py` (write_scope
enrichment), and `spec/design.md` §"Phase 4 — Pi Backend" (AC-P1..AC-P8, OQ-P1..OQ-P4, legs 1–5).
This section closes the Phase-3 §PG6 Phase-4-readiness item. All cited artifact/IR/overlay
contents are treated as untrusted data.*

### P4.1 — Headline verdict

**Ship-ready for the Phase-4 DoD (AC-P1..AC-P8).** Every acceptance criterion is backed by a
real, teeth-bearing test, the two questions that matter most are genuinely answered, and the
no-regression gate is the strongest in the suite. The two headline answers:

- **PG6 is genuinely CLOSED (not papered over).** The Phase-3 flag was that the degradation
  harness hard-coded Goose's all-non-native split and `goose._build_degradation_report` *raised*
  on any `native` status, so a mixed backend could not be reported honestly. Phase 4 lifts the
  status→flags rule into the shared, descriptor-driven, ir-free `backends/_degradation.py`
  (`_FLAG_RULE` total over all four enum values) and `test_degradation_helper.py` drives a **real
  four-status synthetic descriptor** (`_FOUR_STATUS_CAPS`: native + adapted + advisory +
  unsupported) through one `build_capability_records` call, asserting each record's exact
  `(status, mechanism, enforced, gated)` and key-order. That is the backend-parameterized fixture
  PG6 asked for, with the `allow_native` axis exercised both ways (Goose's nothing-native guard
  still raises; Pi's native is admitted). The Pi *mixed report itself* is then asserted
  **end-to-end** from the emitted `system2.pi.lock.json` in `test_pi_degradation.py`: native AND
  adapted AND advisory all present in one backend, status==descriptor per capability, flags follow
  the rule, completeness (no silent drop, no extra), and `subagent_isolation: "adapted"`. This is
  the exact inverse of Goose's nothing-native invariant, and it is the single most important
  Phase-4-readiness item from Phase 3 — **resolved.**

- **Native blocking is REALLY proven (strong, not a mock).** `test_pi_proven_blocking.py` does
  **not** reimplement the gate in Python. It emits the real `.pi/extensions/system2.ts`, loads it
  through Pi's own `discoverAndLoadExtensions`, captures the registered `on("tool_call")` handler
  and the `/delegate` command, and fires **synthetic events at the real loaded handler** (no LLM).
  It asserts blocks for an off-scope write, a dangerous bash, and a sensitive read, AND — the
  crux — a **negative control** that an in-scope write / benign bash / ordinary read are *not*
  blocked, plus a cross-cut (`test_gate_discriminates_block_vs_allow`) proving the gate is neither
  block-everything nor pass-everything. This is the strongest native-fidelity evidence in the
  whole project. Caveat: it is **PASS-required only when node/pi is present**; absent → LOUD-skip.
  So CI readiness depends on installing node + Pi (see P4.6).

### P4.2 — Coverage vs the Phase 4 design / AC-P* / NFRs

| AC / NFR | Design claim | Reality |
|---|---|---|
| AC-P1 / NFR-001 (PG6 byte-preserving) | claude-code + goose locks byte-identical after the `_degradation` refactor; goldens empty-diff | **Met, strong.** `test_phase4_no_regression`: both-driver keystone empty-diff, Pi-emit-does-not-perturb-claude/goose bytes, Pi writes only its own surface. |
| AC-P2 (valid + loadable extension) | emitted `.ts` loads under Pi with `errors:[]`, registers the expected seams | **Met** via the live load leg (`PiLoadValidityTest`) — **but PASS-required only with node/pi present; LOUD-skip otherwise.** The `tsc --noEmit` type-check sub-leg the design calls for (AC-P2/leg 1) is **not implemented** — load-without-error stands in for type-check. → PG-P3 |
| AC-P3 (PROVEN native blocking) | synthetic `tool_call` harness blocks the 3 dangerous classes, allows benign; no LLM | **Met, strongest in suite** (real loaded handler; negative control; discriminator). Happy-path + benign-control only; bypass-adjacent inputs not fired. → PG-P1 |
| AC-P4 (mixed-status honesty) | report status==descriptor; flags follow rule; completeness; FIDELITY + unscoped note present | **Met, strong**, with a data-driven negative control (full-scope IR *drops* the unscoped note; unscoped IR carries it). |
| AC-P5 (mixed-status harness, PG6) | backend-parameterized fixture validates a 4-status synthetic descriptor through the shared helper | **Met** — `test_degradation_helper` `_FOUR_STATUS_CAPS`. PG6 closed. |
| AC-P6 (faithful representation) | 13 role prompts + `/delegate` targets; gate graph + contract render from IR; 3 skills + orchestrator + AGENTS.md | **Met structurally** (file-set count = 21, 13 role prompts, 3 skills, seams present in text). **Rendered prose is asserted only by substring + file-count, never by a committed byte-snapshot.** → PG-P2 |
| AC-P7 (no regression / stdlib-only / IR-only) | claude+goose byte-unchanged; pi.py imports only ir.graph + _degradation + base + stdlib; no carriers; no transpiler; no-network | **Met, strong** — AST import scan, forbidden-loader set, carrier-attribute scan, transpiler-token scan, `check_no_network_calls`, Goose still 14/14 `recipe validate`. |
| AC-P8 (honest isolation) | `/delegate` bounded to 13 roles; isolation reported `adapted` per probe, never silently native | **Met** — valid/unknown-role dispatch fired at the real command; `subagent_isolation` asserted `adapted` in both the degradation and proven-blocking modules. |
| OQ-P2 (injection seam) | `before_agent_start` survives and augments the prompt | **Met** — fired at the real handler; asserts the base prompt survives AND `.pi/SYSTEM.md` is injected. |
| OQ-P3 / NFR-001 (write_scope enrichment) | `_derive_roles` populates write_scope from the `.regex` allowlists; IR imports no backend | **Met, strong** — `test_write_scope_enrichment`: every write-capable role's scope == its allowlist file (drift guard), `code-reviewer` empty (no over-permit), multi-line preserved, all 13 classified, static AST proof `ir/build.py` imports no backend/cli. |
| NFR-004 (Pi higher fidelity than Goose) | mixed native+adapted+advisory in one backend | **Met** — the inverse-of-Goose mixed report, asserted end-to-end. |
| NFR-006 (cross-repo freshness / doctor + CI drift guard) | Phase 5 | **Not built (correctly out of Phase-4 scope), and the harness is not yet shaped for it.** → PG-P5 (readiness note, not a Phase-4 gap). |

### P4.3 — Ranked gaps

#### PG-P1 — Proven-blocking covers only happy-path + benign control; bypass-adjacent inputs not fired (AC-P3 / NFR-004; security-adjacent) — **MEDIUM; does NOT block Phase-4 ship**

The native gate is the project's central enforcement claim, and `test_pi_proven_blocking.py`
proves it *discriminates* — but only on clean, unambiguous inputs (`/etc/passwd`, `rm -rf /`,
`.env`, `src/main.py`, `ls -la`, `README.md`). Reading `backends/pi.py`, the generated gate has
three substring/regex seams whose bypass behavior is **unexercised**:

- **Path traversal vs the lease regex.** `offLeasePath` does `new RegExp(scope).test(p)` on the
  **raw** path with no normalization. A write to `src/../../etc/passwd` against a `^src/`-style
  scope, or a relative path that escapes the scope, is never fired — so whether the lease gate
  fails-open on traversal is **unknown**. This is the single most security-relevant missing case.
- **Dangerous-command obfuscation.** `dangerousReason` is `command.includes(pattern)`. Inputs
  like `rm  -rf  /` (extra whitespace), `r''m -rf /`, or `X=/; rm -rf $X` are not fired — the
  test cannot distinguish "blocks `rm -rf /`" from "blocks the *literal substring* `rm -rf /`".
- **Sensitive-path matching edge cases.** `sensitiveHit` is also substring `includes`, so
  `foo.environment` would match marker `.env` (false-positive, fail-*closed*, harmless) while a
  traversal-obscured `.env` is untested. Worth a case to pin the matcher's actual semantics.
- **Active-role switching effect on the gate.** `/delegate` mutates `activeRole`, and the lease
  gate reads `ROLE_WRITE_SCOPES.get(activeRole)`. The proven-blocking test fires writes only
  under the **default** `executor` role; it never delegates to a *different* role and then fires
  a write that is in/out of the **new** role's scope. So the "role-switch re-scopes the lease"
  behavior (the whole point of `/delegate` + multi-role scopes) is asserted only at emit-text
  level, never through the live handler.

*Why it matters:* these are exactly the inputs an adversary or a confused model produces; the
gate's *teeth* are proven, its *bypass-resistance* is not. *Why it does not block ship:* the
honest-blocking claim (AC-P3) is met for the canonical cases, and the design scopes the pattern
sets as backend-owned defaults (not a completeness guarantee). Coordinate with the security
review — these belong in the same suite as the injection-vector cases. *Recommended additions
(in `evals/test_pi_proven_blocking.py`'s harness, additive cases):* (1) an off-scope write via a
traversal path; (2) a `/delegate <other-role>` then an in/out-of-new-scope write fired at the
handler; (3) ≥1 whitespace-obfuscated dangerous command; (4) a sensitive read whose path is
traversal-obscured. Each should assert block/allow against the *normalized intent*, surfacing any
fail-open as a loud failure.

#### PG-P2 — No committed Pi golden snapshot; emit is compared only to itself (AC-P6; leg 3) — **MEDIUM; does NOT block ship**

`test_pi_goldens.py` is honest about what it proves: emit-twice byte-identity (determinism) and a
comparator-self-teeth test (one-byte mutation → exactly one failure). But there is **no committed
snapshot** of the Pi tree under `evals/goldens/` — the comparator runs `emit` against a second
`emit`, never against a frozen, reviewed artifact. So the suite catches *non-determinism* and
*comparator stubbing*, but a change that alters the **rendered prose/structure consistently**
(e.g. a regressed role-prompt body, a reworded gate-graph block in `SYSTEM.md`, an escaping
change in the `.ts`) would pass: both emits would agree, and no human-reviewed baseline would
disagree. This is the identical gap flagged for Claude (§PG2) and Goose, now also true for Pi —
and it is the gap leg 3 of the design's test strategy explicitly calls for ("snapshot every
emitted artifact … and byte-compare … default `byte-identical`"). *Why it matters:* prose/structure
drift is the most likely silent regression for a text-emitting backend. *Why it does not block
ship:* the load-validity leg (with node/pi) independently proves the `.ts` is *valid*, and the
degradation/role-count tests pin the machine-readable structure. *Recommended:* commit a reviewed
`evals/goldens/<cell>/` Pi tree for `core+overlay` and diff emit against it (the harness's
`_byte_diff` + `_read_tree` already do this — they just need a committed `snapshot` argument
instead of a second emit). **NOTE (allowlist):** committing goldens under `System2-Compiler/evals/`
is the compiler's own tree, outside this reviewer's `evals/*` allowlist — this is a recommendation
for the compiler test author, not a change made here.

#### PG-P3 — Single matrix cell exercised; `tsc --noEmit` type-check sub-leg absent (AC-P2/AC-P6; leg 1/leg 3) — **MEDIUM; does NOT block ship**

Every Pi test emits **only `core+overlay`**. The `core` (no-overlay) and `core+overlay+profile`
matrix cells — which the design's "Matrix" paragraph says to reuse for the Pi target — are never
emitted through Pi. So Pi emit is unproven on a minimal cell and on a profile-bearing cell;
single-capability vs multi-capability role rendering co-existence (the design's "a role carries
multiple capabilities → native + advisory blocks co-render" check) is exercised only incidentally
via the one cell. Separately, AC-P2/leg-1 call for a `npx tsc --noEmit` type-check sub-leg against
`@earendil-works/pi-coding-agent` types; the suite implements **load-without-error** but not the
static type-check, so a type-level regression that still *loads* would pass. *Why it matters:*
backend correctness across cells and at the type boundary is part of "faithful representation."
*Why it does not block ship:* refusal-parity is covered (conflict cell), determinism holds, and
runtime load is a strong substitute for type-check. *Recommended:* parameterize the goldens +
load legs over all three emit cells; add the `tsc --noEmit` sub-leg as a LOUD-skip-when-absent
companion to the load leg.

#### PG-P4 — Advisory/adapted artifacts asserted in the report, not in the emitted prose; `/delegate` dispatcher tested but role-switch *consequence* not (AC-P4/AC-P8) — **LOW**

`test_pi_degradation` proves `format`/`typecheck` are `advisory` and `budget` is `adapted` in the
**lock report**, and the proven-blocking test fires `agent_end` indirectly via the seam check —
but no test asserts the **advisory/adapted artifacts actually render**: that the role prompts /
`SYSTEM.md` carry the labelled `"ADVISORY — NOT ENFORCED ON PI"` block (per `pi.json` mechanism
text), or that the `agent_end` budget-notify handler actually calls `ctx.ui.notify` when fired
(it is registered, per the seam check, but never *invoked* in the harness). The `/delegate`
handler is fired (valid + unknown role), but as noted in PG-P1 the *consequence* of a successful
switch (re-scoped lease) is not. *Why it matters (low):* the report is honest; this is "the
prose/handler is asserted to exist, not to behave." *Recommended:* (1) assert the advisory label
substring in the emitted role prompt / `SYSTEM.md` text (cheap, no node); (2) fire the captured
`agent_end` handler in the node harness and assert a `notify` call.

#### PG-P5 — Harness not yet shaped for Phase-5 convergence (vendored stdlib bundle + doctor/CI drift) (NFR-006) — **READINESS NOTE; not a Phase-4 gap**

Phase 5 wires the plugin to consume a vendored stdlib bundle and adds `doctor` + CI drift guards.
The Phase-4 harness has the **right primitives** but none aimed at this: `_dir_fingerprint` +
hermetic-HOME purity (emit touches only `project_path`, real `~/.pi` untouched) is exactly the
shape a drift guard needs, and `test_vendored_pin.py` (Phase 3) already proves byte-identity of
vendored `ir/profiles.py` / `ir/_hook_security.py` against the plugin originals with logic-line +
unsanctioned-import negative controls — that is the template a Phase-5 stdlib-bundle staleness
guard should follow. **Missing for Phase 5:** (a) no test exercises a `system2-doctor` SKILL that
*verifies the extension loads + gates are live* at runtime (the skill is emitted as a `SKILL.md`
file but its doctor *behavior* is unasserted); (b) no staleness/hash guard over a vendored bundle
exists yet (correctly — it is Phase-5 work). *Recommendation for Phase 5:* extend the
`test_vendored_pin` byte-identity + negative-control pattern to the stdlib bundle, and add a
doctor-drift test that asserts the `system2-doctor` skill detects a tampered/stale extension. This
does **not** block Phase 4.

### P4.4 — Determinism / negative controls (Phase 4)

- **Determinism:** directly proven — `PiEmitDeterminismTest` (emit-twice byte-identical), no
  timestamp in the lock, pure-function-of-IR posture asserted; the shared `_degradation` helper is
  descriptor-ordered/insertion-ordered. **Good.**
- **Negative controls — strong and teeth-bearing:** comparator self-teeth (one-byte mutation →
  exactly one failure; missing-file → exactly one failure; identical tree → empty diff); the
  degradation helper raises on a dropped IR-present descriptor entry (no silent drop) and on
  `allow_native=False` over a native status (Goose's guard preserved); flipping a status flips the
  derived flags; the unscoped-honesty note is **data-driven** (full-scope IR drops it, unscoped IR
  carries it — proven by synthesizing a full-scope graph). The **emit-purity** control (real `~/.pi`
  / `~/.config` fingerprint unchanged) runs across goldens, load, and proven-blocking modules.
  The gaps are the *missing* controls in PG-P1 (bypass-adjacent) and PG-P2 (no committed snapshot),
  not weak existing ones.

### P4.5 — Traceability (AC-P* / OQ-P* / NFR → eval)

| AC / OQ / NFR | Eval case(s) | Status |
|---|---|---|
| AC-P1 / NFR-001 | `test_phase4_no_regression`: `ClaudeKeystoneGoldenGate`, `PiDoesNotPerturbOtherBackendsTest`, `GooseStillValidatesTest` | Met |
| AC-P2 | `test_pi_goldens`: `PiLoadValidityTest` (load-or-LOUD-skip); seams-in-text | Met (load); `tsc --noEmit` → PG-P3 |
| AC-P3 | `test_pi_proven_blocking`: block ×3, allow ×3, discriminator, real loaded handler | Met; bypass-adjacent → PG-P1 |
| AC-P4 | `test_pi_degradation`: status/mechanism==descriptor, mixed, flags-rule, completeness, FIDELITY, unscoped note + negative control | Met |
| AC-P5 (PG6) | `test_degradation_helper`: `_FOUR_STATUS_CAPS`, key-order, order-follows-descriptor, `allow_native` both ways | Met — PG6 closed |
| AC-P6 | `test_pi_goldens`: 21-file set, 13 role prompts, 3 skills, seams; **no committed snapshot** | Met structurally → PG-P2; single cell → PG-P3 |
| AC-P7 | `test_phase4_no_regression`: `PiBoundaryTest` (AST imports, loaders, carriers, transpiler tokens, no-network); `IrChangeIsWriteScopeOnlyTest` | Met |
| AC-P8 / OQ-P1 | `/delegate` valid+unknown fired; `subagent_isolation: adapted` asserted (two modules) | Met; switch-consequence → PG-P1/PG-P4 |
| OQ-P2 | `before_agent_start` fired: base prompt survives + `.pi/SYSTEM.md` injected | Met |
| OQ-P3 / NFR-001 | `test_write_scope_enrichment`: per-role scope==allowlist, empty for read-only, multi-line, AST no-backend-import | Met |
| NFR-004 | `test_pi_degradation` mixed report (inverse of Goose) | Met |
| NFR-006 | — | Phase 5; harness primitives present → PG-P5 |

### P4.6 — How to run (Phase 4)

From `System2-Compiler/` (package root):

- Full unit suite incl. Pi: `python3 -m pytest evals/` (or `python3 -m unittest discover -s evals`).
- Pi modules only: `python3 -m pytest evals/test_pi_goldens.py evals/test_pi_proven_blocking.py
  evals/test_pi_degradation.py evals/test_degradation_helper.py evals/test_write_scope_enrichment.py
  evals/test_phase4_no_regression.py`.
- **Run the real Pi validity + proven-blocking oracle:** install node + Pi v0.79.9
  (`@earendil-works/pi-coding-agent`) — or set `NODE_BIN` / `PI_BIN` — so the load and
  proven-blocking legs run instead of LOUD-skipping. **CI that gates Phase-4 readiness MUST install
  node + Pi**, otherwise the strongest native-fidelity evidence (AC-P2/AC-P3) silently (loudly)
  does not run. The harness pins the package entry to a hard-coded absolute path
  (`/Users/james/.npm-global/lib/node_modules/...`); on any other host that path must exist or the
  leg LOUD-skips — **make the entry path env-overridable** for portable CI (minor; recommendation
  for the compiler test author).
- No-regression gates (unchanged): the Claude `run_goldens --driver compiler` empty-diff gate +
  `goose recipe validate` 14/14 are both re-run inside `test_phase4_no_regression`.

Exact CI wiring remains **unknown: requires user confirmation** (no CI config / CLAUDE.md
run-contract in scope). Recommended CI integration point for Phase 4: gate merges on `pytest evals/`
**with node + Pi AND goose installed on the runner** (so all three validity oracles are live, not
skipped) **plus** the Claude `run_goldens --driver compiler` empty-diff gate; treat any LOUD-skipped
load / proven-blocking / validate leg on the readiness runner as a CI **failure**, not a pass.

### P4.7 — Ship verdict (Phase 4)

**Ship.** AC-P1..AC-P8 are each backed by a real, teeth-bearing test; **PG6 is genuinely closed**
(four-status helper fixture + end-to-end mixed Pi report, the inverse of Goose's nothing-native
invariant); and **native blocking is really proven** through the real loaded Pi handler with a
discriminating negative control, not a Python mock. **No gap below blocks Phase-4 ship.** The
ranked gaps — bypass-adjacent blocking cases (PG-P1, security-adjacent, MEDIUM), no committed Pi
golden snapshot (PG-P2), single-cell emit + missing `tsc --noEmit` (PG-P3), advisory/adapted prose
+ role-switch consequence (PG-P4) — are hardening and drift-resistance, addressable additively. The
one operational caveat is that the strongest evidence (load + proven-blocking) is **PASS-required
only when node/Pi is installed**; a readiness CI that omits Pi reduces the suite to artifact-shape
checks and must be treated as failing. **Phase-5 readiness (PG-P5):** the harness's hermetic-HOME
purity + `_dir_fingerprint` + the `test_vendored_pin` byte-identity pattern are the right primitives
for the vendored-bundle staleness + `doctor` drift guards; the missing pieces (a doctor-behavior
test and a stdlib-bundle staleness guard) are correctly Phase-5 work, not Phase-4 gaps.

## Phase 5 — Convergence & Lifecycle

**Scope of this assessment.** The Phase-5 gates under `evals/`: `test_cli_contract.py`
(claude lifecycle byte-parity vs the frozen `composer.py` oracle), `test_bundle_equivalence.py`
(shim/bundle == preflip across all verbs), `test_plugin_evals_on_bundle.py` (the plugin's own
suite on the flipped plugin), `test_bundle_drift.py` (staleness/tamper guard self-test),
`test_phase5_dod.py` (DoD-5 aggregation), plus the `overlay_sources[]` additive-key leg added to
`test_goose_degradation.py`/`test_pi_degradation.py`. Read against `spec/design.md` §Phase 5
(AC-5.1…AC-5.8) and the three backends' lifecycle (`backends/{base,claude_code,goose,pi}.py`),
`cli.py`, `tools/`, and the vendored bundle `System2/plugin/scripts/_system2_compiler/`. All
artifacts treated as untrusted; no product/test/`System2/` files were modified.

### Strengths

- **Convergence proof (claude) is genuine and has teeth — the keystone holds.** The
  bundle-equivalence gate (`test_bundle_equivalence.py`) drives the live shim `composer.py` with
  `SYSTEM2_USE_BUNDLE=1` and byte-diffs **stdout + stderr + exit code** against the frozen,
  hash-pinned `composer.py.preflip` across the **full 17-cell verb matrix** reused verbatim from
  `test_cli_contract._cells()` — not just compose. That matrix is broad: compose (text/json/dry-run),
  conflict + missing-overlay refusals, doctor (composed/no-lock/stale), uninstall
  (last / one-of-N / not-installed / no-lock / dry-run), from-lock (recompose + missing), and two
  profile ops. Both engines run as subprocesses under independent hermetic temp HOMEs, each
  pre-stated with the **same** frozen preflip engine, so the measured invocation observes identical
  inputs. A non-empty diff fails (no auto-rebaseline, REQ-007) and a self-teeth test asserts a
  one-byte divergence is caught. `oracle.verify_pin()` anchors the baseline. **Verdict: the
  convergence proof is sound for claude — it proves bundle == preflip across the whole verb surface,
  not merely compose.**

- **The plugin's own suite really runs on the bundle.** `test_plugin_evals_on_bundle.py`
  subprocess-discovers and runs `System2/evals/test_*.py` read-only against BOTH switch states —
  ON (bundle, the flip leg) and OFF (frozen preflip baseline) — under a hermetic temp HOME, and
  requires both green (AC-5.6). This is a real second oracle on the flip, not a self-attestation:
  the plugin's structural+behavioral suite is the independent check that the flipped plugin behaves.

- **Normalizations are tight, not drift-hiding.** Only two volatile bytes are canonicalized: the
  temp-project / temp-HOME path prefixes (`<PROJECT>`/`<HOME>`) and the single live
  `<!-- Composed at: <TS> -->` timestamp that appears **only** in the dry-run preview (no prior lock
  to reuse). Every non-dry-run cell reuses `composed_at` from the seeded lock and stays byte-exact.
  Both are applied identically to oracle and bundle legs, so they cannot mask a real divergence —
  they remove genuinely non-deterministic bytes, nothing semantic. **Verdict: sound.**

- **Claude lifecycle parity vs `composer.py` is *asserted*, not just "runs."** `test_cli_contract.py`
  captures the frozen oracle's stdout/stderr/exit-code for uninstall/doctor/from-lock/profile cells
  into committed-style goldens and byte-diffs the compiler CLI (`cli.main()` via subprocess for
  hermetic HOME) against them, with a self-teeth mutation test. `test_phase5_dod.py` re-runs this
  in-process plus the compose→emit goldens under both the compiler and oracle drivers. This is true
  byte-faithful parity (AC-5.2), the strongest form of "parity."

- **Drift guard self-test has teeth on BOTH halves.** `test_bundle_drift.py` proves the CI staleness
  guard (`tools/check_bundle_fresh.py`) passes on a fresh build and FAILS on a one-byte vendored
  mutation, isolates the STALE leg (bundle untampered but lagging newer source → fail), and pins
  bundler determinism + minimal layout (no vendored `evals/`). `test_phase5_dod.py` additionally
  exercises the **plugin-side** tamper check (`_freshness.py`, which ships with the bundle and needs
  no compiler source): asserts the REAL vendored bundle is untampered, that its recompute equals
  `build_bundle.compute_source_hash`, and that a one-byte mutation of a temp copy trips a LOUD
  `bundle_tampered` finding with CLI exit 1 — all without perturbing the real bundle.

- **`overlay_sources[]` is correctly proven additive.** Both goose and pi degradation suites assert
  the new key is the LAST lock key and that stripping it reproduces the prior degradation-report
  bytes byte-for-byte — exactly the "additive, no existing byte shifted" discipline (AC-5.3 / T10).

### Ranked gaps

1. **Goose/Pi lifecycle (AC-5.3) has NO behavioral coverage — only a static stub check. (HIGH;
   does not block the *claude* flip, but blocks an honest AC-5.3 sign-off.)** No eval anywhere
   invokes `GooseBackend`/`PiBackend` `.uninstall()`, `.recompose_from_lock()`, or `.doctor()` —
   grep across `evals/*.py` finds zero call sites. The *only* Phase-5 assertion for these is
   `test_phase5_dod.GrownContractTest`, which uses `inspect.getsource` to check the methods are not
   3-line `NotImplementedError` stubs. That is an existence/non-stub check, not a behavior check.
   AC-5.3 explicitly requires goose/pi `uninstall` to **remove/recompose with atomic restore**,
   `doctor` to **validate via real `goose recipe validate` / `pi load` with a LOUD-skip when absent
   (never a silent "current")**, and `from-lock` to **recompose from `overlay_sources[]`**. The
   real implementations exist (`backends/goose.py:846/1035`, `backends/pi.py:1253/1455`, including
   `validator_available` LOUD branches) but the **validator-absent LOUD path, the atomic uninstall,
   and from-lock recompose are never executed under test for goose or pi**. *Why it matters:* these
   are precisely the lifecycle behaviors most likely to corrupt a user's project on failure
   (partial removal, non-atomic restore) and the no-silent-downgrade contract the whole project
   rests on. The claude side gets full byte-parity; goose/pi get a source-inspection rubber stamp.

2. **The "skipped goose/pi CLI-contract legs" are NOT a false-skip-when-installed hole — but they
   ARE a structural coverage gap. (MEDIUM; reframed.)** Investigated directly: `test_cli_contract`
   has 17 cells, **all `--target claude-code`** — there are no goose/pi legs that LOUD-skip when the
   binaries are present. So there is no "15 legs false-skipping when goose/pi are installed"
   bug-shaped hole. The reason is structural: the CLI-contract oracle *is* `composer.py`, which is
   claude-only; there is no byte-identical oracle for goose/pi (their output is new, not a
   relocation — same rationale as `test_goose_goldens`/`test_pi_goldens`). The real consequence is
   that goose/pi lifecycle verbs have **no contract gate at all**, which is the same finding as gap
   1 viewed from the CLI surface. *Rank below gap 1* because it is a duplicate lens on the same
   debt, not an independent one. **Verdict on the flag: the earlier "false-skip" framing does not
   reproduce; the underlying coverage gap (goose/pi lifecycle untested) is real and is gap 1.**

3. **The CI staleness guard is present with teeth but NOT wired to run. (MEDIUM.)**
   `tools/check_bundle_fresh.py` is exercised only by its own self-test (`test_bundle_drift`,
   `test_phase5_dod`). `System2-Compiler/` has **no `.github/workflows/` and no Makefile**, and a
   workspace-wide grep finds `check_bundle_fresh` referenced only by tests + spec docs + the doctor
   SKILL prose — never by an actual CI job. So the STALENESS half of AC-5.7 (the cross-repo "committed
   bundle lags the compiler source" guard) is a guard with proven teeth that **nothing bites with**:
   a stale bundle could merge because no pipeline runs the check. *Contrast:* the TAMPER half IS
   wired — `System2/plugin/skills/doctor/SKILL.md` invokes `_freshness.py` at runtime, so a
   hand-edited vendored bundle surfaces `bundle_tampered` to the operator. *Why it matters:* the two
   halves are advertised as a pair; one is live, the other is dormant. Until a compiler-side CI job
   (or pre-commit) runs `check_bundle_fresh.py`, the staleness guard is documentation, not
   enforcement.

4. **No committed per-target golden snapshots for the lifecycle/equivalence cells (PG2 carryover).**
   `cli_contract/<cell>/` is materialized by a `--capture` step; the bundle-equivalence and
   plugin-evals gates compute the baseline live each run. Live recomputation is fine for the
   *claude-oracle* legs (the oracle is hash-pinned and deterministic), but there is no committed,
   reviewable snapshot of goose/pi lifecycle output — consistent with PG-P2/PG-G2 from earlier
   phases. *Why it matters:* a silent drift in goose/pi lifecycle artifacts has no frozen reference
   to diff against; review cannot see the bytes change in a PR.

5. **Single-cell / single-overlay-set lifecycle exercise (LOW).** The verb matrix runs uninstall
   one-of-N and last-overlay, but on a small fixed overlay set (`test-overlay`, `anchorfile`,
   conflict pair). Multi-cell breadth for the lifecycle verbs (e.g., uninstall across the full
   capability-bearing breadth matrix, from-lock after a multi-overlay recompose) is not exercised —
   same multi-cell-coverage caveat flagged in Phases 3–4.

### Does anything block ship?

- **The claude flip is shippable.** AC-5.1/5.2/5.5/5.6/5.7(tamper)/5.8 are backed by real gates with
  teeth: byte-identical bundle-equivalence across all verbs, the plugin's own suite green on the
  bundle, claude lifecycle byte-parity vs the frozen oracle, and a reversible one-commit backout.
  Nothing in gaps 1–5 undermines the *claude* convergence proof.
- **AC-5.3 (per-target goose/pi lifecycle) cannot be honestly signed off on current evidence.**
  Gap 1 means the LOUD validator-absent path, atomic uninstall, and from-lock recompose for goose/pi
  are asserted only by source inspection. **Recommendation: do not mark AC-5.3 "Met" — mark it
  "Partially met (static only)"** and add behavioral tests (emit → uninstall → assert tree
  removed/restored; doctor with `GOOSE_BIN`/`pi` unset → assert LOUD `validator_unavailable` +
  `validator_available=False`; from-lock → assert recompose from `overlay_sources[]`). This is the
  one item that gates a *project-complete* claim, not the claude flip.
- **Gap 3 (unwired staleness guard) should be closed before relying on the bundle in CI** but does
  not block the flip itself (the tamper half protects the live plugin at runtime).

### Project-wide eval-debt summary (Phases 0–5 done)

With all three backends and convergence landed, the residual eval debt is **breadth and
enforcement-wiring, not correctness of the proven core**:

- **Behavioral lifecycle coverage for non-claude backends (gap 1)** — the single highest-value
  remaining item; it is the difference between "byte-faithful for claude" and "lifecycle-parity for
  all three targets" that AC-5.3 promises.
- **CI with all validators installed + the staleness guard wired (gaps 3, plus PG-3/PG-4 carryover).**
  The strongest legs are PASS-required-only-when-present: `goose recipe validate`, `pi load`, the Pi
  proven-blocking legs all LOUD-skip when their binaries are absent, and `check_bundle_fresh` runs in
  no pipeline. A readiness CI **must** install goose + node/pi **and** run the staleness guard;
  otherwise the suite silently degrades to artifact-shape checks and a stale bundle can merge.
- **Committed per-target golden snapshots (gap 4 / PG-P2 / PG-G2)** — frozen, reviewable goose/pi
  output so drift is visible in a diff.
- **Multi-cell breadth for lifecycle verbs (gap 5)** and the smaller earlier-phase items
  (`tsc --noEmit` for Pi → PG-P3; the `settings:`-bearing recipe shape → PG1; advisory/adapted prose
  → PG-P4) — all additive hardening, none blocking.

**Bottom line:** the convergence proof is high quality and the claude flip is safe to ship; the
project-complete claim is gated on closing the goose/pi behavioral-lifecycle gap (gap 1) and wiring
the staleness guard + a fully-provisioned CI (gap 3). No silent-correctness risk was found in the
proven core — the debt is honest, enumerated, and additively closeable.
