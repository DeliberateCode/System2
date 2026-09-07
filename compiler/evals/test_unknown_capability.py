"""Unknown intent-capability warning test."""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from evals import oracle
from system2_compiler.ir.capabilities import (
    INTENT_CAPABILITIES,
    validate_declared_capabilities,
)

_BASE = oracle.PLUGIN_ROOT
_UNKNOWN_CAP = "teleport-files"
_KNOWN_CAP = INTENT_CAPABILITIES[0]  # a real vocabulary term, e.g. 'enforce-lease'


def _make_overlay(tmp_root: str, name: str, capabilities) -> str:
    """Write a minimal schema-valid overlay declaring *capabilities*."""
    overlay_dir = os.path.join(tmp_root, name)
    os.makedirs(overlay_dir, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": "runtime fixture for the unknown-capability warning test",
        "schema_version": "1.0.0",
        "contributions": {
            "orchestrator": {
                "principles": [
                    {"id": f"{name}-p1", "content_file": "principle.md"}
                ]
            }
        },
        "capabilities": list(capabilities),
    }
    with open(os.path.join(overlay_dir, "system2.overlay.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    with open(os.path.join(overlay_dir, "principle.md"), "w", encoding="utf-8") as fh:
        fh.write("Benign overlay principle: validate inputs before processing.\n")
    return overlay_dir


class ValidateDeclaredCapabilitiesUnitTest(unittest.TestCase):
    """At the validator seam, unknown warns while known does not."""

    def test_unknown_capability_yields_a_warning(self):
        warnings = validate_declared_capabilities([_UNKNOWN_CAP])
        self.assertEqual(
            len(warnings), 1,
            "an unknown declared capability must yield exactly one warning",
        )
        self.assertIn(_UNKNOWN_CAP, warnings[0])

    def test_known_capability_produces_no_warning(self):
        self.assertEqual(
            [], validate_declared_capabilities([_KNOWN_CAP]),
            "a known capability must produce no warning",
        )

    def test_validator_is_deterministic_and_order_stable(self):
        declared = [_UNKNOWN_CAP, _KNOWN_CAP, "another-unknown"]
        first = validate_declared_capabilities(declared)
        second = validate_declared_capabilities(declared)
        self.assertEqual(first, second, "validator output must be deterministic")
        # Two unknowns in -> two warnings, in declaration order.
        self.assertEqual(len(first), 2)
        self.assertIn(_UNKNOWN_CAP, first[0])
        self.assertIn("another-unknown", first[1])


class UnknownCapabilityComposePathTest(unittest.TestCase):
    """An overlay's unknown capability surfaces through full composition."""

    def setUp(self):
        self.tmp_root = tempfile.mkdtemp(prefix="unknown-cap-")
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.unknown_overlay = _make_overlay(
            self.tmp_root, "unknown-cap-overlay", [_UNKNOWN_CAP]
        )
        self.known_overlay = _make_overlay(
            self.tmp_root, "known-cap-overlay", [_KNOWN_CAP]
        )

    def _compose(self, overlay_dir: str):
        project = tempfile.mkdtemp(prefix="unknown-cap-proj-")
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        return ir.compose(_BASE, [overlay_dir], project)

    def test_unknown_capability_surfaces_into_graph_warnings_without_crash(self):
        result = self._compose(self.unknown_overlay)
        # Does not crash / refuse: graph is produced.
        self.assertIsNotNone(
            result.graph, "an unknown capability must not crash or refuse compose"
        )
        self.assertEqual([], result.errors)
        # The warning is on graph.warnings.validation.
        matching = [
            w for w in result.graph.warnings.validation if _UNKNOWN_CAP in w
        ]
        self.assertEqual(
            len(matching), 1,
            "exactly one unknown-capability warning must surface into "
            f"graph.warnings.validation; got {result.graph.warnings.validation!r}",
        )

    def test_warning_is_deterministic_across_repeated_runs(self):
        first = self._compose(self.unknown_overlay)
        second = self._compose(self.unknown_overlay)
        self.assertEqual(
            first.graph.warnings.validation,
            second.graph.warnings.validation,
            "the validation warning stream must be byte-identical across runs",
        )

    def test_known_capability_produces_no_unknown_warning(self):
        result = self._compose(self.known_overlay)
        self.assertIsNotNone(result.graph)
        unknown_warnings = [
            w for w in result.graph.warnings.validation
            if "unknown intent capability" in w
        ]
        self.assertEqual(
            [], unknown_warnings,
            "an overlay declaring only a known capability must produce no "
            "unknown-capability warning",
        )


if __name__ == "__main__":
    unittest.main()
