"""Focused controls for graph-native provenance and target-native CLI plans."""

import dataclasses
import inspect
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from system2_compiler import cli, ir
from system2_compiler.backends.base import Backend
from system2_compiler.backends.claude_code import ClaudeCodeBackend
from system2_compiler.backends.codex import CodexBackend
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_ANCHORFILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "anchorfile"
)
_FORBIDDEN_CLAUDE_PATHS = (
    "CLAUDE.md",
    os.path.join(".claude", "agents"),
    os.path.join("spec", "overlay-manifest.lock"),
    os.path.join(".system2", "overlays"),
)


def _compose(project, sources):
    result = ir.compose(_BASE, list(sources), project)
    if result.graph is None:
        raise AssertionError(f"compose refused {sources!r}: {result.errors!r}")
    return result.graph


def _run_cli(argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main(argv)
    return code, stdout.getvalue(), stderr.getvalue()


def _tree_bytes(root):
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                entries.append((os.path.relpath(path, root), fh.read()))
    return entries


def _write(path, content=b"caller-owned\n"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _assert_cli_error(test_case, code, stdout, stderr, fmt, message_fragment):
    test_case.assertEqual(code, 3, (stdout, stderr))
    combined = stdout + stderr
    test_case.assertNotIn("Traceback", combined)
    test_case.assertIn(message_fragment, combined)
    if fmt == "json":
        test_case.assertEqual(stderr, "")
        test_case.assertEqual(json.loads(stdout)["status"], "error")
    else:
        test_case.assertEqual(stdout, "")
        test_case.assertTrue(stderr.startswith("ERROR: "), stderr)


class NeutralGraphContractTest(unittest.TestCase):
    def test_composed_graph_pins_workflow_semantics(self):
        with tempfile.TemporaryDirectory(prefix="graph-contract-") as project:
            graph = _compose(project, (_TEST_OVERLAY,))

        self.assertEqual(
            [(gate.number, gate.name) for gate in graph.gate_graph.gates],
            [(0, "scope"), (1, "context"), (2, "requirements"),
             (3, "design"), (4, "tasks"), (5, "ship")],
        )
        self.assertEqual(
            graph.gate_graph.edges, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
        )
        self.assertEqual(
            graph.delegation_contract.required_fields,
            ["Objective", "Inputs", "Outputs", "Constraints", "Non-goals",
             "Change shape", "Completion summary requirements"],
        )
        self.assertEqual(
            graph.delegation_contract.preferred_order,
            ["repo-governor", "spec-coordinator", "requirements-engineer",
             "design-architect", "task-planner", "executor", "test-engineer",
             "security-sentinel", "eval-engineer", "docs-release",
             "code-reviewer", "postmortem-scribe", "mcp-toolsmith"],
        )
        self.assertEqual(
            graph.post_execution.execution_order,
            ["test-engineer", "code-reviewer (simplification)",
             "security-sentinel", "eval-engineer", "docs-release", "code-reviewer"],
        )
        self.assertEqual(graph.post_execution.boomerang_cap, 3)
        self.assertEqual(graph.maintenance_loop.corrective_cycle_cap, 3)
        self.assertEqual(graph.maintenance_loop.classification, ["Local", "Non-local"])
        self.assertEqual(
            [artifact.name for artifact in graph.spec_artifacts],
            ["context", "requirements", "design", "tasks"],
        )


class GraphProvenanceTest(unittest.TestCase):
    def test_graph_json_carries_ordered_deterministic_overlay_sources(self):
        with tempfile.TemporaryDirectory(prefix="graph-provenance-") as project:
            sources = (_TEST_OVERLAY, _ANCHORFILE)
            first_graph = _compose(project, sources)
            second_graph = _compose(project, sources)

            self.assertIsInstance(first_graph.overlay_sources, tuple)
            self.assertEqual(first_graph.overlay_sources, sources)
            first = first_graph.to_json(indent=2)
            second = second_graph.to_json(indent=2)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(first)["overlay_sources"], list(sources))


class BackendProvenanceTest(unittest.TestCase):
    _LIFECYCLE_METHODS = (
        "emit", "uninstall", "doctor", "recompose_from_lock", "lock_path",
        "read_lock_overlay_sources",
    )

    def test_all_registered_backends_implement_the_complete_protocol(self):
        self.assertEqual(set(cli._BACKENDS), {"claude-code", "codex", "pi"})
        for name, backend in cli._BACKENDS.items():
            with self.subTest(backend=name):
                self.assertIsInstance(backend, Backend)
                for method in self._LIFECYCLE_METHODS:
                    implementation = getattr(backend, method, None)
                    self.assertIsNotNone(implementation, method)
                    source = inspect.getsource(implementation)
                    self.assertFalse(
                        "raise NotImplementedError" in source and source.count("\n") < 6,
                        f"{name}.{method} is only a lifecycle stub",
                    )

    def test_neutral_lifecycle_result_shapes_are_pinned(self):
        from system2_compiler.backends import base

        self.assertEqual(
            {field.name for field in dataclasses.fields(base.UninstallResult)},
            {"removed", "remaining", "artifacts_removed", "files_written",
             "is_last_overlay", "injection_warnings", "preview", "errors"},
        )
        self.assertEqual(
            {field.name for field in dataclasses.fields(base.DoctorReport)},
            {"status", "details", "system2_version", "overlays", "composed",
             "exit_code", "validator_available"},
        )

    def test_default_backends_write_exact_graph_provenance(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="graph-lock-") as project:
                    graph = _compose(project, (_TEST_OVERLAY, _ANCHORFILE))
                    backend = backend_cls()
                    backend.emit(graph, project)
                    with open(backend.lock_path(project), encoding="utf-8") as fh:
                        lock = json.load(fh)
                    self.assertEqual(
                        lock["overlay_sources"], list(graph.overlay_sources)
                    )

    def test_constructor_mismatch_refuses_without_writing(self):
        for backend_cls in (CodexBackend, PiBackend):
            with self.subTest(backend=backend_cls.__name__):
                with tempfile.TemporaryDirectory(prefix="legacy-mismatch-") as project:
                    graph = _compose(project, (_TEST_OVERLAY,))
                    backend = backend_cls(overlay_sources=[_ANCHORFILE])
                    before = _tree_bytes(project)
                    with self.assertRaisesRegex(
                        ValueError, "authoritative graph provenance"
                    ):
                        backend.emit(graph, project)
                    self.assertEqual(_tree_bytes(project), before)


class TargetNativeCliPlanTest(unittest.TestCase):
    def test_claude_plan_is_mutation_free(self):
        with tempfile.TemporaryDirectory(prefix="claude-plan-") as project:
            graph = _compose(project, (_TEST_OVERLAY,))
            sentinel = os.path.join(project, "caller.txt")
            _write(sentinel)
            before = _tree_bytes(project)

            planned = ClaudeCodeBackend().plan(graph, project)

            self.assertEqual(_tree_bytes(project), before)
            self.assertIn(os.path.join(project, "CLAUDE.md"), planned)
            self.assertIn(
                os.path.join(project, "spec", "overlay-manifest.lock"),
                planned,
            )

    def test_codex_and_pi_dry_runs_are_native_and_mutation_free(self):
        expected_fragments = {
            "codex": (os.path.join(".codex-plugin", "plugin.json"),
                      "system2.codex.lock.json"),
            "pi": (os.path.join(".pi", "extensions", "system2.ts"),
                   "system2.pi.lock.json"),
        }
        for target in ("codex", "pi"):
            for fmt in ("json", "text"):
                with self.subTest(target=target, format=fmt):
                    with tempfile.TemporaryDirectory(prefix="cli-plan-") as project:
                        sentinel = os.path.join(project, "caller.txt")
                        with open(sentinel, "wb") as fh:
                            fh.write(b"caller-owned\n")
                        before = _tree_bytes(project)
                        code, stdout, stderr = _run_cli([
                            "compile", "--target", target,
                            "--base", _BASE,
                            "--project", project,
                            "--overlays", _TEST_OVERLAY,
                            "--dry-run", "--format", fmt,
                        ])

                        self.assertEqual(code, 0, stderr)
                        self.assertEqual(_tree_bytes(project), before)
                        self.assertNotIn("Composed CLAUDE.md", stdout)
                        for forbidden in _FORBIDDEN_CLAUDE_PATHS:
                            self.assertNotIn(forbidden, stdout)

                        if fmt == "json":
                            report = json.loads(stdout)["report"]
                            self.assertEqual(report["target"], target)
                            self.assertNotIn("composed_lines", report)
                            planned = report["files_to_write"]
                            for fragment in expected_fragments[target]:
                                self.assertTrue(
                                    any(path.endswith(fragment) for path in planned),
                                    (fragment, planned),
                                )
                        else:
                            self.assertIn(f"--- {target} target plan ---", stdout)

    def test_first_install_collisions_are_normal_errors_without_mutation(self):
        cases = (
            ("codex", os.path.join(".codex-plugin", "plugin.json")),
            ("pi", os.path.join(".pi", "SYSTEM.md")),
        )
        for target, relative in cases:
            for fmt in ("json", "text"):
                for dry_run in (False, True):
                    with self.subTest(
                        target=target, format=fmt, dry_run=dry_run
                    ):
                        with tempfile.TemporaryDirectory(
                            prefix="cli-collision-"
                        ) as project:
                            _write(os.path.join(project, relative))
                            before = _tree_bytes(project)
                            argv = [
                                "compile", "--target", target,
                                "--base", _BASE,
                                "--project", project,
                                "--overlays", _TEST_OVERLAY,
                                "--format", fmt,
                            ]
                            if dry_run:
                                argv.append("--dry-run")

                            code, stdout, stderr = _run_cli(argv)

                            _assert_cli_error(
                                self, code, stdout, stderr, fmt,
                                f"Cannot plan/write {target} artifacts:",
                            )
                            self.assertIn("pre-existing", stdout + stderr)
                            self.assertEqual(_tree_bytes(project), before)

    def test_existing_malformed_locks_are_normal_errors_without_mutation(self):
        for target, backend_cls in (("codex", CodexBackend), ("pi", PiBackend)):
            with tempfile.TemporaryDirectory(
                prefix="cli-malformed-lock-"
            ) as project:
                graph = _compose(project, (_TEST_OVERLAY,))
                backend = backend_cls()
                backend.emit(graph, project)
                _write(backend.lock_path(project), b"{not-json\n")

                for fmt in ("json", "text"):
                    for dry_run in (False, True):
                        with self.subTest(
                            target=target, format=fmt, dry_run=dry_run
                        ):
                            before = _tree_bytes(project)
                            argv = [
                                "compile", "--target", target,
                                "--base", _BASE,
                                "--project", project,
                                "--overlays", _TEST_OVERLAY,
                                "--format", fmt,
                            ]
                            if dry_run:
                                argv.append("--dry-run")

                            code, stdout, stderr = _run_cli(argv)

                            _assert_cli_error(
                                self, code, stdout, stderr, fmt,
                                f"Cannot plan/write {target} artifacts:",
                            )
                            self.assertEqual(_tree_bytes(project), before)

    def test_from_lock_ownership_failures_are_normal_and_mutation_free(self):
        cases = (
            ("codex", CodexBackend, os.path.join(".codex-plugin", "plugin.json")),
            ("pi", PiBackend, os.path.join(".pi", "SYSTEM.md")),
        )
        for target, backend_cls, owned_relative in cases:
            for damage in ("tampered", "malformed"):
                with self.subTest(target=target, damage=damage):
                    with tempfile.TemporaryDirectory(
                        prefix="cli-from-lock-ownership-"
                    ) as project:
                        graph = _compose(project, (_TEST_OVERLAY,))
                        backend = backend_cls()
                        backend.emit(graph, project)
                        if damage == "tampered":
                            _write(
                                os.path.join(project, owned_relative),
                                b"caller modification\n",
                            )
                        else:
                            lock_path = backend.lock_path(project)
                            with open(lock_path, encoding="utf-8") as fh:
                                lock = json.load(fh)
                            lock["ownership"]["artifacts"] = {"not": "a list"}
                            with open(lock_path, "w", encoding="utf-8") as fh:
                                json.dump(lock, fh, indent=2)
                                fh.write("\n")

                        for fmt in ("json", "text"):
                            for dry_run in (False, True):
                                with self.subTest(format=fmt, dry_run=dry_run):
                                    before = _tree_bytes(project)
                                    argv = [
                                        "from-lock", "--target", target,
                                        "--base", _BASE,
                                        "--project", project,
                                        "--format", fmt,
                                    ]
                                    if dry_run:
                                        argv.append("--dry-run")

                                    code, stdout, stderr = _run_cli(argv)

                                    _assert_cli_error(
                                        self, code, stdout, stderr, fmt,
                                        f"Cannot plan/write {target} artifacts:",
                                    )
                                    self.assertEqual(_tree_bytes(project), before)

    def test_emit_validation_errors_are_normal_and_oserror_is_unchanged(self):
        cases = (("codex", CodexBackend), ("pi", PiBackend))
        failures = (
            (ValueError("synthetic validation failure"),
             "Cannot plan/write {target} artifacts:"),
            (json.JSONDecodeError("synthetic malformed lock", "{", 0),
             "Cannot plan/write {target} artifacts:"),
            (OSError("synthetic write failure"),
             "I/O error writing outputs:"),
        )
        for target, backend_cls in cases:
            for failure, expected in failures:
                with self.subTest(target=target, failure=type(failure).__name__):
                    with tempfile.TemporaryDirectory(
                        prefix="cli-emit-error-"
                    ) as project:
                        before = _tree_bytes(project)
                        with mock.patch.object(
                            backend_cls, "emit", side_effect=failure
                        ):
                            code, stdout, stderr = _run_cli([
                                "compile", "--target", target,
                                "--base", _BASE,
                                "--project", project,
                                "--overlays", _TEST_OVERLAY,
                                "--format", "json",
                            ])

                        _assert_cli_error(
                            self, code, stdout, stderr, "json",
                            expected.format(target=target),
                        )
                        self.assertEqual(_tree_bytes(project), before)


if __name__ == "__main__":
    unittest.main()
