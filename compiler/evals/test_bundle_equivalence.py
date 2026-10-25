"""Bundle-equivalence regression tests."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from evals import oracle
from evals import test_cli_contract as clic

# The flipped plugin shim and the immutable preflip engine live side-by-side in the plugin scripts dir.
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
    """Run *cell*'s measured composer-flag argv against *engine_path*."""
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
        capture_started = time.time()
        completed = subprocess.run(
            [sys.executable, engine_path] + argv,
            capture_output=True, text=True, env=env, cwd=SCRIPTS_DIR,
        )
        capture_finished = time.time()
        stdout, stderr = clic._normalize_capture(
            completed.stdout, completed.stderr, project, home,
            capture_started, capture_finished,
        )
        return stdout, stderr, completed.returncode
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
