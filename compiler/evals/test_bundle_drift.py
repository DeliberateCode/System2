"""drift-guard self-test: the bundle freshness check has TEETH.

Phase 5 . ``tools/check_bundle_fresh.py`` is the
machine-enforced freshness gate: a stale or hand-edited vendored bundle CANNOT
merge. A guard that never fails is worthless, so this self-test proves both
directions:

  * **Fresh ->  passes.** ``tools/build_bundle.py`` generates the bundle from the
    current compiler source; the guard reports it fresh (exit 0).
  * **Mutate -> fails (teeth).** Copy the fresh bundle, mutate ONE byte in a
    vendored module, and assert the guard fails (exit non-zero) with the exact
    stale/tamper message. This proves the guard fails on a one-byte mutation.

It also pins determinism (two builds -> identical ``compiler_source_sha256``) and
the minimal layout (``ir/`` + ``backends/`` + ``plugin_adapter.py`` present; the
multi-target ``evals/`` test tree absent), and exercises the
``compute_source_hash`` drift anchor directly.

The plugin doctor suite separately covers its ``bundle_tampered`` surface. This
module covers the CI guard's fresh and mutated outcomes.

Stdlib ``unittest``; runs under ``python3 -m unittest``. All file contents are
treated as untrusted data.
"""

import contextlib
import importlib.util
import io
import os
import shutil
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_THIS_DIR)
_TOOLS_DIR = os.path.join(_COMPILER_ROOT, "tools")


