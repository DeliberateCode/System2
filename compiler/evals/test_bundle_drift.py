"""drift-guard self-test: the bundle freshness check has TEETH."""

import contextlib
import importlib.util
import io
import json
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
    """Call the guard with its success-path stdout suppressed (return value intact)."""

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
                "channel_version.py",
                # # the Codex user-hooks reference is now real package-data (pyproject.toml), so it's vendored along with the rest of the package -- intentional, not drift.
                "_packaged_data",
            },
            "unexpected product-package file set inside the bundle",
        )

    def test_freshness_companion_is_emitted_and_canonical(self):
        """The plugin tamper checker ships in every build, byte-identical to source."""
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

    def test_guard_rejects_same_byte_symlinked_member_and_freshness_companion(self):
        cases = (
            ("member", os.path.join("system2_compiler", "ir", "build.py")),
            ("freshness-companion", "_freshness.py"),
        )
        for name, relative in cases:
            with self.subTest(name=name):
                dest = os.path.join(self._tmp, "symlink-file-" + name)
                build_bundle.build_bundle(_COMPILER_ROOT, dest)
                self.assertEqual(check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0)
                bundle = os.path.join(dest, "_system2_compiler")
                victim = os.path.join(bundle, relative)
                external = os.path.join(dest, "external-" + name)
                shutil.copyfile(victim, external)
                os.remove(victim)
                os.symlink(external, victim)
                self.assertNotEqual(
                    check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0,
                    f"same-byte symlinked {name} must fail the guard",
                )

    def test_guard_rejects_symlinked_bundle_package_and_member_directories(self):
        cases = (
            ("bundle-root", ""),
            ("package", "system2_compiler"),
            ("member-directory", os.path.join("system2_compiler", "ir")),
        )
        for name, relative in cases:
            with self.subTest(name=name):
                dest = os.path.join(self._tmp, "symlink-directory-" + name)
                build_bundle.build_bundle(_COMPILER_ROOT, dest)
                self.assertEqual(check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0)
                bundle = os.path.join(dest, "_system2_compiler")
                victim = os.path.join(bundle, relative) if relative else bundle
                external = os.path.join(dest, "external-" + name)
                os.rename(victim, external)
                os.symlink(external, victim)
                self.assertNotEqual(
                    check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0,
                    f"symlinked {name} must fail the guard",
                )

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

    def test_guard_fails_on_missing_or_changed_companion(self):
        for mutation in ("missing", "changed"):
            with self.subTest(mutation=mutation):
                dest = os.path.join(self._tmp, "companion-" + mutation)
                build_bundle.build_bundle(_COMPILER_ROOT, dest)
                companion = os.path.join(dest, "_system2_compiler", "_freshness.py")
                if mutation == "missing":
                    os.remove(companion)
                else:
                    with open(companion, "ab") as fh:
                        fh.write(b"\n# tampered companion\n")
                self.assertNotEqual(
                    check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0,
                    f"a {mutation} companion MUST fail the guard",
                )

    def test_guard_fails_on_extra_bundle_root_entry(self):
        dest = os.path.join(self._tmp, "extra-root")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        with open(os.path.join(dest, "_system2_compiler", "unexpected.txt"), "w") as fh:
            fh.write("unexpected\n")
        self.assertNotEqual(
            check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0,
            "an unrecorded companion/root entry MUST fail the guard",
        )

    def test_guard_fails_on_nonvolatile_manifest_mutations(self):
        mutations = (
            ("compiler_version", "999.0.0"),
            ("compiler_source_sha256", "0" * 64),
            ("unexpected_semantic_field", "unvalidated"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                dest = os.path.join(self._tmp, "manifest-" + field)
                build_bundle.build_bundle(_COMPILER_ROOT, dest)
                path = os.path.join(dest, "_system2_compiler", "BUNDLE.json")
                with open(path, encoding="utf-8") as fh:
                    manifest = json.load(fh)
                manifest[field] = value
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(manifest, fh, indent=2)
                    fh.write("\n")
                self.assertNotEqual(
                    check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0,
                    f"mutating nonvolatile BUNDLE.json field {field!r} MUST fail",
                )

    def test_guard_fails_on_malformed_manifest(self):
        dest = os.path.join(self._tmp, "malformed")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        with open(os.path.join(dest, "_system2_compiler", "BUNDLE.json"), "w") as fh:
            fh.write("[]\n")
        self.assertNotEqual(check_bundle_fresh.check_bundle_fresh(_COMPILER_ROOT, dest), 0)

    def test_stale_compiler_version_source_fails(self):
        dest = os.path.join(self._tmp, "version-source")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)
        newer_root = os.path.join(self._tmp, "new-version-source")
        shutil.copytree(_COMPILER_ROOT, newer_root, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", ".ruff_cache", ".git",
        ))
        pyproject = os.path.join(newer_root, "pyproject.toml")
        with open(pyproject, encoding="utf-8") as fh:
            content = fh.read()
        with open(pyproject, "w", encoding="utf-8") as fh:
            fh.write(content.replace('version = "0.1.0"', 'version = "0.1.1"', 1))
        self.assertNotEqual(
            check_bundle_fresh.check_bundle_fresh(newer_root, dest), 0,
            "changing the compiler version source MUST stale the bundle",
        )

    def test_stale_vs_newer_source_fails(self):
        """A committed bundle that lags the compiler source is stale (untampered)."""
        dest = os.path.join(self._tmp, "stale")
        build_bundle.build_bundle(_COMPILER_ROOT, dest)

        # A copy of the source with one newer byte: the committed bundle's vendored
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
