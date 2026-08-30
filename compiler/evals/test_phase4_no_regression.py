"""Cross-backend regression checks for Claude Code output."""

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

# evals/ -> compiler/ (package root)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)

_PI_FILE = "system2_compiler/backends/pi.py"
_DEGRADATION_FILE = "system2_compiler/backends/_degradation.py"

_FIRST_PARTY_TOP = frozenset({"system2_compiler"})

# Loaders a backend must never import.
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

# The Claude-targeted quarantined carriers Pi must never read.
_FORBIDDEN_CARRIERS = ("base_template", "overlay_inputs")

# Tokens proving TS would be transpiled/run in-process (must be absent from pi.py).
_FORBIDDEN_TRANSPILE_TOKENS = frozenset({"typescript", "tsc", "esbuild", "swc", "babel"})


def _abspath(rel):
    return os.path.join(_PKG_ROOT, rel)


def _read(rel):
    with open(_abspath(rel), "r", encoding="utf-8") as fh:
        return fh.read()


def _iter_imports(source, filename):
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], 0
        elif isinstance(node, ast.ImportFrom):
            top = node.module.split(".")[0] if node.module else None
            yield top, node.level


def _imported_module_paths(rel):
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
                full = f"{base}.{node.module}" if (base and node.module) else (
                    node.module or base
                )
                paths.add(full)
                if not node.module:
                    for alias in node.names:
                        paths.add(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                paths.add(node.module)
    return paths


def _external_imports(rel):
    offenders = []
    for top, level in _iter_imports(_read(rel), rel):
        if level > 0 or top is None or top in _FIRST_PARTY_TOP:
            continue
        if top not in STDLIB_MODULES:
            offenders.append(top)
    return offenders


def _seed_prior_lock(src_project_dir, dst_project_dir):
    """Copy ``spec/overlay-manifest.lock`` from one project into another (if present)."""
    src = os.path.join(src_project_dir, "spec", "overlay-manifest.lock")
    if os.path.isfile(src):
        dst = os.path.join(dst_project_dir, "spec", "overlay-manifest.lock")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)


def _emit_bytes(project_dir, backend):
    """Compose+emit *backend* for core+overlay; return {rel: bytes}."""
    from system2_compiler import ir

    cell = matrix.get_cell("core+overlay")
    result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project_dir)
    if result.graph is None:
        raise AssertionError(f"core+overlay refused: {result.errors!r}")
    backend.emit(result.graph, project_dir)
    out = {}
    for root, _, files in os.walk(project_dir):
        for name in files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, project_dir)
            with open(p, "rb") as fh:
                out[rel] = fh.read()
    return out


class BackendRegistryTest(unittest.TestCase):
    """pi registered additively; --target accepts both."""

    def test_both_backends_registered(self):
        from system2_compiler import cli
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        from system2_compiler.backends.pi import PiBackend

        self.assertIn("claude-code", cli._BACKENDS)
        self.assertIn("pi", cli._BACKENDS)
        self.assertIsInstance(cli._BACKENDS["claude-code"], ClaudeCodeBackend)
        self.assertIsInstance(cli._BACKENDS["pi"], PiBackend)

    def test_pi_backend_name_attribute(self):
        from system2_compiler import cli
        self.assertEqual(cli._BACKENDS["pi"].name, "pi")

    def test_target_accepts_both_and_rejects_unknown(self):
        from system2_compiler import cli
        for target in ("claude-code", "pi"):
            self.assertIs(
                cli._select_backend(target), cli._BACKENDS[target],
                msg=f"_select_backend({target!r}) must return the registered backend",
            )
        with self.assertRaises(SystemExit):
            cli.main([
                "--target", "no-such-backend",
                "--base", _PKG_ROOT,
                "--project", tempfile.mkdtemp(),
            ])


class ClaudeKeystoneGoldenGate(unittest.TestCase):
    """Claude Code goldens remain empty-diff across the matrix."""

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
        failures = run_goldens.run_goldens(driver="oracle")
        self.assertEqual(
            [], failures,
            msg="oracle-driver goldens regressed:\n" + "\n".join(failures),
        )


