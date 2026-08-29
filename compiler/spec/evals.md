# System2 Compiler — Evaluation and Regression Coverage

> Eval cases use descriptive behavior names. This assessment does not rely on
> generated requirement, finding, or task identifiers.

## Overall Verdict

The suite has strong depth: parity goldens, import-boundary checks, target-specific
validity tests, end-to-end blocking harnesses, lifecycle tests, freshness mutation
controls, and negative cases that prove the checks can fail. The principal maintenance
risk is breadth whenever a new input shape or target mechanism is added.

Release-quality verification requires:

- core structural evals;
- the complete compiler test suite;
- both Claude golden drivers when reference output can change;
- target load or validity checks;
- generated artifact freshness checks with zero required-validator skips in CI.

## What Is Evaluated

### Neutral composition

- manifest validation and path containment;
- profile resolution under a hermetic home;
- contribution ordering independent of CLI argument order;
- conflict and ordering-cycle refusal;
- semantic-tension warnings;
- anchor identity, per-agent scoping, and unknown-anchor exclusion;
- unknown capability warnings;
- dry-run behavior and intended file plans.

### Claude Code projection

- byte identity against the frozen composer for composed Markdown, lock files,
  auxiliary agents, copied overlay content, warning text, and refusal output;
- additive degradation-report behavior without changing prior lock content;
- lifecycle CLI parity for compile, uninstall, doctor, from-lock, and profiles;
- atomic rollback and path-safety refusal;
- static plugin inventory and binding stability.

### Pi projection

- exact deterministic artifact inventory;
- skill frontmatter and source-to-emission synchronization;
- extension loading through Pi's own loader;
- synthetic `tool_call` events proving off-scope writes, dangerous commands, and
  sensitive paths block before execution;
- benign writes, commands, and reads proving the gate does not block everything;
- active-role switching and honest adapted isolation reporting;
- mixed native, adapted, and advisory capability records;
- uninstall, doctor, from-lock, and validator-unavailable behavior;
- npm package contents, no install scripts or dependencies, and project materialization.

### Codex projection

- manifest and marketplace pointer containment;
- exact skill inventory and version consistency;
- verbatim trust and coverage messaging across manifest, orchestrator, README, and lock;
- no native or enforced-at-rest over-claims;
- generated Node guards launched with realistic Codex event envelopes;
- command-string and argv forms, chains, heredocs, alternate event keys, and patch/edit
  payload shapes;
- parity with the Pi normalized-input blocking corpus;
- malformed JSON, held-open stdin, watchdog timeout, and oversized input failing closed;
- user-scope hook materialization, backup, idempotency, uninstall, and clean errors;
- doctor honesty when hook trust is not observable.

### Generated artifacts and supply chain

- deterministic regeneration of bundle, Codex distribution, and Pi package;
- provenance fields and source hashes;
- stale and hand-edited bundle detection;
- packaged user-hook mirror equivalence;
- wheel installation followed by Codex hook initialization outside a checkout;
- secret scanning and pinned external validators in CI.

## Golden Strategy

Claude reference goldens compare two independent drivers against the same snapshots:

1. the hash-pinned frozen composer subprocess;
2. in-process `ir.compose` followed by `ClaudeCodeBackend.emit`.

Normal runs never rewrite snapshots. Rebaselining is explicit and reviewed. The comparator
has mutation tests: changing one byte must produce a failure. Lock comparison permits only
documented additive fields and validates those fields structurally.

Pi uses committed target snapshots and tree comparisons. Generated package and distribution
freshness uses regeneration into temporary directories rather than trusting committed output.

## Determinism and Idempotency

The suite checks:

- repeated emission produces identical bytes;
- reversing overlay arguments does not alter composition;
- lock timestamps are reused when content fingerprints match and refreshed when content
  changes;
- regeneration from identical inputs differs only in documented provenance breadcrumbs;
- initialization and lifecycle commands can be repeated without duplicate or destructive
  side effects.

## Negative Controls

Every high-value assertion family should include a bad case through the same code path:

- forbidden imports, network calls, and plugin imports are detected;
- missing descriptor entries and invalid status values fail;
- dropping a capability from a report fails;
- tampering trust text or claiming native Codex enforcement fails;
- mutating a golden byte fails;
- stale and tampered bundles fail freshness checks;
- path traversal, symlink escape, and sibling-prefix mistakes are rejected;
- benign tool calls remain allowed in blocking harnesses.

A test without a negative or mutation control is acceptable only when its assertion is
intrinsically direct and cannot pass vacuously.

## Failure Modes Covered

| Failure mode | Signal |
|---|---|
| Oracle source drift | Hash-pin failure; no automatic rebaseline |
| Backend byte drift | Golden diff naming artifact and changed bytes |
| Missing or extra generated artifact | Exact inventory or tree diff |
| Ordering regression | Reversed-argument and multi-overlay tests |
| Anchor mismatch | Direct identity, scoping, and exclusion tests |
| Silent capability drop | Descriptor/report set equality and mutation test |
| Overstated enforcement | Status, flags, mechanism text, and trust-surface checks |
| Blocking bypass | End-to-end synthetic event corpus |
| Block-everything stub | Benign negative controls |
| Partial write | Backup restoration and leftover scan |
| Missing validator | Loud finding or local skip; CI rejects required skips |
| Stale generated output | Regenerate-and-compare freshness failure |
| Hand-edited vendored output | Recorded hash differs from recomputed subtree hash |

## Coverage Gaps to Watch

When behavior changes, add coverage for any newly introduced branch in these areas:

- multiple overlays contributing to the same scope or anchor;
- non-default role write scopes and model hints;
- newly supported tool payload keys;
- target CLI schema changes;
- new capability statuses or mechanism delivery paths;
- new generated artifact classes;
- recovery from malformed or older lock formats;
- platform-specific filesystem and process behavior.

Do not cite a replaced plan as evidence that a gap is covered. Point to the test name and
the behavior it exercises.

## How to Run

From the repository root:

```bash
python3 evals/run_evals.py
python3 compiler/tools/regen_all.py --check
```

From `compiler/`:

```bash
python3 -m unittest discover -s evals
python3 -m evals.run_goldens --driver compiler
python3 -m evals.run_goldens --driver oracle
```

Required external validators are installed and pinned in CI. A local environment may report
a loud skip when a validator is genuinely unavailable, but CI requires a zero skip count for
those validation legs.

## Coverage Map

| Behavior | Primary evidence |
|---|---|
| Neutral deterministic composition | ordering, breadth, anchor, conflict, and unknown-capability tests |
| Claude compatibility | two-driver goldens, CLI-contract tests, bundle equivalence |
| Pi native blocking | shipped-extension load and synthetic tool-call harness |
| Codex conditional blocking | generated-hook corpus and trust-surface invariants |
| Honest degradation | descriptor completeness, mixed-status, and no-silent-drop tests |
| Lifecycle safety | dry-run, uninstall, doctor, from-lock, containment, and rollback tests |
| Generated freshness | regeneration determinism, source hashes, stale/tamper mutation tests |
| Durable documentation | repository-wide generated-identifier prohibition |
