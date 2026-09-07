"""Regression tests for backend ownership boundaries and dry-run purity."""

import hashlib
import json
import os
import stat
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.codex import CodexBackend
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_CALLER_BYTES = b"caller-owned content\n"


def _compose(project, sources=None):
    overlay_sources = [_TEST_OVERLAY] if sources is None else list(sources)
    result = ir.compose(_BASE, overlay_sources, project)
    if result.graph is None:
        raise AssertionError(
            f"compose refused {overlay_sources!r}: {result.errors!r}"
        )
    return result.graph


def _write(path, content=_CALLER_BYTES):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _tree_fingerprint(root):
    """Capture every entry without following symlinks."""
    entries = []

    def visit(directory):
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda entry: entry.name)
        for entry in children:
            path = entry.path
            relative = os.path.relpath(path, root)
            metadata = entry.stat(follow_symlinks=False)
            mode = stat.S_IMODE(metadata.st_mode) if os.name == "posix" else None
            if entry.is_symlink():
                entries.append((relative, "symlink", mode, os.readlink(path)))
            elif entry.is_dir(follow_symlinks=False):
                entries.append((relative, "directory", mode, None))
                visit(path)
            elif entry.is_file(follow_symlinks=False):
                with open(path, "rb") as fh:
                    content = fh.read()
                entries.append((relative, "file", mode, content))
            else:
                entries.append((relative, "other", mode, None))

    visit(root)
    return tuple(entries)


def _emit(backend_cls, project):
    graph = _compose(project)
    backend = backend_cls(overlay_sources=[_TEST_OVERLAY])
    backend.emit(graph, project)
    return backend


def _assert_dry_run_unchanged(test_case, backend_cls, project, edited_relative):
    backend = _emit(backend_cls, project)
    sources = backend.read_lock_overlay_sources(project)
    graph = _compose(project, sources)

    _write(os.path.join(project, edited_relative), b"caller edit before dry-run\n")
    before = _tree_fingerprint(project)

    recompose_backend = backend_cls(overlay_sources=sources)
    planned = recompose_backend.recompose_from_lock(
        graph, project, dry_run=True
    )
    expected_target = (
        os.path.join(project, ".codex-plugin", "plugin.json")
        if backend_cls is CodexBackend
        else os.path.join(project, ".pi", "SYSTEM.md")
    )
    test_case.assertIn(expected_target, planned)
    test_case.assertNotIn(os.path.join(project, edited_relative), planned)

    after = _tree_fingerprint(project)
    if after != before:
        before_by_path = {entry[0]: entry[1:] for entry in before}
        after_by_path = {entry[0]: entry[1:] for entry in after}
        before_paths = set(before_by_path)
        after_paths = set(after_by_path)
        created = sorted(after_paths - before_paths)
        removed = sorted(before_paths - after_paths)
        modified = sorted(
            path
            for path in before_paths & after_paths
            if before_by_path[path] != after_by_path[path]
        )
        test_case.fail(
            f"{backend_cls.__name__}.recompose_from_lock(dry_run=True) "
            "must leave every project entry byte-identical; "
            f"created={created}, removed={removed}, modified={modified}"
        )


