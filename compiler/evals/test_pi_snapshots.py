"""Committed per-target golden snapshots for Pi.

The existing Pi golden harness emits twice into two temp projects and
byte-diffs the two trees against each other (a determinism check). That catches
NON-determinism but NOT a *render regression*: if the emitter changes shape but
still emits the same bytes on both runs (and ``discoverAndLoadExtensions`` still
passes), the emit-twice gate stays green while the artifacts silently drifted.

This module closes that gap with COMMITTED snapshots. The emitted ``core+overlay``
tree (reusing ``test-overlay``) is captured once under
``evals/goldens_pi/<cell>/``; the regression test re-emits a fresh tree and
byte-diffs every file against the committed snapshot. Any render drift is a hard
failure.

Normalization: the ONLY genuinely-volatile byte in the emitted trees is the overlay
``source_path`` baked into ``overlay_sources[]`` of the per-target lock
(``system2.pi.lock.json``) — an absolute fixture path that varies by checkout
location. It is normalized to the stable token ``<OVERLAY_SRC>`` on BOTH the
freshly-emitted tree and the committed snapshot before diffing. Nothing else is
volatile (the emitters carry no timestamps). The relpath listing itself is also
asserted (no missing / extra files).

Capture mode: run with ``SYSTEM2_CAPTURE_SNAPSHOTS=1`` to (re)write the committed
snapshots from a fresh emit. Capture is deliberate and out-of-band — the regression
test never auto-rebaselines.

Stdlib-only ``unittest``; hermetic (temp projects; the real ``~/.pi`` is never
touched by ``emit``).
"""

import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_CELL = "core-overlay"

_OVERLAY_TOKEN = b"<OVERLAY_SRC>"
_CAPTURE = os.environ.get("SYSTEM2_CAPTURE_SNAPSHOTS") == "1"

# The lock file that embeds the volatile overlay source path.
_LOCKS = {
    "pi": "system2.pi.lock.json",
}
_SNAPSHOT_DIRS = {
    "pi": os.path.join(_THIS_DIR, "goldens_pi", _CELL),
}
_BACKENDS = {"pi": PiBackend}


def _emit_tree(backend_cls, project_dir):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project_dir)
    if result.graph is None:
        raise AssertionError(f"compose refused core+overlay: {result.errors!r}")
    backend_cls(base_path=_BASE, overlay_sources=[_TEST_OVERLAY]).emit(
        result.graph, project_dir
    )


def _normalize(rel, data):
    """Replace the volatile overlay source abspath with a stable token (lock only)."""
    if os.path.basename(rel) in _LOCKS.values():
        ovl_abs = os.path.abspath(_TEST_OVERLAY).encode("utf-8")
        data = data.replace(ovl_abs, _OVERLAY_TOKEN)
    return data


def _read_normalized_tree(root):
    """Return {relpath: normalized bytes} for every file under *root*."""
    tree = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as fh:
                tree[rel] = _normalize(rel, fh.read())
    return tree


def _read_snapshot(snap_dir):
    tree = {}
    for dirpath, _dirs, files in os.walk(snap_dir):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, snap_dir)
            with open(full, "rb") as fh:
                tree[rel] = fh.read()
    return tree


def _capture(target):
    """Write a fresh normalized emit into the committed snapshot dir."""
    snap_dir = _SNAPSHOT_DIRS[target]
    if os.path.isdir(snap_dir):
        shutil.rmtree(snap_dir)
    project = tempfile.mkdtemp(prefix=f"{target}-capture-")
    try:
        _emit_tree(_BACKENDS[target], project)
        tree = _read_normalized_tree(project)
    finally:
        shutil.rmtree(project, ignore_errors=True)
    for rel, data in tree.items():
        dest = os.path.join(snap_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
    return len(tree)


class _SnapshotRegression:
    """Mixin: subclasses set ``target`` and inherit ``unittest.TestCase`` too."""

    target = None  # subclass sets "pi"

    def setUp(self):
        snap_dir = _SNAPSHOT_DIRS[self.target]
        if not os.path.isdir(snap_dir):
            self.skipTest(
                f"no committed {self.target} snapshot at {snap_dir}; "
                f"run with SYSTEM2_CAPTURE_SNAPSHOTS=1 to create it"
            )

    def _fresh_tree(self):
        project = tempfile.mkdtemp(prefix=f"{self.target}-snap-")
        self.addCleanup(shutil.rmtree, project, True)
        _emit_tree(_BACKENDS[self.target], project)
        return _read_normalized_tree(project)

    def test_byte_identical_to_committed_snapshot(self):
        committed = _read_snapshot(_SNAPSHOT_DIRS[self.target])
        fresh = self._fresh_tree()
        self.assertEqual(
            sorted(fresh), sorted(committed),
            f"{self.target} emitted file set drifted from the committed snapshot",
        )
        for rel in sorted(committed):
            self.assertEqual(
                fresh[rel], committed[rel],
                f"{self.target} artifact {rel!r} drifted from the committed snapshot",
            )

    def test_self_teeth_one_byte_mutation_fails(self):
        """A one-byte snapshot mutation MUST be caught (the diff has teeth)."""
        committed = _read_snapshot(_SNAPSHOT_DIRS[self.target])
        # Pick a deterministic, non-empty file to mutate in-memory.
        target_rel = sorted(r for r, b in committed.items() if b)[0]
        mutated = dict(committed)
        original = mutated[target_rel]
        mutated[target_rel] = original[:-1] + bytes([original[-1] ^ 0x01])
        fresh = self._fresh_tree()
        # Fresh matches the REAL committed bytes ...
        self.assertEqual(fresh[target_rel], committed[target_rel])
        # ... and provably does NOT match the mutated snapshot (teeth).
        self.assertNotEqual(
            fresh[target_rel], mutated[target_rel],
            "self-teeth: a one-byte snapshot mutation was not caught",
        )


class PiSnapshotTest(_SnapshotRegression, unittest.TestCase):
    target = "pi"


if _CAPTURE:  # pragma: no cover - capture is an out-of-band, deliberate action.
    class _CaptureRun(unittest.TestCase):
        def test_capture_pi(self):
            n = _capture("pi")
            self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()
