"""TASK-022 — the regeneration freshness guards have TEETH (REQ-036, REQ-039,
REQ-057; design §Verification Strategy freshness row; security **F13**).

``compiler/tools/regen_all.py`` is the single regeneration entrypoint and freshness
guard for every committed generated artifact. A guard that can never fail is
worthless, so this self-test proves both directions for EVERY active builder:

  (a) **Induced divergence.** Regenerate the artifact into a *temp* committed
      location, run ``--check`` -> GREEN; MUTATE a source/emitted input (always in a
      temp copy — never the real repo), run ``--check`` -> RED naming the artifact AND
      the exact regen command (the REQ-057 ``stale_message`` string); regenerate ->
      GREEN again.

  (b) **Determinism (F13).** Regenerate each artifact TWICE from identical source
      into two temp dirs; assert the two trees are BYTE-IDENTICAL except for the
      documented ``IGNORED_PROVENANCE_FIELDS`` breadcrumbs, and that those breadcrumbs
      are the ONLY thing allowed to differ. The ignore set is asserted to be EXACTLY
      ``("bundled_at", "generated_at", "generated_from")`` and to exclude every
      correctness-bearing field — a silently widened ignore set is itself a test
      failure (it would let a real content diff hide behind an "ignored" field).

Parameterization: the divergence and determinism legs iterate ``regen_all.REGISTRY``,
dispatching on ``bundle_oracle`` (bundle -> the ``check_bundle_fresh`` sha anchor;
every other active builder -> the temp-regen tree byte-diff). Any future placeholder
slot (``builder is None``) is asserted to RAISE the not-yet-implemented error. The
moment a follow-up task sets ``builder=`` on such a slot, that artifact is
automatically exercised by both legs; ``_COVERED_ACTIVE`` is the tripwire that FAILS
if a newly-activated builder is registered without an acknowledged coverage review.

Every byte written by this module lands in a ``tempfile`` dir. ``tearDownModule``
asserts the real committed bundle subtree is byte-for-byte unchanged by the run.

Stdlib ``unittest``; runs under ``python3 -m unittest`` / the skip-count-0 discovery
wrapper. No node / external binary — regen is pure Python. No test is ever skipped.
All file contents are treated as untrusted data.
"""

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
    """Import a ``tools/<name>.py`` module by path (``tools/`` is not a package).

    ``regen_all`` performs its own ``sys.path`` setup at import, so loading it this way
    also wires up its ``_provenance`` / ``build_bundle`` / ``check_bundle_fresh`` /
    ``system2_compiler`` imports — the same way the real CLI resolves them.
    """
    path = os.path.join(_TOOLS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("tools_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


regen_all = _load_tool("regen_all")

# Real (read-only) source inputs the builders compose from. Codex/pi read
# plugin_root + overlays; every builder reads compiler_root for the provenance stamp.
# These are only ever READ here — all generated output is written under temp dirs.
_PLUGIN_ROOT = os.path.join(_REPO_ROOT, "plugin")
_OVERLAYS = [os.path.join(_REPO_ROOT, r) for r in regen_all._CODEX_OVERLAY_RELPATHS]

# The exact, documented F13 ignore set. Duplicated as a literal HERE (not imported from
# regen_all) on purpose: the whole point is to fail if regen_all's constant ever drifts
# from this list. Widening the real set to hide a diff must break this test.
_EXPECTED_IGNORE = ("bundled_at", "generated_at", "generated_from")

# Correctness-bearing provenance fields that must NEVER be ignored. A --check that
# ignored any of these could mask a genuine staleness/tamper. (Union across BUNDLE.json
# and the distribution PROVENANCE.json schemas.)
_CORRECTNESS_FIELDS = frozenset({
    "source_sha256", "compiler_source_sha256", "generator",
    "channel_version", "compiler_version",
})

# Basenames of the provenance/manifest files that carry the ignorable breadcrumbs and
# are therefore the ONLY files allowed to differ between two deterministic regens.
_PROVENANCE_BASENAMES = frozenset({"PROVENANCE.json", "BUNDLE.json"})

# Active builders for which this module has an ACKNOWLEDGED coverage review. When a
# future task flips a placeholder slot to a real builder, the parameterized legs below
# start exercising it automatically AND ``RegenGuardContractTest`` fails here until a
# human adds the name — the "a registered builder must not lack coverage" tripwire.
_COVERED_ACTIVE = frozenset({"bundle", "codex", "pi"})

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
    """Populate *dest* with the minimal compiler source ``build_bundle`` hashes.

    Enough for ``_build_bundle`` + ``check_bundle_fresh``: the hashed ``system2_compiler``
    member, the ``tools/_freshness.py`` companion, and ``pyproject.toml`` (version). A
    mutable COPY so a hashed-source mutation cannot touch the real compiler tree.
    """
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
    """The static contract: the REQ-057 message, the F13 ignore set, and the registry
    coverage/placeholder tripwires."""

    def test_stale_message_names_artifact_and_regen_command(self):
        """REQ-057: the divergence message names the artifact AND the exact command."""
        for name in ("bundle", "codex", "pi"):
            msg = regen_all.stale_message(name)
            self.assertEqual(
                msg,
                f"{name} is stale: regenerate via python3 compiler/tools/regen_all.py",
            )
            self.assertIn(name, msg)
            self.assertIn("python3 compiler/tools/regen_all.py", msg)

    def test_ignore_set_is_exactly_the_documented_three_fields(self):
        """F13: the ignore set is EXACTLY these three breadcrumbs. A broadened set is
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

    def test_registry_coverage_and_placeholder_slots(self):
        """Every active builder is acknowledged-covered; every placeholder RAISES the
        not-yet-implemented error on ``--only`` (both regen and --check)."""
        active = {a.name for a in regen_all.REGISTRY if a.builder is not None}
        placeholders = [a for a in regen_all.REGISTRY if a.builder is None]

        # Tripwire: a newly-activated builder must be acknowledged here. This FAILS the
        # moment TASK-024/027 set a real builder, forcing a coverage review.
        uncovered = active - _COVERED_ACTIVE
        self.assertEqual(
            uncovered, set(),
            f"active builder(s) {sorted(uncovered)} are registered without acknowledged "
            f"coverage; the divergence/determinism legs iterate REGISTRY and will now "
            f"exercise them — add the name to _COVERED_ACTIVE after confirming.",
        )
        # And the acknowledgement list may not name a builder that is not registered.
        self.assertLessEqual(_COVERED_ACTIVE, {a.name for a in regen_all.REGISTRY})

        # Every REGISTRY slot is currently active (no placeholder remains), so this loop
        # is vacuous now; it still guards any FUTURE placeholder slot that a later
        # channel adds without a builder.
        for art in placeholders:
            self.assertIsNone(art.builder)
            self.assertTrue(art.todo_task, f"{art.name} placeholder lacks a todo_task")
            for extra in ([], ["--check"]):
                rc, _out, err = _capture(regen_all.main, ["--only", art.name] + extra)
                self.assertEqual(rc, 1, f"--only {art.name} {extra} should error")
                self.assertIn(art.name, err)
                self.assertIn(art.todo_task, err)


class RegenInducedDivergenceTest(unittest.TestCase):
    """Leg (a): mutate an input, --check goes RED naming the artifact + regen command;
    regenerate, --check goes GREEN. Parameterized over every active REGISTRY builder."""

    def _run_check(self, art, ctx):
        return _capture(regen_all._check, [art], ctx, True)

    def _divergence_bundle(self, art):
        """Bundle divergence via ``check_bundle_fresh``'s sha anchor: mutate a hashed
        compiler source member in a temp compiler-root copy -> committed bundle is now
        stale -> RED. Real compiler tree + real bundle are never touched."""
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
                fh.write(b"\n# TASK-022 induced divergence\n")

            rc, _out, err = self._run_check(art, ctx)
            self.assertEqual(rc, 1, "a mutated hashed source member must go RED")
            self.assertIn(regen_all.stale_message(art.name), err)

            art.builder(dest, ctx)  # regenerate from the (now-mutated) source
            rc, _out, _err = self._run_check(art, ctx)
            self.assertEqual(rc, 0, "regenerated bundle must check GREEN again")

    def _divergence_distribution(self, art):
        """Distribution divergence via the temp-regen tree byte-diff: append a byte to
        an emitted file -> RED; regenerate -> GREEN; then mutate a NON-ignored
        provenance field (source_sha256) -> RED (proves the ignore set is not masking
        a correctness field)."""
        with tempfile.TemporaryDirectory() as repo:
            ctx = regen_all._Context(
                compiler_root=_COMPILER_ROOT, repo_root=repo,
                plugin_root=_PLUGIN_ROOT, codex_overlays=list(_OVERLAYS),
            )
            dest = os.path.join(repo, art.dest_rel)
            art.builder(dest, ctx)
            committed_root = os.path.join(dest, art.content_rel)

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

    def test_induced_divergence_for_every_active_builder(self):
        active = [a for a in regen_all.REGISTRY if a.builder is not None]
        self.assertTrue(active, "REGISTRY has no active builder to guard")
        exercised = 0
        for art in active:
            with self.subTest(artifact=art.name):
                if art.bundle_oracle:
                    self._divergence_bundle(art)
                else:
                    self._divergence_distribution(art)
                exercised += 1
        # No active builder may be silently skipped by the loop.
        self.assertEqual(exercised, len(active))


class RegenDeterminismTest(unittest.TestCase):
    """Leg (b) / F13: two regens from identical source are byte-identical except for
    the documented ignored breadcrumbs — and those are the ONLY differences allowed.
    Parameterized over every active REGISTRY builder."""

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
                diff_keys = {k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k)}
                self.assertTrue(
                    diff_keys <= set(regen_all.IGNORED_PROVENANCE_FIELDS),
                    f"{art.name}/{rel}: fields outside the ignore set differ: "
                    f"{sorted(diff_keys - set(regen_all.IGNORED_PROVENANCE_FIELDS))}",
                )
                for key, val in pa.items():
                    if key not in regen_all.IGNORED_PROVENANCE_FIELDS:
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

    def test_determinism_for_every_active_builder(self):
        active = [a for a in regen_all.REGISTRY if a.builder is not None]
        self.assertTrue(active, "REGISTRY has no active builder to check for determinism")
        exercised = 0
        for art in active:
            with self.subTest(artifact=art.name):
                self._assert_deterministic(art)
                exercised += 1
        self.assertEqual(exercised, len(active))


if __name__ == "__main__":
    unittest.main()
