"""Focused ownership checks for generated Codex and Pi artifacts."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from evals import matrix, oracle
from system2_compiler import ir
from system2_compiler.backends.codex import CodexBackend
from system2_compiler.backends.pi import PiBackend

_BASE = oracle.PLUGIN_ROOT
_OVERLAY = matrix.TEST_OVERLAY

_CASES = (
    (
        CodexBackend,
        os.path.join(".codex-plugin", "plugin.json"),
        os.path.join("skills", "caller-skill", "SKILL.md"),
    ),
    (
        PiBackend,
        os.path.join(".pi", "prompts", "system2.md"),
        os.path.join(".pi", "skills", "caller-skill", "SKILL.md"),
    ),
)


def _compose(project, sources=(_OVERLAY,)):
    result = ir.compose(_BASE, list(sources), project)
    if result.graph is None:
        raise AssertionError(result.errors)
    return result.graph


def _tree(root):
    return {
        os.path.relpath(os.path.join(directory, name), root): Path(
            directory, name
        ).read_bytes()
        for directory, _subdirs, files in os.walk(root)
        for name in files
    }


class LifecycleOwnershipTest(unittest.TestCase):
    def test_first_emit_refuses_owned_path_collision(self):
        for backend_cls, owned_relative, _caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                target = Path(project, owned_relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("caller-owned\n")
                before = _tree(project)
                with self.assertRaises(FileExistsError):
                    backend_cls().emit(_compose(project), project)
                self.assertEqual(_tree(project), before)

    def test_emit_preserves_unrelated_caller_files(self):
        for backend_cls, _owned_relative, _caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                caller = Path(project, "NOTES.md")
                caller.write_text("keep me\n")
                backend_cls().emit(_compose(project), project)
                self.assertEqual(caller.read_text(), "keep me\n")

    def test_recompose_refuses_modified_owned_artifact(self):
        for backend_cls, owned_relative, _caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                graph = _compose(project)
                backend = backend_cls()
                backend.emit(graph, project)
                Path(project, owned_relative).write_text("local modification\n")
                before = _tree(project)
                with self.assertRaisesRegex(ValueError, "was modified"):
                    backend_cls(overlay_sources=[_OVERLAY]).recompose_from_lock(
                        graph, project
                    )
                self.assertEqual(_tree(project), before)

    def test_repeat_emit_is_owned_and_idempotent(self):
        for backend_cls, _owned_relative, _caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                graph = _compose(project)
                backend = backend_cls()
                first = backend.emit(graph, project)
                before = _tree(project)
                second = backend.emit(graph, project)
                self.assertEqual(first, second)
                self.assertEqual(_tree(project), before)

    def test_dry_run_does_not_mutate(self):
        for backend_cls, _owned_relative, _caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                graph = _compose(project)
                backend = backend_cls()
                backend.emit(graph, project)
                before = _tree(project)
                planned = backend_cls(overlay_sources=[_OVERLAY]).recompose_from_lock(
                    graph, project, dry_run=True
                )
                self.assertTrue(planned)
                self.assertEqual(_tree(project), before)

    def test_uninstall_last_removes_only_owned_artifacts(self):
        for backend_cls, _owned_relative, caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                backend = backend_cls()
                backend.emit(_compose(project), project)
                caller = Path(project, caller_relative)
                caller.parent.mkdir(parents=True, exist_ok=True)
                caller.write_text("caller-owned\n")

                result = backend_cls(
                    base_path=_BASE, compose_fn=ir.compose
                ).uninstall(project, "test-overlay")

                self.assertEqual(result.errors, [])
                self.assertTrue(result.is_last_overlay)
                self.assertEqual(caller.read_text(), "caller-owned\n")
                self.assertFalse(Path(backend.lock_path(project)).exists())
                for path in result.artifacts_removed:
                    self.assertFalse(os.path.exists(path))

    def test_lock_inventory_matches_written_files(self):
        for backend_cls, _owned_relative, _caller_relative in _CASES:
            with self.subTest(backend=backend_cls.__name__), tempfile.TemporaryDirectory() as project:
                backend = backend_cls()
                written = backend.emit(_compose(project), project)
                lock = json.loads(Path(backend.lock_path(project)).read_text())
                owned = [entry["path"] for entry in lock["ownership"]["artifacts"]]
                expected = [
                    os.path.relpath(path, project).replace(os.sep, "/")
                    for path in written
                ]
                self.assertEqual(owned, expected)
                self.assertEqual(set(lock["ownership"]["artifacts"][-1]), {"path"})


if __name__ == "__main__":
    unittest.main()
