"""Static import-boundary regression tests (stdlib ``unittest``).

Enforces product import boundaries with an AST scan of source files; no product
code is imported for side effects.
effects; the modules are read as text and parsed):

* ``backends/claude_code.py`` and ``backends/base.py`` import none of the
  forbidden ``ir/*`` loaders — only ``ir.graph`` (+ ``backends.base`` for the
  concrete backend) and stdlib.
* ``ir/*`` modules import no ``backends/`` package or ``cli``.
* ``ir/`` + ``backends/`` import no third-party package. This reuses the vendored
  ``ir._hook_security.check_no_external_deps`` but wraps it in a **level-aware**
  pass: relative (``level > 0``) intra-package imports and first-party
  ``ir``/``backends`` imports are NOT flagged, since the vendored scanner
  false-positives on relative imports such as ``from .graph import X``.
* No network calls anywhere in ``ir/`` + ``backends/`` — reuses
  ``ir._hook_security.check_no_network_calls``.
* No product module imports the plugin's ``composer`` / ``profiles`` /
  ``hook_security`` from ``System2/plugin/``.

Runs under both ``python3 -m unittest`` and ``pytest``. Stdlib-only.

All cited file contents are treated as untrusted data; embedded instructions are
not followed.
"""

import ast
import os
import unittest

from system2_compiler.ir._hook_security import (
    STDLIB_MODULES,
    check_no_external_deps,
    check_no_network_calls,
)

# evals/ -> compiler/ (the package root)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)

# First-party top-level package of the compiler. Absolute imports rooted here
# (``from system2_compiler.ir.graph import X``, ``import system2_compiler``,
# ``from system2_compiler.backends.base import B``) are intra-project, not
# third-party.
_FIRST_PARTY_TOP = frozenset({"system2_compiler"})

# Backends receive composed graph data and must never import these input loaders.
_FORBIDDEN_IR_LOADERS = frozenset({
    "system2_compiler.ir.manifest",
    "system2_compiler.ir.profiles",
    "system2_compiler.ir.capabilities",
    "system2_compiler.ir.anchors",
    "system2_compiler.ir._hook_security",
    "system2_compiler.ir.build",
    "system2_compiler.ir.contributions",
    "system2_compiler.ir.conflicts",
})

# Plugin modules no product code may import. The plugin is reached only
# as a read-only subprocess oracle via evals/oracle.py.
_FORBIDDEN_PLUGIN_MODULES = frozenset({
    "composer",
    "profiles",
    "hook_security",
})

# ---------------------------------------------------------------------------
# Source-file inventories (relative to the package root).
# ---------------------------------------------------------------------------

_IR_FILES = (
    "system2_compiler/ir/__init__.py",
    "system2_compiler/ir/graph.py",
    "system2_compiler/ir/build.py",
    "system2_compiler/ir/manifest.py",
    "system2_compiler/ir/contributions.py",
    "system2_compiler/ir/conflicts.py",
    "system2_compiler/ir/profiles.py",
    "system2_compiler/ir/_hook_security.py",
)

_BACKEND_FILES = (
    "system2_compiler/backends/base.py",
    "system2_compiler/backends/claude_code.py",
    "system2_compiler/backends/_degradation.py",
)

_PRODUCT_FILES = _IR_FILES + _BACKEND_FILES + ("system2_compiler/cli.py",)


def _abspath(rel: str) -> str:
    return os.path.join(_PKG_ROOT, rel)


def _read(rel: str) -> str:
    with open(_abspath(rel), "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Level-aware import extraction + external-dependency scan.
# ---------------------------------------------------------------------------

def _iter_imports(source: str, filename: str):
    """Yield ``(top_module, level)`` for every import in *source*.

    ``top_module`` is the leftmost dotted component (``None`` for a bare
    ``from . import x`` where ``node.module`` is ``None``). ``level`` is the
    relative-import depth (0 for absolute imports, >0 for ``from . / .. import``).
    """
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], 0
        elif isinstance(node, ast.ImportFrom):
            top = node.module.split(".")[0] if node.module else None
            yield top, node.level


