"""TASK-025 — Pi package supply-chain policy (F12) + init-materializer semantics (F5).

Two machine-enforced postures over the ``@deliberatecode/pi-system2`` package that
``build_pi_package.py`` produces (built fresh into a temp dir here exactly the way
``regen_all.py --only pi`` builds it: ``ir.compose`` over the BASE plugin with EMPTY
overlays -> ``PiBackend.emit`` -> ``build_pi_package.build``). The Pi backend and the
builder are read-only here; this file only observes their output.

F12 — supply-chain posture (no node required; always runs):
  * ``package.json`` declares NO ``scripts`` (hence no ``postinstall``), NO
    ``dependencies``, NO ``devDependencies``; it DOES carry the ``pi`` manifest, the
    ``pi-package`` keyword, the ``files`` whitelist, and a ``license``.
  * The generated extension sources (``extensions/system2.ts`` AND
    ``extensions/system2-init.ts``) import NOTHING external: only the pi type package
    ``@earendil-works/pi-coding-agent`` and ``node:`` builtins (+ relative paths). Any
    other bare specifier — via static ``from``, side-effect ``import``, dynamic
    ``import()``, or ``require()`` — is a FAIL.
  The policy CHECKERS are additionally proven against hostile fixtures (injected
  ``postinstall``, injected ``dependencies``, an external npm import) so a green result
  means the checker has teeth, not that it is vacuous.

F5 — init-materializer semantics (node REQUIRED; LOUD-skip if absent):
  The generated ``extensions/system2-init.ts`` is driven through ``node`` (v22 native
  TS type-stripping) by a small ``.mjs`` harness that imports the module, feeds it a
  mock ``ExtensionAPI`` to capture the ``/system2-init`` command, and invokes the
  handler against real temp project dirs. Cases: files materialized; second run is a
  zero-diff no-op (idempotent skip-if-identical); a user-modified managed file is left
  untouched without ``--force`` (reported); with ``--force`` it is overwritten and the
  "replacing user-modified <rel>" message is printed BEFORE the write; an out-of-root /
  absolute managed target is rejected fail-closed (exercises ``resolveWithinRoot`` via
  an injected hostile ``MANAGED_FILES``); unmanaged files are never touched.

node handling (skip-count-0 discipline, mirrors ``test_pi_proven_blocking`` /
``test_codex_proven_blocking``): node is REQUIRED. If genuinely absent the F5 legs
LOUD-skip via ``skipTest`` — and the CI skip-guard (``.github/workflows/ci.yml``: skip
count MUST be 0) escalates that skip to a job FAILURE, so a missing node can never
silently pass. node present but the harness subprocess failing is a HARD failure, never
a skip. Stdlib-only ``unittest``; no product code / ``System2/`` edits; all package
contents are treated as untrusted data.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_HERE)
_TOOLS_DIR = os.path.join(_COMPILER_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import build_pi_package  # noqa: E402  (tools/ is on sys.path above)
from system2_compiler import ir  # noqa: E402
from system2_compiler.backends.pi import PiBackend  # noqa: E402
from evals import oracle  # noqa: E402

_FIXTURES = os.path.join(_HERE, "fixtures", "pi_package")

_NODE_BIN = os.environ.get("NODE_BIN") or shutil.which("node")
_LOUD_SKIP = (
    "node not installed — Pi init-materializer (F5) legs SKIPPED (LOUD skip, not a "
    "silent pass; the CI skip-count-0 gate escalates this to a failure)"
)

# The ONLY npm specifier the generated extensions may reference (the pi-injected types).
_PI_TYPE_PACKAGE = "@earendil-works/pi-coding-agent"
_FORBIDDEN_PACKAGE_KEYS = ("scripts", "dependencies", "devDependencies")

# ---------------------------------------------------------------------------
# Package builder — mirrors regen_all._build_pi exactly (BASE plugin, EMPTY overlays).
# ---------------------------------------------------------------------------


def _build_package(dest):
    """Build the pi package into *dest* the way ``regen_all.py --only pi`` does."""
    with tempfile.TemporaryDirectory(prefix="pi-emit-") as staging:
        result = ir.compose(oracle.PLUGIN_ROOT, [], staging)
        if result.graph is None:
            raise AssertionError(f"pi compose refused the BASE cell: {result.errors!r}")
        PiBackend(overlay_sources=[]).emit(result.graph, staging)
        build_pi_package.build(staging, dest, build_pi_package.PACKAGE_VERSION)


# ---------------------------------------------------------------------------
# F12 policy checkers (pure, testable in isolation against hostile fixtures).
# ---------------------------------------------------------------------------

# Import-specifier extractors covering every surface a dependency could sneak in on.
_IMPORT_PATTERNS = (
    re.compile(r"""^\s*(?:import|export)\b[^\n;]*?\bfrom\s*["']([^"']+)["']""", re.M),
    re.compile(r"""^\s*import\s+["']([^"']+)["']""", re.M),          # side-effect import
    re.compile(r"""\bimport\s*\(\s*["']([^"']+)["']"""),             # dynamic import()
    re.compile(r"""\brequire\s*\(\s*["']([^"']+)["']"""),           # CJS require()
)


def _all_import_specifiers(ts_source):
    specs = []
    for pat in _IMPORT_PATTERNS:
        specs.extend(pat.findall(ts_source))
    return specs


def _external_imports(ts_source):
    """Return the sorted set of bare npm specifiers that are NOT the pi type package.

    Relative (``.``/``..``/``/``) and ``node:`` builtins are permitted; the pi type
    package is permitted; anything else is an external dependency = a policy violation.
    """
    external = set()
    for spec in _all_import_specifiers(ts_source):
        if spec.startswith((".", "/")):
            continue
        if spec.startswith("node:"):
            continue
        if spec == _PI_TYPE_PACKAGE:
            continue
        external.add(spec)
    return sorted(external)


def _package_policy_violations(pkg):
    """Return a list of F12 policy violations for a parsed ``package.json`` object."""
    violations = []
    for key in _FORBIDDEN_PACKAGE_KEYS:
        if key in pkg:
            violations.append(f"declares forbidden key {key!r}")
    # postinstall (and every other npm lifecycle hook) can only live under `scripts`,
    # which is already forbidden above; assert its specific absence for a clear signal.
    scripts = pkg.get("scripts")
    if isinstance(scripts, dict) and "postinstall" in scripts:
        violations.append("declares a postinstall script")
    return violations


# ---------------------------------------------------------------------------
# F5 node harness — drives the generated system2-init.ts command handler.
# ---------------------------------------------------------------------------

# Imports the built (or hostile-variant) system2-init.ts, hands it a mock ExtensionAPI to
# capture the /system2-init command, then invokes the handler against a real project dir
# (process.cwd == projectRoot). Captures every ctx.ui.notify call. For a "--force
# replacing user-modified <rel>" message it snapshots the target's ON-DISK content AT
# NOTIFY TIME: if the notify fires BEFORE the write (as required) that snapshot still
# holds the user's bytes, not the payload's. Emits one JSON object on stdout.
_INIT_HARNESS = r"""
import { pathToFileURL } from "node:url";
import * as fs from "node:fs";
import * as path from "node:path";

const modPath = process.argv[2];
const projectRoot = process.argv[3];
const args = process.argv[4] || "";

process.chdir(projectRoot);
const mod = await import(pathToFileURL(modPath).href);

const notes = [];
const replaceSnapshots = {};
let registeredName = null;
let spec = null;
const pi = { registerCommand: (name, s) => { registeredName = name; spec = s; } };
mod.default(pi);

const ctx = {
  ui: {
    notify: (msg, level) => {
      notes.push({ level: level || null, msg });
      const m = /replacing user-modified (\S+)/.exec(msg);
      if (m) {
        const rel = m[1];
        try {
          replaceSnapshots[rel] = fs.readFileSync(path.join(projectRoot, rel), "utf8");
        } catch {
          replaceSnapshots[rel] = null;
        }
      }
    },
  },
};

if (!spec || typeof spec.handler !== "function") {
  process.stdout.write(JSON.stringify({ fatal: "no /system2-init handler registered" }));
  process.exit(0);
}
await spec.handler(args, ctx);
process.stdout.write(JSON.stringify({
  command: registeredName,
  description: spec.description || null,
  notes,
  replaceSnapshots,
}));
"""


def _dir_snapshot(root):
    """Map every file under *root* to its bytes (for exact before/after comparison)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = fh.read()
    return out


class _NodeInitDriver:
    """Owns the harness .mjs file and runs the init command via node."""

    def __init__(self, workdir):
        self.harness = os.path.join(workdir, "init_harness.mjs")
        with open(self.harness, "w", encoding="utf-8") as fh:
            fh.write(_INIT_HARNESS)

    def run(self, mod_path, project_root, args=""):
        completed = subprocess.run(
            [_NODE_BIN, self.harness, mod_path, project_root, args],
            capture_output=True, text=True, timeout=120,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"init harness failed: exit {completed.returncode}\n"
                f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
            )
        return json.loads(completed.stdout)


def _summary_counts(notes):
    """Parse the terminal 'wrote X, skipped Y, replaced Z.' info note into ints."""
    for note in notes:
        m = re.search(r"wrote (\d+), skipped (\d+), replaced (\d+)", note["msg"])
        if m:
            return {
                "wrote": int(m.group(1)),
                "skipped": int(m.group(2)),
                "replaced": int(m.group(3)),
            }
    raise AssertionError(f"no summary note found in {notes!r}")


def _rejected_from_notes(notes):
    """Parse the rejected-managed-paths error note into a set of rels (empty if none)."""
    for note in notes:
        m = re.search(
            r"refused out-of-root or missing managed paths[^:]*:\s*(.+)$", note["msg"]
        )
        if m:
            return {p.strip() for p in m.group(1).split(",") if p.strip()}
    return set()


# ===========================================================================
# F12 — supply-chain policy (no node; always runs).
# ===========================================================================


class PiPackagePolicyTest(unittest.TestCase):
    """F12: package.json declares no scripts/deps; extensions import nothing external."""

    @classmethod
    def setUpClass(cls):
        cls.dest = tempfile.mkdtemp(prefix="pi-pkg-policy-")
        _build_package(cls.dest)
        with open(os.path.join(cls.dest, "package.json"), encoding="utf-8") as fh:
            cls.pkg = json.load(fh)
        cls.ext_sources = {}
        for name in ("system2.ts", "system2-init.ts"):
            with open(os.path.join(cls.dest, "extensions", name), encoding="utf-8") as fh:
                cls.ext_sources[name] = fh.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dest, ignore_errors=True)

    # ---- built-package policy (the real posture) -------------------------

    def test_package_json_has_no_forbidden_keys(self):
        violations = _package_policy_violations(self.pkg)
        self.assertEqual(
            violations, [],
            f"the shipped package.json violates F12 supply-chain policy: {violations}",
        )
        for key in _FORBIDDEN_PACKAGE_KEYS:
            self.assertNotIn(
                key, self.pkg, f"package.json must not declare {key!r} (F12)")

    def test_package_json_declares_no_postinstall(self):
        # Explicit: even if `scripts` ever returned, no install-time execution hook.
        scripts = self.pkg.get("scripts", {})
        self.assertNotIn("postinstall", scripts)
        self.assertEqual(scripts, {}, "the package must ship zero scripts (F12)")

    def test_package_json_has_required_fields(self):
        self.assertIsInstance(self.pkg.get("pi"), dict, "missing pi manifest")
        self.assertIn(
            "pi-package", self.pkg.get("keywords", []),
            "package must carry the 'pi-package' keyword",
        )
        self.assertIsInstance(
            self.pkg.get("files"), list, "package must declare a files whitelist")
        self.assertTrue(self.pkg["files"], "the files whitelist must be non-empty")
        self.assertTrue(self.pkg.get("license"), "package must declare a license")

    def test_extension_sources_import_nothing_external(self):
        for name, src in self.ext_sources.items():
            external = _external_imports(src)
            self.assertEqual(
                external, [],
                f"{name} imports external npm modules {external} — only "
                f"{_PI_TYPE_PACKAGE!r} + node: builtins are permitted (F12)",
            )

    def test_extension_sources_reference_only_pi_type_package(self):
        # Positive: the ONLY non-relative, non-node: specifier anywhere is the pi types.
        for name, src in self.ext_sources.items():
            bare = [
                s for s in _all_import_specifiers(src)
                if not s.startswith((".", "/", "node:"))
            ]
            self.assertTrue(
                set(bare) <= {_PI_TYPE_PACKAGE},
                f"{name} references unexpected packages: {sorted(set(bare))}",
            )

    def test_init_extension_guard_clauses_present(self):
        # Structural belt-and-suspenders for the F5 fail-closed guard: the out-of-root
        # rejection and package-relative payload anchor must be in the shipped source.
        src = self.ext_sources["system2-init.ts"]
        self.assertIn("resolveWithinRoot", src)
        self.assertIn("path.isAbsolute(rel)", src)
        self.assertIn("import.meta.url", src, "payload anchor must be package-relative")
        self.assertIn("MANAGED_FILES", src)

    # ---- checker teeth: hostile fixtures must be flagged -----------------

    def test_policy_checker_flags_injected_postinstall(self):
        with open(os.path.join(_FIXTURES, "bad_postinstall.package.json")) as fh:
            pkg = json.load(fh)
        violations = _package_policy_violations(pkg)
        self.assertTrue(
            any("postinstall" in v for v in violations),
            f"an injected postinstall was NOT caught: {violations}",
        )
        self.assertIn("declares forbidden key 'scripts'", violations)

    def test_policy_checker_flags_injected_dependencies(self):
        with open(os.path.join(_FIXTURES, "bad_dependencies.package.json")) as fh:
            pkg = json.load(fh)
        violations = _package_policy_violations(pkg)
        self.assertIn("declares forbidden key 'dependencies'", violations)
        self.assertIn("declares forbidden key 'devDependencies'", violations)

    def test_import_scanner_flags_external_import(self):
        with open(os.path.join(_FIXTURES, "bad_external_import.ts")) as fh:
            external = _external_imports(fh.read())
        # static default, named, side-effect, dynamic import(), and require() forms.
        self.assertEqual(
            external,
            sorted(["@scope/side-effect-pkg", "execa", "lodash", "zod", "chalk"]),
            f"the import scanner missed an external-import surface: {external}",
        )

    def test_import_scanner_clean_source_has_no_false_positives(self):
        with open(os.path.join(_FIXTURES, "clean_imports.ts")) as fh:
            external = _external_imports(fh.read())
        self.assertEqual(
            external, [],
            f"the scanner false-flagged pi/node:/relative imports: {external}",
        )


# ===========================================================================
# F5 — init-materializer semantics (node REQUIRED; LOUD-skip if absent).
# ===========================================================================


class PiInitMaterializerTest(unittest.TestCase):
    """F5: the generated /system2-init command materializes safely and idempotently."""

    @classmethod
    def setUpClass(cls):
        cls._skip = None
        if not _NODE_BIN:
            cls._skip = _LOUD_SKIP
            return
        cls.dest = tempfile.mkdtemp(prefix="pi-pkg-init-")
        _build_package(cls.dest)
        cls.init_mod = os.path.join(cls.dest, "extensions", "system2-init.ts")
        with open(cls.init_mod, encoding="utf-8") as fh:
            cls.init_src = fh.read()
        # Managed set embedded in the built module (project-relative payload files).
        cls.managed = json.loads(
            re.search(
                r"const MANAGED_FILES: string\[\] = (\[[^\]]*\]);", cls.init_src
            ).group(1)
        )
        cls.payload_root = os.path.join(cls.dest, "payload", "project")
        cls.workdir = tempfile.mkdtemp(prefix="pi-pkg-init-work-")
        cls.driver = _NodeInitDriver(cls.workdir)

    @classmethod
    def tearDownClass(cls):
        for attr in ("dest", "workdir"):
            path = getattr(cls, attr, None)
            if path:
                shutil.rmtree(path, ignore_errors=True)

    def setUp(self):
        if self._skip:
            self.skipTest(self._skip)
        self.project = tempfile.mkdtemp(prefix="pi-proj-")
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def _payload_bytes(self, rel):
        with open(os.path.join(self.payload_root, rel), "rb") as fh:
            return fh.read()

    # -- cases -------------------------------------------------------------

    def test_materialize_creates_all_managed_files(self):
        out = self.driver.run(self.init_mod, self.project)
        self.assertEqual(out["command"], "/system2-init")
        counts = _summary_counts(out["notes"])
        self.assertEqual(counts["wrote"], len(self.managed))
        self.assertEqual(counts["skipped"], 0)
        self.assertEqual(counts["replaced"], 0)
        self.assertEqual(_rejected_from_notes(out["notes"]), set())
        # Every managed file exists with byte-identical payload content.
        for rel in self.managed:
            target = os.path.join(self.project, rel)
            self.assertTrue(os.path.isfile(target), f"{rel} was not materialized")
            with open(target, "rb") as fh:
                self.assertEqual(
                    fh.read(), self._payload_bytes(rel),
                    f"{rel} content does not match the package payload",
                )

    def test_second_run_is_idempotent_zero_diff(self):
        self.driver.run(self.init_mod, self.project)
        before = _dir_snapshot(self.project)
        out = self.driver.run(self.init_mod, self.project)
        after = _dir_snapshot(self.project)
        self.assertEqual(
            before, after, "a second /system2-init run changed the project (not idempotent)")
        counts = _summary_counts(out["notes"])
        self.assertEqual(counts["wrote"], 0, "idempotent re-run must write nothing")
        self.assertEqual(counts["replaced"], 0)
        self.assertEqual(counts["skipped"], len(self.managed))

    def test_user_modified_file_left_untouched_without_force(self):
        rel = self.managed[0]
        target = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        user_bytes = b"USER LOCAL EDIT - do not clobber\n"
        with open(target, "wb") as fh:
            fh.write(user_bytes)
        out = self.driver.run(self.init_mod, self.project)
        with open(target, "rb") as fh:
            self.assertEqual(
                fh.read(), user_bytes,
                f"{rel} was overwritten WITHOUT --force (F5 violation)",
            )
        counts = _summary_counts(out["notes"])
        self.assertEqual(counts["replaced"], 0, "nothing may be replaced without --force")
        # And it is reported (not silently ignored).
        self.assertTrue(
            any(rel in n["msg"] and "left unchanged" in n["msg"] for n in out["notes"]),
            f"the untouched user-modified file {rel} was not reported: {out['notes']}",
        )

    def test_force_overwrites_and_prints_replacing_before_write(self):
        rel = self.managed[0]
        target = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        user_bytes = b"USER LOCAL EDIT - clobber me with --force\n"
        with open(target, "wb") as fh:
            fh.write(user_bytes)
        out = self.driver.run(self.init_mod, self.project, args="--force")
        # Overwritten with the payload bytes.
        with open(target, "rb") as fh:
            self.assertEqual(
                fh.read(), self._payload_bytes(rel),
                f"--force did not overwrite {rel} with the payload content",
            )
        counts = _summary_counts(out["notes"])
        self.assertEqual(counts["replaced"], 1)
        # The "replacing user-modified <rel>" message was emitted...
        self.assertTrue(
            any(f"replacing user-modified {rel}" in n["msg"] for n in out["notes"]),
            f"the --force replace warning for {rel} was not printed: {out['notes']}",
        )
        # ...and it was emitted BEFORE the write: the on-disk snapshot captured at
        # notify time still held the USER bytes, not the payload's.
        self.assertIn(rel, out["replaceSnapshots"])
        self.assertEqual(
            out["replaceSnapshots"][rel], user_bytes.decode(),
            "the 'replacing user-modified' message was printed AFTER the write — the "
            "user's clobbered content must be announced BEFORE it is destroyed (F5)",
        )

    def test_out_of_root_managed_paths_rejected_fail_closed(self):
        # MANAGED_FILES is fixed/in-root in the real package, so exercise resolveWithinRoot
        # by building a HOSTILE variant of the module whose MANAGED_FILES injects a
        # ../-escaping path and an absolute path alongside one legitimate in-root file.
        variant_root = tempfile.mkdtemp(prefix="pi-hostile-")
        self.addCleanup(shutil.rmtree, variant_root, ignore_errors=True)
        # Copy the payload so the legitimate entry can still be written.
        shutil.copytree(
            self.payload_root, os.path.join(variant_root, "payload", "project"))
        legit = self.managed[0]
        abs_escape = os.path.join(
            tempfile.gettempdir(), "s2-abs-escape-must-not-exist.txt")
        parent_escape = "../s2-parent-escape-must-not-exist.txt"
        hostile = [legit, parent_escape, abs_escape]
        src = re.sub(
            r"const MANAGED_FILES: string\[\] = \[[^\]]*\];",
            "const MANAGED_FILES: string[] = " + json.dumps(hostile) + ";",
            self.init_src, count=1,
        )
        variant_ext = os.path.join(variant_root, "extensions")
        os.makedirs(variant_ext)
        variant_mod = os.path.join(variant_ext, "system2-init.ts")
        with open(variant_mod, "w", encoding="utf-8") as fh:
            fh.write(src)

        # Pre-clean any stale escape artifacts.
        for p in (abs_escape, os.path.abspath(
                os.path.join(self.project, os.pardir, os.path.basename(parent_escape)))):
            if os.path.exists(p):
                os.remove(p)

        out = self.driver.run(variant_mod, self.project)
        rejected = _rejected_from_notes(out["notes"])
        self.assertIn(parent_escape, rejected, "a ../ managed path was not rejected")
        self.assertIn(abs_escape, rejected, "an absolute managed path was not rejected")
        # Fail-closed: NOTHING was written outside the project root.
        self.assertFalse(
            os.path.exists(abs_escape),
            "an absolute managed target was written outside the project root (F5)",
        )
        parent_target = os.path.abspath(
            os.path.join(self.project, os.pardir, os.path.basename(parent_escape)))
        self.assertFalse(
            os.path.exists(parent_target),
            "a ../-escaping managed target was written outside the project root (F5)",
        )
        # The legitimate in-root entry still materialized (rejection is targeted, not
        # a blanket abort — the negative control that the guard discriminates).
        self.assertTrue(os.path.isfile(os.path.join(self.project, legit)))

    def test_unmanaged_files_are_never_touched(self):
        # An unrelated file the user owns must survive materialization untouched.
        unrelated = os.path.join(self.project, "MY_NOTES.txt")
        payload_val = b"private user content that is not a managed file\n"
        with open(unrelated, "wb") as fh:
            fh.write(payload_val)
        nested = os.path.join(self.project, "src", "keep.py")
        os.makedirs(os.path.dirname(nested))
        with open(nested, "wb") as fh:
            fh.write(b"print('keep me')\n")
        self.driver.run(self.init_mod, self.project)
        with open(unrelated, "rb") as fh:
            self.assertEqual(fh.read(), payload_val, "an unmanaged file was modified (F5)")
        self.assertTrue(os.path.isfile(nested), "an unmanaged file was deleted (F5)")
        with open(nested, "rb") as fh:
            self.assertEqual(fh.read(), b"print('keep me')\n")


if __name__ == "__main__":
    unittest.main()
