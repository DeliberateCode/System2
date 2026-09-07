# Pre-consolidation ("old-world") lock fixture

`overlay-manifest.lock` has the exact minimum top-level shape emitted by the
frozen pre-consolidation composer for the committed `evals/fixtures/test-overlay`.
It guards existing user projects whose locks were written before this monorepo
consolidation.

Consumed by `evals/test_old_world_lock.py`, which proves that feeding
this lock to `doctor` and `recompose_from_lock` yields **at most** the standard
stale-base nudge — never a schema error or a hard failure.

## What is "old-world" here

The fixture has the legacy seven-key top-level shape and old provenance values:

- `system2_version: "1.1.0"` — a pre-consolidation plugin version, differing from
  the currently installed plugin version (`plugin/.claude-plugin/plugin.json`), so
  `doctor` sees a version mismatch and reports `stale_base` (the standard
  "run --from-lock" nudge).
- `content_fingerprint: sha256:8a53793d…` — the **genuine** fingerprint the engine
  produces for the test-overlay content at `system2_version` `1.1.0`. Because the
  fingerprint hashes the version string, it differs from the current
  `core+overlay` golden fingerprint (the value in
  `evals/goldens/core+overlay/spec/overlay-manifest.lock`) even though the overlay
  content is byte-identical, which is the fingerprint behavior the test asserts.

The legacy shape ends at `warnings`; it intentionally has no additive
`degradation_report` field.

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
