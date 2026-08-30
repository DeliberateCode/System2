"""Validate Pi's mixed native, adapted, and advisory degradation report."""

import copy
import dataclasses
import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends import _degradation
from system2_compiler.backends.pi import PiBackend, _build_degradation_report
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY

# The expected status partition (mirrors backends/capabilities/pi.json — asserted
# equal to the descriptor below, so this is the independent expectation).
_EXPECTED_NATIVE = frozenset({"enforce-lease", "block-dangerous", "protect-sensitive"})
_EXPECTED_ADAPTED = frozenset({"budget"})
_EXPECTED_ADVISORY = frozenset({"format", "typecheck"})

_FLAG_BY_STATUS = {
    "native": (True, False),
    "adapted": (False, True),
    "advisory": (False, False),
    "unsupported": (False, False),
}


def _emit_core_overlay(project_dir):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project_dir)
    if result.graph is None:
        raise AssertionError(
            f"compose refused the core+overlay cell: {result.errors!r}"
        )
    PiBackend().emit(result.graph, project_dir)
    return result.graph


def _descriptor():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "system2_compiler", "backends", "capabilities", "pi.json",
    )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class PiDegradationReportTest(unittest.TestCase):
    """The emitted system2.pi.lock.json is honest, mixed, and complete."""

    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-degr-")
        cls.graph = _emit_core_overlay(cls.project)
        with open(
            os.path.join(cls.project, "system2.pi.lock.json"), encoding="utf-8"
        ) as fh:
            cls.report = json.load(fh)
        cls.caps = cls.report["capabilities"]
        cls.descriptor = _descriptor()
        cls.ir_union = _degradation.ir_capability_union(
            cls.graph.capabilities.by_agent
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_status_equals_descriptor(self):
        # (a) per-capability status == pi.json status.
        descriptor_caps = self.descriptor["capabilities"]
        for cap, entry in self.caps.items():
            self.assertEqual(
                entry["status"], descriptor_caps[cap]["status"],
                f"report status for {cap!r} != descriptor status",
            )

    def test_mechanism_equals_descriptor(self):
        descriptor_caps = self.descriptor["capabilities"]
        for cap, entry in self.caps.items():
            self.assertEqual(
                entry["mechanism"], descriptor_caps[cap]["mechanism"],
                f"report mechanism for {cap!r} != descriptor mechanism",
            )

    def test_report_is_mixed_native_adapted_advisory(self):
        # (b) native AND adapted AND advisory all present (a MIXED report).
        statuses = {entry["status"] for entry in self.caps.values()}
        self.assertIn("native", statuses, "Pi report must contain native capabilities")
        self.assertIn("adapted", statuses, "Pi report must contain an adapted capability")
        self.assertIn("advisory", statuses, "Pi report must contain advisory capabilities")
        # Concretely the expected partition.
        native = {c for c, e in self.caps.items() if e["status"] == "native"}
        adapted = {c for c, e in self.caps.items() if e["status"] == "adapted"}
        advisory = {c for c, e in self.caps.items() if e["status"] == "advisory"}
        self.assertEqual(native, set(_EXPECTED_NATIVE & self.ir_union))
        self.assertEqual(adapted, set(_EXPECTED_ADAPTED & self.ir_union))
        self.assertEqual(advisory, set(_EXPECTED_ADVISORY & self.ir_union))

    def test_flags_follow_status_rule(self):
        # (c) enforced/gated derived from status, total over the enum.
        for cap, entry in self.caps.items():
            expected = _FLAG_BY_STATUS[entry["status"]]
            self.assertEqual(
                (entry["enforced"], entry["gated"]), expected,
                f"{cap!r} (status {entry['status']!r}) flags "
                f"{(entry['enforced'], entry['gated'])} != rule {expected}",
            )

    def test_completeness_no_silent_drop(self):
        # (d) every IR capability appears in the report.
        missing = sorted(self.ir_union - set(self.caps))
        self.assertEqual(
            [], missing,
            f"IR capabilities silently dropped from the report: {missing}",
        )
        # And the report carries nothing the IR does not.
        extra = sorted(set(self.caps) - self.ir_union)
        self.assertEqual([], extra, f"report carries non-IR capabilities: {extra}")

    def test_subagent_isolation_reported_adapted(self):
        # (e)  honest value.
        self.assertEqual(self.report["subagent_isolation"], "adapted")

    def test_fidelity_banner_present(self):
        # (f) loud banner present.
        banner = self.report.get("FIDELITY", "")
        self.assertTrue(banner, "the FIDELITY banner must be present and non-empty")
        for token in ("NATIVE", "ADAPTED", "ADVISORY"):
            self.assertIn(token, banner, f"banner must mention {token}")

    def test_unscoped_honesty_note_present_for_actual_ir(self):
        # The actual core+overlay IR carries >=1 role with an empty write_scope, so the unscoped-lease honesty note MUST be present (no silent vacuous native claim).
        any_empty = any(
            not (r.write_scope or "").strip() for r in self.graph.roles
        )
        self.assertTrue(
            any_empty,
            "precondition: the core+overlay IR is expected to carry an unscoped role",
        )
        self.assertIn(
            "UNSCOPED", self.report["FIDELITY"],
            "with an unscoped role present, the FIDELITY banner must carry the "
            "unscoped-lease honesty note",
        )

    def test_overlay_sources_is_last_additive_key(self):
        # overlay_sources records the inputs and remains the final lock key.
        project = tempfile.mkdtemp(prefix="pi-srcs-")
        try:
            PiBackend(overlay_sources=[_TEST_OVERLAY]).emit(self.graph, project)
            with open(
                os.path.join(project, "system2.pi.lock.json"),
                "r", encoding="utf-8",
            ) as fh:
                produced = json.load(fh)
        finally:
            shutil.rmtree(project, ignore_errors=True)

        keys = list(produced.keys())
        self.assertEqual(
            keys[-1], "overlay_sources",
            "overlay_sources[] must be the LAST lock key (additive, trailing)",
        )
        self.assertEqual(
            produced["overlay_sources"], [_TEST_OVERLAY],
            "overlay_sources[] must record the producing overlay source set",
        )

        stripped = dict(produced)
        del stripped["overlay_sources"]
        stripped_bytes = (json.dumps(stripped, indent=2) + "\n").encode("utf-8")
        prior_bytes = (
            json.dumps(_build_degradation_report(self.graph), indent=2) + "\n"
        ).encode("utf-8")
        self.assertEqual(
            stripped_bytes, prior_bytes,
            "stripping overlay_sources[] must reproduce the prior lock bytes "
            "byte-for-byte (additive-only, no existing key shifted)",
        )


class PiScopedBranchNegativeControlTest(unittest.TestCase):
    """When EVERY role is scoped, the unscoped honesty note is dropped (data-driven)."""

    def test_full_scope_ir_drops_unscoped_note(self):
        project = tempfile.mkdtemp(prefix="pi-scoped-")
        try:
            graph = _emit_core_overlay(project)
        finally:
            shutil.rmtree(project, ignore_errors=True)

        # Synthesize a FULL-scope IR: give every role a non-empty write_scope.
        scoped_roles = [
            dataclasses.replace(r, write_scope=(r.write_scope or "^src/.*$"))
            for r in graph.roles
        ]
        scoped_graph = dataclasses.replace(graph, roles=scoped_roles)

        report = _build_degradation_report(scoped_graph)
        self.assertNotIn(
            "UNSCOPED", report["FIDELITY"],
            "with every role scoped the unscoped-lease note must be DROPPED — the "
            "honesty branch must be data-driven, not hard-coded",
        )
        # And the original (unscoped) report DOES carry it — proving the branch flips.
        original = _build_degradation_report(graph)
        self.assertIn("UNSCOPED", original["FIDELITY"])


class PiDegradationNegativeControlsTest(unittest.TestCase):
    """The helper has teeth: a dropped descriptor entry raises; flags track status."""

    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-degr-neg-")
        cls.graph = _emit_core_overlay(cls.project)
        cls.descriptor = _descriptor()
        cls.ir_union = _degradation.ir_capability_union(
            cls.graph.capabilities.by_agent
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_dropping_a_needed_descriptor_entry_raises(self):
        # Remove an IR-present capability from the descriptor -> the helper must
        # raise (no silent drop), not quietly omit it.
        broken = copy.deepcopy(self.descriptor)
        victim = "block-dangerous"
        self.assertIn(victim, self.ir_union, "precondition: victim is IR-present")
        del broken["capabilities"][victim]
        with self.assertRaises(ValueError):
            _degradation.build_capability_records(
                broken,
                self.ir_union,
                fields=("status", "mechanism", "enforced", "gated"),
                allow_native=True,
            )

    def test_flipping_a_status_flips_the_derived_flags(self):
        # Flip budget native -> the derived flags become enforced:true,gated:false.
        flipped = copy.deepcopy(self.descriptor)
        flipped["capabilities"]["budget"]["status"] = "native"
        records = _degradation.build_capability_records(
            flipped,
            self.ir_union,
            fields=("status", "mechanism", "enforced", "gated"),
            allow_native=True,
        )
        self.assertEqual(
            (records["budget"]["enforced"], records["budget"]["gated"]),
            (True, False),
            "flipping a status must flip the derived enforced/gated flags",
        )
        # Sanity: with the real (adapted) status the flags are (False, True).
        real = _degradation.build_capability_records(
            self.descriptor,
            self.ir_union,
            fields=("status", "mechanism", "enforced", "gated"),
            allow_native=True,
        )
        self.assertEqual(
            (real["budget"]["enforced"], real["budget"]["gated"]), (False, True),
        )


if __name__ == "__main__":
    unittest.main()
