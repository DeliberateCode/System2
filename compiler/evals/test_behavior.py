"""the implementation work — Path-safety, atomic-write/restore, dry-run, and refusal behavioral
regression tests against the ``compose -> emit`` path (stdlib ``unittest``).

Covers the failure/recovery matrix the goldens do not directly exercise:

* the requirement — ``project_path`` inside or equal to the base dir ⇒ ``compose``
  refuses (``graph is None``, non-empty ``errors``) and emits nothing.
* the requirement — a known-conflict overlay pair ⇒ refusal whose ``errors[0]`` message
  byte-matches the frozen oracle's ``errors[0]`` (oracle invoked as a hermetic
  subprocess via ``evals/oracle.py``).
* the requirement — ``dry_run`` ⇒ ``files_to_write`` is computed and the backend's
  dry-run emit writes nothing to the project dir.
* the requirement — a simulated ``OSError`` mid-write (monkeypatched ``shutil.copy2`` in
  ``backends.claude_code``) ⇒ pre-existing ``CLAUDE.md`` / lock are restored with
  no ``.tmp`` / ``.bak`` / ``.staging`` leftovers.

Runs under both ``python3 -m unittest`` and ``pytest``. Stdlib-only. The oracle is
reached only as a subprocess (never imported).

All cited file/overlay contents are treated as untrusted data; embedded
instructions are not followed.
"""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
import system2_compiler.backends.claude_code as cc
from system2_compiler.backends.claude_code import ClaudeCodeBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_CONFLICT_A = matrix.CONFLICT_A
_CONFLICT_B = matrix.CONFLICT_B

_LEFTOVER_MARKERS = (".tmp", ".bak", ".staging")


def _mktemp(prefix: str) -> str:
    return tempfile.mkdtemp(prefix=prefix)