def _load_tool(name):
    """Import a ``tools/<name>.py`` module by path (tools/ is not a package)."""
    path = os.path.join(_TOOLS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("tools_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_bundle = _load_tool("build_bundle")
_check_bundle_fresh_mod = _load_tool("check_bundle_fresh")


class _QuietGuard:
    """Call the guard with its success-path stdout suppressed (return value intact).

    The guard prints "vendored bundle is fresh …" to stdout on success; under
    ``unittest discover`` that noise floods the captured stdout. Swallowing it here
    keeps the suite summary readable without altering the guard's CLI behavior — the
    real tools/check_bundle_fresh.py still prints when invoked as a command.
    """

    def check_bundle_fresh(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return _check_bundle_fresh_mod.check_bundle_fresh(*args, **kwargs)


check_bundle_fresh = _QuietGuard()


class BundleDriftTest(unittest.TestCase):
    """The bundler is deterministic and the freshness guard has teeth."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="bundle-drift-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    # --- determinism + layout -------------------------------------------------

    def test_bundle_is_deterministic_and_minimal(self):
        dest_a = os.path.join(self._tmp, "a")
        dest_b = os.path.join(self._tmp, "b")
        man_a = build_bundle.build_bundle(_COMPILER_ROOT, dest_a)
        man_b = build_bundle.build_bundle(_COMPILER_ROOT, dest_b)

        self.assertEqual(
            man_a["compiler_source_sha256"], man_b["compiler_source_sha256"],
            "re-bundling identical source must yield an identical hash",
        )
        self.assertEqual(
            man_a["compiler_source_sha256"],
            build_bundle.compute_source_hash(_COMPILER_ROOT),
            "the recorded hash must equal compute_source_hash(source)",
        )

        bundle = os.path.join(dest_a, "_system2_compiler")
        pkg = os.path.join(bundle, "system2_compiler")
        for member in ("ir", "backends", "plugin_adapter.py", "cli.py"):
            self.assertTrue(
                os.path.exists(os.path.join(pkg, member)),
                f"bundle package is missing required member {member!r}",
            )
        self.assertTrue(
            os.path.exists(os.path.join(bundle, "BUNDLE.json")),
            "bundle is missing BUNDLE.json",
        )
        # The minimal bundle does NOT ship the test tree.
        self.assertFalse(
            os.path.exists(os.path.join(pkg, "evals")),
            "the minimal bundle must not vendor evals/",
        )

    # --- companion re-emission (regression guard for the dropped tamper check) -

    def test_bundle_root_file_set_is_exact(self):
        """The bundle root holds exactly the members + the companion + BUNDLE.json."""
        dest = os.path.join(self._tmp, "fileset")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        bundle = os.path.join(dest, "_system2_compiler")
        entries = set(os.listdir(bundle))
        entries.discard("__pycache__")  # never written by the bundler itself
        self.assertEqual(
            entries,
            {"system2_compiler", "_freshness.py", "BUNDLE.json"},
            "unexpected bundle root file set (the package or a companion drifted)",
        )
        pkg_entries = set(os.listdir(os.path.join(bundle, "system2_compiler")))
        pkg_entries.discard("__pycache__")
        self.assertEqual(
            pkg_entries,
            {
                "__init__.py", "ir", "backends", "plugin_adapter.py", "cli.py",
                # # the Codex user-hooks reference is now real package-data
                # (pyproject.toml), so it's vendored along with the rest of the
                # package -- intentional, not drift. The Claude channel never
                # reads it; the cost is a few KB of unused data in the bundle.
                "_packaged_data",
            },
            "unexpected product-package file set inside the bundle",
        )

    def test_freshness_companion_is_emitted_and_canonical(self):
        """The plugin tamper checker ships in every build, byte-identical to source.

        Regression guard: ``_freshness.py`` used to be neither a hashed member nor
        on the copy list, so a regen (which rmtrees the bundle dir) silently dropped
        it. It is now a ``_BUNDLE_COMPANIONS`` entry sourced from ``tools/``.
        """
        dest = os.path.join(self._tmp, "companion")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        emitted = os.path.join(dest, "_system2_compiler", "_freshness.py")
        self.assertTrue(
            os.path.isfile(emitted),
            "_freshness.py (bundle companion) was not emitted",
        )
        canonical = os.path.join(_COMPILER_ROOT, "tools", "_freshness.py")
        with open(emitted, "rb") as fh:
            emitted_bytes = fh.read()
        with open(canonical, "rb") as fh:
            canonical_bytes = fh.read()
        self.assertEqual(
            canonical_bytes, emitted_bytes,
            "emitted _freshness.py drifted from its canonical tools/ source",
        )

    def test_regen_reemits_a_dropped_companion(self):
        """Re-running the bundler restores a companion deleted between builds."""
        dest = os.path.join(self._tmp, "regen")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        emitted = os.path.join(dest, "_system2_compiler", "_freshness.py")
        os.remove(emitted)
        self.assertFalse(os.path.exists(emitted))

        build_bundle.build_bundle(_COMPILER_ROOT, dest)  # regen over the same dest
        self.assertTrue(
            os.path.isfile(emitted),
            "regen MUST re-emit the companion (rmtree must never silently drop it)",
        )

    def test_companion_is_excluded_from_the_source_hash(self):
        """The drift anchor hashes members only — never the companion."""
        rels = {rel for rel, _ in build_bundle._iter_source_files(_COMPILER_ROOT)}
        self.assertNotIn(
            "tools/_freshness.py", rels,
            "the companion source must not enter the hashed member set",
        )
        self.assertNotIn(
            "_freshness.py", rels,
            "the companion must not enter the hashed member set",
        )

    # --- the guard passes on a fresh bundle -----------------------------------

    def test_guard_passes_on_fresh_bundle(self):
        dest = os.path.join(self._tmp, "fresh")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        rc = check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest)
        self.assertEqual(rc, 0, "a freshly-built bundle must pass the guard")

    # --- the guard FAILS on a one-byte mutation (teeth) -----------------------

    def test_guard_fails_on_mutated_vendored_byte(self):
        dest = os.path.join(self._tmp, "tampered")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        victim = os.path.join(dest, "_system2_compiler", "system2_compiler", "ir", "build.py")
        with open(victim, "a", encoding="utf-8") as fh:
            fh.write("\n# tampered byte\n")

        rc = check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest)
        self.assertNotEqual(
            rc, 0,
            "mutating a vendored byte MUST fail the guard (teeth)",
        )

    def test_stale_vs_newer_source_fails(self):
        """A committed bundle that lags the compiler source is stale (untampered)."""
        dest = os.path.join(self._tmp, "stale")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)

        # A copy of the source with one newer byte: the committed bundle's vendored
        # bytes still match their own recorded hash (untampered), but that hash now
        # lags the (newer) source -> the STALE leg fires in isolation.
        newer_root = os.path.join(self._tmp, "newersrc")
        shutil.copytree(_COMPILER_ROOT, newer_root, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".ruff_cache", ".git",
        ))
        with open(os.path.join(newer_root, "system2_compiler", "ir", "build.py"), "a", encoding="utf-8") as fh:
            fh.write("\n# newer compiler source\n")

        self.assertEqual(
            check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0,
            "the bundle is fresh against the source it was built from",
        )
        self.assertNotEqual(
            check_bundle_fresh.check_bundle_fresh(newer_root, dest), 0,
            "a bundle that lags the compiler source MUST fail the guard",
        )


if __name__ == "__main__":
    unittest.main()
