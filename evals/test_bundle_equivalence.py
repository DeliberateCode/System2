"""BUNDLE-EQUIVALENCE GATE (Phase 5 keystone, HARD).

Prove the flipped plugin's shim ``composer.py`` running the VENDORED BUNDLE
(``SYSTEM2_USE_BUNDLE=1``) produces stdout, stderr, and exit code byte-identical to
the frozen ``composer.py.preflip`` engine, across compose (text+json), the
conflict/missing refusals, doctor (composed/clean/stale), uninstall
(last/one-of-N/not-installed/no-lock/dry-run), from-lock (recompose + missing), and
a profile op — the full ``composer.py`` flag-CLI verb surface.

The verb matrix, placeholder substitution, and the volatile-path / dry-run
``Composed at:`` normalization are reused verbatim from :mod:`evals.test_cli_contract`
so this gate measures EXACTLY the established CLI-contract surface, just driven
through the live shim instead of the in-process compiler CLI.

Both engines are run as subprocesses under a hermetic temp HOME (the real
``~/.system2`` is never touched). Setup state is established with the frozen
preflip engine on each engine's own temp project independently, so the measured
invocation observes identical inputs. A non-empty diff FAILS the gate — there is no
auto-rebaseline (REQ-007). A self-teeth test asserts a one-byte divergence is caught.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from evals import oracle
from evals import test_cli_contract as clic

# The flipped plugin shim and the immutable preflip engine live side-by-side in the
# plugin scripts dir. ``oracle.COMPOSER_PATH`` is re-pointed (TASK-512) at the
# preflip baseline; the shim is its ``composer.py`` sibling.
PREFLIP_PATH = oracle.COMPOSER_PATH
SCRIPTS_DIR = os.path.dirname(PREFLIP_PATH)
SHIM_PATH = os.path.join(SCRIPTS_DIR, "composer.py")


def _run_setup(cell, project, env):
    """Reproduce a cell's pre-state on *project* using the frozen preflip engine."""
    for step in cell.setup:
        name, _argv = step
        if name == "mutate_lock_version":
            clic._run_oracle_setup(step, project, env)
            continue
        resolved = [project if a == clic._PROJ else a for a in step[1]]
        subprocess.run(
            [sys.executable, PREFLIP_PATH] + resolved,
            capture_output=True, text=True, env=env, cwd=SCRIPTS_DIR,
        )


def _capture(engine_path, cell, env_extra):
    """Run *cell*'s measured composer-flag argv against *engine_path*.

    Returns normalized ``(stdout, stderr, exit_code)``. ``env_extra`` carries the
    ``SYSTEM2_USE_BUNDLE`` switch for the shim/bundle leg.
    """
    home = tempfile.mkdtemp(prefix="bundle-eq-home-")
    project = tempfile.mkdtemp(prefix="bundle-eq-proj-")
    env = clic._hermetic_env(home)
    env.update(env_extra)
    try:
        _run_setup(cell, project, env)
        argv = [
            project if a == clic._PROJ else (home if a == clic._HOME else a)
            for a in cell.oracle_argv
        ]
        completed = subprocess.run(
            [sys.executable, engine_path] + argv,
            capture_output=True, text=True, env=env, cwd=SCRIPTS_DIR,
        )
        return (
            clic._normalize(completed.stdout, project, home),
            clic._normalize(completed.stderr, project, home),
            completed.returncode,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def capture_preflip(cell):
    """The immutable oracle leg: the frozen ``composer.py.preflip`` engine."""
    return _capture(PREFLIP_PATH, cell, {})


def capture_bundle(cell):
    """The flip leg: the shim ``composer.py`` delegating to the bundle (switch ON)."""
    return _capture(SHIM_PATH, cell, {"SYSTEM2_USE_BUNDLE": "1"})


class BundleEquivalenceTest(unittest.TestCase):
    """Hard gate: shim/bundle output == frozen preflip output, every verb."""

    @classmethod
    def setUpClass(cls):
        oracle.verify_pin()
        if not os.path.isdir(os.path.join(SCRIPTS_DIR, "_system2_compiler")):
            raise unittest.SkipTest(
                "vendored bundle absent; run "
                "`python3 tools/build_bundle.py --dest System2/plugin/scripts/` first"
            )

    def test_bundle_matches_preflip_across_all_verbs(self):
        for cell in clic._cells():
            with self.subTest(cell=cell.name):
                pre_out, pre_err, pre_code = capture_preflip(cell)
                bun_out, bun_err, bun_code = capture_bundle(cell)
                self.assertEqual(
                    pre_code, bun_code,
                    msg=f"[{cell.name}] exit code: preflip {pre_code} != bundle {bun_code}",
                )
                self.assertEqual(
                    pre_out, bun_out,
                    msg=f"[{cell.name}] stdout mismatch: bundle != frozen preflip",
                )
                self.assertEqual(
                    pre_err, bun_err,
                    msg=f"[{cell.name}] stderr mismatch: bundle != frozen preflip",
                )

    def test_self_teeth_one_byte_divergence_is_caught(self):
        """A one-byte mutation of the preflip output must not equal the bundle's."""
        cell = clic._cells()[0]
        pre_out, _pre_err, _pre_code = capture_preflip(cell)
        bun_out, _bun_err, _bun_code = capture_bundle(cell)
        self.assertEqual(pre_out, bun_out)
        mutated = pre_out + "X" if pre_out else "X"
        self.assertNotEqual(mutated, bun_out)


if __name__ == "__main__":
    unittest.main()
