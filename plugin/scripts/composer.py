"""Thin shim over the pre-flip engine OR the vendored compiler bundle.

Phase 5 convergence flip. This file replaces the original ``composer.py`` engine
with a thin shim. The original engine is preserved VERBATIM as
``composer.py.preflip`` (the immutable equivalence oracle + one-commit backout).

Two facets:

1. **As an imported module** (``import composer``): this shim loads
   ``composer.py.preflip`` as a *module* (via ``importlib``) and re-exports its
   full public/internal symbol surface (``compose``, ``main``, ``_uninstall``,
   ``drift_check``, ``_activate_profile``, ``_write_outputs``,
   ``_run_profile_mutation``, ...). Because preflip is loaded as a module its
   ``__name__`` is ``"_composer_preflip"`` (NOT ``"__main__"``), so the preflip
   body's trailing ``if __name__ == "__main__": main()`` does NOT fire on load.
   The plugin's own ``System2/evals/`` suite imports those symbols directly and is
   unaffected by the flip.

2. **As the CLI** (``python3 composer.py ...``): the ``__main__`` guard routes the
   process to either the vendored bundle or the pre-flip engine, controlled by the
   ``SYSTEM2_USE_BUNDLE`` switch (see ``_use_bundle`` below).

Stdlib-only (the bundle is stdlib-only by construction; this shim adds only
``importlib``/``os``/``sys``).

--------------------------------------------------------------------------------
SWITCH (this revision): DEFAULT ON — the bundle is the default engine.
    (unset / anything except "0") -> CLI delegates to the vendored bundle
    SYSTEM2_USE_BUNDLE=0  -> ESCAPE HATCH: CLI runs the frozen composer.py.preflip
                            engine (the pre-flip behavior), for in-place A/B checks.
The flip is proven byte-identical: the bundle-equivalence gate
(``System2-Compiler/evals/test_bundle_equivalence.py``) and the plugin's own
``System2/evals/`` suite (``test_plugin_evals_on_bundle.py``) both pass with the
bundle as the default — so the default engine reproduces preflip byte-for-byte.
--------------------------------------------------------------------------------

ONE-COMMIT BACKOUT (returns the plugin to its frozen engine, zero residue):
    cp composer.py.preflip composer.py && rm -rf _system2_compiler/
"""

import importlib.machinery
import importlib.util
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PREFLIP_PATH = os.path.join(_SCRIPT_DIR, "composer.py.preflip")
_BUNDLE_DIR = os.path.join(_SCRIPT_DIR, "_system2_compiler")


def _load_preflip():
    """Load ``composer.py.preflip`` as a module (its ``main()`` does NOT fire).

    The module name is ``_composer_preflip`` — NOT ``"__main__"`` — so the preflip
    body's trailing ``if __name__ == "__main__": main()`` guard stays inert. The
    full module is returned so its public + internal API can be re-exported.
    """
    # The frozen oracle's filename ends in ``.preflip`` (not ``.py``), so the
    # loader must be named explicitly — importlib cannot infer it from the suffix.
    loader = importlib.machinery.SourceFileLoader(
        "_composer_preflip", _PREFLIP_PATH,
    )
    spec = importlib.util.spec_from_loader("_composer_preflip", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_composer_preflip", module)
    spec.loader.exec_module(module)
    return module


def _use_bundle() -> bool:
    """Return True when the CLI should delegate to the vendored bundle.

    This revision is DEFAULT ON: the bundle is the default engine. The only way to
    fall back to the frozen pre-flip engine is the explicit escape hatch
    ``SYSTEM2_USE_BUNDLE=0``.
    """
    return os.environ.get("SYSTEM2_USE_BUNDLE") != "0"


def _run_bundle(argv) -> int:
    """Delegate the composer flag CLI to the vendored bundle's adapter."""
    if _BUNDLE_DIR not in sys.path:
        sys.path.insert(0, _BUNDLE_DIR)
    # The bundle nests the product under ``_system2_compiler/system2_compiler/`` so
    # the vendored package keeps its installed import name; with _BUNDLE_DIR on the
    # path, ``system2_compiler`` is importable as a top-level package.
    from system2_compiler import plugin_adapter  # noqa: E402  (entry; --target pinned claude-code)

    return plugin_adapter.main_composer_contract(argv)


# --- Module facet: re-export the frozen pre-flip engine's full symbol surface. --
# Loading preflip as a module (``__name__ == "_composer_preflip"``) keeps its own
# ``if __name__ == "__main__": main()`` guard inert, then we copy every public and
# internal name into THIS module so ``import composer`` is byte-for-byte the
# pre-flip engine (``compose``, ``main``, ``_activate_profile``, ...).
_preflip = _load_preflip()
for _name, _value in vars(_preflip).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
del _name, _value


if __name__ == "__main__":
    if _use_bundle():
        # Default: delegate to the vendored bundle (proven byte-identical to preflip).
        raise SystemExit(_run_bundle(sys.argv[1:]))
    # Escape hatch (SYSTEM2_USE_BUNDLE=0): run the frozen pre-flip engine.
    raise SystemExit(_preflip.main())
