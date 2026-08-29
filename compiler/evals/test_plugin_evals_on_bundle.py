"""Plugin-evals-on-bundle gate (convergence implementation, HARD).

Run the plugin's OWN structural + behavioral suite (``System2/evals/test_*.py``)
against the FLIPPED plugin — the shim delegating to the vendored bundle
(``SYSTEM2_USE_BUNDLE=1``) — and assert it stays green. The flip is not done until
the plugin's own suite passes on the bundle (AC-5.6).

``System2/evals/`` is run READ-ONLY as a subprocess (it is never imported or
modified here). A hermetic temp HOME isolates the run from the real ``~/.system2``.
Both switch states are exercised: ON (the bundle, the flip leg) and OFF (the frozen
preflip engine, the baseline) — both must be green.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from evals import oracle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Portable plugin-repo resolution (sibling layout or SYSTEM2_PLUGIN_ROOT override).
PLUGIN_REPO = oracle.PLUGIN_REPO_ROOT
PLUGIN_EVALS = os.path.join(PLUGIN_REPO, "evals")


def _run_plugin_suite(use_bundle):
    """Run ``System2/evals/`` as a subprocess; return the CompletedProcess.

    *use_bundle* toggles ``SYSTEM2_USE_BUNDLE`` (the shim's switch). Runs under a
    hermetic temp HOME so the plugin's profile store resolves into a throwaway dir.
    """
    home = tempfile.mkdtemp(prefix="plugin-evals-home-")
    env = {"HOME": home}
    for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if use_bundle:
        env["SYSTEM2_USE_BUNDLE"] = "1"
    try:
        return subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "evals", "-p", "test_*.py"],
            capture_output=True, text=True, env=env, cwd=PLUGIN_REPO,
        )
    finally:
        import shutil
        shutil.rmtree(home, ignore_errors=True)


class PluginEvalsOnBundleTest(unittest.TestCase):
    """Hard gate: the plugin's own suite is green on the flipped (bundle) plugin."""

    @classmethod
    def setUpClass(cls):
        oracle.verify_pin()
        if not os.path.isdir(os.path.join(PLUGIN_REPO, "plugin", "scripts", "_system2_compiler")):
            raise unittest.SkipTest("vendored bundle absent; run build_bundle.py first")

    def test_plugin_suite_green_on_bundle(self):
        result = _run_plugin_suite(use_bundle=True)
        self.assertEqual(
            result.returncode, 0,
            msg=f"plugin System2/evals/ FAILED under SYSTEM2_USE_BUNDLE=1:\n{result.stderr[-4000:]}",
        )

    def test_plugin_suite_green_on_preflip_baseline(self):
        result = _run_plugin_suite(use_bundle=False)
        self.assertEqual(
            result.returncode, 0,
            msg=f"plugin System2/evals/ FAILED under the frozen engine (switch OFF):\n{result.stderr[-4000:]}",
        )


if __name__ == "__main__":
    unittest.main()
