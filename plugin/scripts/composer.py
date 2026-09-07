"""Compatibility shim for the vendored and pre-flip composer engines."""

import importlib.machinery
import importlib.util
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PREFLIP_PATH = os.path.join(_SCRIPT_DIR, "composer.py.preflip")
_BUNDLE_DIR = os.path.join(_SCRIPT_DIR, "_system2_compiler")


def _load_preflip():
    """Load ``composer.py.preflip`` as a module (its ``main()`` does NOT fire)."""
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
    """Return True when the CLI should delegate to the vendored bundle."""
    return os.environ.get("SYSTEM2_USE_BUNDLE") != "0"


def _run_bundle(argv) -> int:
    """Delegate the composer flag CLI to the vendored bundle's adapter."""
    if _BUNDLE_DIR not in sys.path:
        sys.path.insert(0, _BUNDLE_DIR)
    # Add the bundle root so its system2_compiler package is importable.
    from system2_compiler import plugin_adapter  # noqa: E402  (entry; --target pinned claude-code)

    return plugin_adapter.main_composer_contract(argv)


# Re-export pre-flip symbols for callers that import composer as a module.
_preflip = _load_preflip()
for _name, _value in vars(_preflip).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
del _name, _value


if __name__ == "__main__":
    if _use_bundle():
        # Use the vendored bundle by default.
        raise SystemExit(_run_bundle(sys.argv[1:]))
    # SYSTEM2_USE_BUNDLE=0 selects the pre-flip engine.
    raise SystemExit(_preflip.main())
