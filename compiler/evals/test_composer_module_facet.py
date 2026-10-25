"""Pin the composer shim's in-process module-import behavior."""

import importlib
import os
import sys
import unittest

from evals import oracle

_SCRIPTS_DIR = os.path.dirname(oracle.COMPOSER_PATH)

_FACET_SYMBOLS = ("compose", "main", "drift_check", "_write_outputs", "_uninstall")


def _check_facet_matches_preflip(test_case, shim, preflip):
    """Assert that selected shim symbols are the pre-flip objects."""
    for name in _FACET_SYMBOLS:
        with test_case.subTest(symbol=name):
            test_case.assertIs(
                getattr(shim, name),
                getattr(preflip, name),
                f"composer.{name} is not composer.py.preflip's {name} object -- "
                f"the module facet's unconditional re-export changed behavior",
            )


class ModuleImportFacetTest(unittest.TestCase):
    """The in-process ``import composer`` facet always resolves to
    ``composer.py.preflip``, unconditionally -- never the vendored bundle."""

    @classmethod
    def setUpClass(cls):
        if _SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, _SCRIPTS_DIR)

    def _fresh_import(self, use_bundle_env):
        """Import composer.py without using a cached module."""
        for name in ("composer", "_composer_preflip"):
            sys.modules.pop(name, None)
        env_backup = os.environ.get("SYSTEM2_USE_BUNDLE")
        try:
            if use_bundle_env is None:
                os.environ.pop("SYSTEM2_USE_BUNDLE", None)
            else:
                os.environ["SYSTEM2_USE_BUNDLE"] = use_bundle_env
            shim = importlib.import_module("composer")
        finally:
            if env_backup is None:
                os.environ.pop("SYSTEM2_USE_BUNDLE", None)
            else:
                os.environ["SYSTEM2_USE_BUNDLE"] = env_backup
        return shim

    def test_in_process_import_is_preflip_unset(self):
        self._assert_is_preflip(self._fresh_import(None))

    def test_in_process_import_is_preflip_bundle_on(self):
        self._assert_is_preflip(self._fresh_import("1"))

    def test_in_process_import_is_preflip_bundle_off(self):
        # SYSTEM2_USE_BUNDLE=0 is documented as the CLI-subprocess escape hatch;
        # the module facet is unaffected by it either way -- prove that too.
        self._assert_is_preflip(self._fresh_import("0"))

    def _assert_is_preflip(self, shim):
        # The shim's own module-loading mechanism names the loaded preflip module
        preflip = sys.modules.get("_composer_preflip")
        self.assertIsNotNone(
            preflip, "composer.py did not register '_composer_preflip' in sys.modules"
        )
        _check_facet_matches_preflip(self, shim, preflip)

    def test_guard_trips_if_the_module_facet_stopped_reexporting(self):
        """Mutation self-test (teeth): a shim that DIDN'T re-export from preflip (e.g."""
        self._fresh_import(None)
        preflip = sys.modules.get("_composer_preflip")
        self.assertIsNotNone(preflip)

        class _FakeShim:
            def compose(self):
                pass

            def main(self):
                pass

            def drift_check(self):
                pass

            def _write_outputs(self):
                pass

            def _uninstall(self):
                pass

        class _Probe(unittest.TestCase):
            def runTest(self):
                _check_facet_matches_preflip(self, _FakeShim, preflip)

        result = unittest.TestResult()
        _Probe().run(result)
        self.assertTrue(
            result.failures or result.errors,
            "_check_facet_matches_preflip did not fail against a shim that does "
            "NOT re-export from preflip -- the mutation-teeth check has no teeth",
        )


if __name__ == "__main__":
    unittest.main()
