"""Focused controls for graph-native provenance and target-native CLI plans."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from system2_compiler import cli, ir
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


class GraphProvenanceTest(unittest.TestCase):
    def test_graph_json_carries_ordered_deterministic_overlay_sources(self):
        with tempfile.TemporaryDirectory(prefix="graph-provenance-") as project:
            sources = (_TEST_OVERLAY, _ANCHORFILE)
            graph = _compose(project, sources)

            self.assertIsInstance(graph.overlay_sources, tuple)
            self.assertEqual(graph.overlay_sources, sources)
            first = graph.to_json(indent=2)
            self.assertEqual(first, graph.to_json(indent=2))
            self.assertEqual(json.loads(first)["overlay_sources"], list(sources))


class BackendProvenanceTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
