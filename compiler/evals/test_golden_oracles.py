"""Independent mutation controls for the Claude golden and capture oracles."""

import json
import os
import shutil
import tempfile
import unittest

from evals import capture, matrix, run_goldens


class GoldenInventoryControlTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="golden-controls-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        shutil.copytree(run_goldens.DEFAULT_GOLDENS_DIR, self.temp, dirs_exist_ok=True)

    def test_missing_snapshot_fails_declared_inventory(self):
        cell = matrix.get_cell("core+overlay")
        expected = set(cell.expected_files)
        victim = sorted(expected)[0]
        os.remove(os.path.join(cell.snapshot_dir(self.temp), victim))
        with self.assertRaisesRegex(AssertionError, "missing=.*" + os.path.basename(victim)):
            matrix.assert_complete(self.temp)

    def test_extra_snapshot_fails_declared_inventory(self):
        cell = matrix.get_cell("core+overlay")
        extra = os.path.join(cell.snapshot_dir(self.temp), "unexpected.txt")
        with open(extra, "wb") as fh:
            fh.write(b"unexpected\n")
        with self.assertRaisesRegex(AssertionError, "extra=.*unexpected.txt"):
            matrix.assert_complete(self.temp)

    def test_missing_and_extra_produced_files_use_real_inventory_comparator(self):
        cell = matrix.get_cell("core+overlay")
        expected = set(cell.expected_files)
        missing = sorted(expected)[0]
        failures = run_goldens._inventory_failures(
            cell, (expected - {missing}) | {"unexpected.txt"}
        )
        self.assertIn(f"[{cell.name}] missing produced artifact: {missing}", failures)
        self.assertIn(
            f"[{cell.name}] unexpected produced artifact: unexpected.txt", failures
        )


class LockByteControlTest(unittest.TestCase):
    def setUp(self):
        self.cell = matrix.get_cell("core+overlay")
        self.source = matrix.resolved_overlay_sources(self.cell)[0]
        self.produced = (
            json.dumps({
                "composed_at": "2026-01-01T00:00:00Z",
                "overlays": [{"source_path": self.source}],
                "warnings": [],
                "degradation_report": {
                    "backend": "claude-code",
                    "capabilities": {
                        "test": {"status": "native", "mechanism": "control"}
                    },
                },
            }, indent=2) + "\n"
        ).encode()
        stripped = run_goldens._strip_top_level_field(
            self.produced, "degradation_report"
        )
        self.expected = run_goldens._normalize_produced_lock_paths(
            stripped, self.cell
        )

    def _compare(self, expected=None, actual=None):
        return run_goldens._compare_lock(
            "control", self.expected if expected is None else expected,
            self.produced if actual is None else actual,
            require_report=True, cell=self.cell,
        )

    def test_unmodified_lock_passes(self):
        self.assertEqual(self._compare(), [])

    def test_compact_lock_mutation_fails_byte_comparison(self):
        compact = json.dumps(json.loads(self.expected)).encode()
        self.assertTrue(any("byte mismatch" in failure for failure in self._compare(expected=compact)))

    def test_malformed_lock_mutation_fails(self):
        self.assertTrue(any("not parseable JSON" in failure for failure in self._compare(actual=b"{")))

    def test_wrong_suffix_matching_fixture_root_fails(self):
        wrong = self.produced.replace(
            os.fsencode(self.source), b"/wrong/evals/fixtures/test-overlay"
        )
        self.assertTrue(any("exact cell inputs" in failure for failure in self._compare(actual=wrong)))


class CaptureNormalizationControlTest(unittest.TestCase):
    def test_nested_agents_are_captured_with_closed_world_relative_paths(self):
        cell = matrix.get_cell("core+overlay")
        with tempfile.TemporaryDirectory(prefix="nested-agent-source-") as project:
            with tempfile.TemporaryDirectory(prefix="nested-agent-capture-") as cell_dir:
                agents = os.path.join(project, ".claude", "agents")
                files = {
                    os.path.join(agents, "scout.md"): "root scout\n",
                    os.path.join(agents, "team", "scout.md"): "nested scout\n",
                    os.path.join(agents, "team", "notes.txt"): "not an agent\n",
                }
                for path, content in files.items():
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(content)

                capture._copy_artifacts(project, cell_dir, cell)
                captured = {
                    os.path.relpath(os.path.join(root, name), cell_dir)
                    for root, _, names in os.walk(cell_dir)
                    for name in names
                }
                self.assertEqual(
                    captured,
                    {
                        os.path.join(".claude", "agents", "scout.md"),
                        os.path.join(".claude", "agents", "team", "scout.md"),
                    },
                )
                nested = os.path.join(
                    cell_dir, ".claude", "agents", "team", "scout.md"
                )
                with open(nested, encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), "nested scout\n")

    def test_capture_emits_reproducible_repo_root_token(self):
        cell = matrix.get_cell("core+overlay")
        lock = {
            "overlays": [
                {"source_path": matrix.resolved_overlay_sources(cell)[0]}
            ]
        }
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            json.dump(lock, fh, indent=2)
            fh.write("\n")
            path = fh.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        captured = capture._captured_lock_bytes(path, cell)
        self.assertIn(b"<REPO_ROOT>/evals/fixtures/test-overlay", captured)
        self.assertNotIn(os.fsencode(matrix.resolved_overlay_sources(cell)[0]), captured)


class ComparisonPolicyControlTest(unittest.TestCase):
    def test_semantic_equivalent_is_rejected_until_implemented(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            json.dump({"default": {"mode": "semantic-equivalent", "justification": "control"}}, fh)
            path = fh.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        with self.assertRaises(run_goldens.PolicyError):
            run_goldens.load_policy(path)


if __name__ == "__main__":
    unittest.main()
