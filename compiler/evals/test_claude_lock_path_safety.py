"""Claude prior-lock deletion-selector containment regressions."""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest

from evals import matrix, oracle, run_goldens
from system2_compiler import cli, ir
from system2_compiler.backends.claude_code import ClaudeCodeBackend

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY


def _tree_fingerprint(root):
    """Capture file bytes, modes, directories, and symlinks without following links."""
    entries = []

    def visit(directory):
        with os.scandir(directory) as iterator:
            children = sorted(iterator, key=lambda entry: entry.name)
        for entry in children:
            relative = os.path.relpath(entry.path, root)
            mode = stat.S_IMODE(entry.stat(follow_symlinks=False).st_mode)
            if entry.is_symlink():
                entries.append((relative, "symlink", mode, os.readlink(entry.path)))
            elif entry.is_dir(follow_symlinks=False):
                entries.append((relative, "directory", mode, None))
                visit(entry.path)
            elif entry.is_file(follow_symlinks=False):
                with open(entry.path, "rb") as fh:
                    entries.append((relative, "file", mode, fh.read()))

    visit(root)
    return tuple(entries)


def _require_symlinks(test_case, root):
    target = os.path.join(root, "symlink-probe-target")
    link = os.path.join(root, "symlink-probe-link")
    os.mkdir(target)
    try:
        os.symlink(target, link, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        test_case.skipTest(f"symlinks are unavailable: {exc}")
    else:
        os.unlink(link)
    finally:
        os.rmdir(target)


def _compose_graph(project):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project)
    if result.graph is None:
        raise AssertionError(f"fixture composition refused: {result.errors!r}")
    return result.graph


def _valid_lock():
    return {
        "schema_version": "1.0.0",
        "system2_version": "1.2.0",
        "overlays": [
            {
                "name": "test-overlay",
                "version": "1.0.0",
                "source_path": _TEST_OVERLAY,
            }
        ],
        "contributions_applied": {"auxiliary_agents": ["test-scout"]},
    }