class PiDoesNotPerturbOtherBackendsTest(unittest.TestCase):
    """emitting Pi alters no claude-code artifact bytes."""

    def test_claude_bytes_identical_with_or_without_pi_emit(self):
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        from system2_compiler.backends.pi import PiBackend

        baseline_dir = tempfile.mkdtemp(prefix="phase4-claude-")
        baseline = _emit_bytes(baseline_dir, ClaudeCodeBackend())

        pi_dir = tempfile.mkdtemp(prefix="phase4-pi-")
        _emit_bytes(pi_dir, PiBackend())

        # Seed the second claude project with the baseline lock so the matching content_fingerprint reuses ``composed_at`` (idempotency) — mirrors how run_goldens seeds the prior lock.
        after_dir = tempfile.mkdtemp(prefix="phase4-claude2-")
        _seed_prior_lock(baseline_dir, after_dir)
        after = _emit_bytes(after_dir, ClaudeCodeBackend())

        try:
            self.assertEqual(
                set(baseline.keys()), set(after.keys()),
                msg="claude-code artifact set changed after a Pi emit",
            )
            for rel in baseline:
                self.assertEqual(
                    baseline[rel], after[rel],
                    msg=f"claude-code artifact {rel!r} bytes changed after a Pi emit",
                )
        finally:
            for d in (baseline_dir, pi_dir, after_dir):
                shutil.rmtree(d, ignore_errors=True)

    def test_pi_writes_only_its_own_surface(self):
        from system2_compiler.backends.pi import PiBackend
        from system2_compiler import ir

        pi_dir = tempfile.mkdtemp(prefix="phase4-pi-only-")
        cell = matrix.get_cell("core+overlay")
        result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), pi_dir)
        self.assertIsNotNone(result.graph)
        PiBackend().emit(result.graph, pi_dir)

        produced = set()
        for root, _, files in os.walk(pi_dir):
            for name in files:
                produced.add(os.path.relpath(os.path.join(root, name), pi_dir))
        try:
            # No claude-code surface.
            self.assertNotIn("CLAUDE.md", produced)
            self.assertNotIn(os.path.join("spec", "overlay-manifest.lock"), produced)
            # The Pi surface present.
            self.assertIn(os.path.join(".pi", "extensions", "system2.ts"), produced)
            self.assertIn("system2.pi.lock.json", produced)
        finally:
            shutil.rmtree(pi_dir, ignore_errors=True)


