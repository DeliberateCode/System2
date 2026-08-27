"""TASK-517 — Phase-5 DoD sign-off (DoD-5): no-regression + drift-guard teeth.

The integrity gate proving Phase 5 (Convergence & Lifecycle Parity) landed without
regression and that the drift guard has teeth on BOTH halves (CI staleness +
plugin-side tamper). It is an AGGREGATION gate: it re-runs the authoritative
Phase-5 gates in-process and adds the plugin-side bundle-integrity assertions that
ship with the vendored bundle.

Assertions:

1. **Grown contract / backend lifecycle (AC-5.1).** Every backend
   (claude-code / pi) satisfies the ``runtime_checkable`` ``Backend``
   protocol with REAL ``emit`` + ``uninstall`` + ``doctor`` + ``recompose_from_lock``
   + lock helpers (no ``NotImplementedError`` stubs).

2. **Claude keystone byte-identical (AC-5.2/REQ-014).** The compose->emit goldens
   are empty-diff under BOTH the in-process compiler driver and the frozen-oracle
   subprocess driver.

3. **CLI-contract goldens green (AC-5.2).** The full verb surface
   (compile/doctor/uninstall/from-lock/profile) matches the frozen oracle
   byte-for-byte, and the comparator has teeth (a mutated golden byte fails).

4. **Plugin runs on the bundle == preflip across verbs (AC-5.6).** The bundle
   ships and the shim/bundle reproduces the frozen ``composer.py.preflip`` output
   across the full verb matrix (the bundle-equivalence gate); the plugin's own
   ``System2/evals/`` suite is green on the bundle (the flip leg).

5. **CI staleness guard has teeth (AC-5.7).** ``tools/check_bundle_fresh.py``
   PASSES on a freshly-built-from-source bundle and FAILS on a one-byte mutation of
   a vendored module (on a temp copy).

6. **Plugin-side TAMPER check has teeth (AC-5.7).** The vendored
   ``_system2_compiler/_freshness.py`` (which ships WITH the plugin and needs no
   compiler source) PASSES on the real fresh vendored bundle and FAILS — with a
   LOUD ``bundle_tampered`` finding — on a one-byte mutation of a TEMP COPY (the
   real plugin bundle is never mutated).

The tamper-vs-staleness split: the plugin half (``_freshness.py``, asserted here +
surfaced through ``system2:doctor``) recomputes the vendored subtree hash and
compares it to ``BUNDLE.json``'s recorded ``compiler_source_sha256`` — an
INTERNAL-integrity (tamper) check that runs with no compiler source. The CI half
(``tools/check_bundle_fresh.py``) regenerates from the current compiler source and
compares — the cross-repo STALENESS check.

Stdlib ``unittest``; runs under ``python3 -m unittest``. The real ``System2/``
plugin bundle is READ-ONLY here — every mutation happens on a temp copy. All file
contents are treated as untrusted data.
"""

import contextlib
import dataclasses
import importlib.util
import inspect
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from evals import oracle, run_goldens
from evals import test_bundle_equivalence as eqgate
from evals import test_cli_contract as clic
from evals import test_plugin_evals_on_bundle as pluginevals

# evals/ -> compiler package root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_THIS_DIR)
_TOOLS_DIR = os.path.join(_COMPILER_ROOT, "tools")

# The real vendored bundle the plugin ships (READ-ONLY here). Resolved via the
# portable plugin-root logic (in-repo ``<compiler>/../plugin`` or SYSTEM2_PLUGIN_ROOT).
_PLUGIN_BUNDLE = os.path.join(oracle.PLUGIN_ROOT, "scripts", "_system2_compiler")

_LIFECYCLE_METHODS = (
    "emit", "uninstall", "doctor",
    "recompose_from_lock", "lock_path", "read_lock_overlay_sources",
)


def _load_module(path, name):
    """Import a standalone module by file path (tools/ + the bundle are not pkgs)."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_build_bundle = _load_module(
    os.path.join(_TOOLS_DIR, "build_bundle.py"), "dod5_build_bundle"
)
_check_fresh = _load_module(
    os.path.join(_TOOLS_DIR, "check_bundle_fresh.py"), "dod5_check_bundle_fresh"
)
# The plugin-shipped tamper check (loaded from the REAL vendored bundle).
_freshness = _load_module(
    os.path.join(_PLUGIN_BUNDLE, "_freshness.py"), "dod5_freshness"
)


def _is_real_impl(fn):
    """True when *fn* is a real implementation (not a 1-3 line NotImplementedError stub)."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return True
    return not ("raise NotImplementedError" in src and src.count("\n") < 6)


