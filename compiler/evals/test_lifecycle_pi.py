"""Pi generated-artifact lifecycle checks."""

import json
import os
import shutil
import tempfile
import unittest

from evals import matrix, oracle
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_FIXTURES = os.path.dirname(matrix.CONFLICT_A)
_REMOVABLE = os.path.join(_FIXTURES, "lifecycle-removable")


def _compose(sources, project):
    result = ir.compose(_BASE, list(sources), project)
    if result.graph is None:
        raise AssertionError(result.errors)
    return result.graph


def _emit(project, sources):
    graph = _compose(sources, project)
    backend = PiBackend(overlay_sources=list(sources))
    backend.emit(graph, project)
    return backend


class PiLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="pi-lifecycle-")
        self.addCleanup(shutil.rmtree, self.project, True)

    def test_doctor_reports_ok_for_an_intact_projection(self):
        _emit(self.project, [_TEST_OVERLAY])
        report = PiBackend().doctor(self.project)
        self.assertEqual(report.status, "ok", report.details)
        self.assertEqual(report.exit_code, 0)
        self.assertTrue(report.validator_available)
        self.assertTrue(any(
            detail["kind"] == "runtime_boundary" for detail in report.details
        ))

    def test_doctor_reports_missing_owned_skill(self):
        _emit(self.project, [_TEST_OVERLAY])
        os.unlink(os.path.join(
            self.project,
            ".pi",
            "skills",
            "system2-role-executor",
            "SKILL.md",
        ))
        report = PiBackend().doctor(self.project)
        self.assertEqual(report.status, "broken", report.details)
        self.assertEqual(report.exit_code, 1)

    def test_doctor_reports_missing_overlay_source(self):
        copied = tempfile.mkdtemp(prefix="pi-overlay-")
        self.addCleanup(shutil.rmtree, copied, True)
        source = os.path.join(copied, "lifecycle-removable")
        shutil.copytree(_REMOVABLE, source)
        _emit(self.project, [_TEST_OVERLAY, source])
        shutil.rmtree(copied)
        report = PiBackend().doctor(self.project)
        self.assertEqual(report.status, "stale_overlay", report.details)

    def test_uninstall_one_overlay_recomposes(self):
        _emit(self.project, [_TEST_OVERLAY, _REMOVABLE])
        backend = PiBackend(base_path=_BASE, compose_fn=ir.compose)
        result = backend.uninstall(self.project, "lifecycle-removable")
        self.assertEqual(result.errors, [])
        self.assertFalse(result.is_last_overlay)
        with open(backend.lock_path(self.project), encoding="utf-8") as stream:
            lock = json.load(stream)
        self.assertEqual(
            [os.path.basename(source) for source in lock["overlay_sources"]],
            ["test-overlay"],
        )

    def test_uninstall_last_overlay_removes_owned_artifacts(self):
        backend = _emit(self.project, [_TEST_OVERLAY])
        result = PiBackend(
            base_path=_BASE, compose_fn=ir.compose
        ).uninstall(self.project, "test-overlay")
        self.assertEqual(result.errors, [])
        self.assertTrue(result.is_last_overlay)
        self.assertFalse(os.path.exists(backend.lock_path(self.project)))
        self.assertFalse(os.path.exists(os.path.join(self.project, ".pi", "prompts", "system2.md")))

    def test_recompose_from_lock_keeps_the_same_file_set(self):
        backend = _emit(self.project, [_TEST_OVERLAY, _REMOVABLE])
        before = {
            os.path.relpath(os.path.join(root, name), self.project)
            for root, _dirs, files in os.walk(self.project)
            for name in files
        }
        sources = backend.read_lock_overlay_sources(self.project)
        graph = _compose(sources, self.project)
        PiBackend(overlay_sources=sources).recompose_from_lock(graph, self.project)
        after = {
            os.path.relpath(os.path.join(root, name), self.project)
            for root, _dirs, files in os.walk(self.project)
            for name in files
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