class PiBoundaryTest(unittest.TestCase):
    """Pi and degradation helpers preserve their import boundaries."""

    def test_pi_imports_only_allowed_modules(self):
        imported = _imported_module_paths(_PI_FILE)
        ir_imports = {p for p in imported if p == "system2_compiler.ir" or p.startswith("system2_compiler.ir.")}
        self.assertEqual(
            {"system2_compiler.ir.graph"}, ir_imports,
            msg=(
                f"{_PI_FILE} may import only ir.graph from ir/, got: "
                f"{sorted(ir_imports)}"
            ),
        )
        forbidden = imported & _FORBIDDEN_IR_LOADERS
        self.assertEqual(
            set(), forbidden,
            msg=f"{_PI_FILE} imports forbidden ir/* loader(s): {sorted(forbidden)}",
        )

    def test_pi_imports_only_degradation_backend_submodule(self):
        imported = _imported_module_paths(_PI_FILE)
        backend_imports = {p for p in imported if p.startswith("system2_compiler.backends.")}
        # pi.py uses only shared backend helpers: _degradation, _enforcement, and _yaml (the deterministic serializer used for skill frontmatter).
        self.assertTrue(
            backend_imports <= {
                "system2_compiler.backends._degradation",
                "system2_compiler.backends._enforcement",
                "system2_compiler.backends._yaml",
                "system2_compiler.backends.base",
            },
            msg=(
                f"{_PI_FILE} imports unexpected backend submodule(s): "
                f"{sorted(backend_imports)} (only backends._degradation / "
                f"backends._enforcement / backends._yaml / backends.base)"
            ),
        )

    def test_pi_is_stdlib_only(self):
        offenders = _external_imports(_PI_FILE)
        self.assertEqual(
            [], offenders,
            msg=f"{_PI_FILE} imports third-party package(s): {offenders}",
        )

    def test_degradation_is_stdlib_only_and_ir_free(self):
        offenders = _external_imports(_DEGRADATION_FILE)
        self.assertEqual(
            [], offenders,
            msg=f"{_DEGRADATION_FILE} imports third-party package(s): {offenders}",
        )
        imported = _imported_module_paths(_DEGRADATION_FILE)
        ir_or_backend = {
            p for p in imported
            if p == "system2_compiler.ir" or p.startswith("system2_compiler.ir.")
            or p == "system2_compiler.backends" or p.startswith("system2_compiler.backends.")
        }
        self.assertEqual(
            set(), ir_or_backend,
            msg=(
                f"{_DEGRADATION_FILE} must be ir/-free and backend-free, got: "
                f"{sorted(ir_or_backend)}"
            ),
        )

    def test_new_backend_files_no_network(self):
        for rel in (_PI_FILE, _DEGRADATION_FILE):
            violations = check_no_network_calls(_abspath(rel))
            self.assertEqual(
                [], violations,
                msg=f"{rel} contains network call pattern(s): {violations}",
            )

    def test_pi_never_references_quarantined_carriers(self):
        source = _read(_PI_FILE)
        tree = ast.parse(source, filename=_PI_FILE)
        offending = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_CARRIERS:
                offending.append(node.attr)
        self.assertEqual(
            [], offending,
            msg=(
                f"{_PI_FILE} references the Claude-targeted carrier(s) "
                f"{sorted(set(offending))}; Pi must render from structured IR "
                "fields only"
            ),
        )

    def test_pi_emits_ts_as_text_no_transpiler(self):
        # the compiler emits TS as TEXT — no node/tsc/transpiler import.
        offenders = [
            top for top, _level in _iter_imports(_read(_PI_FILE), _PI_FILE)
            if top in _FORBIDDEN_TRANSPILE_TOKENS
        ]
        self.assertEqual(
            [], offenders,
            msg=f"{_PI_FILE} imports a TS transpiler/runtime: {offenders}",
        )


class IrChangeIsWriteScopeOnlyTest(unittest.TestCase):
    """the only behavioral IR delta is non-empty write_scope; claude unchanged."""

    def test_roles_carry_non_empty_write_scope(self):
        from system2_compiler import ir

        project = tempfile.mkdtemp(prefix="phase4-scope-")
        try:
            cell = matrix.get_cell("core+overlay")
            result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project)
            self.assertIsNotNone(result.graph)
            roles = result.graph.roles
            # populated write_scope from the .regex allowlists: at least the pipeline-implementing roles carry a non-empty scope (the enrichment is real, not a no-op).
            non_empty = [r.name for r in roles if (r.write_scope or "").strip()]
            self.assertGreater(
                len(non_empty), 0,
                " enrichment must populate write_scope for >=1 role",
            )
            # And the field is the carrier of the delta: every role still exposes the
            # write_scope attribute (no other new neutral field was introduced).
            for r in roles:
                self.assertTrue(
                    hasattr(r, "write_scope"),
                    f"role {r.name!r} lost its write_scope attribute",
                )
        finally:
            shutil.rmtree(project, ignore_errors=True)

    def test_claude_bytes_unchanged_by_write_scope_enrichment(self):
        # The write_scope enrichment is claude-byte-neutral: covered by the keystone
        # golden gate above. Re-assert cheaply that the claude path still composes.
        from system2_compiler.backends.claude_code import ClaudeCodeBackend

        d = tempfile.mkdtemp(prefix="phase4-claude-scope-")
        try:
            out = _emit_bytes(d, ClaudeCodeBackend())
            self.assertIn("CLAUDE.md", out)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
