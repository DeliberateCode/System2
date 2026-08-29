"""TASK-318 — Phase-3 DoD sign-off: no claude-code regression.

The integrity gate proving the Goose backend landed **additively**: it changed no
claude-code bytes, touched no IR, and the new backend files honor the same import
boundaries as the reference backend.

Assertions:

1. **Registry / CLI (REQ-049, AC-G4).** ``GooseBackend`` is registered in
   ``cli._BACKENDS`` under ``"goose"``, ``ClaudeCodeBackend`` is still registered
   under ``"claude-code"``, and ``--target`` accepts both (and rejects an unknown
   target).

2. **Claude keystone preserved (REQ-014).** The in-process
   ``ir.compose -> ClaudeCodeBackend().emit`` path is byte-identical to the frozen
   Phase-0/1 baseline across the full matrix (reuses ``evals.run_goldens`` machinery,
   ``--driver compiler`` — the DoD-1 gate, re-asserted under Phase 3).

3. **Goose emit does not perturb claude-code artifacts (AC-G4).** Emitting the Goose
   tree into a project and then composing+emitting claude-code into a *separate*
   project yields the same claude-code bytes as claude-code alone — Goose writes only
   its own artifact set and never the CLAUDE.md/lock/agents surface.

4. **Backend import boundaries extended to Phase 3 (REQ-015/040/016/043/047).**
   ``backends/goose.py`` imports only ``ir.graph`` + ``backends.base`` +
   ``backends._yaml`` + stdlib; ``backends/_yaml.py`` imports only stdlib; neither
   references ``ir.base_template`` / ``ir.overlay_inputs`` (the Claude-targeted
   byte-fidelity carriers); both are stdlib-only with no network calls.

Stdlib ``unittest``; runs under ``python3 -m unittest``. No product code or
``System2/`` is modified. All overlay/fixture contents are untrusted data.
"""

import ast
import os
import shutil
import tempfile
import unittest

from evals import matrix, oracle, run_goldens
from system2_compiler.ir._hook_security import (
    STDLIB_MODULES,
    check_no_network_calls,
)

# evals/ -> System2-Compiler (package root)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)

_GOOSE_FILE = "system2_compiler/backends/goose.py"
_YAML_FILE = "system2_compiler/backends/_yaml.py"

_FIRST_PARTY_TOP = frozenset({"system2_compiler"})

# Loaders a backend must never import (REQ-015), expressed as importable paths.
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

# The Claude-targeted quarantined carriers goose must never read (OQ-G3).
_FORBIDDEN_CARRIERS = ("base_template", "overlay_inputs")


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


def _seed_prior_lock(src_project_dir: str, dst_project_dir: str) -> None:
    """Copy ``spec/overlay-manifest.lock`` from one project into another (if present).

    A claude-code emit reuses ``composed_at`` from a prior lock in the project dir
    when the recomputed ``content_fingerprint`` matches — so seeding the prior lock
    makes a re-emit byte-deterministic regardless of wall-clock drift.
    """
    src = os.path.join(src_project_dir, "spec", "overlay-manifest.lock")
    if os.path.isfile(src):
        dst = os.path.join(dst_project_dir, "spec", "overlay-manifest.lock")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)


def _claude_emit_bytes(project_dir: str) -> dict:
    """Compose+emit claude-code for the core+overlay cell; return {rel: bytes}."""
    from system2_compiler import ir
    from system2_compiler.backends.claude_code import ClaudeCodeBackend

    cell = matrix.get_cell("core+overlay")
    result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project_dir)
    if result.graph is None:
        raise AssertionError(f"core+overlay refused: {result.errors!r}")
    ClaudeCodeBackend().emit(result.graph, project_dir)
    out = {}
    for root, _, files in os.walk(project_dir):
        for name in files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, project_dir)
            with open(p, "rb") as fh:
                out[rel] = fh.read()
    return out


