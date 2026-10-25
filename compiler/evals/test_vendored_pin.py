"""Pin active compiler mirrors without conflating them with frozen oracles."""

import ast
import os
import unittest

# evals/ -> compiler package root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)

# The plugin ships as a sibling of the compiler inside the consolidated repo.
_PLUGIN_SCRIPTS = os.path.abspath(os.path.join(_PKG_ROOT, "..", "plugin", "scripts"))

DRIFT_MESSAGE = "vendored copy drifted / re-vendor required"

# (active_compiler_copy, plugin_mirror) pairs to pin. Frozen oracle sources are
# independently SHA-pinned by oracle.lock.json and must not be added here.
_PINS = (
    (
        os.path.join(_PKG_ROOT, "system2_compiler", "ir", "_hook_security.py"),
        os.path.join(_PLUGIN_SCRIPTS, "hook_security.py"),
    ),
)


def _read_lines(path: str):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.read().splitlines(keepends=True)


def _normalize(lines):
    """Return executable AST, excluding comments and documentation strings."""
    tree = ast.parse("".join(lines))
    documented = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if (
            isinstance(node, documented)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            del node.body[0]
    return ast.dump(tree, include_attributes=False)


class VendoredPinTest(unittest.TestCase):
    """Active compiler mirrors must not drift in executable behavior."""

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

    def test_mirrored_sources_have_identical_executable_ast(self):
        for vendored, original in _PINS:
            vnorm = _normalize(_read_lines(vendored))
            onorm = _normalize(_read_lines(original))
            self.assertEqual(
                onorm,
                vnorm,
                msg=(
                    f"{DRIFT_MESSAGE}: {os.path.basename(vendored)} has an "
                    "executable difference from its plugin mirror"
                ),
            )


class VendoredPinNegativeControlTest(unittest.TestCase):
    """Prove the drift guard has teeth: simulated drifts must be rejected."""

    def test_logic_line_drift_is_detected(self):
        original = _read_lines(_PINS[0][1])
        # Simulate a plugin tightening that the active copy fails to mirror.
        mutated = list(original)
        mutated.append("__vendored_pin_negative_control__ = True\n")
        self.assertNotEqual(
            _normalize(original),
            _normalize(mutated),
            msg="a simulated logic-line drift was NOT detected by the pin",
        )

    def test_import_drift_is_detected(self):
        original = _read_lines(_PINS[0][1])
        mutated = ["import os  # injected drift\n"] + list(original)
        self.assertNotEqual(
            _normalize(original),
            _normalize(mutated),
            msg="a simulated import drift was NOT detected by the pin",
        )


if __name__ == "__main__":
    unittest.main()