class LifecycleOwnershipTest(unittest.TestCase):
    def test_codex_emit_preserves_caller_owned_readme(self):
        with tempfile.TemporaryDirectory(prefix="codex-owned-readme-") as project:
            readme = os.path.join(project, "README.md")
            _write(readme)

            _emit(CodexBackend, project)

            with open(readme, "rb") as fh:
                self.assertEqual(
                    fh.read(),
                    _CALLER_BYTES,
                    "Codex emit must not overwrite a caller-owned root README.md",
                )

    def test_codex_uninstall_last_preserves_caller_skill(self):
        with tempfile.TemporaryDirectory(prefix="codex-owned-skill-") as project:
            _emit(CodexBackend, project)
            caller_skill = os.path.join(
                project, "skills", "caller-skill", "SKILL.md"
            )
            _write(caller_skill)

            backend = CodexBackend(base_path=_BASE, compose_fn=ir.compose)
            result = backend.uninstall(project, "test-overlay")

            self.assertEqual(result.errors, [], "Codex uninstall-last must succeed")
            self.assertTrue(
                os.path.isfile(caller_skill),
                "Codex uninstall-last must not remove skills/caller-skill/SKILL.md",
            )

    def test_pi_emit_preserves_caller_owned_agents(self):
        with tempfile.TemporaryDirectory(prefix="pi-owned-agents-") as project:
            agents = os.path.join(project, "AGENTS.md")
            _write(agents)

            _emit(PiBackend, project)

            with open(agents, "rb") as fh:
                self.assertEqual(
                    fh.read(),
                    _CALLER_BYTES,
                    "Pi emit must not overwrite a caller-owned root AGENTS.md",
                )

    def test_pi_uninstall_last_preserves_caller_skill_and_prompt(self):
        with tempfile.TemporaryDirectory(prefix="pi-owned-artifacts-") as project:
            _emit(PiBackend, project)
            caller_skill = os.path.join(
                project, ".pi", "skills", "caller-skill", "SKILL.md"
            )
            caller_prompt = os.path.join(
                project, ".pi", "prompts", "role-caller.md"
            )
            _write(caller_skill)
            _write(caller_prompt)

            backend = PiBackend(base_path=_BASE, compose_fn=ir.compose)
            result = backend.uninstall(project, "test-overlay")

            self.assertEqual(result.errors, [], "Pi uninstall-last must succeed")
            caller_artifacts = (
                (caller_skill, ".pi/skills/caller-skill/SKILL.md"),
                (caller_prompt, ".pi/prompts/role-caller.md"),
            )
            for path, relative in caller_artifacts:
                with self.subTest(caller_artifact=relative):
                    self.assertTrue(
                        os.path.isfile(path),
                        f"Pi uninstall-last must not remove {relative}",
                    )

    def test_codex_recompose_dry_run_leaves_tree_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="codex-dry-run-") as project:
            _assert_dry_run_unchanged(self, CodexBackend, project, "README.md")

    def test_pi_recompose_dry_run_leaves_tree_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="pi-dry-run-") as project:
            _assert_dry_run_unchanged(self, PiBackend, project, "AGENTS.md")

    def test_first_emit_collision_refuses_without_partial_mutation(self):
        cases = (
            (CodexBackend, os.path.join(".codex-plugin", "plugin.json")),
            (PiBackend, os.path.join(".pi", "SYSTEM.md")),
        )
        for backend_cls, relative in cases:
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="emit-collision-") as project:
                    _write(os.path.join(project, relative))
                    before = _tree_fingerprint(project)
                    graph = _compose(project)
                    backend = backend_cls(overlay_sources=[_TEST_OVERLAY])
                    with self.assertRaises(FileExistsError):
                        backend.emit(graph, project)
                    self.assertEqual(_tree_fingerprint(project), before)

    def test_modified_owned_artifact_refuses_recompose_and_uninstall(self):
        cases = (
            (CodexBackend, os.path.join(".codex-plugin", "plugin.json")),
            (PiBackend, os.path.join(".pi", "SYSTEM.md")),
        )
        for backend_cls, relative in cases:
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="modified-owned-") as project:
                    backend = _emit(backend_cls, project)
                    _write(os.path.join(project, relative), b"caller modification\n")
                    before = _tree_fingerprint(project)
                    sources = backend.read_lock_overlay_sources(project)
                    graph = _compose(project, sources)
                    with self.assertRaisesRegex(ValueError, "was modified"):
                        backend_cls(
                            overlay_sources=sources
                        ).recompose_from_lock(graph, project)
                    self.assertEqual(_tree_fingerprint(project), before)

                    result = backend_cls(
                        base_path=_BASE, compose_fn=ir.compose
                    ).uninstall(project, "test-overlay")
                    self.assertTrue(result.errors)
                    self.assertIn("was modified", result.errors[0])
                    self.assertEqual(_tree_fingerprint(project), before)

    def test_invalid_ownership_inventory_refuses_uninstall(self):
        for backend_cls in (CodexBackend, PiBackend):
            for damage in ("missing", "malformed"):
                with self.subTest(backend=backend_cls.__name__, damage=damage):
                    with tempfile.TemporaryDirectory(prefix="invalid-owned-") as project:
                        backend = _emit(backend_cls, project)
                        lock_path = backend.lock_path(project)
                        with open(lock_path, "r", encoding="utf-8") as fh:
                            lock = json.load(fh)
                        if damage == "missing":
                            lock.pop("ownership")
                        else:
                            lock["ownership"]["artifacts"] = {"not": "a list"}
                        with open(lock_path, "w", encoding="utf-8") as fh:
                            json.dump(lock, fh)
                        before = _tree_fingerprint(project)

                        result = backend_cls(
                            base_path=_BASE, compose_fn=ir.compose
                        ).uninstall(project, "test-overlay")
                        self.assertTrue(result.errors)
                        self.assertEqual(_tree_fingerprint(project), before)

    def test_ownership_inventory_exactly_matches_emitted_artifacts(self):
        cases = (
            (
                CodexBackend,
                os.path.join("skills", "caller-skill", "SKILL.md"),
                "README.md",
            ),
            (
                PiBackend,
                os.path.join(".pi", "prompts", "role-caller.md"),
                "AGENTS.md",
            ),
        )
        for backend_cls, foreign_relative, excluded_root in cases:
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="ownership-inventory-") as project:
                    _write(os.path.join(project, foreign_relative))
                    graph = _compose(project)
                    backend = backend_cls(overlay_sources=[_TEST_OVERLAY])
                    written = backend.emit(graph, project)
                    with open(backend.lock_path(project), "r", encoding="utf-8") as fh:
                        lock = json.load(fh)

                    self.assertEqual(lock["ownership"]["schema_version"], 1)
                    entries = lock["ownership"]["artifacts"]
                    paths = [entry["path"] for entry in entries]
                    expected = [os.path.relpath(path, project) for path in written]
                    self.assertEqual(paths, expected)
                    self.assertNotIn(foreign_relative, paths)
                    self.assertNotIn(excluded_root, paths)
                    self.assertEqual(set(entries[-1]), {"path"})
                    for entry in entries[:-1]:
                        with open(
                            os.path.join(project, entry["path"]), "rb"
                        ) as fh:
                            digest = hashlib.sha256(fh.read()).hexdigest()
                        self.assertEqual(entry["sha256"], digest)


if __name__ == "__main__":
    unittest.main()
