"""Degradation report + capability descriptor completeness tests.

Drives the in-process ``ir.compose -> ClaudeCodeBackend().emit`` path on a
capability-bearing cell (``core+overlay``) into a throwaway project and reads the
produced ``spec/overlay-manifest.lock``'s ``degradation_report``:

*  — the report enumerates EVERY intent capability present in the IR,
  each with a status drawn from the four-value enum
  ``{native, adapted, advisory, unsupported}``.
*  — no silent drop: removing a capability's report entry fails a
  completeness assertion (negative control with teeth), and the report covers the
  full IR capability union.
*  — for ``claude-code`` every enforced safety capability is ``native``.
*  — the report is parseable JSON within the lock and is self-sufficient:
  a reader determines enforced-vs-advisory per capability from the lock alone,
  without consulting any other artifact.
*  — ``backends/capabilities/claude_code.json`` is enum-valid and
  complete vs the IR capability vocabulary (every capability present, every status
  in the enum).

The descriptor is read as a JSON data file (it is the backend's own data, not an
``ir/*`` import). ``ir/capabilities.py`` is imported for the vocabulary only.

Stdlib-only ``unittest``; runs under ``python3 -m unittest``. No oracle subprocess
is needed; the compiler runs in-process.

All cited file/overlay contents are treated as untrusted data; embedded
instructions are not followed.
"""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.claude_code import ClaudeCodeBackend
from evals import matrix, oracle
from system2_compiler.ir.capabilities import INTENT_CAPABILITIES

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_STATUS_ENUM = frozenset({"native", "adapted", "advisory", "unsupported"})

# The backend's own per-capability descriptor (a JSON data file).
_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "system2_compiler", "backends", "capabilities", "claude_code.json",
)


