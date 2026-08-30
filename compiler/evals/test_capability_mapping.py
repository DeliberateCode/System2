"""Mechanism->capability mapping completeness test."""

import unittest

from system2_compiler.ir.capabilities import (
    INTENT_CAPABILITIES,
    blocking_semantics,
)


# Each enforced Claude mechanism maps to exactly one intent capability.

_MECHANISM_TO_CAPABILITY = {
    # Write-lease lifecycle + validate-file-paths against per-agent .regex.
    "write-lease-lifecycle": "enforce-lease",
    "validate-file-paths.py": "enforce-lease",
    "dangerous-command-blocker.py": "block-dangerous",
    "sensitive-file-protector.py": "protect-sensitive",
    "boundary-check.py": "protect-sensitive",
    "auto-formatter.py": "format",
    "type-checker.py": "typecheck",
    "change-budget-reporter.py": "budget",
}

# Mechanisms that are deliberately not capabilities:
_NON_CAPABILITY_MECHANISMS = frozenset({"tts-notify.py"})

# Valid enforcement points for a blocking-semantics record.
_VALID_ENFORCEMENT_POINTS = frozenset({
    "PreToolUse",
    "PostToolUse",
    "SubagentStop",
    "orchestrator-lifecycle",
})


class MechanismMappingTest(unittest.TestCase):
    """each enforced mechanism maps to exactly one intent capability."""

    def test_every_mechanism_maps_to_exactly_one_capability(self):
        # Each value is a single capability string (not a list/tuple) -> exactly one.
        for mechanism, capability in _MECHANISM_TO_CAPABILITY.items():
            self.assertIsInstance(
                capability, str,
                f"mechanism {mechanism!r} must map to exactly one capability string",
            )
            self.assertIn(
                capability, INTENT_CAPABILITIES,
                f"mechanism {mechanism!r} maps to {capability!r}, which is not in the "
                f"intent vocabulary {INTENT_CAPABILITIES}",
            )

    def test_no_mechanism_is_left_unrepresented(self):
        # Every mapped mechanism is represented (the dict cannot hold a None/empty
        # capability) — guards against an unmapped enforced mechanism.
        for mechanism, capability in _MECHANISM_TO_CAPABILITY.items():
            self.assertTrue(
                capability,
                f"enforced mechanism {mechanism!r} is left unrepresented (no capability)",
            )

    def test_union_exactly_covers_the_enforced_surface(self):
        # The union of mapped capabilities equals the enforced intent vocabulary:
        mapped_union = set(_MECHANISM_TO_CAPABILITY.values())
        self.assertEqual(
            mapped_union,
            set(INTENT_CAPABILITIES),
            "the union of mechanism->capability mappings must exactly cover the "
            "enforced intent surface (the six-term vocabulary)",
        )

    def test_tts_notify_is_an_explicit_non_capability(self):
        # tts-notify.py must NOT appear as a mapped mechanism, and must be recorded
        # as an explicit non-capability (notification side-effect, not safety).
        for mechanism in _NON_CAPABILITY_MECHANISMS:
            self.assertNotIn(
                mechanism, _MECHANISM_TO_CAPABILITY,
                f"{mechanism!r} is a notification side-effect; it must not be mapped "
                "to a safety capability ( note (3))",
            )
        self.assertIn(
            "tts-notify.py", _NON_CAPABILITY_MECHANISMS,
            "tts-notify.py must be recorded as an explicit non-capability so the "
            "mapping stays exhaustive",
        )


class BlockingSemanticsTest(unittest.TestCase):
    """every BlockingSemantic has a valid enforcement_point + blocking."""

    def setUp(self):
        self.records = blocking_semantics()

    def test_one_record_per_enforced_capability(self):
        # Exactly one BlockingSemantic per intent capability (completeness): the
        # set of record capabilities equals the vocabulary, with no duplicates.
        record_caps = [r.capability for r in self.records]
        self.assertEqual(
            len(record_caps), len(set(record_caps)),
            "BlockingSemantic records must not duplicate a capability",
        )
        self.assertEqual(
            set(record_caps),
            set(INTENT_CAPABILITIES),
            "every intent capability must have exactly one BlockingSemantic record",
        )

    def test_each_record_has_valid_enforcement_point_and_blocking(self):
        for rec in self.records:
            self.assertIn(
                rec.enforcement_point, _VALID_ENFORCEMENT_POINTS,
                f"BlockingSemantic for {rec.capability!r} has invalid "
                f"enforcement_point {rec.enforcement_point!r}",
            )
            self.assertIsInstance(
                rec.blocking, bool,
                f"BlockingSemantic for {rec.capability!r} blocking must be a bool",
            )
            self.assertTrue(
                rec.description and isinstance(rec.description, str),
                f"BlockingSemantic for {rec.capability!r} must carry a description",
            )

    def test_every_mapped_capability_has_a_blocking_semantic(self):
        # Cross-check: each capability the mapping table produces is backed by a
        # BlockingSemantic record (no enforced capability without honest semantics).
        record_caps = {r.capability for r in self.records}
        for capability in set(_MECHANISM_TO_CAPABILITY.values()):
            self.assertIn(
                capability, record_caps,
                f"mapped capability {capability!r} has no BlockingSemantic record",
            )


class NegativeControlTest(unittest.TestCase):
    """Prove the completeness assertion fails on an unmapped/invalid mechanism."""

    def test_unmapped_mechanism_breaks_union_coverage(self):
        # Inject a mechanism that maps to a BOGUS capability outside the vocabulary;
        # the union-coverage assertion must then fail.
        bad_table = dict(_MECHANISM_TO_CAPABILITY)
        bad_table["rogue-hook.py"] = "exfiltrate-data"  # not an intent capability
        mapped_union = set(bad_table.values())
        self.assertNotEqual(
            mapped_union, set(INTENT_CAPABILITIES),
            "negative control: a mechanism mapped to a non-vocabulary capability must "
            "break exact union coverage (proving the assertion has teeth)",
        )
        # And the per-mechanism validity check must reject the bogus capability.
        self.assertNotIn(
            "exfiltrate-data", INTENT_CAPABILITIES,
            "negative control: the bogus capability must not be in the vocabulary",
        )

    def test_dropping_an_enforced_capability_breaks_coverage(self):
        # Remove every mechanism for 'budget' -> the union no longer covers the
        # enforced surface, so completeness fails (an enforced mechanism unrepresented).
        partial = {
            m: c for m, c in _MECHANISM_TO_CAPABILITY.items() if c != "budget"
        }
        self.assertNotEqual(
            set(partial.values()), set(INTENT_CAPABILITIES),
            "negative control: dropping an enforced mechanism must leave a capability "
            "uncovered (completeness must fail)",
        )


if __name__ == "__main__":
    unittest.main()
