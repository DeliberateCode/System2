"""Self-test for the dead-repo grep oracle (``tools/check_no_old_repo_refs.py``)."""

import os
import sys
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)            # compiler/
_REPO_ROOT = os.path.dirname(_PKG_ROOT)           # consolidated repo root
_TOOLS = os.path.join(_PKG_ROOT, "tools")

if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

import check_no_old_repo_refs as oracle  # noqa: E402

# Reconstructed at runtime; the source bytes of this file never hold the literal.
_NEEDLE = "System2-" + "Compiler"


class CleanTreeTest(unittest.TestCase):
    """The real consolidated tree must be clean under the principled allowlist."""

    def test_current_tree_has_no_live_refs(self):
        hits = oracle.scan(_REPO_ROOT)
        self.assertEqual(
            hits,
            [],
            msg=(
                "non-allowlisted dead-repo reference(s) on the current tree: "
                + ", ".join(f"{rel}:{ln}" for rel, ln, _ in hits)
            ),
        )

    def test_main_exits_zero_on_current_tree(self):
        self.assertEqual(oracle._main(["--root", _REPO_ROOT]), 0)


class SeededViolationTest(unittest.TestCase):
    """A reference outside the allowlist must trip the oracle and be named."""

    def test_seeded_ref_exits_one_and_names_file(self):
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "live_ref.py")
            with open(bad, "w", encoding="utf-8") as fh:
                fh.write("# stale pointer: " + _NEEDLE + "/tools/foo.py\n")

            hits = oracle.scan(td)
            self.assertTrue(hits, msg="seeded reference was not detected")
            self.assertTrue(
                any(rel == "live_ref.py" for rel, _, _ in hits),
                msg=f"offending file not named in hits: {hits}",
            )
            self.assertEqual(oracle._main(["--root", td]), 1)

    def test_allowlisted_path_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            good = os.path.join(td, "CHANGELOG.md")
            with open(good, "w", encoding="utf-8") as fh:
                fh.write("historical: migrated off " + _NEEDLE + "\n")

            self.assertEqual(
                oracle.scan(td), [], msg="allowlisted CHANGELOG.md was flagged"
            )
            self.assertEqual(oracle._main(["--root", td]), 0)


if __name__ == "__main__":
    unittest.main()