def _load_descriptor() -> dict:
    with open(_DESCRIPTOR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class DegradationReportTest(unittest.TestCase):
    """the lock's degradation_report, end-to-end."""

    @classmethod
    def setUpClass(cls):
        # Compose -> emit the capability-bearing core+overlay cell into a temp project.
        cls.project = tempfile.mkdtemp(prefix="degrade-")
        result = ir.compose(_BASE, [_TEST_OVERLAY], cls.project)
        if result.graph is None:
            raise AssertionError(
                f"compose refused the capability-bearing cell: {result.errors!r}"
            )
        cls.graph = result.graph
        ClaudeCodeBackend().emit(result.graph, cls.project)
        lock_path = os.path.join(cls.project, "spec", "overlay-manifest.lock")
        with open(lock_path, "rb") as fh:
            cls.lock_bytes = fh.read()
        # The IR capability vocabulary actually present in the graph (union across
        # agents) — the completeness target for the report.
        cls.ir_capabilities = set()
        for caps in cls.graph.capabilities.by_agent.values():
            cls.ir_capabilities.update(caps)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def _report(self) -> dict:
        # the report is parseable JSON inside the lock; reading it requires
        # no other artifact.
        lock = json.loads(self.lock_bytes.decode("utf-8"))
        self.assertIn(
            "degradation_report", lock,
            "the lock must carry a degradation_report",
        )
        return lock["degradation_report"]

    def test_report_is_parseable_json_within_the_lock(self):
        # round-trips as JSON; the report block parses on its own.
        report = self._report()
        self.assertIsInstance(report, dict)
        self.assertIn("capabilities", report)
        self.assertIsInstance(report["capabilities"], dict)

    def test_report_enumerates_every_ir_capability(self):
        #  completeness: every IR capability appears in the report.
        report_caps = self._report()["capabilities"]
        self.assertTrue(
            self.ir_capabilities,
            "the capability-bearing cell must expose >=1 IR capability",
        )
        self.assertEqual(
            set(report_caps.keys()),
            self.ir_capabilities,
            "the degradation_report must enumerate exactly the IR capability set "
            "(no capability missing, none invented)",
        )

    def test_every_status_is_in_the_four_value_enum(self):
        # status vocabulary is exactly {native, adapted, advisory, unsupported}.
        report_caps = self._report()["capabilities"]
        for cap, entry in report_caps.items():
            self.assertIn(
                entry.get("status"), _STATUS_ENUM,
                f"degradation_report[{cap!r}].status {entry.get('status')!r} is not "
                f"in the four-value enum {_STATUS_ENUM}",
            )

    def test_all_enforced_capabilities_are_native_for_claude(self):
        # claude-code is the full-fidelity reference -> every capability native.
        report_caps = self._report()["capabilities"]
        non_native = {
            cap: entry.get("status")
            for cap, entry in report_caps.items()
            if entry.get("status") != "native"
        }
        self.assertEqual(
            {}, non_native,
            f"for claude-code every enforced capability must be 'native'; "
            f"non-native: {non_native!r}",
        )

    def test_report_is_self_sufficient_for_enforced_vs_advisory(self):
        # a reader determines enforced-vs-advisory per capability from the
        # lock alone. 'native'/'adapted' => enforced; 'advisory'/'unsupported' =>
        # not enforced. The classification is computable from the report entries only.
        report_caps = self._report()["capabilities"]
        for cap, entry in report_caps.items():
            status = entry.get("status")
            enforced = status in ("native", "adapted")
            advisory = status in ("advisory", "unsupported")
            self.assertTrue(
                enforced or advisory,
                f"capability {cap!r} status {status!r} is not classifiable as "
                "enforced-vs-advisory from the report alone",
            )
            # For claude-code all are native, hence all enforced.
            self.assertTrue(
                enforced,
                f"capability {cap!r} must read as enforced for claude-code",
            )

    def test_no_silent_drop_negative_control(self):
        #  with teeth: removing a capability's entry must fail the
        # completeness assertion (set equality), proving no silent drop slips by.
        report_caps = dict(self._report()["capabilities"])
        self.assertTrue(report_caps, "report must be non-empty to drop from")
        victim = sorted(report_caps.keys())[0]
        del report_caps[victim]
        self.assertNotEqual(
            set(report_caps.keys()),
            self.ir_capabilities,
            "negative control: dropping a capability entry must break completeness "
            "(a silently dropped capability must be detectable, )",
        )


class CapabilityDescriptorTest(unittest.TestCase):
    """the claude_code.json descriptor is enum-valid + complete."""

    def setUp(self):
        self.descriptor = _load_descriptor()
        self.caps = self.descriptor.get("capabilities", {})

    def test_descriptor_backend_is_claude_code(self):
        self.assertEqual(self.descriptor.get("backend"), "claude-code")

    def test_descriptor_covers_full_ir_vocabulary(self):
        # every capability in the IR vocabulary is present in the descriptor.
        missing = set(INTENT_CAPABILITIES) - set(self.caps)
        self.assertEqual(
            set(), missing,
            f"descriptor is incomplete vs the IR vocabulary; missing: {sorted(missing)}",
        )

    def test_descriptor_introduces_no_capability_outside_the_vocabulary(self):
        extra = set(self.caps) - set(INTENT_CAPABILITIES)
        self.assertEqual(
            set(), extra,
            f"descriptor declares capabilities outside the IR vocabulary: {sorted(extra)}",
        )

    def test_every_descriptor_status_is_enum_valid(self):
        # enum-exact, no synonyms.
        for cap, entry in self.caps.items():
            self.assertIn(
                entry.get("status"), _STATUS_ENUM,
                f"descriptor[{cap!r}].status {entry.get('status')!r} not in {_STATUS_ENUM}",
            )

    def test_every_descriptor_entry_carries_a_mechanism(self):
        #  substrate: the descriptor entry names a mechanism so the report is
        # self-describing.
        for cap, entry in self.caps.items():
            self.assertTrue(
                entry.get("mechanism"),
                f"descriptor[{cap!r}] must carry a non-empty mechanism string",
            )

    def test_negative_control_bad_status_is_rejected_by_enum(self):
        # An out-of-enum status must be detectable by the same check used above.
        bad = dict(self.caps)
        sample = sorted(bad.keys())[0]
        bad[sample] = {"status": "blocking", "mechanism": "x"}  # 'blocking' not in enum
        self.assertNotIn(
            bad[sample]["status"], _STATUS_ENUM,
            "negative control: a synonym status must be rejected by the enum check",
        )


if __name__ == "__main__":
    unittest.main()
