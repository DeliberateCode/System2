"""MODULE-IMPORT FACET GATE (Codex second-opinion review on PR #10).

``plugin/scripts/composer.py``'s own docstring documents a real, deliberately
accepted architectural gap (tracked debt, not fixed -- see the docstring's
"RETIREMENT TRIGGER" note): the shim's module-level re-export
(``for _name, _value in vars(_preflip).items(): ...``) is UNCONDITIONAL, so any
in-process ``from composer import compose`` (this is how the plugin's own
``evals/run_evals.py`` and several ``evals/test_*.py`` files consume it) always
gets ``composer.py.preflip``, never the vendored bundle, regardless of
``SYSTEM2_USE_BUNDLE``. Only the ``__main__``/CLI-subprocess path actually flips
(proven by :mod:`test_bundle_equivalence` and :mod:`test_plugin_evals_on_bundle`,
both of which drive the CLI as a subprocess and never assert which engine an
in-process import resolves to).

This module closes that specific, previously-untested gap: it PINS the current,
accepted behavior as a real, machine-checked fact, across all three
``SYSTEM2_USE_BUNDLE`` states, so future work can't silently regress it further (or
silently "fix" it without this test being updated to say so). It does not fix the
underlying architecture -- see the retirement trigger for that.
"""

import importlib
import os
import sys
import unittest

from evals import oracle

_SCRIPTS_DIR = os.path.dirname(oracle.COMPOSER_PATH)

_FACET_SYMBOLS = ("compose", "main", "drift_check", "_write_outputs", "_uninstall")


def _check_facet_matches_preflip(test_case, shim, preflip):
    """The identity check itself, as a plain function taking an explicit
    ``test_case`` -- shared by the real assertion and the mutation-teeth self-test
    below, so the teeth test drives this SAME code rather than a reimplementation
    of it (a prior version compared two unrelated function objects directly,
    which is true of any two distinct functions and proves nothing)."""
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
        """Import composer.py fresh (never cached) under the given env, and
        return it alongside a fresh, independent import of composer.py.preflip
        loaded the same way the shim loads it (so identity comparison is
        apples-to-apples, not comparing against the shim's own cached copy)."""
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
        # "_composer_preflip" (see composer.py's docstring) -- read it back via
        # sys.modules rather than re-importing composer.py.preflip by path, so this
        # is checking the SAME object the shim actually re-exported, not a
        # coincidentally-equal-looking second copy.
        preflip = sys.modules.get("_composer_preflip")
        self.assertIsNotNone(
            preflip, "composer.py did not register '_composer_preflip' in sys.modules"
        )
        _check_facet_matches_preflip(self, shim, preflip)

    def test_guard_trips_if_the_module_facet_stopped_reexporting(self):
        """Mutation self-test (teeth): a shim that DIDN'T re-export from preflip
        (e.g. defined its own no-op ``compose``) must fail the REAL identity check
        above -- not a reimplementation of it.

        Codex second-opinion review, round 4: the prior version of this test
        asserted ``_FakeModule.compose is not preflip.compose`` directly, which is
        true of ANY two distinct function objects and would pass even if
        ``_check_facet_matches_preflip`` itself were broken (e.g. always returning
        without asserting anything) -- it never exercised the production check at
        all. ``subTest`` swallows its ``AssertionError`` locally rather than
        raising it synchronously (verified directly: a failing ``assertIs`` inside
        ``with self.subTest(...)`` does not propagate out of the ``with`` block),
        so wrapping the call in ``assertRaises`` would not work either -- it would
        see no exception and fail with a confusing message instead of a clear one.
        Driving the check through a throwaway ``TestResult`` is the standard way to
        assert that subTest-using code actually fails, and lets this test inspect
        the real recorded failure.
        """
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
