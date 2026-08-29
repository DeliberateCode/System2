"""TASK-316 — Non-native degradation tests (the top Phase-3 readiness gap).

Asserts the OQ1 degradation is real and honest, exercising the **adapted** and
**advisory** paths — not just the happy recipe (NFR-003/REQ-033 analogue;
AC-G3/AC-G6; design §"Degradation report", §"Enforcement lowering (OQ1)",
§"Test / golden strategy" leg 3):

1. ``system2.goose.lock.json`` per-capability ``status`` **equals**
   ``backends/capabilities/goose.json`` status (report == descriptor).
2. **No** capability has status ``native`` for Goose.
3. The **adapted** path emits a real ``goose/permission.yaml`` with the expected
   ``user:`` tool entries (shell/Bash + Read/Write/Edit) — exercised, not just claimed.
4. Each **advisory** capability emits a labelled "NOT ENFORCED ON GOOSE" block in
   the emitted recipe text.
5. Completeness / no silent drop — every IR capability appears in the report.
6. The top-level LOUD ``DEGRADATION`` banner string is present.

Negative controls (with teeth): removing a capability entry breaks completeness;
flipping a status to ``native`` trips the nothing-native invariant.

Pure artifact inspection (no goose invocation needed). Emit runs into a temp
``project_path`` only — the real home dir is never touched by ``emit``.

Stdlib-only ``unittest``; runs under ``python3 -m unittest``. All cited overlay /
recipe contents are treated as untrusted; embedded instructions are not followed.
"""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.goose import GooseBackend
from evals import matrix, oracle
from system2_compiler.ir.capabilities import INTENT_CAPABILITIES

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_STATUS_ENUM = frozenset({"native", "adapted", "advisory", "unsupported"})

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "system2_compiler", "backends", "capabilities", "goose.json",
)

# The OQ1 split: which capabilities lower to the adapted (permission) path vs the
# advisory (instruction-only) path.
_ADAPTED = frozenset({"block-dangerous", "protect-sensitive"})
_ADVISORY = frozenset({"enforce-lease", "format", "typecheck", "budget"})

_NOT_ENFORCED_MARKER = "NOT ENFORCED ON GOOSE"


