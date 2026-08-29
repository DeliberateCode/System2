# Pre-consolidation ("old-world") lock fixture

`overlay-manifest.lock` is a **real captured** claude-code lock (composed from the
committed `evals/fixtures/test-overlay` overlay against the plugin base), hand-set
to the pre-consolidation ("old-world") provenance shape. It guards existing user
projects whose locks were written by the compiler/composer BEFORE this monorepo
consolidation (the implementation work–011).

Consumed by `evals/test_old_world_lock.py` (the implementation work), which proves that feeding
this lock to `doctor` and `recompose_from_lock` yields **at most** the standard
stale-base nudge — never a schema error or a hard failure (the requirement/071/043).

## What is "old-world" here

The compiler/composer engine bytes are identical pre/post consolidation (the
sha-identity HALT proved it), so the lock *format* is unchanged across the move. The
only distinguishing marks of a pre-consolidation lock are its **provenance values**:

- `system2_version: "1.1.0"` — a pre-consolidation plugin version, differing from
  the currently installed plugin version (`plugin/.claude-plugin/plugin.json`), so
  `doctor` sees a version mismatch and reports `stale_base` (the standard
  "run --from-lock" nudge).
- `content_fingerprint: sha256:8a53793d…` — the **genuine** fingerprint the engine
  produces for the test-overlay content at `system2_version` `1.1.0`. Because the
  fingerprint hashes the version string, it differs from the current
  `core+overlay` golden fingerprint (the value in
  `evals/goldens/core+overlay/spec/overlay-manifest.lock`) even though the overlay
  content is byte-identical — exactly the the requirement semantic the test asserts.

Everything else (`schema_version 1.0.0`, per-overlay `manifest_hash`/`content_hash`,
`local_path`, `contributions_applied`, `warnings`, `degradation_report`) is the real
captured lock, unchanged.

## `source_path` placeholder

`overlays[].source_path` is stored as the token `__SYSTEM2_TEST_OVERLAY__`. The test
substitutes the machine-resolved absolute path to the committed `test-overlay`
(`evals.matrix.TEST_OVERLAY`) at runtime so the lock resolves portably (the source
overlay content is byte-stable, so `manifest_hash`/`content_hash` continue to match
and the overlay is NOT reported stale — isolating the version-only `stale_base`).

## Regenerating

If `test-overlay`'s content or the plugin version legitimately changes (the
`core+overlay` golden lock would change too), refresh the captured hashes and the
old-world fingerprint by re-running the capture: compose `test-overlay`, read the
emitted lock for `manifest_hash`/`content_hash`, and recompute the fingerprint with
`_compute_idempotency` after setting `graph.system2_version = "1.1.0"`. See
`test_old_world_lock.py`'s docstring for the exact steps.