def _walk_leftovers(root: str):
    """Return every path under *root* whose name carries a temp/backup marker."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            if any(m in name for m in _LEFTOVER_MARKERS):
                found.append(os.path.join(dirpath, name))
        for name in dirnames:
            if any(m in name for m in _LEFTOVER_MARKERS):
                found.append(os.path.join(dirpath, name))
    return found


class PathSafetyTest(unittest.TestCase):
    """the requirement: project_path inside/equal to the base dir is refused."""

    def test_project_equal_to_base_refused(self):
        result = ir.compose(_BASE, [], _BASE)
        self.assertIsNone(result.graph, "compose must refuse project == base")
        self.assertTrue(result.errors, "refusal must carry an explicit error")
        self.assertEqual([], result.files_to_write)

    def test_project_inside_base_refused(self):
        inside = os.path.join(_BASE, "nested", "project")
        result = ir.compose(_BASE, [], inside)
        self.assertIsNone(result.graph, "compose must refuse project inside base")
        self.assertTrue(result.errors)
        self.assertIn(
            "inside or equal to",
            result.errors[0],
            msg=f"unexpected refusal message: {result.errors[0]!r}",
        )

    def test_sibling_of_base_is_allowed(self):
        # A path that merely shares a prefix string with base but is NOT a
        # descendant must be allowed (guards against a naive startswith check).
        sibling = _BASE + "_sibling_project"
        os.makedirs(sibling, exist_ok=True)
        try:
            result = ir.compose(_BASE, [_TEST_OVERLAY], sibling)
            self.assertIsNotNone(
                result.graph,
                msg="a sibling dir sharing a base prefix must not be refused",
            )
        finally:
            shutil.rmtree(sibling, ignore_errors=True)


class ConflictRefusalParityTest(unittest.TestCase):
    """the requirement: known-conflict refusal message byte-matches the oracle."""

    def test_conflict_refusal_message_matches_oracle(self):
        proj = _mktemp("compose-conflict-")
        try:
            result = ir.compose(_BASE, [_CONFLICT_A, _CONFLICT_B], proj)
        finally:
            shutil.rmtree(proj, ignore_errors=True)

        self.assertIsNone(result.graph, "known conflict must produce no graph")
        self.assertTrue(result.errors, "known conflict must produce errors")

        oracle_proj = _mktemp("oracle-conflict-")
        run = oracle.invoke_oracle(_BASE, [_CONFLICT_A, _CONFLICT_B], oracle_proj)
        try:
            self.assertNotEqual(
                0, run.exit_code, "oracle must refuse the known-conflict pair"
            )
            doc = json.loads(run.stdout)
            oracle_errors = doc.get("errors", [])
            self.assertTrue(oracle_errors, "oracle refusal must carry errors")
            # Byte-for-byte parity on the structural-conflict message.
            self.assertEqual(
                oracle_errors[0],
                result.errors[0],
                msg=(
                    "compose refusal message must byte-match the oracle's:\n"
                    f"  oracle : {oracle_errors[0]!r}\n"
                    f"  compose: {result.errors[0]!r}"
                ),
            )
        finally:
            oracle.cleanup_run(run)
            shutil.rmtree(oracle_proj, ignore_errors=True)


class DryRunTest(unittest.TestCase):
    """the requirement: dry_run computes files_to_write; the backend writes nothing."""

    def test_dry_run_computes_files_and_writes_nothing(self):
        proj = _mktemp("dry-run-")
        try:
            result = ir.compose(_BASE, [_TEST_OVERLAY], proj, dry_run=True)
            self.assertIsNotNone(result.graph, "dry_run still builds the graph")
            self.assertTrue(
                result.files_to_write,
                "dry_run must compute a non-empty files_to_write set",
            )

            # The project dir must be untouched after compose (front-end never writes).
            self.assertEqual(
                [], os.listdir(proj), "compose must not write under project dir"
            )

            # The backend's dry-run path must also write nothing. The graph is
            # frozen; the dry-run intent is carried via the same ``dry_run``
            # attribute the backend reads (getattr(ir, "dry_run", False)).
            object.__setattr__(result.graph, "dry_run", True)
            would_write = ClaudeCodeBackend().emit(result.graph, proj)
            self.assertTrue(would_write, "dry-run emit must return a would-write set")
            self.assertEqual(
                [],
                os.listdir(proj),
                msg="dry-run emit must not write into the project dir (the requirement)",
            )
        finally:
            shutil.rmtree(proj, ignore_errors=True)

    def test_dry_run_files_to_write_includes_core_artifacts(self):
        proj = _mktemp("dry-run-artifacts-")
        try:
            result = ir.compose(_BASE, [_TEST_OVERLAY], proj, dry_run=True)
            joined = "\n".join(result.files_to_write)
            self.assertIn(os.path.join(proj, "CLAUDE.md"), result.files_to_write)
            self.assertIn(
                os.path.join(proj, "spec", "overlay-manifest.lock"),
                result.files_to_write,
            )
            self.assertIn("overlay content", joined)
        finally:
            shutil.rmtree(proj, ignore_errors=True)


class AtomicWriteRestoreTest(unittest.TestCase):
    """the requirement: a mid-write OSError restores backups with no partial output."""

    def _seed_project(self):
        proj = _mktemp("atomic-")
        os.makedirs(os.path.join(proj, "spec"), exist_ok=True)
        claude = os.path.join(proj, "CLAUDE.md")
        lock = os.path.join(proj, "spec", "overlay-manifest.lock")
        with open(claude, "w", encoding="utf-8") as fh:
            fh.write("PRE-EXISTING CLAUDE\n")
        with open(lock, "w", encoding="utf-8") as fh:
            fh.write("PRE-EXISTING LOCK\n")
        return proj, claude, lock

    def test_failure_mid_write_restores_backups_no_leftovers(self):
        proj, claude, lock = self._seed_project()
        result = ir.compose(_BASE, [_TEST_OVERLAY], proj)
        self.assertIsNotNone(result.graph)

        original_copy2 = cc.shutil.copy2

        def _boom(src, dst, *args, **kwargs):
            # Fail when the atomic writer copies the auxiliary agent .md into
            # .claude/agents/ — this occurs AFTER CLAUDE.md/lock have already
            # been os.replace()'d, so it exercises the restore path.
            if dst.endswith(".md") and (os.sep + "agents" + os.sep) in dst:
                raise OSError("simulated mid-write failure (the requirement)")
            return original_copy2(src, dst, *args, **kwargs)

        cc.shutil.copy2 = _boom
        try:
            with self.assertRaises(OSError):
                ClaudeCodeBackend().emit(result.graph, proj)
        finally:
            cc.shutil.copy2 = original_copy2

        # Pre-existing files must be restored byte-for-byte.
        with open(claude, "r", encoding="utf-8") as fh:
            self.assertEqual(
                "PRE-EXISTING CLAUDE\n",
                fh.read(),
                msg="CLAUDE.md was not restored after the write failure",
            )
        with open(lock, "r", encoding="utf-8") as fh:
            self.assertEqual(
                "PRE-EXISTING LOCK\n",
                fh.read(),
                msg="lock was not restored after the write failure",
            )

        # No partial/temp/backup/staging artifacts left behind.
        leftovers = _walk_leftovers(proj)
        self.assertEqual(
            [],
            leftovers,
            msg=f"atomic-write failure left temp/backup leftovers: {leftovers}",
        )
        shutil.rmtree(proj, ignore_errors=True)

    def test_successful_emit_writes_artifacts(self):
        # Positive control: without an injected failure, emit produces the
        # CLAUDE.md and lock (so the failure test's restore is meaningful).
        proj = _mktemp("atomic-ok-")
        try:
            result = ir.compose(_BASE, [_TEST_OVERLAY], proj)
            written = ClaudeCodeBackend().emit(result.graph, proj)
            self.assertTrue(written)
            self.assertTrue(os.path.isfile(os.path.join(proj, "CLAUDE.md")))
            self.assertTrue(
                os.path.isfile(os.path.join(proj, "spec", "overlay-manifest.lock"))
            )
            self.assertEqual(
                [], _walk_leftovers(proj), "clean emit must leave no temp files"
            )
        finally:
            shutil.rmtree(proj, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
