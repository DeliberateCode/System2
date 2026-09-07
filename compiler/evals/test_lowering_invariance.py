"""Lowering-invariance /  sign-off gate."""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.claude_code import ClaudeCodeBackend
from evals import capture, matrix, oracle, run_goldens

_GOLDENS_DIR = run_goldens.DEFAULT_GOLDENS_DIR
_WORKSPACE_ROOT = capture._WORKSPACE_ROOT


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


class CompilerDriverGreenGate(unittest.TestCase):
    """the compiler driver is empty-diff across the matrix."""

    def test_matrix_is_empty_diff_under_the_compiler_driver(self):
        failures = run_goldens.run_goldens(
            goldens_dir=_GOLDENS_DIR, driver="compiler"
        )
        self.assertEqual(
            [], failures,
            " gate: the compose->emit compiler driver must be empty-diff across "
            "the matrix (CLAUDE.md/aux/warnings byte-identical; lock delta additive-"
            f"only). Failures:\n  " + "\n  ".join(failures),
        )

    def test_oracle_cross_check_driver_also_green(self):
        # Backout cross-check: the frozen oracle driver remains green (the lock strip
        # is a no-op there since the oracle never emits a degradation_report).
        failures = run_goldens.run_goldens(
            goldens_dir=_GOLDENS_DIR, driver="oracle"
        )
        self.assertEqual(
            [], failures,
            "the oracle cross-check driver must remain green:\n  "
            + "\n  ".join(failures),
        )


class AdditiveLockDeltaGate(unittest.TestCase):
    """the ONLY lock delta vs the baseline is the additive degradation_report."""

    def _composed_cells(self):
        for cell in matrix.all_cells():
            if cell.name == "core" or cell.refusal:
                continue
            yield cell

    def _emit_lock_bytes(self, cell) -> bytes:
        """Compose+emit *cell* in-process (seeding the prior golden lock so
        ``composed_at`` is reused) and return the produced lock bytes."""
        cell_dir = cell.snapshot_dir(_GOLDENS_DIR)
        project = tempfile.mkdtemp(prefix=f"dod2-{cell.name}-")
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        capture._seed_prior_lock(project, cell_dir)

        import importlib

        home = None
        saved_home = os.environ.get("HOME")
        profile = cell.profile
        try:
            if profile is not None:
                home = capture._materialize_profile_home(cell)
                self.addCleanup(shutil.rmtree, home, ignore_errors=True)
                os.environ["HOME"] = home
                importlib.reload(ir.profiles)
            result = ir.compose(
                oracle.PLUGIN_ROOT, list(cell.overlays), project, profile=profile
            )
            self.assertIsNotNone(
                result.graph, f"cell {cell.name!r} unexpectedly refused"
            )
            ClaudeCodeBackend().emit(result.graph, project)
        finally:
            if profile is not None:
                if saved_home is not None:
                    os.environ["HOME"] = saved_home
                else:
                    os.environ.pop("HOME", None)
                importlib.reload(ir.profiles)

        lock_path = os.path.join(project, "spec", "overlay-manifest.lock")
        return _read_bytes(lock_path)

    def test_stripping_report_reproduces_baseline_lock_byte_for_byte(self):
        seen = 0
        for cell in self._composed_cells():
            cell_dir = cell.snapshot_dir(_GOLDENS_DIR)
            baseline_lock_path = os.path.join(
                cell_dir, "spec", "overlay-manifest.lock"
            )
            if not os.path.isfile(baseline_lock_path):
                continue
            seen += 1
            baseline = _read_bytes(baseline_lock_path)
            produced_bytes = self._emit_lock_bytes(cell)
            produced = json.loads(produced_bytes.decode("utf-8"))
            report = produced.get("degradation_report")
            self.assertIsInstance(
                report, dict,
                f"[{cell.name}] the produced lock must carry an additive "
                "degradation_report",
            )
            failures = run_goldens._compare_lock(
                f"[{cell.name}] lock", baseline, produced_bytes,
                require_report=True, cell=cell,
            )
            self.assertEqual(
                failures, [],
                f"[{cell.name}] additive lock comparison failed: {failures}",
            )

            caps = report.get("capabilities", {})
            self.assertTrue(
                caps, f"[{cell.name}] degradation_report.capabilities must be non-empty"
            )
            for cap, entry in caps.items():
                self.assertEqual(
                    entry.get("status"), "native",
                    f"[{cell.name}] capability {cap!r} must be native for claude-code "
                    f"(got {entry.get('status')!r})",
                )
        self.assertGreater(
            seen, 0, "expected >=1 composed cell with a baseline lock to verify"
        )

    def test_no_enforced_capability_dropped_from_report(self):
        for cell in self._composed_cells():
            cell_dir = cell.snapshot_dir(_GOLDENS_DIR)
            if not os.path.isfile(
                os.path.join(cell_dir, "spec", "overlay-manifest.lock")
            ):
                continue
            project = tempfile.mkdtemp(prefix=f"dod2-drop-{cell.name}-")
            self.addCleanup(shutil.rmtree, project, ignore_errors=True)
            capture._seed_prior_lock(project, cell_dir)

            import importlib

            home = None
            saved_home = os.environ.get("HOME")
            profile = cell.profile
            try:
                if profile is not None:
                    home = capture._materialize_profile_home(cell)
                    self.addCleanup(shutil.rmtree, home, ignore_errors=True)
                    os.environ["HOME"] = home
                    importlib.reload(ir.profiles)
                result = ir.compose(
                    oracle.PLUGIN_ROOT, list(cell.overlays), project, profile=profile
                )
                self.assertIsNotNone(result.graph)
                ClaudeCodeBackend().emit(result.graph, project)
                ir_caps = set()
                for cl in result.graph.capabilities.by_agent.values():
                    ir_caps.update(cl)
            finally:
                if profile is not None:
                    if saved_home is not None:
                        os.environ["HOME"] = saved_home
                    else:
                        os.environ.pop("HOME", None)
                    importlib.reload(ir.profiles)

            lock = json.loads(
                _read_bytes(
                    os.path.join(project, "spec", "overlay-manifest.lock")
                ).decode("utf-8")
            )
            report_caps = set(lock["degradation_report"]["capabilities"].keys())
            self.assertEqual(
                report_caps, ir_caps,
                f"[{cell.name}] every IR capability must appear in the report; no "
                f"enforced capability may be dropped. "
                f"missing={ir_caps - report_caps}",
            )


