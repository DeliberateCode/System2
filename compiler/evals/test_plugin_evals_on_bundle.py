"""Run plugin evaluations against the vendored bundle."""

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
    """Run ``System2/evals/`` as a subprocess; return the CompletedProcess."""
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
