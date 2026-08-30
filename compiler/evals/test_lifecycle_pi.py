"""Pi lifecycle behavioral tests."""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_FIXTURES = os.path.dirname(matrix.CONFLICT_A)
_REMOVABLE = os.path.join(_FIXTURES, "lifecycle-removable")
_NEWER_SCHEMA = os.path.join(_FIXTURES, "lifecycle-newer-schema")

_REAL_PI = os.path.join(os.path.expanduser("~"), ".pi")


def _dir_fingerprint(path):
    """Sorted (relpath, size, mtime_ns) listing of *path*, or None when absent."""
    if not os.path.isdir(path):
        return None
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append((os.path.relpath(full, path), st.st_size, st.st_mtime_ns))
    return tuple(entries)


def _compose(sources, project, *, allow_newer_schema=False):
    result = ir.compose(
        _BASE, list(sources), project, allow_newer_schema=allow_newer_schema
    )
    if result.graph is None:
        raise AssertionError(f"compose refused {sources!r}: {result.errors!r}")
    return result.graph


def _read_lock(lock_path):
    with open(lock_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class _LifecycleBase(unittest.TestCase):
    """Shared scaffolding: each backend supplies a factory + fixed artifacts."""

    #: subclass overrides
    backend_cls = None
    validator_env_keys = ()  # env overrides to clear for the validator-absent test

    def _backend(self, *, base_path=None, compose_fn=None, overlay_sources=None):
        return self.backend_cls(
            base_path=base_path,
            compose_fn=compose_fn,
            overlay_sources=overlay_sources,
        )

    def _emit(self, project, sources):
        """Compose + emit *sources* into *project*, recording them in the lock."""
        graph = _compose(sources, project)
        backend = self._backend(overlay_sources=list(sources))
        written = backend.emit(graph, project)
        return backend, written

    def _shadowed_env(self):
        """An os.environ patch that hides the validator binaries off PATH."""
        empty = tempfile.mkdtemp(prefix="empty-path-")
        self.addCleanup(shutil.rmtree, empty, True)
        patched = dict(os.environ)
        patched["PATH"] = empty
        for k in self.validator_env_keys:
            patched.pop(k, None)
        return patched


class PiLifecycleTest(_LifecycleBase):
    backend_cls = PiBackend
    validator_env_keys = ("NODE_BIN", "PI_BIN", "PI_PKG_ENTRY")

    @classmethod
    def setUpClass(cls):
        cls._real_pi_before = _dir_fingerprint(_REAL_PI)

    @classmethod
    def tearDownClass(cls):
        assert _dir_fingerprint(_REAL_PI) == cls._real_pi_before, (
            "a Pi lifecycle test modified the real ~/.pi"
        )

    def _pi_validator_present(self):
        from system2_compiler.backends.pi import _resolve_pi_pkg_entry
        node = os.environ.get("NODE_BIN") or shutil.which("node")
        pi = os.environ.get("PI_BIN") or shutil.which("pi")
        return bool(node and pi and _resolve_pi_pkg_entry(pi))

    # -- doctor -------------------------------------------------------------

    def test_doctor_current_validator_ran(self):
        if not self._pi_validator_present():
            self.skipTest("node/pi not available — cannot assert the validator RAN")
        project = tempfile.mkdtemp(prefix="pi-doctor-current-")
        self.addCleanup(shutil.rmtree, project, True)
        self._emit(project, [_TEST_OVERLAY])
        report = self._backend(base_path=_BASE).doctor(project)
        self.assertEqual(report.status, "current", report.details)
        self.assertTrue(report.validator_available, "pi validator must have run")
        self.assertEqual(report.exit_code, 0)
        kinds = {d["kind"] for d in report.details}
        self.assertNotIn("validator_unavailable", kinds)

    def test_doctor_no_lock(self):
        project = tempfile.mkdtemp(prefix="pi-doctor-nolock-")
        self.addCleanup(shutil.rmtree, project, True)
        report = self._backend(base_path=_BASE).doctor(project)
        self.assertEqual(report.status, "no_lock")
        self.assertEqual(report.exit_code, 1)
        self.assertFalse(report.composed)

    def test_doctor_broken_missing_extension(self):
        project = tempfile.mkdtemp(prefix="pi-doctor-broken-")
        self.addCleanup(shutil.rmtree, project, True)
        self._emit(project, [_TEST_OVERLAY])
        os.unlink(os.path.join(project, ".pi", "extensions", "system2.ts"))
        report = self._backend(base_path=_BASE).doctor(project)
        self.assertEqual(report.status, "broken", report.details)
        self.assertEqual(report.exit_code, 1)

    def test_doctor_stale_overlay_source_moved(self):
        project = tempfile.mkdtemp(prefix="pi-doctor-stale-")
        self.addCleanup(shutil.rmtree, project, True)
        moved = tempfile.mkdtemp(prefix="pi-stale-src-")
        shutil.copytree(_REMOVABLE, os.path.join(moved, "lifecycle-removable"))
        src = os.path.join(moved, "lifecycle-removable")
        self._emit(project, [_TEST_OVERLAY, src])
        shutil.rmtree(moved, ignore_errors=True)
        report = self._backend(base_path=_BASE).doctor(project)
        self.assertEqual(report.status, "stale_overlay", report.details)
        self.assertEqual(report.exit_code, 1)
        self.assertTrue(
            any(d["kind"] == "stale_overlay" for d in report.details)
        )

    def test_doctor_validator_absent_is_loud_exit_zero(self):
        project = tempfile.mkdtemp(prefix="pi-doctor-loud-")
        self.addCleanup(shutil.rmtree, project, True)
        self._emit(project, [_TEST_OVERLAY])
        patched = self._shadowed_env()
        old = dict(os.environ)
        os.environ.clear()
        os.environ.update(patched)
        try:
            report = self._backend(base_path=_BASE).doctor(project)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertFalse(
            report.validator_available,
            "shadowing node/pi off PATH must report validator_available=False",
        )
        self.assertTrue(
            any(d["kind"] == "validator_unavailable" for d in report.details),
            "absent validator must record a LOUD validator_unavailable finding",
        )
        self.assertEqual(report.status, "current", report.details)
        self.assertEqual(
            report.exit_code, 0,
            "absent validator on a current tree is exit 0, not punished",
        )

    # -- uninstall ----------------------------------------------------------

    def test_uninstall_one_of_n_recomposes_and_trims_lock(self):
        project = tempfile.mkdtemp(prefix="pi-uninstall-1ofn-")
        self.addCleanup(shutil.rmtree, project, True)
        self._emit(project, [_TEST_OVERLAY, _REMOVABLE])
        backend = self._backend(base_path=_BASE, compose_fn=ir.compose)
        result = backend.uninstall(project, "lifecycle-removable")
        self.assertEqual(result.errors, [])
        self.assertFalse(result.is_last_overlay)
        self.assertEqual(result.removed, {"name": "lifecycle-removable"})
        self.assertEqual([r["name"] for r in result.remaining], ["test-overlay"])
        self.assertTrue(
            os.path.isfile(os.path.join(project, ".pi", "extensions", "system2.ts"))
        )
        lock = _read_lock(backend.lock_path(project))
        names = [os.path.basename(s) for s in lock.get("overlay_sources", [])]
        self.assertEqual(names, ["test-overlay"])

    def test_uninstall_last_overlay_full_teardown(self):
        project = tempfile.mkdtemp(prefix="pi-uninstall-last-")
        self.addCleanup(shutil.rmtree, project, True)
        self._emit(project, [_TEST_OVERLAY])
        backend = self._backend(base_path=_BASE, compose_fn=ir.compose)
        result = backend.uninstall(project, "test-overlay")
        self.assertEqual(result.errors, [])
        self.assertTrue(result.is_last_overlay)
        self.assertTrue(result.artifacts_removed)
        self.assertFalse(
            os.path.isfile(os.path.join(project, ".pi", "extensions", "system2.ts"))
        )
        self.assertFalse(os.path.isfile(backend.lock_path(project)))

    def test_uninstall_not_installed_refuses(self):
        project = tempfile.mkdtemp(prefix="pi-uninstall-absent-")
        self.addCleanup(shutil.rmtree, project, True)
        self._emit(project, [_TEST_OVERLAY])
        backend = self._backend(base_path=_BASE, compose_fn=ir.compose)
        result = backend.uninstall(project, "lifecycle-removable")
        self.assertTrue(result.errors)
        self.assertIn("is not installed", result.errors[0])

    def test_uninstall_allow_newer_schema_forwarded(self):
        project = tempfile.mkdtemp(prefix="pi-uninstall-newer-")
        self.addCleanup(shutil.rmtree, project, True)
        graph = _compose([_REMOVABLE, _NEWER_SCHEMA], project, allow_newer_schema=True)
        self._backend(
            overlay_sources=[_REMOVABLE, _NEWER_SCHEMA]
        ).emit(graph, project)
        backend = self._backend(base_path=_BASE, compose_fn=ir.compose)

        refused = backend.uninstall(project, "lifecycle-removable")
        self.assertTrue(
            refused.errors,
            "without the flag, recomposing a newer-schema remaining overlay must "
            "refuse",
        )

        graph2 = _compose(
            [_REMOVABLE, _NEWER_SCHEMA], project, allow_newer_schema=True
        )
        self._backend(
            overlay_sources=[_REMOVABLE, _NEWER_SCHEMA]
        ).emit(graph2, project)
        ok = backend.uninstall(
            project, "lifecycle-removable", allow_newer_schema=True
        )
        self.assertEqual(
            ok.errors, [],
            "with allow_newer_schema=True the newer-schema remaining overlay must "
            "recompose cleanly (oracle parity)",
        )
        self.assertEqual([r["name"] for r in ok.remaining], ["lifecycle-newer-schema"])

    # -- recompose_from_lock ------------------------------------------------

    def test_recompose_from_lock_round_trips(self):
        project = tempfile.mkdtemp(prefix="pi-recompose-")
        self.addCleanup(shutil.rmtree, project, True)
        backend, _ = self._emit(project, [_TEST_OVERLAY, _REMOVABLE])
        before = _dir_fingerprint(project)

        sources = backend.read_lock_overlay_sources(project)
        self.assertEqual(
            [os.path.basename(s) for s in sources],
            ["test-overlay", "lifecycle-removable"],
        )
        graph = _compose(sources, project)
        re_backend = self._backend(overlay_sources=list(sources))
        re_backend.recompose_from_lock(graph, project)

        after = _dir_fingerprint(project)
        self.assertEqual(
            {p for p, _, _ in before}, {p for p, _, _ in after},
            "recompose_from_lock must reproduce the same artifact set",
        )


# The shared base must not be collected as a test case on its own.
del _LifecycleBase


if __name__ == "__main__":
    unittest.main()
