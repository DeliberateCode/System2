"""Prove every regeneration freshness guard passes fresh and fails on drift."""

import contextlib
import glob
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_THIS_DIR)
_REPO_ROOT = os.path.dirname(_COMPILER_ROOT)
_TOOLS_DIR = os.path.join(_COMPILER_ROOT, "tools")


def _load_tool(name):
    """Import a ``tools/<name>.py`` module by path (``tools/`` is not a package)."""
    path = os.path.join(_TOOLS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("tools_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


regen_all = _load_tool("regen_all")

# Real (read-only) source inputs the builders compose from.
_PLUGIN_ROOT = os.path.join(_REPO_ROOT, "plugin")
_OVERLAYS = [os.path.join(_REPO_ROOT, r) for r in regen_all._CODEX_OVERLAY_RELPATHS]

# Only volatile provenance breadcrumbs may be ignored.
_EXPECTED_IGNORE = ("generated_at", "generated_from")

# Correctness-bearing provenance fields that must NEVER be ignored.
_CORRECTNESS_FIELDS = frozenset({
    "source_sha256", "artifact_sha256", "artifact_inventory",
    "compiler_source_sha256", "generator", "channel_version", "compiler_version",
})

# Basenames of the provenance/manifest files that carry the ignorable breadcrumbs and
# are therefore the ONLY files allowed to differ between two deterministic regens.
_PROVENANCE_BASENAMES = frozenset({"PROVENANCE.json", "BUNDLE.json"})

# Registered builders for which this module has an acknowledged coverage review.
_COVERED_BUILDERS = frozenset({"bundle", "codex", "pi"})

# The real committed bundle subtree; hashed at import (before any test writes) so
# ``tearDownModule`` can prove the run left the real tree untouched.
_REAL_BUNDLE_DIR = os.path.join(_REPO_ROOT, "plugin", "scripts", "_system2_compiler")
_REAL_BUNDLE_HASH_AT_IMPORT = regen_all.build_bundle.compute_source_hash(_REAL_BUNDLE_DIR)


def tearDownModule():
    """Fail loudly if the run mutated the real committed bundle (it must not)."""
    after = regen_all.build_bundle.compute_source_hash(_REAL_BUNDLE_DIR)
    if after != _REAL_BUNDLE_HASH_AT_IMPORT:
        raise AssertionError(
            "test_regen_guards mutated the REAL committed bundle subtree "
            f"({_REAL_BUNDLE_DIR}); all mutation must stay in temp copies"
        )


def _capture(func, *args, **kwargs):
    """Run *func* with stdout/stderr captured; return ``(rc, stdout, stderr)``."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = func(*args, **kwargs)
    return rc, out.getvalue(), err.getvalue()


def _build_temp_compiler_root(dest):
    """Populate *dest* with the minimal compiler source ``build_bundle`` hashes."""
    shutil.copytree(
        os.path.join(_COMPILER_ROOT, "system2_compiler"),
        os.path.join(dest, "system2_compiler"),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    os.makedirs(os.path.join(dest, "tools"))
    shutil.copyfile(
        os.path.join(_COMPILER_ROOT, "tools", "_freshness.py"),
        os.path.join(dest, "tools", "_freshness.py"),
    )
    shutil.copyfile(
        os.path.join(_COMPILER_ROOT, "pyproject.toml"),
        os.path.join(dest, "pyproject.toml"),
    )


class RegenGuardContractTest(unittest.TestCase):
    """Check stale diagnostics, the minimal ignore set, and builder coverage."""

    def test_stale_message_names_artifact_and_regen_command(self):
        """the divergence message names the artifact AND the exact command."""
        for name in ("bundle", "codex", "pi"):
            msg = regen_all.stale_message(name)
            self.assertEqual(
                msg,
                f"{name} is stale: regenerate via python3 compiler/tools/regen_all.py",
            )
            self.assertIn(name, msg)
            self.assertIn("python3 compiler/tools/regen_all.py", msg)

    def test_ignore_set_is_exactly_the_documented_provenance_fields(self):
        """the ignore set is EXACTLY these two breadcrumbs. A broadened set is
        itself a defect — this literal equality makes any widening a hard failure."""
        self.assertEqual(regen_all.IGNORED_PROVENANCE_FIELDS, _EXPECTED_IGNORE)

    def test_ignore_set_excludes_every_correctness_field(self):
        """No correctness-bearing field may be ignored; that would let a real content
        drift hide behind an "ignored" provenance key (the silent-widen attack)."""
        for field in _CORRECTNESS_FIELDS:
            self.assertNotIn(
                field, regen_all.IGNORED_PROVENANCE_FIELDS,
                f"{field!r} is correctness-bearing and must never be ignored by --check",
            )

    def test_real_committed_bundle_is_fresh(self):
        """The committed bundle guarded by --check is currently fresh + untampered
        (and, by tearDownModule, is left untouched by this module)."""
        rc, _out, _err = _capture(
            regen_all.check_bundle_fresh.check_bundle_fresh,
            _COMPILER_ROOT, _REAL_BUNDLE_DIR,
        )
        self.assertEqual(rc, 0)

    def test_tree_comparison_rejects_same_byte_symlinked_files_and_directories(self):
        with tempfile.TemporaryDirectory() as root:
            committed = os.path.join(root, "committed")
            regenerated = os.path.join(root, "regenerated")
            for tree in (committed, regenerated):
                os.makedirs(os.path.join(tree, "nested"))
                with open(os.path.join(tree, "nested", "artifact.txt"), "wb") as fh:
                    fh.write(b"same bytes")

            self.assertTrue(
                regen_all._trees_match(committed, regenerated),
                "regular-file trees remain the accepted negative control",
            )

            victim = os.path.join(committed, "nested", "artifact.txt")
            external_file = os.path.join(root, "external-file")
            shutil.copyfile(victim, external_file)
            os.remove(victim)
            os.symlink(external_file, victim)
            self.assertFalse(regen_all._trees_match(committed, regenerated))

            shutil.rmtree(committed)
            shutil.copytree(regenerated, committed)
            external_dir = os.path.join(root, "external-dir")
            os.rename(os.path.join(committed, "nested"), external_dir)
            os.symlink(external_dir, os.path.join(committed, "nested"))
            self.assertFalse(regen_all._trees_match(committed, regenerated))
            with self.assertRaisesRegex(ValueError, "artifact directory"):
                regen_all._relfiles(committed)

    def test_tree_comparison_rejects_distribution_through_symlinked_parent(self):
        with tempfile.TemporaryDirectory() as root:
            repository = os.path.join(root, "repository")
            distributions = os.path.join(repository, "distributions")
            committed = os.path.join(distributions, "codex")
            regenerated = os.path.join(root, "regenerated")
            for tree in (committed, regenerated):
                os.makedirs(os.path.join(tree, "nested"))
                with open(os.path.join(tree, "nested", "artifact.txt"), "wb") as fh:
                    fh.write(b"same bytes")
            self.assertTrue(
                regen_all._trees_match(
                    committed,
                    regenerated,
                    committed_trusted_root=repository,
                    regen_trusted_root=root,
                )
            )

            external = os.path.join(root, "external-distributions")
            os.rename(distributions, external)
            os.symlink(external, distributions)
            self.assertFalse(
                regen_all._trees_match(
                    committed,
                    regenerated,
                    committed_trusted_root=repository,
                    regen_trusted_root=root,
                )
            )

    def test_registry_coverage_is_exact(self):
        """Every registered builder has acknowledged regen coverage."""
        registered = {art.name for art in regen_all.REGISTRY}
        self.assertEqual(registered, _COVERED_BUILDERS)
        for art in regen_all.REGISTRY:
            self.assertTrue(callable(art.builder), art.name)


class RegenInducedDivergenceTest(unittest.TestCase):
    """Leg (a): mutate an input, --check goes RED naming the artifact + regen command; regenerate, --check goes GREEN."""

    def _run_check(self, art, ctx):
        return _capture(regen_all._check, [art], ctx, True)

    def _divergence_bundle(self, art):
        """Bundle divergence via ``check_bundle_fresh``'s sha anchor: mutate a hashed compiler source member in a temp compiler-root copy -> committed bundle is now stale -> RED."""
        with tempfile.TemporaryDirectory() as tc, tempfile.TemporaryDirectory() as repo:
            _build_temp_compiler_root(tc)
            ctx = regen_all._Context(
                compiler_root=tc, repo_root=repo,
                plugin_root=_PLUGIN_ROOT, codex_overlays=list(_OVERLAYS),
            )
            dest = os.path.join(repo, art.dest_rel)
            art.builder(dest, ctx)

            rc, _out, _err = self._run_check(art, ctx)
            self.assertEqual(rc, 0, "fresh bundle must check GREEN")

            members = sorted(glob.glob(
                os.path.join(tc, "system2_compiler", "**", "*.py"), recursive=True))
            self.assertTrue(members, "temp compiler root has no hashed source members")
            with open(members[0], "ab") as fh:
                fh.write(b"\n# induced divergence\n")

            rc, _out, err = self._run_check(art, ctx)
            self.assertEqual(rc, 1, "a mutated hashed source member must go RED")
            self.assertIn(regen_all.stale_message(art.name), err)

            art.builder(dest, ctx)  # regenerate from the (now-mutated) source
            rc, _out, _err = self._run_check(art, ctx)
            self.assertEqual(rc, 0, "regenerated bundle must check GREEN again")

            bundle_root = os.path.join(dest, art.content_rel)
            for mutation in ("missing companion", "changed companion", "compiler version"):
                with self.subTest(bundle_mutation=mutation):
                    art.builder(dest, ctx)
                    if mutation == "missing companion":
                        os.remove(os.path.join(bundle_root, "_freshness.py"))
                    elif mutation == "changed companion":
                        with open(os.path.join(bundle_root, "_freshness.py"), "ab") as fh:
                            fh.write(b"\n# changed companion\n")
                    else:
                        manifest_path = os.path.join(bundle_root, "BUNDLE.json")
                        with open(manifest_path, encoding="utf-8") as fh:
                            manifest = json.load(fh)
                        manifest["compiler_version"] = "999.0.0"
                        with open(manifest_path, "w", encoding="utf-8") as fh:
                            json.dump(manifest, fh, indent=2)
                            fh.write("\n")
                    rc, _out, err = self._run_check(art, ctx)
                    self.assertEqual(rc, 1, f"{mutation} must make regen --check RED")
                    self.assertIn(regen_all.stale_message(art.name), err)

    def _divergence_distribution(self, art):
        """Assert that temporary distribution drift is detected."""
        with tempfile.TemporaryDirectory() as repo:
            compiler_root = os.path.join(repo, "compiler-source")
            shutil.copytree(
                _COMPILER_ROOT, compiler_root,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
            ctx = regen_all._Context(
                compiler_root=compiler_root, repo_root=repo,
                plugin_root=_PLUGIN_ROOT, codex_overlays=list(_OVERLAYS),
            )
            dest = os.path.join(repo, art.dest_rel)
            art.builder(dest, ctx)
            committed_root = os.path.join(dest, art.content_rel)
            if art.name == "codex":
                mirror = os.path.join(compiler_root, regen_all._PACKAGED_USER_HOOKS_REL)
                shutil.rmtree(mirror, ignore_errors=True)
                shutil.copytree(os.path.join(dest, "user-hooks"), mirror)

            rc, _out, _err = self._run_check(art, ctx)
            self.assertEqual(rc, 0, f"freshly emitted {art.name} must check GREEN")

            # (a) append a byte to an emitted non-provenance file -> RED.
            emitted = sorted(regen_all._relfiles(committed_root))
            targets = [f for f in emitted
                       if os.path.basename(f) not in _PROVENANCE_BASENAMES]
            self.assertTrue(targets, f"{art.name} emitted no diffable content files")
            with open(os.path.join(committed_root, targets[0]), "ab") as fh:
                fh.write(b"\n")
            rc, _out, err = self._run_check(art, ctx)
            self.assertEqual(rc, 1, f"a mutated emitted {art.name} file must go RED")
            self.assertIn(regen_all.stale_message(art.name), err)

            # regenerate -> GREEN again.
            art.builder(dest, ctx)
            rc, _out, _err = self._run_check(art, ctx)
            self.assertEqual(rc, 0, f"regenerated {art.name} must check GREEN again")

            # (b) mutate a NON-ignored provenance field -> RED. If source_sha256 were
            # ever added to the ignore set, this would (wrongly) stay GREEN.
            prov_path = os.path.join(
                committed_root, regen_all._provenance.PROVENANCE_FILENAME)
            if os.path.isfile(prov_path):
                with open(prov_path, "r", encoding="utf-8") as fh:
                    prov = json.load(fh)
                self.assertIn("source_sha256", prov)
                self.assertNotIn("source_sha256", regen_all.IGNORED_PROVENANCE_FIELDS)
                prov["source_sha256"] = "0" * 64
                with open(prov_path, "w", encoding="utf-8") as fh:
                    json.dump(prov, fh, indent=2)
                    fh.write("\n")
                rc, _out, err = self._run_check(art, ctx)
                self.assertEqual(
                    rc, 1, "mutating the non-ignored source_sha256 must go RED")
                self.assertIn(regen_all.stale_message(art.name), err)

            for mutation in ("extra artifact", "missing artifact", "malformed provenance"):
                with self.subTest(distribution=art.name, mutation=mutation):
                    art.builder(dest, ctx)
                    if mutation == "extra artifact":
                        with open(os.path.join(committed_root, "unexpected.txt"), "w") as fh:
                            fh.write("unexpected\n")
                    elif mutation == "missing artifact":
                        os.remove(os.path.join(committed_root, targets[0]))
                    else:
                        with open(prov_path, "w", encoding="utf-8") as fh:
                            fh.write("[]\n")
                    rc, _out, err = self._run_check(art, ctx)
                    self.assertEqual(rc, 1, f"{mutation} must make regen --check RED")
                    self.assertIn(regen_all.stale_message(art.name), err)

    def test_induced_divergence_for_every_registered_builder(self):
        registered = list(regen_all.REGISTRY)
        self.assertTrue(registered, "REGISTRY has no builder to guard")
        exercised = 0
        for art in registered:
            with self.subTest(artifact=art.name):
                if art.bundle_oracle:
                    self._divergence_bundle(art)
                else:
                    self._divergence_distribution(art)
                exercised += 1
        # No registered builder may be silently skipped by the loop.
        self.assertEqual(exercised, len(registered))


class RegenDeterminismTest(unittest.TestCase):
    """Leg (b) / : two regens from identical source are byte-identical except for the documented ignored breadcrumbs — and those are the ONLY differences allowed."""

    def _assert_deterministic(self, art):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ctx = regen_all._Context(
                compiler_root=_COMPILER_ROOT, repo_root=a,
                plugin_root=_PLUGIN_ROOT, codex_overlays=list(_OVERLAYS),
            )
            art.builder(os.path.join(a, "art"), ctx)
            art.builder(os.path.join(b, "art"), ctx)
            root_a = os.path.join(a, "art", art.content_rel)
            root_b = os.path.join(b, "art", art.content_rel)

            files_a = regen_all._relfiles(root_a)
            files_b = regen_all._relfiles(root_b)
            self.assertEqual(files_a, files_b, f"{art.name}: file set is non-deterministic")

            differing = []
            for rel in sorted(files_a):
                with open(os.path.join(root_a, rel), "rb") as fa, \
                        open(os.path.join(root_b, rel), "rb") as fb:
                    if fa.read() != fb.read():
                        differing.append(rel)

            # Every NON-provenance file must be byte-identical.
            non_prov = [r for r in differing
                        if os.path.basename(r) not in _PROVENANCE_BASENAMES]
            self.assertEqual(
                non_prov, [],
                f"{art.name}: non-provenance files differ between two regens: {non_prov}",
            )

            # In every differing provenance file, ONLY ignored breadcrumbs may differ,
            # and every correctness field must be byte-equal.
            for rel in differing:
                with open(os.path.join(root_a, rel), "r", encoding="utf-8") as fa:
                    pa = json.load(fa)
                with open(os.path.join(root_b, rel), "r", encoding="utf-8") as fb:
                    pb = json.load(fb)
                ignored = set(regen_all.IGNORED_PROVENANCE_FIELDS)
                if os.path.basename(rel) == "BUNDLE.json":
                    ignored.add("bundled_at")
                diff_keys = {k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k)}
                self.assertTrue(
                    diff_keys <= ignored,
                    f"{art.name}/{rel}: fields outside the ignore set differ: "
                    f"{sorted(diff_keys - ignored)}",
                )
                for key, val in pa.items():
                    if key not in ignored:
                        self.assertEqual(
                            val, pb.get(key),
                            f"{art.name}/{rel}: non-ignored field {key!r} is unstable",
                        )
                # Sanity: the provenance file actually carried a correctness anchor,
                # so "identical modulo ignored" is a meaningful claim, not vacuous.
                self.assertTrue(
                    _CORRECTNESS_FIELDS & set(pa),
                    f"{art.name}/{rel}: provenance carried no correctness field to pin",
                )

    def test_determinism_for_every_registered_builder(self):
        registered = list(regen_all.REGISTRY)
        self.assertTrue(registered, "REGISTRY has no builder to check for determinism")
        exercised = 0
        for art in registered:
            with self.subTest(artifact=art.name):
                self._assert_deterministic(art)
                exercised += 1
        self.assertEqual(exercised, len(registered))


if __name__ == "__main__":
    unittest.main()
