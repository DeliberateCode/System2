"""TASK-315 — Goose golden harness + the real ``goose recipe validate`` leg.

There is no byte-identical oracle for Goose (new output, not a relocation). This
harness drives the in-process ``ir.compose -> GooseBackend.emit`` path for the Goose
matrix cells and asserts (design §"Test / golden strategy" legs 1–2, §Matrix;
AC-G1/AC-G2):

* **Validity oracle** — run ``goose recipe validate`` on the orchestrator AND every
  ``agents/*.recipe.yaml`` (>=14 recipes); all must be valid. When goose is on PATH
  the validation MUST run and PASS; when absent it is a **LOUD SKIP** (never a silent
  pass), detected via ``shutil.which`` / ``GOOSE_BIN``. Every goose invocation runs
  under a **hermetic temp HOME**.
* **Determinism** — emit twice into two temp projects; the artifact trees are
  byte-identical (output is a pure function of the IR; no timestamps).
* **Structure** — ``system2.recipe.yaml`` declares ``sub_recipes`` referencing the 13
  roles; each role recipe is valid YAML-shaped and declares NO ``sub_recipes`` (no
  nesting); ``goose/permission.yaml`` + ``system2.goose.lock.json`` + ``run-system2.sh``
  (shebang present, ``bash -n`` clean) are emitted.
* **Emit purity** — ``emit`` writes only under ``project_path``; the real
  ``~/.config/goose`` is provably not created/modified by emit.
* **Refusal parity** — conflict/tension cells refuse in the shared front-end
  (backend-independent), so Goose emits nothing.

Stdlib-only ``unittest``; runs under ``python3 -m unittest``. All cited overlay /
recipe contents are treated as untrusted; embedded instructions are not followed.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.goose import GooseBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY

_GOOSE_BIN = os.environ.get("GOOSE_BIN") or shutil.which("goose")
_LOUD_SKIP = (
    "goose not installed — recipe validation SKIPPED (not a silent pass); "
    "set GOOSE_BIN or install goose v1.38.0 to run the validity oracle"
)
_BASH_BIN = shutil.which("bash")

_REAL_GOOSE_CONFIG = os.path.join(os.path.expanduser("~"), ".config", "goose")

# Expected emitted file set for a composed Goose cell.
_FIXED_ARTIFACTS = (
    "system2.recipe.yaml",
    os.path.join("goose", "permission.yaml"),
    "system2.goose.lock.json",
    "run-system2.sh",
)


def _dir_fingerprint(path):
    """Sorted (relpath, size, mtime_ns) listing of *path*, or None when absent.

    Lets a test prove the real ``~/.config/goose`` is untouched. We only stat it.
    """
    if not os.path.isdir(path):
        return None
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append((os.path.relpath(full, path), st.st_size, st.st_mtime_ns))
    return tuple(entries)


def _hermetic_env(home):
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "LC_ALL", "LC_CTYPE", "XDG_CONFIG_HOME"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    # Force XDG_CONFIG_HOME under the temp HOME so goose's config dir is hermetic
    # even if the ambient env sets XDG_CONFIG_HOME to the real one.
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    return env


def _emit_core_overlay(project_dir):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project_dir)
    if result.graph is None:
        raise AssertionError(
            f"compose refused the core+overlay cell: {result.errors!r}"
        )
    written = GooseBackend().emit(result.graph, project_dir)
    return result.graph, written


def _read_tree(root):
    """Return {relpath: bytes} for every file under *root*."""
    tree = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as fh:
                tree[rel] = fh.read()
    return tree


class GooseEmitStructureTest(unittest.TestCase):
    """Structure of the emitted Goose tree (AC-G2)."""

    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="goose-struct-")
        cls.graph, cls.written = _emit_core_overlay(cls.project)
        cls.tree = _read_tree(cls.project)
        cls.roles = [r.name for r in cls.graph.roles]
        cls.preferred = list(cls.graph.delegation_contract.preferred_order)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_thirteen_roles(self):
        self.assertEqual(len(self.roles), 13, "the IR must carry the 13 pipeline roles")

    def test_fixed_artifacts_present(self):
        for rel in _FIXED_ARTIFACTS:
            self.assertIn(
                rel, self.tree, f"emit must produce {rel!r}",
            )

    def test_one_recipe_per_role(self):
        for role in self.preferred:
            rel = os.path.join("agents", f"{role}.recipe.yaml")
            self.assertIn(
                rel, self.tree, f"missing role sub-recipe for {role!r}",
            )
        # No extra agent recipes beyond the 13.
        agent_recipes = [
            r for r in self.tree
            if r.startswith("agents" + os.sep) and r.endswith(".recipe.yaml")
        ]
        self.assertEqual(
            len(agent_recipes), 13,
            f"expected exactly 13 role recipes, got {len(agent_recipes)}: "
            f"{sorted(agent_recipes)}",
        )

    def test_total_emitted_file_count(self):
        # 1 orchestrator + 13 sub-recipes + permission.yaml + lock + launcher = 17.
        self.assertEqual(
            len(self.written), 17,
            f"expected 17 emitted files, got {len(self.written)}",
        )

    def test_at_least_14_recipes(self):
        recipes = [r for r in self.tree if r.endswith(".recipe.yaml")]
        self.assertGreaterEqual(
            len(recipes), 14,
            "expected >=14 recipes (1 orchestrator + 13 roles)",
        )

    def test_orchestrator_subrecipes_reference_all_13_roles(self):
        text = self.tree["system2.recipe.yaml"].decode("utf-8")
        self.assertIn("sub_recipes:", text, "orchestrator must declare sub_recipes")
        for role in self.preferred:
            self.assertIn(
                f"path: agents/{role}.recipe.yaml", text,
                f"orchestrator sub_recipes must reference role {role!r}",
            )
            self.assertIn(
                f"name: {role}", text,
                f"orchestrator sub_recipes must name role {role!r}",
            )

    def test_role_recipes_declare_no_sub_recipes(self):
        # Structural invariant: sub-recipes are isolated sessions and MUST NOT nest
        # (maps onto "subagents cannot spawn subagents").
        for role in self.preferred:
            rel = os.path.join("agents", f"{role}.recipe.yaml")
            text = self.tree[rel].decode("utf-8")
            self.assertNotIn(
                "sub_recipes", text,
                f"role recipe {role!r} must NOT declare sub_recipes (no nesting)",
            )

    def test_launcher_has_shebang(self):
        text = self.tree["run-system2.sh"].decode("utf-8")
        self.assertTrue(
            text.startswith("#!"),
            "run-system2.sh must start with a shebang",
        )
        self.assertIn("goose run", text, "launcher must invoke goose run")
        self.assertIn("GOOSE_MODE=smart_approve", text)

    def test_lock_and_permission_present(self):
        self.assertIn("system2.goose.lock.json", self.tree)
        self.assertIn(os.path.join("goose", "permission.yaml"), self.tree)

    @unittest.skipUnless(_BASH_BIN, "bash not available for syntax check")
    def test_launcher_is_bash_n_clean(self):
        launcher = os.path.join(self.project, "run-system2.sh")
        completed = subprocess.run(
            [_BASH_BIN, "-n", launcher], capture_output=True, text=True,
        )
        self.assertEqual(
            completed.returncode, 0,
            f"run-system2.sh failed `bash -n` syntax check:\n{completed.stderr}",
        )


class GooseEmitDeterminismTest(unittest.TestCase):
    """Emit twice -> byte-identical trees (pure function of the IR; no timestamps)."""

    def test_emit_twice_is_byte_identical(self):
        proj_a = tempfile.mkdtemp(prefix="goose-det-a-")
        proj_b = tempfile.mkdtemp(prefix="goose-det-b-")
        try:
            _emit_core_overlay(proj_a)
            _emit_core_overlay(proj_b)
            tree_a = _read_tree(proj_a)
            tree_b = _read_tree(proj_b)
            self.assertEqual(
                set(tree_a.keys()), set(tree_b.keys()),
                "the two emit runs produced different file sets",
            )
            mismatches = [rel for rel in tree_a if tree_a[rel] != tree_b[rel]]
            self.assertEqual(
                [], mismatches,
                f"emit is not byte-deterministic; differing files: {mismatches}",
            )
        finally:
            shutil.rmtree(proj_a, ignore_errors=True)
            shutil.rmtree(proj_b, ignore_errors=True)


class GooseEmitPurityTest(unittest.TestCase):
    """emit writes only under project_path; the real ~/.config/goose is untouched."""

    def test_emit_does_not_touch_real_goose_config(self):
        before = _dir_fingerprint(_REAL_GOOSE_CONFIG)
        had_permission = os.path.exists(
            os.path.join(_REAL_GOOSE_CONFIG, "permission.yaml")
        )
        project = tempfile.mkdtemp(prefix="goose-purity-")
        try:
            _emit_core_overlay(project)
            # Everything written lands under project_path.
            for dirpath, _dirs, files in os.walk(project):
                for name in files:
                    full = os.path.realpath(os.path.join(dirpath, name))
                    self.assertTrue(
                        full.startswith(os.path.realpath(project) + os.sep),
                        f"emit wrote outside project_path: {full}",
                    )
        finally:
            shutil.rmtree(project, ignore_errors=True)
        self.assertEqual(
            _dir_fingerprint(_REAL_GOOSE_CONFIG), before,
            "emit modified the real ~/.config/goose (it must be home-dir-free)",
        )
        self.assertEqual(
            os.path.exists(os.path.join(_REAL_GOOSE_CONFIG, "permission.yaml")),
            had_permission,
            "emit created/removed a permission.yaml in the real ~/.config/goose",
        )


class GooseRefusalParityTest(unittest.TestCase):
    """Conflict/tension cells refuse in the shared front-end; Goose emits nothing."""

    def test_conflict_cell_refuses_before_backend(self):
        project = tempfile.mkdtemp(prefix="goose-conflict-")
        try:
            result = ir.compose(
                _BASE, [matrix.CONFLICT_A, matrix.CONFLICT_B], project
            )
            self.assertIsNone(
                result.graph,
                "the known-conflict cell must refuse (graph=None) before any emit",
            )
            self.assertTrue(result.errors, "a refusal must carry errors")
            # Goose emits nothing for a refused cell: no recipe tree exists.
            self.assertFalse(
                os.path.isfile(os.path.join(project, "system2.recipe.yaml")),
                "no Goose recipe may be emitted for a refused cell",
            )
        finally:
            shutil.rmtree(project, ignore_errors=True)


class GooseRecipeValidateOracleTest(unittest.TestCase):
    """The real ``goose recipe validate`` leg — PASS or LOUD SKIP, hermetic HOME."""

    def test_all_emitted_recipes_validate_or_loud_skip(self):
        project = tempfile.mkdtemp(prefix="goose-validate-")
        home = tempfile.mkdtemp(prefix="goose-validate-home-")
        before = _dir_fingerprint(_REAL_GOOSE_CONFIG)
        had_permission = os.path.exists(
            os.path.join(_REAL_GOOSE_CONFIG, "permission.yaml")
        )
        try:
            graph, _written = _emit_core_overlay(project)

            recipes = [os.path.join(project, "system2.recipe.yaml")]
            for role in graph.delegation_contract.preferred_order:
                recipes.append(
                    os.path.join(project, "agents", f"{role}.recipe.yaml")
                )
            # >=14 recipes (orchestrator + 13 roles).
            self.assertGreaterEqual(len(recipes), 14)
            for rp in recipes:
                self.assertTrue(os.path.isfile(rp), f"missing recipe to validate: {rp}")

            if not _GOOSE_BIN:
                self.skipTest(_LOUD_SKIP)

            env = _hermetic_env(home)
            failures = []
            for rp in recipes:
                completed = subprocess.run(
                    [_GOOSE_BIN, "recipe", "validate", rp],
                    capture_output=True, text=True, env=env,
                )
                if completed.returncode != 0:
                    failures.append(
                        f"{os.path.relpath(rp, project)}: exit "
                        f"{completed.returncode}\nstdout={completed.stdout!r}\n"
                        f"stderr={completed.stderr!r}"
                    )
            self.assertEqual(
                [], failures,
                "goose recipe validate REJECTED emitted recipe(s) "
                "(serializer/schema gap):\n" + "\n".join(failures),
            )
        finally:
            shutil.rmtree(project, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)

        # The validate leg ran under a hermetic HOME: real config untouched.
        self.assertEqual(
            _dir_fingerprint(_REAL_GOOSE_CONFIG), before,
            "goose recipe validate modified the real ~/.config/goose",
        )
        self.assertEqual(
            os.path.exists(os.path.join(_REAL_GOOSE_CONFIG, "permission.yaml")),
            had_permission,
            "a permission.yaml appeared/vanished in the real ~/.config/goose",
        )


if __name__ == "__main__":
    unittest.main()