def _write_lock(project, lock_data):
    path = os.path.join(project, "spec", "overlay-manifest.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lock_data, fh, indent=2)
        fh.write("\n")


def _assert_value_error_without_mutation(test_case, operation, root):
    before = _tree_fingerprint(root)
    with test_case.assertRaises(ValueError):
        operation()
    test_case.assertEqual(before, _tree_fingerprint(root))


def _assert_uninstall_refusal_without_mutation(test_case, operation, root):
    before = _tree_fingerprint(root)
    try:
        result = operation()
    except ValueError:
        pass
    else:
        test_case.assertTrue(result.errors, "uninstall accepted a poisoned lock")
    test_case.assertEqual(before, _tree_fingerprint(root))


class ClaudeEmitLockSafetyTest(unittest.TestCase):
    def _run_overlay_selector_case(self, selector, victim_relative):
        with tempfile.TemporaryDirectory(prefix="claude-overlay-lock-") as root:
            project = os.path.join(root, "project")
            os.mkdir(project)
            victim = os.path.join(root, victim_relative)
            os.makedirs(victim)
            with open(os.path.join(victim, "sentinel"), "wb") as fh:
                fh.write(b"external bytes\x00")

            lock_data = _valid_lock()
            lock_data["overlays"] = [{"name": selector}]
            _write_lock(project, lock_data)
            graph = _compose_graph(project)

            _assert_value_error_without_mutation(
                self, lambda: ClaudeCodeBackend().emit(graph, project), root
            )

    def test_emit_refuses_absolute_traversal_and_separator_overlay_names(self):
        with tempfile.TemporaryDirectory(prefix="claude-absolute-name-") as holder:
            absolute = os.path.join(holder, "victim")
            os.mkdir(absolute)
            with open(os.path.join(absolute, "sentinel"), "wb") as fh:
                fh.write(b"absolute victim")
            with tempfile.TemporaryDirectory(prefix="claude-absolute-project-") as root:
                project = os.path.join(root, "project")
                os.mkdir(project)
                lock_data = _valid_lock()
                lock_data["overlays"] = [{"name": absolute}]
                _write_lock(project, lock_data)
                graph = _compose_graph(project)
                before_project = _tree_fingerprint(root)
                before_external = _tree_fingerprint(holder)
                with self.assertRaises(ValueError):
                    ClaudeCodeBackend().emit(graph, project)
                self.assertEqual(before_project, _tree_fingerprint(root))
                self.assertEqual(before_external, _tree_fingerprint(holder))

        for selector, victim in (
            ("../../../outside-overlay", "outside-overlay"),
            (
                "nested/overlay",
                os.path.join(
                    "project", ".system2", "overlays", "nested", "overlay"
                ),
            ),
        ):
            with self.subTest(selector=selector):
                self._run_overlay_selector_case(selector, victim)

    def test_recompose_refuses_malformed_lock_shapes(self):
        malformed = (
            [],
            {"overlays": {}, "contributions_applied": {}},
            {"overlays": ["test-overlay"], "contributions_applied": {}},
            {"overlays": [{"name": 7}], "contributions_applied": {}},
            {"overlays": [], "contributions_applied": []},
            {"overlays": [], "contributions_applied": {"auxiliary_agents": {}}},
            {"overlays": [], "contributions_applied": {"auxiliary_agents": [7]}},
        )
        for lock_data in malformed:
            with self.subTest(lock_data=lock_data):
                with tempfile.TemporaryDirectory(prefix="claude-lock-shape-") as root:
                    project = os.path.join(root, "project")
                    os.mkdir(project)
                    _write_lock(project, lock_data)
                    graph = _compose_graph(project)
                    _assert_value_error_without_mutation(
                        self,
                        lambda: ClaudeCodeBackend().recompose_from_lock(
                            graph, project
                        ),
                        root,
                    )

    def test_recompose_refuses_poisoned_auxiliary_agent_ids(self):
        with tempfile.TemporaryDirectory(prefix="claude-aux-absolute-") as holder:
            absolute = os.path.join(holder, "outside-agent")
            with open(absolute + ".md", "wb") as fh:
                fh.write(b"outside agent")
            cases = (absolute, "../../../outside-agent", "nested/agent", 7)
            for selector in cases:
                with self.subTest(selector=selector):
                    with tempfile.TemporaryDirectory(prefix="claude-aux-lock-") as root:
                        project = os.path.join(root, "project")
                        os.mkdir(project)
                        lock_data = _valid_lock()
                        lock_data["contributions_applied"][
                            "auxiliary_agents"
                        ] = [selector]
                        _write_lock(project, lock_data)
                        graph = _compose_graph(project)
                        before_external = _tree_fingerprint(holder)
                        _assert_value_error_without_mutation(
                            self,
                            lambda: ClaudeCodeBackend().recompose_from_lock(
                                graph, project
                            ),
                            root,
                        )
                        self.assertEqual(before_external, _tree_fingerprint(holder))

    def test_emit_refuses_symlinked_overlay_and_agent_selectors(self):
        for selector_kind in ("overlay", "agent", "agent-parent"):
            with self.subTest(selector_kind=selector_kind):
                with tempfile.TemporaryDirectory(prefix="claude-lock-link-") as root:
                    _require_symlinks(self, root)
                    project = os.path.join(root, "project")
                    external = os.path.join(root, "external")
                    os.mkdir(project)
                    os.mkdir(external)
                    lock_data = _valid_lock()

                    if selector_kind == "overlay":
                        lock_data["overlays"].append({"name": "stale-overlay"})
                        parent = os.path.join(project, ".system2", "overlays")
                        os.makedirs(parent)
                        os.symlink(
                            external,
                            os.path.join(parent, "stale-overlay"),
                            target_is_directory=True,
                        )
                    else:
                        lock_data["contributions_applied"]["auxiliary_agents"].append(
                            "stale-agent"
                        )
                        agents = os.path.join(project, ".claude", "agents")
                        if selector_kind == "agent-parent":
                            os.makedirs(os.path.dirname(agents))
                            os.symlink(external, agents, target_is_directory=True)
                            external_agent = os.path.join(
                                external, "stale-agent.md"
                            )
                            with open(external_agent, "wb") as fh:
                                fh.write(b"external agent")
                        else:
                            os.makedirs(agents)
                            target = os.path.join(external, "agent.md")
                            with open(target, "wb") as fh:
                                fh.write(b"external agent")
                            os.symlink(target, os.path.join(agents, "stale-agent.md"))

                    _write_lock(project, lock_data)
                    graph = _compose_graph(project)
                    _assert_value_error_without_mutation(
                        self, lambda: ClaudeCodeBackend().emit(graph, project), root
                    )

    def test_cli_reports_poisoned_prior_lock_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="claude-lock-cli-") as root:
            project = os.path.join(root, "project")
            os.mkdir(project)
            lock_data = _valid_lock()
            lock_data["overlays"] = [{"name": "../outside"}]
            _write_lock(project, lock_data)
            before = _tree_fingerprint(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ):
                exit_code = cli.main([
                    "compile", "--target", "claude-code", "--base", _BASE,
                    "--project", project, "--overlays", _TEST_OVERLAY,
                    "--format", "text",
                ])
            self.assertNotEqual(0, exit_code)
            self.assertIn(
                "Cannot plan/write claude-code artifacts", stderr.getvalue()
            )
            self.assertEqual(before, _tree_fingerprint(root))