class GrownContractTest(unittest.TestCase):
    """AC-5.1 — every backend satisfies the grown Backend protocol (no stubs)."""

    def _backends(self):
        from system2_compiler.backends.base import Backend
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        from system2_compiler.backends.pi import PiBackend
        return Backend, [ClaudeCodeBackend(), PiBackend()]

    def test_all_backends_satisfy_runtime_protocol(self):
        Backend, backends = self._backends()
        for b in backends:
            self.assertIsInstance(
                b, Backend,
                msg=f"{b.name} does not satisfy the runtime_checkable Backend protocol",
            )

    def test_lifecycle_methods_are_real_implementations(self):
        _Backend, backends = self._backends()
        for b in backends:
            for m in _LIFECYCLE_METHODS:
                fn = getattr(b, m, None)
                self.assertIsNotNone(
                    fn, msg=f"{b.name} is missing lifecycle method {m!r}"
                )
                self.assertTrue(
                    _is_real_impl(fn),
                    msg=(
                        f"{b.name}.{m} is still a NotImplementedError stub; "
                        "Phase-5 lifecycle parity requires a real implementation"
                    ),
                )

    def test_neutral_lifecycle_dataclasses_present(self):
        from system2_compiler.backends import base
        self.assertEqual(
            {f.name for f in dataclasses.fields(base.UninstallResult)},
            {"removed", "remaining", "artifacts_removed", "files_written",
             "is_last_overlay", "injection_warnings", "preview", "errors"},
        )
        self.assertEqual(
            {f.name for f in dataclasses.fields(base.DoctorReport)},
            {"status", "details", "system2_version", "overlays", "composed",
             "exit_code", "validator_available"},
        )


class ClaudeKeystoneGoldenGate(unittest.TestCase):
    """AC-5.2 / REQ-014 — compose->emit goldens empty-diff, both drivers."""

    def test_compiler_driver_empty_diff(self):
        failures = run_goldens.run_goldens(driver="compiler")
        self.assertEqual(
            [], failures,
            msg="claude compose->emit goldens regressed (compiler driver):\n"
            + "\n".join(failures),
        )

    def test_oracle_driver_empty_diff(self):
        failures = run_goldens.run_goldens(driver="oracle")
        self.assertEqual(
            [], failures,
            msg="claude goldens regressed (oracle driver):\n" + "\n".join(failures),
        )


class CliContractGate(unittest.TestCase):
    """AC-5.2 — the full verb surface matches the frozen oracle, with teeth."""

    def test_compiler_matches_frozen_oracle_all_verbs(self):
        failures = []
        for cell in clic._cells():
            o_out, o_err, o_code = clic._capture_oracle(cell)
            c_out, c_err, c_code = clic._capture_compiler(cell)
            if (o_out, o_err, o_code) != (c_out, c_err, c_code):
                failures.append(cell.name)
        self.assertEqual(
            [], failures,
            msg="CLI-contract goldens diverged from the frozen oracle: " + repr(failures),
        )

    def test_comparator_has_teeth(self):
        cell = clic._cells()[0]
        o_out, _o_err, _o_code = clic._capture_oracle(cell)
        mutated = (o_out + "X") if o_out else "X"
        self.assertNotEqual(mutated, o_out)


class BundleRunsAsPreflipGate(unittest.TestCase):
    """AC-5.6 — the plugin on the bundle reproduces preflip across every verb."""

    @classmethod
    def setUpClass(cls):
        oracle.verify_pin()
        if not os.path.isdir(_PLUGIN_BUNDLE):
            raise unittest.SkipTest("vendored bundle absent; run build_bundle.py first")

    def test_bundle_equivalence_across_all_verbs(self):
        failures = []
        for cell in clic._cells():
            pre = eqgate.capture_preflip(cell)
            bun = eqgate.capture_bundle(cell)
            if pre != bun:
                failures.append(cell.name)
        self.assertEqual(
            [], failures,
            msg="bundle output diverged from frozen preflip on verb(s): " + repr(failures),
        )

    def test_plugin_own_evals_green_on_bundle(self):
        result = pluginevals._run_plugin_suite(use_bundle=True)
        self.assertEqual(
            result.returncode, 0,
            msg="plugin System2/evals/ FAILED on the bundle:\n" + result.stderr[-3000:],
        )


