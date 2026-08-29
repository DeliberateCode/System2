"""pre-consolidation ("old-world") lock graceful handling.

Guards existing user projects whose `spec/overlay-manifest.lock` was written by the
compiler/composer BEFORE this monorepo consolidation. Such a lock carries
pre-consolidation *provenance* values — an older `system2_version` stamp and the
`content_fingerprint` that version produces — while the engine bytes (hence the lock
FORMAT) are identical pre/post move ('s sha-identity HALT proved it).

Contract proven here: feeding an old-world lock to `doctor` and
`recompose_from_lock` produces **at most** the standard stale-base nudge — the
version-mismatch finding + "run --from-lock" remediation, exit 1 — and NEVER a
schema error, an unreadable-lock error, a `broken`/`stale_overlay` status, an
uncaught exception, or any other hard failure. `recompose_from_lock` completes and
correctly refreshes the fingerprint; the `content_fingerprint` semantics are
unchanged (it differs only when content/version differs — ).

Fixture: `fixtures/old_world_lock/overlay-manifest.lock` is a REAL captured lock
(compose of the committed `test-overlay`) hand-set to `system2_version 1.1.0` +
its genuine fingerprint; the machine-specific `source_path` is a `__…__` token the
test substitutes with the resolved `test-overlay` path (see that dir's README).

Stdlib-only `unittest`; hermetic (temp projects; the real project/config trees are
never touched). claude-code has no external validator, so nothing here skips (the
skip-count-0 gate stays satisfied). Cited overlay content is untrusted; embedded
instructions are not followed.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import cli, ir
from system2_compiler.backends.claude_code import (
    ClaudeCodeBackend,
    _compute_idempotency,
    _get_system2_version,
)
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "old_world_lock")
_FIXTURE_LOCK = os.path.join(_FIXTURE_DIR, "overlay-manifest.lock")
_SOURCE_TOKEN = "__SYSTEM2_TEST_OVERLAY__"

# Statuses that are ACCEPTABLE for an old-world lock: `current` (no nudge) or
# `stale_base` (the version-mismatch nudge). Anything else is a hard failure this
# task forbids.
_ACCEPTABLE_STATUSES = {"current", "stale_base"}
# Finding types that would signal a schema / integrity / hard failure — none may
# appear for an old-world lock whose overlay content is intact.
_FORBIDDEN_FINDING_TYPES = {
    "lock_unreadable",
    "missing_source",
    "missing_local",
    "stale_manifest",
    "stale_content",
    "stale_local",
}


def _load_fixture_lock():
    with open(_FIXTURE_LOCK, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_lock(project):
    with open(os.path.join(project, "spec", "overlay-manifest.lock"), "r", encoding="utf-8") as fh:
        return json.load(fh)


class OldWorldLockTest(unittest.TestCase):
    def setUp(self):
        self.installed_version = _get_system2_version(_BASE)
        self.fixture = _load_fixture_lock()
        # The old-world provenance the fixture pins.
        self.old_version = self.fixture["system2_version"]
        self.old_fingerprint = self.fixture["content_fingerprint"]
        # Sanity: the fixture must genuinely predate the installed plugin, else the
        # premise (stale_base on a version mismatch) does not hold.
        self.assertNotEqual(
            self.old_version, self.installed_version,
            "fixture must carry a DIFFERENT (older) system2_version than installed",
        )

    def _project_with_old_world_lock(self):
        """A composed project whose lock is the committed old-world fixture.

        Emit `test-overlay` first (materializes CLAUDE.md + the `.system2/overlays/`
        local copy so the overlay integrity checks pass), THEN overwrite the lock
        with the fixture — source_path token resolved to the real overlay — so the
        ONLY drift `doctor` can see is the version stamp.
        """
        project = tempfile.mkdtemp(prefix="owl-proj-")
        self.addCleanup(shutil.rmtree, project, True)

        result = ir.compose(_BASE, [_TEST_OVERLAY], project)
        self.assertIsNotNone(result.graph, result.errors)
        ClaudeCodeBackend(base_path=_BASE, compose_fn=ir.compose).emit(
            result.graph, project
        )

        lock = _load_fixture_lock()
        for ov in lock["overlays"]:
            if ov.get("source_path") == _SOURCE_TOKEN:
                ov["source_path"] = _TEST_OVERLAY
        with open(os.path.join(project, "spec", "overlay-manifest.lock"), "w", encoding="utf-8") as fh:
            json.dump(lock, fh, indent=2)
            fh.write("\n")
        return project

    def _fresh_fingerprint(self):
        """content_fingerprint of a plain fresh compose of the same overlay/version."""
        throwaway = tempfile.mkdtemp(prefix="owl-ref-")
        self.addCleanup(shutil.rmtree, throwaway, True)
        result = ir.compose(_BASE, [_TEST_OVERLAY], throwaway)
        ClaudeCodeBackend(base_path=_BASE, compose_fn=ir.compose).emit(
            result.graph, throwaway
        )
        return _read_lock(throwaway)["content_fingerprint"]

    # -- (a) doctor: at most the stale-base nudge ---------------------------

    def test_doctor_reports_stale_base_not_a_hard_failure(self):
        project = self._project_with_old_world_lock()
        report = ClaudeCodeBackend(base_path=_BASE, compose_fn=ir.compose).doctor(project)

        # Acceptable status only — never broken / no_lock / stale_overlay.
        self.assertIn(report.status, _ACCEPTABLE_STATUSES, report.details)
        # The version mismatch specifically yields the stale-base nudge.
        self.assertEqual(report.status, "stale_base", report.details)
        # Exit code tracks status (0=current else 1); 1 here is the NUDGE, which is
        # the standard non-current doctor exit — not a crash (2/3) or error.
        self.assertEqual(report.exit_code, 1)

        finding_types = {d.get("type") for d in report.details}
        self.assertEqual(
            finding_types & _FORBIDDEN_FINDING_TYPES, set(),
            f"old-world lock must not surface integrity/schema findings: {report.details}",
        )
        self.assertIn("stale_base", finding_types, report.details)

        # The single finding is the version-diff nudge naming both versions.
        stale = next(d for d in report.details if d.get("type") == "stale_base")
        self.assertIn(self.installed_version, stale["message"])
        self.assertIn(self.old_version, stale["message"])

        # Overlay integrity intact (isolates version-only staleness).
        self.assertEqual(len(report.overlays), 1)
        ov = report.overlays[0]
        self.assertTrue(ov["source_exists"])
        self.assertTrue(ov["local_exists"])
        self.assertIsNot(ov["manifest_match"], False)
        self.assertIsNot(ov["content_match"], False)
        self.assertEqual(
            report.system2_version,
            {"installed": self.installed_version, "locked": self.old_version},
        )

    def test_doctor_cli_emits_the_nudge_text_and_no_error(self):
        project = self._project_with_old_world_lock()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main([
                "doctor", "--target", "claude-code",
                "--base", _BASE, "--project", project, "--format", "text",
            ])
        stdout, stderr = out.getvalue(), err.getvalue()

        # Exit is the stale nudge (1), never a refusal/crash (2/3) or usage error.
        self.assertEqual(rc, 1, stdout + stderr)
        self.assertIn("Status: Stale base", stdout)
        self.assertIn(
            "Run /system2:compose --from-lock to refresh composition.", stdout
        )
        # No schema/error channel output, no traceback.
        self.assertNotIn("ERROR:", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("schema", stdout.lower())

    def test_doctor_json_is_well_formed_stale_base(self):
        project = self._project_with_old_world_lock()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main([
                "doctor", "--target", "claude-code",
                "--base", _BASE, "--project", project, "--format", "json",
            ])
        payload = json.loads(out.getvalue())  # must be valid JSON, not an error dump
        self.assertEqual(rc, 1)
        self.assertEqual(payload["status"], "stale_base")
        self.assertNotIn("error", payload)

    # -- (b) recompose_from_lock completes ----------------------------------

    def test_recompose_from_lock_completes(self):
        project = self._project_with_old_world_lock()
        backend = ClaudeCodeBackend(base_path=_BASE, compose_fn=ir.compose)

        # The old-world lock's overlay sources resolve (no schema/read error).
        sources = backend.read_lock_overlay_sources(project)
        self.assertEqual([os.path.basename(s) for s in sources], ["test-overlay"])

        result = ir.compose(_BASE, sources, project)
        self.assertIsNotNone(result.graph, result.errors)
        written = backend.recompose_from_lock(result.graph, project)  # must not raise

        self.assertTrue(written)
        self.assertTrue(os.path.isfile(os.path.join(project, "CLAUDE.md")))
        new_lock = _read_lock(project)
        # Recompose refreshes the provenance to the installed world.
        self.assertEqual(new_lock["system2_version"], self.installed_version)
        # A post-recompose doctor is clean (`current`) — the nudge is resolved.
        self.assertEqual(backend.doctor(project).status, "current")

    # -- (c) content_fingerprint semantics unchanged --------------

    def test_recompose_refreshes_fingerprint_only_for_version_change(self):
        project = self._project_with_old_world_lock()
        backend = ClaudeCodeBackend(base_path=_BASE, compose_fn=ir.compose)
        sources = backend.read_lock_overlay_sources(project)
        result = ir.compose(_BASE, sources, project)
        backend.recompose_from_lock(result.graph, project)
        recomposed_fp = _read_lock(project)["content_fingerprint"]

        # (i) DIFFERS from the old-world fingerprint — because the version differs
        # (1.1.0 -> installed) while the overlay content is byte-identical.
        self.assertNotEqual(recomposed_fp, self.old_fingerprint)
        # (ii) IDENTICAL to a plain fresh compose of the same content+version — the
        # recompute ignores the stale locked value; fingerprint tracks content+version
        # alone, unperturbed by the old-world lock.
        self.assertEqual(recomposed_fp, self._fresh_fingerprint())

    def test_fingerprint_is_version_sensitive_and_deterministic(self):
        # Directly demonstrate  over identical content: the fingerprint
        # changes ONLY when the version changes, and is deterministic otherwise.
        project = tempfile.mkdtemp(prefix="owl-fp-")
        self.addCleanup(shutil.rmtree, project, True)
        result = ir.compose(_BASE, [_TEST_OVERLAY], project)
        ClaudeCodeBackend(base_path=_BASE, compose_fn=ir.compose).emit(
            result.graph, project
        )
        overlay_info = _read_lock(project)["overlays"]
        graph = result.graph

        object.__setattr__(graph, "system2_version", self.old_version)
        fp_old_a, _ = _compute_idempotency(graph, overlay_info, project)
        fp_old_b, _ = _compute_idempotency(graph, overlay_info, project)
        object.__setattr__(graph, "system2_version", self.installed_version)
        fp_new, _ = _compute_idempotency(graph, overlay_info, project)

        # Deterministic for a fixed (content, version).
        self.assertEqual(fp_old_a, fp_old_b)
        # Version-sensitive: same content, different version -> different fingerprint.
        self.assertNotEqual(fp_old_a, fp_new)
        # The historical fixture records the old template's fingerprint. The current
        # template may legitimately evolve, so this compatibility test pins the
        # fingerprint algorithm's determinism and version sensitivity rather than
        # claiming a new composition must reproduce obsolete template bytes.
        self.assertTrue(fp_old_a.startswith("sha256:"))
        self.assertEqual(len(fp_old_a), len("sha256:") + 64)


if __name__ == "__main__":
    unittest.main()