class ClaudeUninstallLockSafetyTest(unittest.TestCase):
    def test_uninstall_refuses_malformed_and_escaping_auxiliary_selectors(self):
        cases = (
            {},
            [7],
            ["/tmp/outside-agent"],
            ["../../../outside-agent"],
            ["nested/agent"],
        )
        for auxiliary_agents in cases:
            with self.subTest(auxiliary_agents=auxiliary_agents):
                with tempfile.TemporaryDirectory(
                    prefix="claude-uninstall-lock-"
                ) as root:
                    project = os.path.join(root, "project")
                    os.mkdir(project)
                    lock_data = _valid_lock()
                    lock_data["contributions_applied"][
                        "auxiliary_agents"
                    ] = auxiliary_agents
                    _write_lock(project, lock_data)
                    _assert_uninstall_refusal_without_mutation(
                        self,
                        lambda: ClaudeCodeBackend(base_path=_BASE).uninstall(
                            project, "test-overlay"
                        ),
                        root,
                    )

    def test_uninstall_refuses_symlinked_overlay_and_agent_selectors(self):
        for selector_kind in ("overlay", "agent", "cached-agent"):
            with self.subTest(selector_kind=selector_kind):
                with tempfile.TemporaryDirectory(
                    prefix="claude-uninstall-link-"
                ) as root:
                    _require_symlinks(self, root)
                    project = os.path.join(root, "project")
                    external = os.path.join(root, "external")
                    os.mkdir(project)
                    os.mkdir(external)
                    lock_data = _valid_lock()

                    overlay_dir = os.path.join(
                        project, ".system2", "overlays", "test-overlay"
                    )
                    os.makedirs(os.path.dirname(overlay_dir))
                    if selector_kind == "overlay":
                        os.symlink(external, overlay_dir, target_is_directory=True)
                    else:
                        cached_dir = os.path.join(overlay_dir, "agents")
                        os.makedirs(cached_dir)
                        cached_agent = os.path.join(cached_dir, "test-scout.md")
                        external_agent = os.path.join(external, "test-scout.md")
                        with open(external_agent, "wb") as fh:
                            fh.write(b"external agent")
                        if selector_kind == "cached-agent":
                            os.symlink(external_agent, cached_agent)
                        else:
                            with open(cached_agent, "wb") as fh:
                                fh.write(b"cached agent")

                        agents = os.path.join(project, ".claude", "agents")
                        os.makedirs(agents)
                        deployed_agent = os.path.join(agents, "test-scout.md")
                        if selector_kind == "agent":
                            os.symlink(external_agent, deployed_agent)
                        else:
                            with open(deployed_agent, "wb") as fh:
                                fh.write(b"deployed agent")

                    _write_lock(project, lock_data)
                    _assert_uninstall_refusal_without_mutation(
                        self,
                        lambda: ClaudeCodeBackend(base_path=_BASE).uninstall(
                            project, "test-overlay"
                        ),
                        root,
                    )


class ClaudeValidLockControlsTest(unittest.TestCase):
    def test_valid_lock_removes_only_exact_stale_targets(self):
        with tempfile.TemporaryDirectory(prefix="claude-valid-lock-") as project:
            graph = _compose_graph(project)
            ClaudeCodeBackend().emit(graph, project)
            lock_path = os.path.join(project, "spec", "overlay-manifest.lock")
            with open(lock_path, "r", encoding="utf-8") as fh:
                lock_data = json.load(fh)
            lock_data["overlays"].append({"name": "stale-overlay"})
            lock_data["contributions_applied"]["auxiliary_agents"].append(
                "stale-agent"
            )
            stale_overlay = os.path.join(
                project, ".system2", "overlays", "stale-overlay"
            )
            os.mkdir(stale_overlay)
            stale_agent = os.path.join(
                project, ".claude", "agents", "stale-agent.md"
            )
            with open(stale_agent, "wb") as fh:
                fh.write(b"stale agent")
            _write_lock(project, lock_data)

            ClaudeCodeBackend().recompose_from_lock(graph, project)

            self.assertFalse(os.path.lexists(stale_overlay))
            self.assertFalse(os.path.lexists(stale_agent))
            self.assertTrue(os.path.isdir(os.path.join(
                project, ".system2", "overlays", "test-overlay"
            )))
            self.assertTrue(os.path.isfile(os.path.join(
                project, ".claude", "agents", "test-scout.md"
            )))

    def test_claude_compiler_and_oracle_goldens_remain_byte_equivalent(self):
        cell = next(c for c in matrix.all_cells() if c.name == "core+overlay")
        cell_dir = cell.snapshot_dir(run_goldens.DEFAULT_GOLDENS_DIR)
        policy = run_goldens.load_policy()
        self.assertEqual(
            [], run_goldens._diff_composed_compiler(cell, cell_dir, policy)
        )
        self.assertEqual([], run_goldens._diff_composed(cell, cell_dir, policy))


if __name__ == "__main__":
    unittest.main()
