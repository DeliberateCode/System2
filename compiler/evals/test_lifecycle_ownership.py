"""Regression tests for backend ownership boundaries and dry-run purity."""

import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from system2_compiler import cli, ir
from system2_compiler.backends import codex, pi
from system2_compiler.backends.base import (
    build_artifact_ownership,
    validate_artifact_ownership,
)
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


def _add_synthetic_owned_artifact(backend, project, content=b"stale owned\n"):
    relative = "system2-stale-owned.txt"
    path = os.path.join(project, relative)
    _write(path, content)
    lock_path = backend.lock_path(project)
    with open(lock_path, "r", encoding="utf-8") as fh:
        lock = json.load(fh)
    lock["ownership"]["artifacts"].insert(
        -1,
        {"path": relative, "sha256": hashlib.sha256(content).hexdigest()},
    )
    with open(lock_path, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2)
        fh.write("\n")
    return relative, path


def _run_cli(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


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
    _MALFORMED_CODEX_LOCKS = (
        ("list-root", [], "Lock file is malformed: expected a JSON object"),
        ("null-root", None, "Lock file is malformed: expected a JSON object"),
        ("boolean-root", True, "Lock file is malformed: expected a JSON object"),
        ("numeric-root", 1, "Lock file is malformed: expected a JSON object"),
        ("string-root", "lock", "Lock file is malformed: expected a JSON object"),
        (
            "string-sources",
            {"overlay_sources": _TEST_OVERLAY},
            "Lock file is malformed: 'overlay_sources' is not a list",
        ),
        (
            "empty-source",
            {"overlay_sources": [""]},
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths",
        ),
        (
            "null-source",
            {"overlay_sources": [None]},
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths",
        ),
        (
            "numeric-source",
            {"overlay_sources": [1]},
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths",
        ),
        (
            "boolean-source",
            {"overlay_sources": [True]},
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths",
        ),
        (
            "object-source",
            {"overlay_sources": [{}]},
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths",
        ),
        (
            "list-source",
            {"overlay_sources": [[]]},
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths",
        ),
    )

    def test_codex_backend_refuses_malformed_lock_sources_without_mutation(self):
        for label, lock, expected in self._MALFORMED_CODEX_LOCKS:
            with self.subTest(lock_shape=label):
                with tempfile.TemporaryDirectory(
                    prefix="codex-malformed-lock-backend-"
                ) as project:
                    backend = CodexBackend(base_path=_BASE, compose_fn=ir.compose)
                    with open(backend.lock_path(project), "w", encoding="utf-8") as fh:
                        json.dump(lock, fh)
                    before = _tree_fingerprint(project)

                    with self.assertRaises(ValueError) as raised:
                        backend.read_lock_overlay_sources(project)
                    self.assertEqual(str(raised.exception), expected)
                    self.assertEqual(_tree_fingerprint(project), before)

                    result = backend.uninstall(project, "test-overlay")
                    self.assertEqual(result.errors, [expected])
                    self.assertEqual(_tree_fingerprint(project), before)

    def test_codex_cli_refuses_malformed_lock_sources_without_mutation(self):
        operations = (
            ("compile-from-lock", ["compile", "--from-lock"]),
            ("from-lock", ["from-lock"]),
            ("uninstall", ["uninstall", "--name", "test-overlay"]),
        )
        for label, lock, expected in self._MALFORMED_CODEX_LOCKS:
            for operation, prefix in operations:
                for fmt in ("json", "text"):
                    with self.subTest(
                        lock_shape=label, operation=operation, format=fmt
                    ):
                        with tempfile.TemporaryDirectory(
                            prefix="codex-malformed-lock-cli-"
                        ) as project:
                            lock_path = CodexBackend().lock_path(project)
                            with open(lock_path, "w", encoding="utf-8") as fh:
                                json.dump(lock, fh)
                            before = _tree_fingerprint(project)
                            argv = prefix + [
                                "--target", "codex", "--base", _BASE,
                                "--project", project, "--format", fmt,
                            ]

                            code, stdout, stderr = _run_cli(argv)

                            self.assertEqual(code, 1, (stdout, stderr))
                            self.assertNotIn("Traceback", stdout + stderr)
                            if fmt == "json":
                                self.assertEqual(stderr, "")
                                payload = json.loads(stdout)
                                self.assertEqual(payload["status"], "error")
                                if operation == "uninstall":
                                    self.assertEqual(payload["errors"], [expected])
                                    self.assertEqual(payload["report"], {})
                                else:
                                    self.assertEqual(payload["message"], expected)
                            else:
                                self.assertEqual(stdout, "")
                                self.assertEqual(stderr, f"ERROR: {expected}\n")
                            self.assertEqual(_tree_fingerprint(project), before)

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

    def test_repeat_emit_and_cli_compose_are_authorized_by_valid_lock(self):
        for backend_cls, target in ((CodexBackend, "codex"), (PiBackend, "pi")):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="repeat-emit-") as project:
                    graph = _compose(project)
                    backend = backend_cls(overlay_sources=[_TEST_OVERLAY])
                    first = backend.emit(graph, project)
                    self.assertEqual(backend.emit(graph, project), first)

                with tempfile.TemporaryDirectory(prefix="repeat-cli-") as project:
                    compile_argv = [
                        "compile", "--target", target, "--base", _BASE,
                        "--project", project, "--overlays", _TEST_OVERLAY,
                        "--format", "json",
                    ]
                    first_code, _out, first_err = _run_cli(compile_argv)
                    repeat_code, _out, repeat_err = _run_cli(compile_argv)
                    from_lock_code, _out, from_lock_err = _run_cli([
                        "from-lock", "--target", target, "--base", _BASE,
                        "--project", project, "--format", "json",
                    ])
                    self.assertEqual(first_code, 0, first_err)
                    self.assertEqual(repeat_code, 0, repeat_err)
                    self.assertEqual(from_lock_code, 0, from_lock_err)

    def test_legacy_lock_without_ownership_refuses_repeat_emit(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="legacy-lock-") as project:
                    backend = _emit(backend_cls, project)
                    lock_path = backend.lock_path(project)
                    with open(lock_path, "r", encoding="utf-8") as fh:
                        lock = json.load(fh)
                    del lock["ownership"]
                    with open(lock_path, "w", encoding="utf-8") as fh:
                        json.dump(lock, fh)
                    before = _tree_fingerprint(project)
                    graph = _compose(project)
                    with self.assertRaisesRegex(ValueError, "ownership record"):
                        backend.emit(graph, project)
                    self.assertEqual(_tree_fingerprint(project), before)

    def test_ownership_paths_are_canonical_and_reject_ambiguous_inputs(self):
        ownership = build_artifact_ownership(
            [(r".pi\SYSTEM.md", "content")], r"locks\system2.pi.lock.json"
        )
        self.assertEqual(
            [entry["path"] for entry in ownership["artifacts"]],
            [".pi/SYSTEM.md", "locks/system2.pi.lock.json"],
        )
        lock = {"ownership": ownership}
        self.assertEqual(
            validate_artifact_ownership(lock, "locks/system2.pi.lock.json"),
            [(".pi/SYSTEM.md", hashlib.sha256(b"content").hexdigest())],
        )

        invalid_paths = (
            r".pi\SYSTEM.md",
            "../outside",
            "safe/../outside",
            r"..\outside",
            r"safe\..\outside",
            "safe//artifact",
        )
        for invalid in invalid_paths:
            with self.subTest(path=invalid):
                damaged = json.loads(json.dumps(lock))
                damaged["ownership"]["artifacts"][0]["path"] = invalid
                with self.assertRaisesRegex(ValueError, "invalid owned artifact path"):
                    validate_artifact_ownership(
                        damaged, "locks/system2.pi.lock.json"
                    )

    def test_overlay_sources_is_last_lock_key_for_both_backends(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="lock-order-") as project:
                    backend = _emit(backend_cls, project)
                    with open(backend.lock_path(project), "r", encoding="utf-8") as fh:
                        lock = json.load(fh)
                    self.assertEqual(list(lock)[-1], "overlay_sources")

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

    def test_recompose_removes_unchanged_stale_owned_artifact(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="stale-owned-") as project:
                    backend = _emit(backend_cls, project)
                    relative, path = _add_synthetic_owned_artifact(backend, project)
                    sources = backend.read_lock_overlay_sources(project)

                    backend_cls(overlay_sources=sources).recompose_from_lock(
                        _compose(project, sources), project
                    )

                    self.assertFalse(os.path.lexists(path))
                    with open(backend.lock_path(project), "r", encoding="utf-8") as fh:
                        lock = json.load(fh)
                    self.assertNotIn(
                        relative,
                        [entry["path"] for entry in lock["ownership"]["artifacts"]],
                    )

    def test_recompose_dry_run_reports_and_preserves_stale_owned_artifact(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="stale-dry-run-") as project:
                    backend = _emit(backend_cls, project)
                    _relative, path = _add_synthetic_owned_artifact(backend, project)
                    sources = backend.read_lock_overlay_sources(project)
                    before = _tree_fingerprint(project)

                    planned = backend_cls(
                        overlay_sources=sources
                    ).recompose_from_lock(
                        _compose(project, sources), project, dry_run=True
                    )

                    self.assertIn("(remove) " + path, planned)
                    self.assertEqual(_tree_fingerprint(project), before)

    def test_modified_stale_owned_artifact_refuses_before_mutation(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="stale-modified-") as project:
                    backend = _emit(backend_cls, project)
                    _relative, path = _add_synthetic_owned_artifact(backend, project)
                    _write(path, b"caller modification\n")
                    sources = backend.read_lock_overlay_sources(project)
                    before = _tree_fingerprint(project)

                    with self.assertRaisesRegex(ValueError, "was modified"):
                        backend_cls(overlay_sources=sources).recompose_from_lock(
                            _compose(project, sources), project
                        )

                    self.assertEqual(_tree_fingerprint(project), before)

    def test_write_rollback_continues_after_raced_validation_failure(self):
        for backend_module in (codex, pi):
            with self.subTest(backend=backend_module.__name__):
                with tempfile.TemporaryDirectory(prefix="raced-rollback-") as project:
                    first = os.path.join(project, "first.txt")
                    second = os.path.join(project, "second.txt")
                    third = os.path.join(project, "third.txt")
                    _write(first, b"first before\n")
                    _write(second, b"second before\n")
                    real_replace = backend_module.os.replace
                    real_validate = backend_module.validate_project_target
                    rollback_started = False

                    def fail_third_replace(source, destination):
                        nonlocal rollback_started
                        if destination == third:
                            rollback_started = True
                            raise OSError("synthetic original write failure")
                        return real_replace(source, destination)

                    def refuse_second_restore(project_path, relative_path):
                        if rollback_started and relative_path == "second.txt":
                            raise ValueError("synthetic raced unsafe path")
                        return real_validate(project_path, relative_path)

                    with mock.patch.object(
                        backend_module,
                        "validate_project_target",
                        side_effect=refuse_second_restore,
                    ), mock.patch.object(
                        backend_module.os,
                        "replace",
                        side_effect=fail_third_replace,
                    ):
                        with self.assertRaisesRegex(
                            OSError, "synthetic original write failure"
                        ):
                            backend_module._write_outputs(
                                project,
                                [
                                    ("first.txt", "first after\n"),
                                    ("second.txt", "second after\n"),
                                    ("third.txt", "third after\n"),
                                ],
                            )

                    with open(first, "rb") as fh:
                        self.assertEqual(fh.read(), b"first before\n")
                    with open(second, "rb") as fh:
                        self.assertEqual(fh.read(), b"second after\n")
                    self.assertFalse(os.path.exists(third))
                    leftovers = sorted(os.listdir(project))
                    self.assertFalse(
                        any(name.startswith(".first.txt.") for name in leftovers)
                    )
                    self.assertTrue(
                        any(name.startswith(".second.txt.") for name in leftovers),
                        "an unsafe backup must be left untouched",
                    )

    def test_recompose_rollback_restores_stale_owned_artifact(self):
        cases = ((CodexBackend, codex), (PiBackend, pi))
        for backend_cls, backend_module in cases:
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="stale-rollback-") as project:
                    backend = _emit(backend_cls, project)
                    _relative, path = _add_synthetic_owned_artifact(backend, project)
                    sources = backend.read_lock_overlay_sources(project)
                    before = _tree_fingerprint(project)
                    lock_path = backend.lock_path(project)
                    real_replace = os.replace

                    def fail_lock_replace(source, destination):
                        if destination == lock_path:
                            self.assertFalse(
                                os.path.lexists(path),
                                "stale artifact must be removed before the late failure",
                            )
                            raise OSError("synthetic late lock write failure")
                        return real_replace(source, destination)

                    with mock.patch.object(
                        backend_module.os,
                        "replace",
                        side_effect=fail_lock_replace,
                    ):
                        with self.assertRaisesRegex(
                            OSError, "synthetic late lock write failure"
                        ):
                            backend_cls(
                                overlay_sources=sources
                            ).recompose_from_lock(
                                _compose(project, sources), project
                            )

                    self.assertTrue(os.path.isfile(path))
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
                    expected = [
                        os.path.relpath(path, project).replace(os.sep, "/")
                        for path in written
                    ]
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
