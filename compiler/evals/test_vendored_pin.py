"""pin the vendored copies to their plugin originals."""

import os
import unittest

# evals/ -> compiler package root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)

# The plugin ships as a sibling of the compiler inside the consolidated repo.
_PLUGIN_SCRIPTS = os.path.abspath(os.path.join(_PKG_ROOT, "..", "plugin", "scripts"))

DRIFT_MESSAGE = "vendored copy drifted / re-vendor required"

# (vendored_copy, plugin_original) pairs to pin.
_PINS = (
    (
        os.path.join(_PKG_ROOT, "system2_compiler", "ir", "profiles.py"),
        os.path.join(_PLUGIN_SCRIPTS, "profiles.py"),
    ),
    (
        os.path.join(_PKG_ROOT, "system2_compiler", "ir", "_hook_security.py"),
        os.path.join(_PLUGIN_SCRIPTS, "hook_security.py"),
    ),
)

# Only import-path relocations are sanctioned; logic must remain identical.
_SANCTIONED_IMPORT_DELTAS: tuple = ()


def _read_lines(path: str):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read().splitlines(keepends=True)


def _normalize(lines):
    """Apply the sanctioned import-line substitutions, returning normalized lines."""
    out = []
    for ln in lines:
        stripped = ln.rstrip("\n").rstrip("\r")
        replaced = None
        for original, vendored in _SANCTIONED_IMPORT_DELTAS:
            if stripped == original or stripped == vendored:
                replaced = f"<<SANCTIONED-IMPORT::{original}>>"
                break
        out.append(replaced if replaced is not None else ln)
    return out


class VendoredPinTest(unittest.TestCase):
    """the vendored copies must not drift from the plugin originals."""

    def test_pinned_files_exist(self):
        for vendored, original in _PINS:
            self.assertTrue(
                os.path.isfile(vendored),
                msg=f"vendored copy missing: {vendored}",
            )
            self.assertTrue(
                os.path.isfile(original),
                msg=f"plugin original missing: {original}",
            )

    def test_vendored_copies_are_byte_identical_to_originals(self):
        # Default + strongest assertion: with no sanctioned deltas declared, the
        # vendored copies must be byte-for-byte identical to the plugin originals.
        for vendored, original in _PINS:
            with open(vendored, "rb") as fh:
                vbytes = fh.read()
            with open(original, "rb") as fh:
                obytes = fh.read()
            if not _SANCTIONED_IMPORT_DELTAS:
                self.assertEqual(
                    obytes,
                    vbytes,
                    msg=(
                        f"{DRIFT_MESSAGE}: {os.path.basename(vendored)} differs "
                        f"from {original} (no sanctioned import deltas are "
                        "declared, so byte-identity is required)"
                    ),
                )

    def test_vendored_copies_match_modulo_sanctioned_imports(self):
        # The normalized comparison: equal after collapsing the enumerated import relocations.
        for vendored, original in _PINS:
            vnorm = _normalize(_read_lines(vendored))
            onorm = _normalize(_read_lines(original))
            self.assertEqual(
                onorm,
                vnorm,
                msg=(
                    f"{DRIFT_MESSAGE}: {os.path.basename(vendored)} has a "
                    "non-import-path difference from its plugin original; re-vendor "
                    "(or add the exact line to _SANCTIONED_IMPORT_DELTAS if it is a "
                    "sanctioned relocation)"
                ),
            )


class VendoredPinNegativeControlTest(unittest.TestCase):
    """Prove the drift guard has teeth: simulated drifts must be rejected."""

    def test_logic_line_drift_is_detected(self):
        original = _read_lines(_PINS[0][1])
        # Simulate a plugin tightening that the vendored copy fails to mirror: flip a
        # non-import line. The normalized comparison must NOT consider it equal.
        mutated = list(original)
        # Pick the last non-empty line to mutate deterministically.
        idx = max(i for i, ln in enumerate(mutated) if ln.strip())
        mutated[idx] = mutated[idx].rstrip("\n") + "  # injected drift\n"
        self.assertNotEqual(
            _normalize(original),
            _normalize(mutated),
            msg="a simulated logic-line drift was NOT detected by the pin",
        )

    def test_unsanctioned_import_drift_is_detected(self):
        # An import-line change that is NOT in the sanctioned allow-list must fail.
        original = _read_lines(_PINS[1][1])
        mutated = ["import os  # unsanctioned new import\n"] + list(original)
        self.assertNotEqual(
            _normalize(original),
            _normalize(mutated),
            msg=(
                "an unsanctioned import-line drift was NOT detected; only the "
                "enumerated _SANCTIONED_IMPORT_DELTAS may be normalized away"
            ),
        )

    def test_sanctioned_delta_normalization_is_symmetric_when_declared(self):
        # Validate import relocation normalization with a synthetic pair.
        orig_line = "from hook_security import check_hook_security"
        vend_line = "from ._hook_security import check_hook_security"
        global _SANCTIONED_IMPORT_DELTAS
        saved = _SANCTIONED_IMPORT_DELTAS
        try:
            _SANCTIONED_IMPORT_DELTAS = ((orig_line, vend_line),)
            self.assertEqual(
                _normalize([orig_line + "\n"]),
                _normalize([vend_line + "\n"]),
                msg="sanctioned import relocation must normalize to equal",
            )
        finally:
            _SANCTIONED_IMPORT_DELTAS = saved


if __name__ == "__main__":
    unittest.main()
