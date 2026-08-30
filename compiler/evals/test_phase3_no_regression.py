"""Regression checks for the Claude Code backend."""

import ast
import os
import tempfile
import unittest

from evals import run_goldens
from system2_compiler.ir._hook_security import (
    STDLIB_MODULES,
    check_no_network_calls,
)

# evals/ -> compiler/ (package root)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)

_YAML_FILE = "system2_compiler/backends/_yaml.py"

_FIRST_PARTY_TOP = frozenset({"system2_compiler"})


def _abspath(rel: str) -> str:
    return os.path.join(_PKG_ROOT, rel)


def _read(rel: str) -> str:
    with open(_abspath(rel), "r", encoding="utf-8") as fh:
        return fh.read()


def _iter_imports(source: str, filename: str):
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], 0
        elif isinstance(node, ast.ImportFrom):
            top = node.module.split(".")[0] if node.module else None
            yield top, node.level


def _imported_module_paths(rel: str):
    """Absolute + package-resolved relative dotted module paths imported by *rel*."""
    source = _read(rel)
    tree = ast.parse(source, filename=rel)
    pkg = os.path.dirname(rel).replace(os.sep, ".")
    paths = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                paths.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                base = pkg
                if node.module:
                    full = f"{base}.{node.module}" if base else node.module
                else:
                    full = base
                paths.add(full)
                if not node.module:
                    for alias in node.names:
                        paths.add(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                paths.add(node.module)
    return paths


def _external_imports(rel: str):
    offenders = []
    for top, level in _iter_imports(_read(rel), rel):
        if level > 0 or top is None or top in _FIRST_PARTY_TOP:
            continue
        if top not in STDLIB_MODULES:
            offenders.append(top)
    return offenders


class BackendRegistryTest(unittest.TestCase):
    """claude-code registered; --target accepts it and rejects unknown."""

    def test_claude_code_backend_registered(self):
        from system2_compiler import cli
        from system2_compiler.backends.claude_code import ClaudeCodeBackend

        self.assertIn("claude-code", cli._BACKENDS)
        self.assertIsInstance(cli._BACKENDS["claude-code"], ClaudeCodeBackend)

    def test_backend_name_attributes(self):
        from system2_compiler import cli
        self.assertEqual(cli._BACKENDS["claude-code"].name, "claude-code")

    def test_target_accepts_claude_code_and_rejects_unknown(self):
        from system2_compiler import cli
        for target in ("claude-code",):
            self.assertIs(
                cli._select_backend(target), cli._BACKENDS[target],
                msg=f"_select_backend({target!r}) must return the registered backend",
            )
        with self.assertRaises(SystemExit):
            # argparse rejects an unknown --target choice with SystemExit(2).
            cli.main([
                "--target", "no-such-backend",
                "--base", _PKG_ROOT,
                "--project", tempfile.mkdtemp(),
            ])


class ClaudeKeystoneGoldenGate(unittest.TestCase):
    """claude-code goldens empty-diff across the matrix ( sign-off)."""

    def test_compiler_driver_empty_diff(self):
        failures = run_goldens.run_goldens(driver="compiler")
        self.assertEqual(
            [], failures,
            msg=(
                "claude-code compose->emit goldens regressed:\n"
                + "\n".join(failures)
            ),
        )

    def test_oracle_driver_still_green(self):
        # The frozen-oracle cross-check (rollout backout path) must also stay green.
        failures = run_goldens.run_goldens(driver="oracle")
        self.assertEqual(
            [], failures,
            msg="oracle-driver goldens regressed:\n" + "\n".join(failures),
        )


class YamlModuleBoundaryTest(unittest.TestCase):
    """the retained ``backends/_yaml.py``'s import boundary."""

    def test_yaml_is_stdlib_only(self):
        offenders = _external_imports(_YAML_FILE)
        self.assertEqual(
            [], offenders,
            msg=f"{_YAML_FILE} imports third-party package(s): {offenders}",
        )
        imported = _imported_module_paths(_YAML_FILE)
        ir_or_backend = {
            p for p in imported
            if p == "system2_compiler.ir" or p.startswith("system2_compiler.ir.")
            or p == "system2_compiler.backends" or p.startswith("system2_compiler.backends.")
        }
        self.assertEqual(
            set(), ir_or_backend,
            msg=f"{_YAML_FILE} must have no IR knowledge / backend imports: {sorted(ir_or_backend)}",
        )

    def test_new_backend_files_no_network(self):
        for rel in (_YAML_FILE,):
            violations = check_no_network_calls(_abspath(rel))
            self.assertEqual(
                [], violations,
                msg=f"{rel} contains network call pattern(s): {violations}",
            )


if __name__ == "__main__":
    unittest.main()