class StaticSurfaceInventoryInvariantGate(unittest.TestCase):
    """The static plugin surface remains unchanged byte-for-byte."""

    @classmethod
    def setUpClass(cls):
        core_dir = matrix.get_cell("core").snapshot_dir(_GOLDENS_DIR)
        cls.ref_path = os.path.join(core_dir, "structural_goldens.json")
        with open(cls.ref_path, "r", encoding="utf-8") as fh:
            cls.record = json.load(fh)
        cls.entries = cls.record.get("structural_goldens", [])

    def test_reference_record_exists_and_is_nonempty(self):
        self.assertTrue(
            self.entries,
            "the core cell must reference >=1 read-only structural golden",
        )

    def test_every_structural_golden_matches_its_pinned_sha256(self):
        for entry in self.entries:
            rel = entry["path"]
            abs_path = os.path.join(_WORKSPACE_ROOT, rel)
            self.assertTrue(
                os.path.isfile(abs_path),
                f"referenced structural golden missing: {rel}",
            )
            actual = capture._sha256_file(abs_path)
            self.assertEqual(
                actual, entry["sha256"],
                f"static plugin surface inventory drift: {rel} sha256 changed",
            )

    def test_inventory_invariant_goldens_are_referenced(self):
        # Lock every static inventory and binding surface.
        referenced = {os.path.basename(e["path"]) for e in self.entries}
        for required in (
            "agent_inventory.json",
            "delegation_map.json",
            "hook_inventory.json",
            "allowlist_inventory.json",
            "agent_allowlist_bindings.json",
        ):
            self.assertIn(
                required, referenced,
                f"the static-surface invariant must lock {required}",
            )

    def test_thirteen_agent_inventory_unchanged(self):
        # Concretely assert the 13-agent count from the locked, unchanged inventory.
        inv_entry = next(
            e for e in self.entries
            if os.path.basename(e["path"]) == "agent_inventory.json"
        )
        inv_path = os.path.join(_WORKSPACE_ROOT, inv_entry["path"])
        with open(inv_path, "r", encoding="utf-8") as fh:
            inventory = json.load(fh)
        # The agent inventory is a list of agents (or a dict carrying one); count 13.
        if isinstance(inventory, dict):
            agents = (
                inventory.get("agents")
                or inventory.get("pipeline_agents")
                or inventory.get("expected")
                or []
            )
            count = inventory.get("expected_count", len(agents))
        else:
            count = len(inventory)
        self.assertEqual(
            count, 13,
            f"the pipeline-agent inventory must remain 13 agents (got {count})",
        )

    def test_base_template_unchanged(self):
        # The base CLAUDE.md template the composer starts from is pinned too.
        expected = self.record.get("base_template_sha256")
        self.assertTrue(expected, "structural_goldens.json must pin base_template_sha256")
        current = capture._sha256_bytes(
            capture._read_base_template().encode("utf-8")
        )
        self.assertEqual(
            current, expected,
            "base CLAUDE.md template drift: the byte-source for the "
            "composed output changed",
        )


if __name__ == "__main__":
    unittest.main()
