"""Thin shim over the pre-flip engine OR the vendored compiler bundle.

Phase 5 convergence flip. This file replaces the original ``composer.py`` engine
with a thin shim. The original engine is preserved VERBATIM as
``composer.py.preflip`` (the immutable equivalence oracle + one-commit backout).

Two facets:

1. **As an imported module** (``import composer``): this shim executes the
   ``composer.py.preflip`` source into ITS OWN namespace, so every public/internal
   symbol the engine exposed (``compose``, ``_activate_profile``, ``_write_outputs``,
   ``_run_profile_mutation``, ``main``, ...) is present and byte-for-byte the
   pre-flip behavior. The plugin's own ``System2/evals/`` suite imports those
   symbols directly and is unaffected by the flip.

2. **As the CLI** (``python3 composer.py ...``): the ``__main__`` guard routes the
   process to either the vendored bundle or the pre-flip engine, controlled by the
   ``SYSTEM2_USE_BUNDLE`` switch (see ``_use_bundle`` below).

Stdlib-only (the bundle is stdlib-only by construction; this shim adds only
``os``/``sys``).

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

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PREFLIP_PATH = os.path.join(_SCRIPT_DIR, "composer.py.preflip")
_BUNDLE_DIR = os.path.join(_SCRIPT_DIR, "_system2_compiler")


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
    import plugin_adapter  # noqa: E402  (vendored entry; --target pinned claude-code)

    return plugin_adapter.main_composer_contract(argv)


# --- Module facet: expose the frozen pre-flip engine's full symbol surface. -----
# Executing the preflip source into THIS module's globals makes ``import composer``
# byte-for-byte the pre-flip engine (its ``main``, ``compose``, ``_activate_profile``,
# etc.). ``__name__`` is "composer" here (NOT "__main__"), so the preflip body's own
# ``if __name__ == "__main__": main()`` guard does NOT fire on import.
with open(_PREFLIP_PATH, "r", encoding="utf-8") as _fh:
    _PREFLIP_SOURCE = _fh.read()

exec(compile(_PREFLIP_SOURCE, _PREFLIP_PATH, "exec"), globals())


if __name__ == "__main__":
    if _use_bundle():
        # Default: delegate to the vendored bundle (proven byte-identical to preflip).
        raise SystemExit(_run_bundle(sys.argv[1:]))
    # Escape hatch (SYSTEM2_USE_BUNDLE=0): run the frozen pre-flip engine.
    raise SystemExit(main())  # noqa: F821  (main is bound by the exec'd preflip body)
