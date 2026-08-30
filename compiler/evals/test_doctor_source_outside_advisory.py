"""doctor advisory: lock-recorded overlay source resolving OUTSIDE the project."""

import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.base import lock_sources_outside_project
from system2_compiler.backends.claude_code import _drift_check
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY


def _emit(backend_cls, project, sources):
    result = ir.compose(_BASE, list(sources), project)
    if result.graph is None:
        raise AssertionError(f"compose refused {sources!r}: {result.errors!r}")
    backend = backend_cls(base_path=_BASE, overlay_sources=list(sources))
    backend.emit(result.graph, project)
    return backend_cls(base_path=_BASE)


def _kinds(report):
    return [d["kind"] for d in report.details]


class HelperTest(unittest.TestCase):
    def test_inside_source_no_finding(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj, True)
        inside = os.path.join(proj, "ov")
        os.makedirs(inside)
        self.assertEqual(lock_sources_outside_project([inside], proj), [])

    def test_outside_source_flagged(self):
        proj = tempfile.mkdtemp()
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj, True)
        self.addCleanup(shutil.rmtree, outside, True)
        found = lock_sources_outside_project([outside], proj)
        self.assertEqual([f["kind"] for f in found], ["source_outside_project"])

    def test_empty_entries_ignored(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj, True)
        self.assertEqual(lock_sources_outside_project(["", ""], proj), [])

    def test_symlink_back_into_project_not_flagged(self):
        proj = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, proj, True)
        real = os.path.join(proj, "real-ov")
        os.makedirs(real)
        link = tempfile.mkdtemp() + "-link"
        os.symlink(real, link)
        self.addCleanup(lambda: os.path.lexists(link) and os.unlink(link))
        self.assertEqual(lock_sources_outside_project([link], proj), [])


class PiDoctorAdvisoryTest(unittest.TestCase):
    def _run(self, backend_cls, sources):
        proj = tempfile.mkdtemp(prefix="c1-doctor-")
        self.addCleanup(shutil.rmtree, proj, True)
        backend = _emit(backend_cls, proj, sources)
        return backend.doctor(proj)

    def test_pi_outside_source_advisory(self):
        rpt = self._run(PiBackend, [_TEST_OVERLAY])
        self.assertIn("source_outside_project", _kinds(rpt))

    def test_pi_inside_source_no_advisory(self):
        proj = tempfile.mkdtemp(prefix="c1-doctor-pi-in-")
        self.addCleanup(shutil.rmtree, proj, True)
        local = os.path.join(proj, "local-overlay")
        shutil.copytree(_TEST_OVERLAY, local)
        backend = _emit(PiBackend, proj, [local])
        rpt = backend.doctor(proj)
        self.assertNotIn("source_outside_project", _kinds(rpt))


class ClaudeDriftAdvisoryGateTest(unittest.TestCase):
    """The claude advisory is OFF by default (frozen CLI contract) / opt-in via env."""

    def _composed_project(self):
        proj = tempfile.mkdtemp(prefix="c1-claude-")
        self.addCleanup(shutil.rmtree, proj, True)
        result = ir.compose(_BASE, [_TEST_OVERLAY], proj)
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        ClaudeCodeBackend(base_path=_BASE).emit(result.graph, proj)
        return proj

    def test_default_off_no_advisory(self):
        proj = self._composed_project()
        os.environ.pop("SYSTEM2_DOCTOR_ADVISORIES", None)
        result = _drift_check(_BASE, proj)
        kinds = [d["type"] for d in result["details"]]
        self.assertNotIn("source_outside_project", kinds)

    def test_opt_in_surfaces_advisory(self):
        proj = self._composed_project()
        old = os.environ.get("SYSTEM2_DOCTOR_ADVISORIES")
        os.environ["SYSTEM2_DOCTOR_ADVISORIES"] = "1"
        try:
            result = _drift_check(_BASE, proj)
        finally:
            if old is None:
                os.environ.pop("SYSTEM2_DOCTOR_ADVISORIES", None)
            else:
                os.environ["SYSTEM2_DOCTOR_ADVISORIES"] = old
        kinds = [d.get("kind") for d in result["details"]]
        self.assertIn("source_outside_project", kinds)


if __name__ == "__main__":
    unittest.main()
