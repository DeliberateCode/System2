"""Regressions for project/source overlap and backend symlink containment."""

import os
import shutil
import stat
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.codex import CodexBackend
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY


def _tree_fingerprint(root):
    """Capture a tree without following symlinks."""
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
                    entries.append((relative, "file", mode, fh.read()))
            else:
                entries.append((relative, "other", mode, None))

    visit(root)
    return tuple(entries)


def _require_symlinks(test_case, root):
    """Skip only when this platform/filesystem cannot create symlinks."""
    target = os.path.join(root, "symlink-probe-target")
    link = os.path.join(root, "symlink-probe-link")
    os.mkdir(target)
    try:
        os.symlink(target, link, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        test_case.skipTest(f"symlinks are unavailable on this platform: {exc}")
    else:
        os.unlink(link)
    finally:
        os.rmdir(target)


def _compose_graph(project):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project)
    if result.graph is None:
        raise AssertionError(f"test graph composition refused: {result.errors!r}")
    return result.graph


def _assert_refused_without_mutation(
    test_case, operation, roots, *, refused_result=lambda _result: False
):
    before = {name: _tree_fingerprint(path) for name, path in roots.items()}
    refused = False
    outcome = None
    try:
        result = operation()
    except (OSError, ValueError) as exc:
        refused = True
        outcome = type(exc).__name__
    else:
        refused = refused_result(result)
        outcome = type(result).__name__

    after = {name: _tree_fingerprint(path) for name, path in roots.items()}
    changed = sorted(name for name in roots if after[name] != before[name])
    failures = []
    if not refused:
        failures.append(f"operation did not refuse (returned {outcome})")
    if changed:
        failures.append("changed tree(s): " + ", ".join(changed))
    if failures:
        test_case.fail("; ".join(failures))


def _replace_with_matching_external_symlink(project, external, relative):
    owned = os.path.join(project, relative)
    with open(owned, "rb") as fh:
        content = fh.read()
    target = os.path.join(external, "matching-owned-artifact")
    with open(target, "wb") as fh:
        fh.write(content)
    os.unlink(owned)
    os.symlink(target, owned)


class ComposeSourceOverlapTest(unittest.TestCase):
    def test_compose_refuses_project_overlay_overlap_in_every_direction(self):
        cases = (
            "project_contains_source",
            "source_contains_project",
            "same_directory",
            "symlink_alias",
        )
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory(prefix="compose-overlap-") as root:
                    if case == "project_contains_source":
                        project = os.path.join(root, "project")
                        source = os.path.join(project, "overlay")
                        os.makedirs(project)
                        shutil.copytree(_TEST_OVERLAY, source)
                    elif case == "source_contains_project":
                        source = os.path.join(root, "overlay")
                        project = os.path.join(source, "project")
                        shutil.copytree(_TEST_OVERLAY, source)
                        os.mkdir(project)
                    elif case == "same_directory":
                        project = source = os.path.join(root, "project-overlay")
                        shutil.copytree(_TEST_OVERLAY, source)
                    else:
                        _require_symlinks(self, root)
                        project = os.path.join(root, "project-overlay")
                        source = os.path.join(root, "overlay-alias")
                        shutil.copytree(_TEST_OVERLAY, project)
                        os.symlink(project, source, target_is_directory=True)

                    result = ir.compose(_BASE, [source], project)
                    if result.graph is not None:
                        self.fail(
                            "compose must refuse overlapping project/source "
                            f"({case})"
                        )
                    self.assertTrue(result.errors)
                    self.assertEqual(result.files_to_write, [])


