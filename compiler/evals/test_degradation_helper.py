"""Direct unit tests for the shared degradation helper."""

import unittest

from system2_compiler.backends import _degradation


def _descriptor(caps):
    return {"version": "1.0.0", "backend": "synthetic", "capabilities": caps}


# A synthetic descriptor exercising every status value the four-value enum allows.
_FOUR_STATUS_CAPS = {
    "enforce-lease": {"status": "native", "mechanism": "hard pre-execution block"},
    "block-dangerous": {"status": "adapted", "mechanism": "permission gate"},
    "format": {"status": "advisory", "mechanism": "instruction only"},
    "typecheck": {"status": "unsupported", "mechanism": "no seam"},
}


class IrCapabilityUnionTest(unittest.TestCase):
    def test_union_is_the_set_of_present_capabilities(self):
        by_agent = {
            "planner": ["enforce-lease", "format"],
            "executor": ["enforce-lease", "block-dangerous"],
            "reviewer": [],
        }
        self.assertEqual(
            _degradation.ir_capability_union(by_agent),
            {"enforce-lease", "format", "block-dangerous"},
        )

    def test_empty_ir_is_empty_union(self):
        self.assertEqual(_degradation.ir_capability_union({}), set())


class FourStatusFixtureTest(unittest.TestCase):
    """Exercise all four statuses through one builder."""

    def test_all_four_statuses_flagged_correctly(self):
        union = set(_FOUR_STATUS_CAPS)
        records = _degradation.build_capability_records(
            _descriptor(_FOUR_STATUS_CAPS),
            union,
            fields=("status", "mechanism", "enforced", "gated"),
            allow_native=True,
        )

        self.assertEqual(
            records["enforce-lease"],
            {
                "status": "native",
                "mechanism": "hard pre-execution block",
                "enforced": True,
                "gated": False,
            },
        )
        self.assertEqual(
            records["block-dangerous"],
            {
                "status": "adapted",
                "mechanism": "permission gate",
                "enforced": False,
                "gated": True,
            },
        )
        self.assertEqual(
            records["format"],
            {
                "status": "advisory",
                "mechanism": "instruction only",
                "enforced": False,
                "gated": False,
            },
        )
        self.assertEqual(
            records["typecheck"],
            {
                "status": "unsupported",
                "mechanism": "no seam",
                "enforced": False,
                "gated": False,
            },
        )

    def test_record_key_order_is_exactly_fields(self):
        records = _degradation.build_capability_records(
            _descriptor(_FOUR_STATUS_CAPS),
            set(_FOUR_STATUS_CAPS),
            fields=("status", "mechanism", "enforced", "gated"),
        )
        for cap, rec in records.items():
            self.assertEqual(
                list(rec.keys()),
                ["status", "mechanism", "enforced", "gated"],
                f"key order drift for {cap!r}",
            )

    def test_capability_order_follows_descriptor_filtered_to_union(self):
        # Descriptor order is enforce-lease, block-dangerous, format, typecheck;
        # dropping one from the union filters it out while preserving order.
        union = {"enforce-lease", "format", "typecheck"}
        records = _degradation.build_capability_records(
            _descriptor(_FOUR_STATUS_CAPS),
            union,
            fields=("status", "mechanism"),
        )
        self.assertEqual(
            list(records.keys()), ["enforce-lease", "format", "typecheck"]
        )


class FieldsSelectionTest(unittest.TestCase):
    """Each backend's own ``fields`` selection (the byte-fidelity crux)."""

    def test_claude_two_key_records(self):
        records = _degradation.build_capability_records(
            _descriptor(_FOUR_STATUS_CAPS),
            set(_FOUR_STATUS_CAPS),
            fields=("status", "mechanism"),
            allow_native=True,
        )
        self.assertEqual(
            list(records["enforce-lease"].keys()), ["status", "mechanism"]
        )

    def test_four_key_records_allow_native_false(self):
        caps = {
            "block-dangerous": {"status": "adapted", "mechanism": "gate"},
            "format": {"status": "advisory", "mechanism": "note"},
        }
        records = _degradation.build_capability_records(
            _descriptor(caps),
            set(caps),
            fields=("status", "mechanism", "enforced", "gated"),
            allow_native=False,
        )
        self.assertEqual(
            list(records["block-dangerous"].keys()),
            ["status", "mechanism", "enforced", "gated"],
        )


class NegativeControlTest(unittest.TestCase):
    def test_ir_capability_absent_from_descriptor_raises(self):
        caps = {"format": {"status": "advisory", "mechanism": "note"}}
        with self.assertRaises(ValueError) as ctx:
            _degradation.build_capability_records(
                _descriptor(caps),
                {"format", "budget"},
                fields=("status", "mechanism"),
            )
        self.assertIn("budget", str(ctx.exception))

    def test_allow_native_false_raises_on_native(self):
        caps = {"enforce-lease": {"status": "native", "mechanism": "block"}}
        with self.assertRaises(ValueError) as ctx:
            _degradation.build_capability_records(
                _descriptor(caps),
                {"enforce-lease"},
                fields=("status", "mechanism", "enforced", "gated"),
                allow_native=False,
            )
        self.assertIn("native", str(ctx.exception))

    def test_allow_native_true_admits_native(self):
        caps = {"enforce-lease": {"status": "native", "mechanism": "block"}}
        records = _degradation.build_capability_records(
            _descriptor(caps),
            {"enforce-lease"},
            fields=("status", "mechanism", "enforced", "gated"),
            allow_native=True,
        )
        self.assertTrue(records["enforce-lease"]["enforced"])


if __name__ == "__main__":
    unittest.main()