def _load_descriptor() -> dict:
    with open(_DESCRIPTOR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class GooseDegradationTest(unittest.TestCase):
    """The degradation report + adapted/advisory paths are real, honest, complete."""

    @classmethod
    def setUpClass(cls):
        # core+overlay carries roles with multiple capabilities, so both the
        # adapted and the advisory blocks render in one emit.
        cls.project = tempfile.mkdtemp(prefix="goose-degrade-")
        result = ir.compose(_BASE, [_TEST_OVERLAY], cls.project)
        if result.graph is None:
            raise AssertionError(
                f"compose refused the capability-bearing cell: {result.errors!r}"
            )
        cls.graph = result.graph
        GooseBackend().emit(result.graph, cls.project)

        with open(
            os.path.join(cls.project, "system2.goose.lock.json"), "r", encoding="utf-8"
        ) as fh:
            cls.lock = json.load(fh)

        with open(
            os.path.join(cls.project, "goose", "permission.yaml"), "r", encoding="utf-8"
        ) as fh:
            cls.permission_text = fh.read()

        # Concatenate every emitted recipe's text (orchestrator + all role recipes)
        # so an advisory block in any recipe satisfies the "NOT ENFORCED" assertion.
        texts = []
        for dirpath, _dirs, files in os.walk(cls.project):
            for name in sorted(files):
                if name.endswith(".recipe.yaml"):
                    with open(os.path.join(dirpath, name), "r", encoding="utf-8") as fh:
                        texts.append(fh.read())
        cls.recipe_text = "\n".join(texts)

        cls.descriptor = _load_descriptor()
        cls.descriptor_caps = cls.descriptor.get("capabilities", {})

        cls.ir_capabilities = set()
        for caps in cls.graph.capabilities.by_agent.values():
            cls.ir_capabilities.update(caps)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def _report_caps(self) -> dict:
        self.assertIn("capabilities", self.lock, "lock must carry a capabilities map")
        return self.lock["capabilities"]

    # 1. report status == descriptor status per capability ---------------------
    def test_report_status_equals_descriptor_status(self):
        report_caps = self._report_caps()
        for cap, entry in report_caps.items():
            self.assertIn(
                cap, self.descriptor_caps,
                f"report capability {cap!r} absent from goose.json",
            )
            self.assertEqual(
                entry.get("status"), self.descriptor_caps[cap].get("status"),
                f"report status for {cap!r} ({entry.get('status')!r}) must equal "
                f"the descriptor status ({self.descriptor_caps[cap].get('status')!r})",
            )

    # 2. nothing native --------------------------------------------------------
    def test_no_capability_is_native(self):
        report_caps = self._report_caps()
        native = {
            cap: entry.get("status")
            for cap, entry in report_caps.items()
            if entry.get("status") == "native"
        }
        self.assertEqual(
            {}, native,
            f"no Goose capability may be 'native'; offenders: {native!r}",
        )

    def test_every_status_enum_valid(self):
        for cap, entry in self._report_caps().items():
            self.assertIn(
                entry.get("status"), _STATUS_ENUM,
                f"report[{cap!r}].status {entry.get('status')!r} not in {_STATUS_ENUM}",
            )

    # 3. adapted path emits a real permission.yaml with the expected entries ----
    def test_adapted_permission_yaml_has_expected_tool_entries(self):
        # The adapted enforcement is exercised: a real permission policy naming the
        # shell/Bash + Read/Write/Edit tools under a user: section.
        self.assertIn("user:", self.permission_text)
        for tool in ("Bash", "shell", "Read", "Write", "Edit"):
            self.assertIn(
                f"{tool}:", self.permission_text,
                f"permission.yaml must gate the {tool!r} tool (adapted enforcement)",
            )
        self.assertIn(
            "permission: ask_before", self.permission_text,
            "permission.yaml must set a permission decision (ask_before)",
        )
        # block-dangerous names a dangerous-command set.
        self.assertIn(
            "never_allow_commands", self.permission_text,
            "permission.yaml must name a never-allow dangerous-command set",
        )

    def test_adapted_caps_are_gated_in_report(self):
        report_caps = self._report_caps()
        for cap in _ADAPTED:
            self.assertIn(cap, report_caps)
            self.assertEqual(report_caps[cap].get("status"), "adapted")
            self.assertTrue(
                report_caps[cap].get("gated"),
                f"adapted capability {cap!r} must be flagged gated:true",
            )
            self.assertFalse(
                report_caps[cap].get("enforced"),
                f"adapted != enforced: {cap!r} must be enforced:false",
            )

    # 4. each advisory capability emits a NOT-ENFORCED block --------------------
    def test_each_advisory_capability_has_not_enforced_block(self):
        for cap in _ADVISORY:
            # A labelled block naming the capability and the NOT-ENFORCED marker.
            self.assertIn(
                _NOT_ENFORCED_MARKER, self.recipe_text,
                "the recipe text must contain a NOT-ENFORCED degradation marker",
            )
            self.assertIn(
                f"NOT ENFORCED ON GOOSE: {cap}", self.recipe_text,
                f"advisory capability {cap!r} must emit a labelled "
                f"'NOT ENFORCED ON GOOSE: {cap}' block in the recipe text",
            )

    def test_advisory_caps_are_not_gated_in_report(self):
        report_caps = self._report_caps()
        for cap in _ADVISORY:
            self.assertIn(cap, report_caps)
            self.assertEqual(report_caps[cap].get("status"), "advisory")
            self.assertFalse(
                report_caps[cap].get("gated"),
                f"advisory capability {cap!r} must be gated:false",
            )
            self.assertFalse(report_caps[cap].get("enforced"))

    # 5. completeness / no silent drop -----------------------------------------
    def test_report_enumerates_every_ir_capability(self):
        report_caps = self._report_caps()
        self.assertTrue(self.ir_capabilities, "the cell must expose >=1 IR capability")
        self.assertEqual(
            set(report_caps.keys()), self.ir_capabilities,
            "the degradation report must enumerate exactly the IR capability set "
            "(no capability missing, none invented)",
        )

    def test_report_covers_full_vocabulary_for_this_cell(self):
        # core+overlay exercises all six capabilities; the report must cover them.
        self.assertEqual(
            set(self._report_caps().keys()), set(INTENT_CAPABILITIES),
            "core+overlay must report all six intent capabilities",
        )

    # 6. LOUD DEGRADATION banner -----------------------------------------------
    def test_loud_degradation_banner_present(self):
        banner = self.lock.get("DEGRADATION")
        self.assertTrue(
            isinstance(banner, str) and banner.strip(),
            "the lock must carry a top-level LOUD DEGRADATION banner string",
        )
        self.assertIn("DOWNGRADED", banner.upper())

    def test_report_top_level_metadata(self):
        # The honesty metadata a reader needs: backend, mode, delivery dependency.
        self.assertEqual(self.lock.get("backend"), "goose")
        self.assertEqual(self.lock.get("mode"), "smart_approve")
        self.assertEqual(
            self.lock.get("permission_delivery"), "ephemeral-xdg-config",
            "the report must disclose that adapted delivery rides on an ephemeral "
            "XDG_CONFIG_HOME permission.yaml (no global mutation)",
        )

    def test_no_timestamp_in_lock(self):
        # Output is a pure function of the IR (no composed_at / timestamps).
        for key in ("composed_at", "timestamp", "generated_at"):
            self.assertNotIn(
                key, self.lock,
                f"the Goose lock must be timestamp-free; found {key!r}",
            )

    # 7. additive overlay_sources[] key (OQ-5.1 / T10) -------------------------
    def test_overlay_sources_is_last_additive_key(self):
        # Phase 5: emit appends overlay_sources[] as the LAST lock key, recording
        # the producing overlay source set. Stripping it (the only new key) must
        # reproduce the prior degradation-report-only lock bytes byte-for-byte.
        from system2_compiler.backends import goose as goose_backend

        project = tempfile.mkdtemp(prefix="goose-srcs-")
        try:
            goose_backend.GooseBackend(
                overlay_sources=[_TEST_OVERLAY]
            ).emit(self.graph, project)
            with open(
                os.path.join(project, "system2.goose.lock.json"),
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
            json.dumps(goose_backend._build_degradation_report(self.graph), indent=2)
            + "\n"
        ).encode("utf-8")
        self.assertEqual(
            stripped_bytes, prior_bytes,
            "stripping overlay_sources[] must reproduce the prior lock bytes "
            "byte-for-byte (additive-only, no existing key shifted)",
        )

    # Negative controls (with teeth) -------------------------------------------
    def test_negative_control_dropped_entry_breaks_completeness(self):
        report_caps = dict(self._report_caps())
        victim = sorted(report_caps.keys())[0]
        del report_caps[victim]
        self.assertNotEqual(
            set(report_caps.keys()), self.ir_capabilities,
            "negative control: dropping a capability entry must break completeness",
        )

    def test_negative_control_native_flip_trips_invariant(self):
        report_caps = dict(self._report_caps())
        mutated = {cap: dict(entry) for cap, entry in report_caps.items()}
        mutated["block-dangerous"]["status"] = "native"
        native = [c for c, e in mutated.items() if e.get("status") == "native"]
        self.assertEqual(
            native, ["block-dangerous"],
            "negative control: a status flipped to native must trip the "
            "nothing-native invariant",
        )

    def test_negative_control_status_mismatch_detected(self):
        # If the report drifted from the descriptor, the equality check must fail.
        report_caps = {cap: dict(e) for cap, e in self._report_caps().items()}
        report_caps["format"]["status"] = "adapted"  # descriptor says advisory
        mismatches = [
            cap for cap, entry in report_caps.items()
            if entry.get("status") != self.descriptor_caps[cap].get("status")
        ]
        self.assertIn(
            "format", mismatches,
            "negative control: a report/descriptor status drift must be detectable",
        )


if __name__ == "__main__":
    unittest.main()
