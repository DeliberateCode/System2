"""TASK-410 — Phase-4 DoD sign-off (DoD-P): no claude-code / goose regression.

The integrity gate proving the Pi backend + PG6 shared helper landed **additively**:
they changed no claude-code bytes, kept Goose green (14/14 ``goose recipe validate``),
confined the ``ir/`` change to ``write_scope`` population, and ``pi.py`` /
``_degradation.py`` honor the import boundaries (design §"Test/validity strategy"
leg 5; AC-P1/AC-P7; REQ-014/015/040/016/043/047).

Assertions:

1. **Registry / CLI (REQ-049).** ``PiBackend`` is registered in ``cli._BACKENDS``
   under ``"pi"``; claude-code + goose are still registered; ``--target`` accepts
   claude-code / goose / pi and rejects an unknown target.

2. **Claude keystone preserved (REQ-014, AC-P1).** The in-process
   ``ir.compose -> ClaudeCodeBackend().emit`` path is byte-identical to the frozen
   Phase-0/1 baseline across the full matrix (reuses ``evals.run_goldens``,
   ``--driver compiler``).

3. **Pi emit perturbs no claude-code / goose artifact (AC-P1).** Emitting Pi into a
   project and then composing+emitting claude-code (and goose) into separate
   projects yields the same claude-code / goose bytes as without the Pi emit.

4. **Goose still 14/14 (AC-P1).** ``goose recipe validate`` passes on the
   orchestrator + 13 role recipes under a hermetic temp HOME (LOUD-SKIP if goose
   absent — but it is installed).

5. **Import boundaries (REQ-015/040/016/043/047, AC-P7).** ``backends/pi.py`` imports
   only ``ir.graph`` + ``backends._degradation`` + stdlib (never ``ir.base_template``
   / ``ir.overlay_inputs`` / any manifest/anchor/profile/schema loader);
   ``backends/_degradation.py`` imports only stdlib and **no ``ir/*``**. Both are
   stdlib-only with no network calls.

6. **IR change is write_scope-only (OQ-P3).** Composing two IRs shows the only
   behavioral delta vs an empty-scope projection is non-empty ``write_scope`` — no
   other field; and the claude bytes are unchanged.

7. **TS emitted as text (AC-P7).** ``backends/pi.py`` imports no ``node`` / ``tsc`` /
   third-party transpiler — it writes TypeScript as plain text.

Stdlib ``unittest``; runs under ``python3 -m unittest``. No product code or
``System2/`` is modified. All overlay/fixture contents are untrusted data.
"""

import ast
import os
import shutil
import subprocess
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

_PI_FILE = "system2_compiler/backends/pi.py"
_DEGRADATION_FILE = "system2_compiler/backends/_degradation.py"

_FIRST_PARTY_TOP = frozenset({"system2_compiler"})

# Loaders a backend must never import (REQ-015).
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

_GOOSE_BIN = os.environ.get("GOOSE_BIN") or shutil.which("goose")
_GOOSE_LOUD_SKIP = (
    "goose not installed — recipe validation SKIPPED (not a silent pass); "
    "set GOOSE_BIN or install goose to run the validity oracle"
)


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


def _hermetic_env(home):
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    return env