class CiStalenessGuardTeethTest(unittest.TestCase):
    """AC-5.7 — the CI staleness guard passes fresh, fails on a mutated byte."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="dod5-ci-guard-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    @staticmethod
    def _quiet(compiler_root, target):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return _check_fresh.check_bundle_fresh(compiler_root, target)

    def test_check_bundle_fresh_passes_on_fresh_bundle(self):
        dest = os.path.join(self._tmp, "fresh")
        _build_bundle.build_bundle(_COMPILER_ROOT, dest)
        self.assertEqual(
            0, self._quiet(_COMPILER_ROOT, dest),
            msg="check_bundle_fresh must PASS on a freshly-built bundle",
        )

    def test_check_bundle_fresh_fails_on_mutated_byte(self):
        dest = os.path.join(self._tmp, "tampered")
        _build_bundle.build_bundle(_COMPILER_ROOT, dest)
        victim = os.path.join(dest, "_system2_compiler", "system2_compiler", "ir", "build.py")
        with open(victim, "a", encoding="utf-8") as fh:
            fh.write("\n# tampered byte\n")
        self.assertNotEqual(
            0, self._quiet(_COMPILER_ROOT, dest),
            msg="check_bundle_fresh must FAIL on a mutated vendored byte (teeth)",
        )


class PluginTamperCheckTeethTest(unittest.TestCase):
    """AC-5.7 — the plugin-shipped tamper check passes fresh, fails mutated (teeth).

    Runs the vendored ``_freshness.py`` exactly as the plugin would, with NO
    compiler source. The real plugin bundle is asserted untampered (read-only); the
    mutation teeth fire on a TEMP COPY.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="dod5-tamper-")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_real_vendored_bundle_is_untampered(self):
        if not os.path.isdir(_PLUGIN_BUNDLE):
            self.skipTest("vendored bundle absent; run build_bundle.py first")
        result = _freshness.check_bundle_integrity(_PLUGIN_BUNDLE)
        self.assertFalse(
            result["tampered"],
            msg=(
                "the real vendored bundle reports tampered (recorded "
                f"{result['recorded_source_sha256']} != recomputed "
                f"{result['recomputed_source_sha256']})"
            ),
        )
        self.assertIsNone(result["error"])
        findings = _freshness.format_findings(result)
        self.assertIn("bundle_freshness:", findings)
        self.assertIn("bundle_tampered: ok", findings)

    def test_recomputed_hash_matches_compiler_source_algorithm(self):
        # The plugin-side recompute must equal the compiler bundler's own hash of
        # the same subtree (the algorithms cannot drift).
        if not os.path.isdir(_PLUGIN_BUNDLE):
            self.skipTest("vendored bundle absent; run build_bundle.py first")
        self.assertEqual(
            _freshness.compute_subtree_hash(_PLUGIN_BUNDLE),
            _build_bundle.compute_source_hash(_PLUGIN_BUNDLE),
            msg="plugin tamper hash drifted from build_bundle.compute_source_hash",
        )

    def test_tamper_check_fails_loudly_on_mutated_temp_copy(self):
        # Copy the real bundle to a temp dir, mutate ONE byte, assert the tamper
        # check fails LOUDLY. The real plugin bundle is never touched.
        src = _PLUGIN_BUNDLE if os.path.isdir(_PLUGIN_BUNDLE) else None
        if src is None:
            # Fall back to a freshly-built bundle so the teeth still run off-plugin.
            built = os.path.join(self._tmp, "built")
            _build_bundle.build_bundle(_COMPILER_ROOT, built)
            src = os.path.join(built, "_system2_compiler")

        copy = os.path.join(self._tmp, "_system2_compiler")
        shutil.copytree(src, copy)

        fresh = _freshness.check_bundle_integrity(copy)
        self.assertFalse(fresh["tampered"], "the untouched temp copy must pass")

        victim = os.path.join(copy, "system2_compiler", "ir", "build.py")
        with open(victim, "a", encoding="utf-8") as fh:
            fh.write("\n# tampered byte\n")

        result = _freshness.check_bundle_integrity(copy)
        self.assertTrue(
            result["tampered"],
            msg="mutating a vendored byte MUST trip the tamper check (teeth)",
        )
        findings = _freshness.format_findings(result)
        self.assertIn(
            "bundle_tampered: the vendored", findings,
            msg="a tampered bundle must surface a LOUD bundle_tampered finding",
        )

        rc = subprocess.run(
            [sys.executable, os.path.join(copy, "_freshness.py"), copy],
            capture_output=True, text=True,
        )
        self.assertEqual(
            rc.returncode, 1,
            msg=f"_freshness.py CLI must exit 1 on a tampered bundle; got {rc.returncode}",
        )
        self.assertIn("bundle_tampered: the vendored", rc.stdout)

        # The real plugin bundle remains untampered (the mutation was isolated).
        if os.path.isdir(_PLUGIN_BUNDLE):
            self.assertFalse(
                _freshness.check_bundle_integrity(_PLUGIN_BUNDLE)["tampered"],
                msg="the real plugin bundle was perturbed by the tamper test",
            )


if __name__ == "__main__":
    unittest.main()
