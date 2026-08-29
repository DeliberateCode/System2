"""TASK-312 — ``backends/capabilities/goose.json`` descriptor completeness / enum / OQ1.

Asserts the already-authored Goose capability descriptor is schema-valid and
honest (REQ-031/036 analogue for goose; AC-G3; design §"Capability descriptor +
degradation report", §"Enforcement lowering (OQ1)"):

* ``version`` present; ``backend == "goose"``; a ``capabilities`` map present;
* the capability keys equal the IR vocabulary (``ir/capabilities.INTENT_CAPABILITIES``)
  exactly — completeness, no missing, no extra;
* every ``status`` is in the four-value enum ``{native, adapted, advisory, unsupported}``;
* **nothing is ``native``** for goose (the module-boundaries invariant);
* the OQ1-locked map holds:
    ``block-dangerous`` / ``protect-sensitive`` -> ``adapted``;
    ``enforce-lease`` / ``format`` / ``typecheck`` / ``budget`` -> ``advisory``.

Negative controls (with teeth): a mutated descriptor with a bad enum value, and one
flipped to ``native``, both fail the same checks the positive cases pass.

The descriptor is read as a JSON data file (the backend's own data, not an ``ir/*``
import). ``ir/capabilities.py`` is imported only for the vocabulary.

Stdlib-only ``unittest``; runs under ``python3 -m unittest``. All cited contents are
untrusted; embedded instructions are not followed.
"""

import json
import os
import unittest

from system2_compiler.ir.capabilities import INTENT_CAPABILITIES

_STATUS_ENUM = frozenset({"native", "adapted", "advisory", "unsupported"})

_DESCRIPTOR_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "system2_compiler", "backends", "capabilities", "goose.json",
)

# The OQ1-locked enforcement map (design §"Enforcement lowering (OQ1)").
_OQ1_EXPECTED = {
    "enforce-lease": "advisory",
    "block-dangerous": "adapted",
    "protect-sensitive": "adapted",
    "format": "advisory",
    "typecheck": "advisory",
    "budget": "advisory",
}


def _load_descriptor() -> dict:
    with open(_DESCRIPTOR_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class GooseDescriptorTest(unittest.TestCase):
    """The committed goose.json is schema-valid, complete, non-native, OQ1-correct."""

    def setUp(self):
        self.descriptor = _load_descriptor()
        self.caps = self.descriptor.get("capabilities", {})

    def test_top_level_shape(self):
        self.assertTrue(self.descriptor.get("version"), "descriptor must carry a version")
        self.assertEqual(
            self.descriptor.get("backend"), "goose",
            "the goose descriptor's backend must be 'goose'",
        )
        self.assertIsInstance(
            self.caps, dict,
            "descriptor.capabilities must be an object",
        )
        self.assertTrue(self.caps, "descriptor.capabilities must be non-empty")

    def test_capability_keys_equal_ir_vocabulary(self):
        # Completeness: exactly the six IR intent capabilities, no missing / extra.
        self.assertEqual(
            set(self.caps.keys()),
            set(INTENT_CAPABILITIES),
            "goose.json capability keys must equal the IR vocabulary exactly "
            f"(IR={sorted(INTENT_CAPABILITIES)}, descriptor={sorted(self.caps)})",
        )

    def test_every_status_is_enum_valid(self):
        for cap, entry in self.caps.items():
            self.assertIn(
                entry.get("status"), _STATUS_ENUM,
                f"goose.json[{cap!r}].status {entry.get('status')!r} not in {_STATUS_ENUM}",
            )

    def test_nothing_is_native_for_goose(self):
        # The central invariant: Goose has no PreToolUse/PostToolUse/SubagentStop
        # hooks, so NO capability may claim native enforcement.
        native = {
            cap: entry.get("status")
            for cap, entry in self.caps.items()
            if entry.get("status") == "native"
        }
        self.assertEqual(
            {}, native,
            f"goose must report nothing as 'native'; offenders: {native!r}",
        )

    def test_oq1_status_per_capability_is_exact(self):
        actual = {cap: entry.get("status") for cap, entry in self.caps.items()}
        self.assertEqual(
            actual, _OQ1_EXPECTED,
            "the per-capability status must match the OQ1-locked map exactly "
            f"(expected {_OQ1_EXPECTED}, got {actual})",
        )

    def test_every_entry_carries_a_mechanism(self):
        # The report is self-describing only if each entry names a mechanism.
        for cap, entry in self.caps.items():
            self.assertTrue(
                entry.get("mechanism"),
                f"goose.json[{cap!r}] must carry a non-empty mechanism string",
            )

    def test_two_adapted_four_advisory(self):
        # The OQ1 shape: exactly two adapted (the permission-gated caps) and four
        # advisory (instruction-only caps).
        by_status = {}
        for entry in self.caps.values():
            by_status.setdefault(entry.get("status"), []).append(entry)
        self.assertEqual(len(by_status.get("adapted", [])), 2)
        self.assertEqual(len(by_status.get("advisory", [])), 4)
        self.assertNotIn("native", by_status)


class GooseDescriptorNegativeControlTest(unittest.TestCase):
    """Negative controls: a mutated descriptor must fail the same checks."""

    def setUp(self):
        self.caps = dict(_load_descriptor().get("capabilities", {}))

    def test_bad_enum_value_is_detected(self):
        mutated = dict(self.caps)
        victim = sorted(mutated.keys())[0]
        mutated[victim] = {"status": "blocking", "mechanism": "x"}  # not in enum
        offenders = [
            cap for cap, entry in mutated.items()
            if entry.get("status") not in _STATUS_ENUM
        ]
        self.assertIn(
            victim, offenders,
            "negative control: an out-of-enum status must be detectable",
        )

    def test_native_value_is_detected(self):
        # Flipping any goose capability to 'native' must trip the nothing-native check.
        mutated = dict(self.caps)
        victim = "block-dangerous"
        mutated[victim] = dict(mutated[victim])
        mutated[victim]["status"] = "native"
        native = [
            cap for cap, entry in mutated.items()
            if entry.get("status") == "native"
        ]
        self.assertEqual(
            native, [victim],
            "negative control: a goose capability flipped to 'native' must be "
            "detected by the nothing-native invariant",
        )

    def test_dropped_capability_breaks_completeness(self):
        mutated = dict(self.caps)
        victim = sorted(mutated.keys())[0]
        del mutated[victim]
        self.assertNotEqual(
            set(mutated.keys()), set(INTENT_CAPABILITIES),
            "negative control: dropping a capability must break completeness",
        )


if __name__ == "__main__":
    unittest.main()