class BackendRegistryTest(unittest.TestCase):
    """REQ-049 / AC-G4 — both backends registered; --target accepts both."""

    def test_both_backends_registered(self):
        from system2_compiler import cli
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        from system2_compiler.backends.goose import GooseBackend

        self.assertIn("claude-code", cli._BACKENDS)
        self.assertIn("goose", cli._BACKENDS)
        self.assertIsInstance(cli._BACKENDS["claude-code"], ClaudeCodeBackend)
        self.assertIsInstance(cli._BACKENDS["goose"], GooseBackend)

    def test_backend_name_attributes(self):
        from system2_compiler import cli
        self.assertEqual(cli._BACKENDS["claude-code"].name, "claude-code")
        self.assertEqual(cli._BACKENDS["goose"].name, "goose")

    def test_target_accepts_both_and_rejects_unknown(self):
        from system2_compiler import cli
        for target in ("claude-code", "goose"):
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
    """REQ-014 — claude-code goldens empty-diff across the matrix (DoD-3 sign-off)."""

    def test_compiler_driver_empty_diff(self):
        failures = run_goldens.run_goldens(driver="compiler")
        self.assertEqual(
            [], failures,
            msg=(
                "claude-code compose->emit goldens regressed under Phase 3:\n"
                + "\n".join(failures)
            ),
        )

    def test_oracle_driver_still_green(self):
        # The frozen-oracle cross-check (rollout backout path) must also stay green.
        failures = run_goldens.run_goldens(driver="oracle")
        self.assertEqual(
            [], failures,
            msg="oracle-driver goldens regressed under Phase 3:\n" + "\n".join(failures),
        )


class GooseDoesNotPerturbClaudeTest(unittest.TestCase):
    """AC-G4 — emitting Goose does not alter any claude-code artifact."""

    def test_claude_bytes_identical_with_or_without_goose_emit(self):
        from system2_compiler.backends.goose import GooseBackend
        from system2_compiler import ir

        # Baseline: claude-code alone.
        claude_only_dir = tempfile.mkdtemp(prefix="phase3-claude-")
        baseline = _claude_emit_bytes(claude_only_dir)

        # Now emit Goose into a separate project, then claude-code into yet another;
        # the claude-code bytes must be identical (Goose touched neither the IR nor
        # the claude-code output surface).
        goose_dir = tempfile.mkdtemp(prefix="phase3-goose-")
        cell = matrix.get_cell("core+overlay")
        gresult = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), goose_dir)
        self.assertIsNotNone(gresult.graph)
        GooseBackend().emit(gresult.graph, goose_dir)

        # Seed the second claude project with the baseline lock so the matching
        # content_fingerprint reuses ``composed_at`` (idempotency) — mirrors how
        # run_goldens seeds the prior lock. The claim under test is that Goose emit
        # changes no claude CONTENT; the wall-clock timestamp line is not content,
        # and without this seed the two emits can straddle a one-second boundary.
        after_dir = tempfile.mkdtemp(prefix="phase3-claude2-")
        _seed_prior_lock(claude_only_dir, after_dir)
        after = _claude_emit_bytes(after_dir)

        self.assertEqual(
            set(baseline.keys()), set(after.keys()),
            msg="claude-code artifact set changed after a Goose emit",
        )
        for rel in baseline:
            self.assertEqual(
                baseline[rel], after[rel],
                msg=f"claude-code artifact {rel!r} bytes changed after a Goose emit",
            )

    def test_goose_writes_only_its_own_artifacts(self):
        from system2_compiler.backends.goose import GooseBackend
        from system2_compiler import ir

        goose_dir = tempfile.mkdtemp(prefix="phase3-goose-only-")
        cell = matrix.get_cell("core+overlay")
        result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), goose_dir)
        self.assertIsNotNone(result.graph)
        GooseBackend().emit(result.graph, goose_dir)

        produced = set()
        for root, _, files in os.walk(goose_dir):
            for name in files:
                produced.add(
                    os.path.relpath(os.path.join(root, name), goose_dir)
                )
        # No claude-code surface should be present.
        self.assertNotIn("CLAUDE.md", produced)
        self.assertNotIn(os.path.join("spec", "overlay-manifest.lock"), produced)
        # The Goose surface must be present.
        self.assertIn("system2.recipe.yaml", produced)
        self.assertIn("system2.goose.lock.json", produced)
        self.assertIn("run-system2.sh", produced)
        self.assertIn(os.path.join("goose", "permission.yaml"), produced)