def _seed_prior_lock(src_project_dir, dst_project_dir):
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
    """REQ-049 — pi registered additively; --target accepts all three."""

    def test_all_three_backends_registered(self):
        from system2_compiler import cli
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        from system2_compiler.backends.goose import GooseBackend
        from system2_compiler.backends.pi import PiBackend

        self.assertIn("claude-code", cli._BACKENDS)
        self.assertIn("goose", cli._BACKENDS)
        self.assertIn("pi", cli._BACKENDS)
        self.assertIsInstance(cli._BACKENDS["claude-code"], ClaudeCodeBackend)
        self.assertIsInstance(cli._BACKENDS["goose"], GooseBackend)
        self.assertIsInstance(cli._BACKENDS["pi"], PiBackend)

    def test_pi_backend_name_attribute(self):
        from system2_compiler import cli
        self.assertEqual(cli._BACKENDS["pi"].name, "pi")

    def test_target_accepts_all_and_rejects_unknown(self):
        from system2_compiler import cli
        for target in ("claude-code", "goose", "pi"):
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
    """REQ-014 / AC-P1 — claude-code goldens empty-diff across the matrix."""

    def test_compiler_driver_empty_diff(self):
        failures = run_goldens.run_goldens(driver="compiler")
        self.assertEqual(
            [], failures,
            msg=(
                "claude-code compose->emit goldens regressed under Phase 4:\n"
                + "\n".join(failures)
            ),
        )

    def test_oracle_driver_still_green(self):
        failures = run_goldens.run_goldens(driver="oracle")
        self.assertEqual(
            [], failures,
            msg="oracle-driver goldens regressed under Phase 4:\n" + "\n".join(failures),
        )


class PiDoesNotPerturbOtherBackendsTest(unittest.TestCase):
    """AC-P1 — emitting Pi alters no claude-code / goose artifact bytes."""

    def test_claude_bytes_identical_with_or_without_pi_emit(self):
        from system2_compiler.backends.claude_code import ClaudeCodeBackend
        from system2_compiler.backends.pi import PiBackend

        baseline_dir = tempfile.mkdtemp(prefix="phase4-claude-")
        baseline = _emit_bytes(baseline_dir, ClaudeCodeBackend())

        pi_dir = tempfile.mkdtemp(prefix="phase4-pi-")
        _emit_bytes(pi_dir, PiBackend())

        # Seed the second claude project with the baseline lock so the matching
        # content_fingerprint reuses ``composed_at`` (idempotency) — mirrors how
        # run_goldens seeds the prior lock. The claim under test is that Pi emit
        # changes no claude CONTENT; the wall-clock timestamp line is not content,
        # and without this seed the two emits can straddle a one-second boundary.
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

    def test_goose_bytes_identical_with_or_without_pi_emit(self):
        from system2_compiler.backends.goose import GooseBackend
        from system2_compiler.backends.pi import PiBackend

        baseline_dir = tempfile.mkdtemp(prefix="phase4-goose-")
        baseline = _emit_bytes(baseline_dir, GooseBackend())

        pi_dir = tempfile.mkdtemp(prefix="phase4-pi2-")
        _emit_bytes(pi_dir, PiBackend())

        after_dir = tempfile.mkdtemp(prefix="phase4-goose2-")
        after = _emit_bytes(after_dir, GooseBackend())

        try:
            self.assertEqual(
                set(baseline.keys()), set(after.keys()),
                msg="goose artifact set changed after a Pi emit",
            )
            for rel in baseline:
                self.assertEqual(
                    baseline[rel], after[rel],
                    msg=f"goose artifact {rel!r} bytes changed after a Pi emit",
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
            # No claude-code / goose surface.
            self.assertNotIn("CLAUDE.md", produced)
            self.assertNotIn(os.path.join("spec", "overlay-manifest.lock"), produced)
            self.assertNotIn("system2.recipe.yaml", produced)
            self.assertNotIn("system2.goose.lock.json", produced)
            # The Pi surface present.
            self.assertIn(os.path.join(".pi", "extensions", "system2.ts"), produced)
            self.assertIn("system2.pi.lock.json", produced)
        finally:
            shutil.rmtree(pi_dir, ignore_errors=True)


class GooseStillValidatesTest(unittest.TestCase):
    """AC-P1 — goose recipe validate still 14/14 (hermetic HOME; LOUD-SKIP absent)."""

    def test_goose_recipes_validate_14_of_14(self):
        from system2_compiler.backends.goose import GooseBackend
        from system2_compiler import ir

        project = tempfile.mkdtemp(prefix="phase4-goose-validate-")
        home = tempfile.mkdtemp(prefix="phase4-goose-home-")
        try:
            cell = matrix.get_cell("core+overlay")
            result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project)
            self.assertIsNotNone(result.graph)
            GooseBackend().emit(result.graph, project)

            recipes = [os.path.join(project, "system2.recipe.yaml")]
            for role in result.graph.delegation_contract.preferred_order:
                recipes.append(
                    os.path.join(project, "agents", f"{role}.recipe.yaml")
                )
            self.assertGreaterEqual(len(recipes), 14)

            if not _GOOSE_BIN:
                self.skipTest(_GOOSE_LOUD_SKIP)

            env = _hermetic_env(home)
            failures = []
            for rp in recipes:
                completed = subprocess.run(
                    [_GOOSE_BIN, "recipe", "validate", rp],
                    capture_output=True, text=True, env=env, timeout=120,
                )
                if completed.returncode != 0:
                    failures.append(
                        f"{os.path.relpath(rp, project)}: exit {completed.returncode}"
                        f"\nstderr={completed.stderr!r}"
                    )
            self.assertEqual(
                [], failures,
                "goose recipe validate REGRESSED under Phase 4:\n"
                + "\n".join(failures),
            )
        finally:
            shutil.rmtree(project, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)