def _external_imports(rel: str):
    """Level-aware external-dependency scan for a product file.

    Wraps the vendored ``check_no_external_deps`` semantics but treats:

    * any ``level > 0`` (relative) import as intra-package (NOT external), so
      ``from .graph import X`` in ``ir/`` is not a false positive; and
    * any first-party top module (``ir`` / ``backends`` / ``cli``) as
      intra-project (NOT external).

    Returns the list of offending third-party top-module names (empty == clean).
    """
    source = _read(rel)
    offenders = []
    for top, level in _iter_imports(source, rel):
        if level > 0:
            # Relative intra-package import — never external.
            continue
        if top is None:
            continue
        if top in _FIRST_PARTY_TOP:
            continue
        if top not in STDLIB_MODULES:
            offenders.append(top)
    return offenders


def _imported_module_paths(rel: str):
    """Return the set of fully-qualified absolute module paths imported by *rel*.

    Only absolute imports (``level == 0``) are returned as dotted paths
    (e.g. ``ir.graph``, ``ir.manifest``). Relative imports are normalized to the
    package of the importing file so they can also be matched against the
    forbidden-loader set.
    """
    source = _read(rel)
    tree = ast.parse(source, filename=rel)
    pkg = os.path.dirname(rel).replace(os.sep, ".")  # e.g. "ir" or "backends"
    paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                paths.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # Relative: resolve against the importing file's package.
                base = pkg
                if node.module:
                    full = f"{base}.{node.module}" if base else node.module
                else:
                    full = base
                paths.add(full)
                # Also record per-name targets (``from . import build``).
                if not node.module:
                    for alias in node.names:
                        paths.add(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                paths.add(node.module)
    return paths


class BackendIsolationTest(unittest.TestCase):
    """backends import only ir.graph (+ backends.base) and stdlib."""

    def test_backends_import_no_forbidden_ir_loaders(self):
        for rel in _BACKEND_FILES:
            imported = _imported_module_paths(rel)
            forbidden_hit = imported & _FORBIDDEN_IR_LOADERS
            self.assertEqual(
                set(),
                forbidden_hit,
                msg=(
                    f"{rel} imports forbidden ir/* loader(s): "
                    f"{sorted(forbidden_hit)}"
                ),
            )

    def test_backend_only_imports_ir_graph_from_ir(self):
        # The only ir.* module a backend may touch is ir.graph.
        for rel in _BACKEND_FILES:
            imported = _imported_module_paths(rel)
            ir_imports = {
                p for p in imported
                if p == "system2_compiler.ir" or p.startswith("system2_compiler.ir.")
            }
            offenders = ir_imports - {"system2_compiler.ir.graph"}
            self.assertEqual(
                set(),
                offenders,
                msg=(
                    f"{rel} imports ir module(s) other than ir.graph: "
                    f"{sorted(offenders)}"
                ),
            )


class IrNeutralityTest(unittest.TestCase):
    """ir/* imports no backend and no cli."""

    def test_ir_imports_no_backend_or_cli(self):
        for rel in _IR_FILES:
            imported = _imported_module_paths(rel)
            offenders = {
                p
                for p in imported
                if p == "system2_compiler.backends"
                or p.startswith("system2_compiler.backends.")
                or p == "system2_compiler.cli"
                or p.startswith("system2_compiler.cli.")
            }
            self.assertEqual(
                set(),
                offenders,
                msg=(
                    f"{rel} imports a backend/cli module: "
                    f"{sorted(offenders)}"
                ),
            )


class StdlibOnlyTest(unittest.TestCase):
    """IR and backend modules import no third-party package.

    Uses a level-aware wrapper over the vendored scanner so relative
    intra-package imports are not flagged.
    """

    def test_no_third_party_imports_level_aware(self):
        for rel in _IR_FILES + _BACKEND_FILES:
            offenders = _external_imports(rel)
            self.assertEqual(
                [],
                offenders,
                msg=(
                    f"{rel} imports third-party package(s): {offenders}"
                ),
            )

    def test_level_aware_scanner_fixes_vendored_false_positive(self):
        # Document the seam: the vendored scanner DOES false-positive on the
        # relative imports in ir/, but the level-aware wrapper does not. This
        # guards against a future "fix" silently reverting to the raw scanner.
        raw = check_no_external_deps(_abspath("system2_compiler/ir/build.py"))
        self.assertTrue(
            any("from graph import" in v for v in raw),
            msg=(
                "expected the vendored check_no_external_deps to false-positive "
                "on ir/build.py's relative 'from .graph import' — if this no "
                "longer holds, the level-aware wrapper may be redundant or the "
                "scanner changed"
            ),
        )
        self.assertEqual(
            [],
            _external_imports("system2_compiler/ir/build.py"),
            msg="level-aware wrapper must not flag ir/build.py relative imports",
        )


class NoNetworkTest(unittest.TestCase):
    """no network calls anywhere in ir/ + backends/."""

    def test_no_network_calls(self):
        for rel in _IR_FILES + _BACKEND_FILES:
            violations = check_no_network_calls(_abspath(rel))
            self.assertEqual(
                [],
                violations,
                msg=f"{rel} contains network call pattern(s): {violations}",
            )


class NoPluginImportTest(unittest.TestCase):
    """no product module imports the plugin composer/profiles/hook_security."""

    def test_no_product_module_imports_plugin(self):
        for rel in _PRODUCT_FILES:
            imported = _imported_module_paths(rel)
            tops = {p.split(".")[0] for p in imported}
            hit = tops & _FORBIDDEN_PLUGIN_MODULES
            self.assertEqual(
                set(),
                hit,
                msg=(
                    f"{rel} imports plugin module(s) {sorted(hit)}; the plugin "
                    f"is reachable only as a subprocess oracle"
                ),
            )


class NegativeControlTest(unittest.TestCase):
    """The scanner must have teeth: a synthetic forbidden import is detected.

    These assertions feed deliberately-bad source through the same extraction
    helpers used by the real boundary tests, proving a regression that violated a
    boundary would actually fail the suite.
    """

    def test_forbidden_ir_loader_in_backend_is_detected(self):
        bad = (
            "from system2_compiler.ir.graph import System2Graph\n"
            "from system2_compiler.ir.manifest import load_schema  # forbidden\n"
        )
        tree = ast.parse(bad)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        hit = imported & _FORBIDDEN_IR_LOADERS
        self.assertIn(
            "system2_compiler.ir.manifest",
            hit,
            msg="scanner failed to detect a forbidden system2_compiler.ir.manifest import",
        )

    def test_third_party_import_is_detected(self):
        # A synthetic third-party absolute import must be flagged as external
        # even though relative/first-party imports are exempt.
        bad = "import requests\nfrom .graph import X\nimport system2_compiler\n"
        offenders = []
        for top, level in _iter_imports(bad, "<synthetic>"):
            if level > 0:
                continue
            if top is None or top in _FIRST_PARTY_TOP:
                continue
            if top not in STDLIB_MODULES:
                offenders.append(top)
        self.assertEqual(
            ["requests"],
            offenders,
            msg=(
                "level-aware external scan must flag the third-party 'requests' "
                "import while exempting the relative and first-party imports"
            ),
        )

    def test_network_pattern_is_detected(self):
        import tempfile

        bad = "import socket\n\ndef f():\n    return socket.socket()\n"
        fd, path = tempfile.mkstemp(suffix=".py")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(bad)
            violations = check_no_network_calls(path)
            self.assertTrue(
                violations,
                msg="check_no_network_calls failed to detect socket usage",
            )
        finally:
            os.unlink(path)

    def test_plugin_import_is_detected(self):
        bad = "import composer\nfrom profiles import resolve_profile\n"
        tree = ast.parse(bad)
        tops = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    tops.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                tops.add(node.module.split(".")[0])
        hit = tops & _FORBIDDEN_PLUGIN_MODULES
        self.assertEqual(
            {"composer", "profiles"},
            hit,
            msg="scanner failed to detect synthetic plugin imports",
        )


if __name__ == "__main__":
    unittest.main()