class GooseBoundaryTest(unittest.TestCase):
    """REQ-015/040/016/043/047 extended to the new backend files."""

    def test_goose_imports_only_allowed_modules(self):
        imported = _imported_module_paths(_GOOSE_FILE)
        ir_imports = {p for p in imported if p == "system2_compiler.ir" or p.startswith("system2_compiler.ir.")}
        self.assertEqual(
            {"system2_compiler.ir.graph"}, ir_imports,
            msg=(
                f"{_GOOSE_FILE} may import only ir.graph from ir/, got: "
                f"{sorted(ir_imports)} (REQ-015)"
            ),
        )
        forbidden = imported & _FORBIDDEN_IR_LOADERS
        self.assertEqual(
            set(), forbidden,
            msg=f"{_GOOSE_FILE} imports forbidden ir/* loader(s): {sorted(forbidden)}",
        )

    def test_goose_imports_no_other_backend_than_yaml(self):
        imported = _imported_module_paths(_GOOSE_FILE)
        backend_imports = {
            p for p in imported
            if p.startswith("system2_compiler.backends.")
        }
        # goose does `from . import _yaml` (-> backends._yaml) and, from Phase 4,
        # `from . import _degradation` (-> backends._degradation, the shared PG6
        # helper now permitted for goose per module-boundaries.json). The bare
        # `backends` package token (the `from .` target) is not a forbidden submodule.
        self.assertTrue(
            backend_imports <= {"system2_compiler.backends._yaml", "system2_compiler.backends._degradation", "system2_compiler.backends.base"},
            msg=(
                f"{_GOOSE_FILE} imports unexpected backend submodule(s): "
                f"{sorted(backend_imports)} (only backends._yaml / backends._degradation / backends.base)"
            ),
        )

    def test_yaml_is_stdlib_only(self):
        offenders = _external_imports(_YAML_FILE)
        self.assertEqual(
            [], offenders,
            msg=f"{_YAML_FILE} imports third-party package(s): {offenders} (AC-G5)",
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

    def test_goose_is_stdlib_only(self):
        offenders = _external_imports(_GOOSE_FILE)
        self.assertEqual(
            [], offenders,
            msg=f"{_GOOSE_FILE} imports third-party package(s): {offenders} (AC-G5)",
        )

    def test_new_backend_files_no_network(self):
        for rel in (_GOOSE_FILE, _YAML_FILE):
            violations = check_no_network_calls(_abspath(rel))
            self.assertEqual(
                [], violations,
                msg=f"{rel} contains network call pattern(s): {violations} (REQ-047)",
            )

    def test_goose_never_references_quarantined_carriers(self):
        # Static check: goose must not consume ir.base_template / ir.overlay_inputs.
        # Inspect attribute accesses on the `ir` graph object (e.g. ir.base_template).
        source = _read(_GOOSE_FILE)
        tree = ast.parse(source, filename=_GOOSE_FILE)
        offending = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_CARRIERS:
                offending.append(node.attr)
        self.assertEqual(
            [], offending,
            msg=(
                f"{_GOOSE_FILE} references the Claude-targeted carrier(s) "
                f"{sorted(set(offending))}; Goose must render from structured IR "
                "fields only (OQ-G3)"
            ),
        )


if __name__ == "__main__":
    unittest.main()