class FirstEmitSymlinkContainmentTest(unittest.TestCase):
    def test_codex_emit_refuses_external_skills_parent(self):
        with tempfile.TemporaryDirectory(prefix="codex-parent-link-") as root:
            _require_symlinks(self, root)
            project = os.path.join(root, "project")
            external = os.path.join(root, "external")
            os.mkdir(project)
            os.mkdir(external)
            graph = _compose_graph(project)
            os.symlink(
                external,
                os.path.join(project, "skills"),
                target_is_directory=True,
            )

            _assert_refused_without_mutation(
                self,
                lambda: CodexBackend(
                    overlay_sources=[_TEST_OVERLAY]
                ).emit(graph, project),
                {"project": project, "external": external},
            )

    def test_pi_emit_refuses_external_pi_or_nested_parent(self):
        for linked_parent in (".pi", os.path.join(".pi", "prompts")):
            with self.subTest(linked_parent=linked_parent):
                with tempfile.TemporaryDirectory(prefix="pi-parent-link-") as root:
                    _require_symlinks(self, root)
                    project = os.path.join(root, "project")
                    external = os.path.join(root, "external")
                    os.mkdir(project)
                    os.mkdir(external)
                    graph = _compose_graph(project)
                    link = os.path.join(project, linked_parent)
                    os.makedirs(os.path.dirname(link), exist_ok=True)
                    os.symlink(external, link, target_is_directory=True)

                    _assert_refused_without_mutation(
                        self,
                        lambda: PiBackend(
                            overlay_sources=[_TEST_OVERLAY]
                        ).emit(graph, project),
                        {"project": project, "external": external},
                    )

    def _assert_dangling_emit_refused(self, backend_cls, relative):
        with tempfile.TemporaryDirectory(prefix="dangling-output-") as root:
            _require_symlinks(self, root)
            project = os.path.join(root, "project")
            external = os.path.join(root, "external")
            os.mkdir(project)
            os.mkdir(external)
            graph = _compose_graph(project)
            link = os.path.join(project, relative)
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(os.path.join(external, "missing-target"), link)

            _assert_refused_without_mutation(
                self,
                lambda: backend_cls(
                    overlay_sources=[_TEST_OVERLAY]
                ).emit(graph, project),
                {"project": project, "external": external},
            )

    def test_codex_emit_refuses_dangling_planned_output(self):
        self._assert_dangling_emit_refused(
            CodexBackend, os.path.join(".codex-plugin", "plugin.json")
        )

    def test_codex_emit_refuses_dangling_planned_parent(self):
        self._assert_dangling_emit_refused(CodexBackend, "skills")

    def test_pi_emit_refuses_dangling_planned_output(self):
        self._assert_dangling_emit_refused(
            PiBackend, os.path.join(".pi", "SYSTEM.md")
        )

    def test_pi_emit_refuses_dangling_planned_parent(self):
        self._assert_dangling_emit_refused(
            PiBackend, os.path.join(".pi", "prompts")
        )


class OwnedArtifactSymlinkContainmentTest(unittest.TestCase):
    _CASES = (
        (CodexBackend, os.path.join(".codex-plugin", "plugin.json")),
        (PiBackend, os.path.join(".pi", "SYSTEM.md")),
    )

    def test_recompose_refuses_matching_external_owned_artifact_symlink(self):
        for backend_cls, relative in self._CASES:
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="owned-recompose-link-") as root:
                    _require_symlinks(self, root)
                    project = os.path.join(root, "project")
                    external = os.path.join(root, "external")
                    os.mkdir(project)
                    os.mkdir(external)
                    graph = _compose_graph(project)
                    backend = backend_cls(overlay_sources=[_TEST_OVERLAY])
                    backend.emit(graph, project)
                    sources = backend.read_lock_overlay_sources(project)
                    _replace_with_matching_external_symlink(
                        project, external, relative
                    )

                    _assert_refused_without_mutation(
                        self,
                        lambda: backend_cls(
                            overlay_sources=sources
                        ).recompose_from_lock(
                            _compose_graph(project), project
                        ),
                        {"project": project, "external": external},
                    )

    def test_uninstall_refuses_matching_external_owned_artifact_symlink(self):
        for backend_cls, relative in self._CASES:
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="owned-uninstall-link-") as root:
                    _require_symlinks(self, root)
                    project = os.path.join(root, "project")
                    external = os.path.join(root, "external")
                    os.mkdir(project)
                    os.mkdir(external)
                    graph = _compose_graph(project)
                    backend_cls(
                        overlay_sources=[_TEST_OVERLAY]
                    ).emit(graph, project)
                    _replace_with_matching_external_symlink(
                        project, external, relative
                    )

                    _assert_refused_without_mutation(
                        self,
                        lambda: backend_cls(
                            base_path=_BASE, compose_fn=ir.compose
                        ).uninstall(project, "test-overlay"),
                        {"project": project, "external": external},
                        refused_result=lambda result: bool(result.errors),
                    )


if __name__ == "__main__":
    unittest.main()