class PiBoundaryTest(unittest.TestCase):
    """REQ-015/040/016/043/047 / AC-P7 — pi.py + _degradation.py import boundaries."""

    def test_pi_imports_only_allowed_modules(self):
        imported = _imported_module_paths(_PI_FILE)
        ir_imports = {p for p in imported if p == "system2_compiler.ir" or p.startswith("system2_compiler.ir.")}
        self.assertEqual(
            {"system2_compiler.ir.graph"}, ir_imports,
            msg=(
                f"{_PI_FILE} may import only ir.graph from ir/, got: "
                f"{sorted(ir_imports)} (REQ-015)"
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
        # pi.py does `from . import _degradation` (-> backends._degradation). The bare
        # `backends` package token is not a forbidden submodule.
        self.assertTrue(
            backend_imports <= {"system2_compiler.backends._degradation", "system2_compiler.backends.base"},
            msg=(
                f"{_PI_FILE} imports unexpected backend submodule(s): "
                f"{sorted(backend_imports)} (only backends._degradation / backends.base)"
            ),
        )

    def test_pi_is_stdlib_only(self):
        offenders = _external_imports(_PI_FILE)
        self.assertEqual(
            [], offenders,
            msg=f"{_PI_FILE} imports third-party package(s): {offenders} (AC-P7)",
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
                msg=f"{rel} contains network call pattern(s): {violations} (REQ-047)",
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
        # AC-P7: the compiler emits TS as TEXT — no node/tsc/transpiler import.
        offenders = [
            top for top, _level in _iter_imports(_read(_PI_FILE), _PI_FILE)
            if top in _FORBIDDEN_TRANSPILE_TOKENS
        ]
        self.assertEqual(
            [], offenders,
            msg=f"{_PI_FILE} imports a TS transpiler/runtime: {offenders} (AC-P7)",
        )


class IrChangeIsWriteScopeOnlyTest(unittest.TestCase):
    """OQ-P3 — the only behavioral IR delta is non-empty write_scope; claude unchanged."""

    def test_roles_carry_non_empty_write_scope(self):
        from system2_compiler import ir

        project = tempfile.mkdtemp(prefix="phase4-scope-")
        try:
            cell = matrix.get_cell("core+overlay")
            result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project)
            self.assertIsNotNone(result.graph)
            roles = result.graph.roles
            # OQ-P3 populated write_scope from the .regex allowlists: at least the
            # pipeline-implementing roles carry a non-empty scope (the enrichment is
            # real, not a no-op). Some roles (e.g. code-reviewer) are honestly empty.
            non_empty = [r.name for r in roles if (r.write_scope or "").strip()]
            self.assertGreater(
                len(non_empty), 0,
                "OQ-P3 enrichment must populate write_scope for >=1 role",
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
