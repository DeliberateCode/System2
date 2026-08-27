"""TASK-407 — Pi artifact goldens + the load-validity leg.

Drives the in-process ``ir.compose -> PiBackend.emit`` path for the Pi matrix cells
and asserts (design §"Phase 4 — Pi Backend", §"Test/validity strategy" legs 1 + 3;
AC-P2/AC-P6/AC-P7):

* **Artifact set** — the full deterministic Pi tree is emitted under ``project_path``
  only: ``.pi/extensions/system2.ts``, ``.pi/SYSTEM.md``, ``AGENTS.md``, the
  orchestrator prompt + the 13 ``.pi/prompts/role-*.md``, all six
  ``.pi/skills/system2-{init,compose,doctor,codex,gemini,stateless-loop}/SKILL.md``
  (exact name-set, not just a count), and ``system2.pi.lock.json``.
* **Determinism** — emit twice into two temp projects; the trees are byte-identical
  (output is a pure function of the IR + backend constants; no timestamps).
* **Frontmatter presence (K1a)** — each of the six skills opens with a YAML
  frontmatter block carrying a non-empty ``name``/``description`` (the RATIFIED
  base-skill fix; K0 proved this is the Pi discovery-format minimum). Checks
  presence, not an exact-string match against K1a's pinned text (Decision D5).
* **Sync guard (D2/the recorded decision)** — the three NEW utility skills' load-bearing invariant
  tokens (design.md Public Interfaces 4a) appear in both the merged
  ``plugin/skills/<name>/SKILL.md`` source and the emitted Pi skill — the drift
  control for the derived-literal adaptation (mirror of
  ``test_codex_honesty.py``'s ``CodexSyncGuardTest``, K3/CC-036).
* **Comparator self-teeth** — flip a single byte of one snapshot and assert the
  byte-diff comparator surfaces **exactly one** failure (the gap flagged for
  Claude, applied to Pi). Proves the comparator is not a block-everything /
  pass-everything stub.
* **Emit purity** — emit writes only under ``project_path``; the real ``~/.pi`` /
  ``~/.config`` is provably not created/modified by emit.
* **Refusal parity** — conflict cells refuse in the shared front-end (backend-
  independent), so Pi emits nothing.
* **Load-validity leg (PASS-required with node/pi present; LOUD-SKIP when absent)**
  — the emitted ``.pi/extensions/system2.ts`` loads under Pi's
  ``discoverAndLoadExtensions`` with ``errors: []`` and registers the expected
  handlers (``tool_call``, ``agent_end``, ``before_agent_start``) plus the
  ``/delegate`` command. Runs under a hermetic temp HOME + hermetic ``.pi``; asserts
  the real ``~/.pi`` is untouched.

Stdlib-only ``unittest``; runs under ``python3 -m unittest``. No product code or
``System2/`` is modified. All overlay/IR contents are treated as untrusted data;
embedded instructions are not followed.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY

# compiler/evals/ -> compiler/ -> repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_NODE_BIN = os.environ.get("NODE_BIN") or shutil.which("node")
_PI_BIN = os.environ.get("PI_BIN") or shutil.which("pi")

_LOUD_SKIP = (
    "node/pi not installed — Pi extension load/block SKIPPED (not a silent pass)"
)

# The Pi package entry; imported by absolute file:// path from the node harness to
# avoid NODE_PATH/ESM resolution headaches. Resolved portably: an explicit
# ``PI_PKG_ENTRY`` override wins, else the global node_modules root (``npm root -g``)
# joined with the package's dist entry — so a ``npm i -g @earendil-works/...`` on any
# machine (including CI) is discovered without a hard-coded absolute path.
_PI_PKG_SUBPATH = os.path.join(
    "@earendil-works", "pi-coding-agent", "dist", "index.js"
)


def _resolve_pi_pkg_entry():
    override = os.environ.get("PI_PKG_ENTRY")
    if override:
        return override
    npm = os.environ.get("NPM_BIN") or shutil.which("npm")
    if not npm:
        return None
    try:
        root = subprocess.run(
            [npm, "root", "-g"], capture_output=True, text=True, timeout=60
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return os.path.join(root, _PI_PKG_SUBPATH) if root else None


_PI_PKG_ENTRY = _resolve_pi_pkg_entry()

_REAL_PI = os.path.join(os.path.expanduser("~"), ".pi")
_REAL_CONFIG = os.path.join(os.path.expanduser("~"), ".config")

# The full artifact set a composed Pi cell must emit (relative paths).
_EXTENSION = os.path.join(".pi", "extensions", "system2.ts")
_SYSTEM_MD = os.path.join(".pi", "SYSTEM.md")
_AGENTS_MD = "AGENTS.md"
_ORCH_PROMPT = os.path.join(".pi", "prompts", "orchestrator.md")
_LOCK = "system2.pi.lock.json"
_SKILLS = tuple(
    os.path.join(".pi", "skills", f"system2-{name}", "SKILL.md")
    for name in ("init", "compose", "doctor", "codex", "gemini", "stateless-loop")
)


def _hermetic_env(home):
    """Minimal env with HOME + XDG_CONFIG_HOME forced under a temp dir."""
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    # A hermetic Pi config/discovery dir.
    env["PI_CONFIG_DIR"] = os.path.join(home, ".pi")
    return env


def _dir_fingerprint(path):
    """Sorted (relpath, size, mtime_ns) listing of *path*, or None when absent."""
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


def _emit_core_overlay(project_dir):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project_dir)
    if result.graph is None:
        raise AssertionError(
            f"compose refused the core+overlay cell: {result.errors!r}"
        )
    written = PiBackend().emit(result.graph, project_dir)
    return result.graph, written


def committed_pi_dist():
    """Return the committed ``distributions/pi`` package dir (the SHIPPED bytes), or None.

    ``SYSTEM2_PI_DIST`` overrides the location. Present iff the package's gate extension
    exists on disk — i.e. TASK-026 has committed the npm package.
    """
    cand = os.environ.get("SYSTEM2_PI_DIST") or os.path.join(
        _REPO_ROOT, "distributions", "pi"
    )
    return cand if os.path.isfile(
        os.path.join(cand, "extensions", "system2.ts")
    ) else None


def materialize_pi_project(project_dir):
    """Populate *project_dir* with the loadable Pi layout under test; return the source.

    Path-parameterized exactly like the codex proven-blocking test: when the committed
    npm package exists (``committed``), reconstruct the materialized project from the
    SHIPPED bytes — ``payload/project/*`` into the project root and the package's
    ``extensions/system2.ts`` into ``.pi/extensions/`` (where Pi's loader discovers it) —
    so the load + block legs validate the exact bytes that get published. Otherwise
    (``emitted``) emit the backend's canonical BASE+overlay cell in-process (the
    pre-commit path). The committed gate extension is byte-identical to that emission
    (empty vs test overlay does not change the gate), so both sources exercise the same
    gate — the committed leg additionally proves the SHIPPED payload loads under Pi.
    """
    dist = committed_pi_dist()
    if dist is None:
        _emit_core_overlay(project_dir)
        return "emitted"
    payload = os.path.join(dist, "payload", "project")
    for dirpath, _dirs, files in os.walk(payload):
        for fn in files:
            src = os.path.join(dirpath, fn)
            dst = os.path.join(project_dir, os.path.relpath(src, payload))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    ext_dst = os.path.join(project_dir, ".pi", "extensions", "system2.ts")
    os.makedirs(os.path.dirname(ext_dst), exist_ok=True)
    shutil.copyfile(os.path.join(dist, "extensions", "system2.ts"), ext_dst)
    return "committed"


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


def _parse_leading_frontmatter(text):
    """Parse a leading ``---\\nkey: value\\n...\\n---\\n\\n`` YAML block (K1a's exact
    emitted shape — single-line key/value pairs only). Returns ``{key: value}``, or
    ``None`` if *text* does not open with one."""
    if not text.startswith("---\n"):
        return None
    close = text.find("\n---\n", 4)
    if close == -1:
        return None
    fields = {}
    for line in text[4:close].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


# PR #10 review finding 1: use Pi's actual YAML parser, not a hand-written
# approximation. _PI_PKG_ENTRY resolves the installed, CI-pinned Pi package.
_FRONTMATTER_PARSE_HARNESS = r'''
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const { parseFrontmatter } = await import(pathToFileURL(process.argv[2]).href);
for (const path of process.argv.slice(3)) {
  const { frontmatter } = parseFrontmatter(readFileSync(path, "utf8"));
  if (!frontmatter.name || !frontmatter.description) {
    throw new Error(`${path}: missing name or description after YAML parse`);
  }
}
'''


def _byte_diff(snapshot, current):
    """Byte-diff two {rel: bytes} trees; return the list of differing rel paths.

    The minimal comparator the goldens rely on: a missing file, an extra file, or a
    differing file each count as one failure. No normalization (byte-identical).
    """
    failures = []
    for rel in sorted(set(snapshot) | set(current)):
        if rel not in current:
            failures.append(f"MISSING {rel}")
        elif rel not in snapshot:
            failures.append(f"EXTRA {rel}")
        elif snapshot[rel] != current[rel]:
            failures.append(f"DIFF {rel}")
    return failures


class PiEmitArtifactSetTest(unittest.TestCase):
    """The full Pi artifact set is emitted (AC-P6)."""

    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-goldens-")
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
        for rel in (_EXTENSION, _SYSTEM_MD, _AGENTS_MD, _ORCH_PROMPT, _LOCK):
            self.assertIn(rel, self.tree, f"emit must produce {rel!r}")
        for rel in _SKILLS:
            self.assertIn(rel, self.tree, f"emit must produce skill {rel!r}")

    def test_thirteen_role_prompts(self):
        for role in self.preferred:
            rel = os.path.join(".pi", "prompts", f"role-{role}.md")
            self.assertIn(rel, self.tree, f"missing role prompt for {role!r}")
        role_prompts = [
            r for r in self.tree
            if r.startswith(os.path.join(".pi", "prompts", "role-"))
            and r.endswith(".md")
        ]
        self.assertEqual(
            len(role_prompts), 13,
            f"expected exactly 13 role prompts, got {len(role_prompts)}: "
            f"{sorted(role_prompts)}",
        )

    def test_six_skill_name_set(self):
        # Exact name-set (K4/K3, CC-036) — fails on a missing OR an extra skill,
        # not just a count.
        skills = {
            r for r in self.tree
            if r.startswith(os.path.join(".pi", "skills") + os.sep)
            and r.endswith("SKILL.md")
        }
        self.assertEqual(
            skills, set(_SKILLS),
            f"expected exactly the six system2-* skill paths {sorted(_SKILLS)}, "
            f"got {sorted(skills)}",
        )

    def test_total_emitted_file_count(self):
        # Recomputed from actual emission (CC-REQ-087) — never asserted from
        # arithmetic. The RATIFIED base-skill fix (K1a) changes zero file COUNTS,
        # only the bytes of the three existing skill files, so the count observed
        # here still equals the pre-K1 21 + the 3 new skills.
        self.assertEqual(
            len(self.written), 24,
            f"expected 24 emitted files, got {len(self.written)}",
        )

    def test_all_six_skills_carry_nonempty_frontmatter(self):
        for rel in _SKILLS:
            fields = _parse_leading_frontmatter(self.tree[rel].decode("utf-8"))
            self.assertIsNotNone(
                fields, f"{rel} does not open with a YAML frontmatter block"
            )
            self.assertTrue(
                fields.get("name"), f"{rel}: frontmatter 'name' is missing/empty"
            )
            self.assertTrue(
                fields.get("description"),
                f"{rel}: frontmatter 'description' is missing/empty",
            )

    def test_frontmatter_parses_with_pis_actual_yaml_parser(self):
        """Pi must load every emitted skill; a syntax error makes it disappear."""
        if not (_NODE_BIN and _PI_PKG_ENTRY and os.path.isfile(_PI_PKG_ENTRY)):
            self.skipTest(_LOUD_SKIP)
        with tempfile.TemporaryDirectory(prefix="pi-frontmatter-") as temp:
            harness = os.path.join(temp, "parse-frontmatter.mjs")
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write(_FRONTMATTER_PARSE_HARNESS)
            result = subprocess.run(
                [_NODE_BIN, harness, _PI_PKG_ENTRY, *[
                    os.path.join(self.project, rel) for rel in _SKILLS
                ]],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(
            result.returncode,
            0,
            "Pi rejected emitted skill frontmatter:\n" + result.stderr,
        )

    def test_extension_registers_expected_seams_in_text(self):
        # Structural (text) confirmation; the live load leg proves it for real below.
        text = self.tree[_EXTENSION].decode("utf-8")
        for seam in ('pi.on("tool_call"', 'pi.on("agent_end"', 'pi.on("before_agent_start"'):
            self.assertIn(seam, text, f"extension must register {seam!r}")
        self.assertIn('pi.registerCommand("/delegate"', text)

    def test_lock_is_valid_json_with_capabilities(self):
        report = json.loads(self.tree[_LOCK].decode("utf-8"))
        self.assertEqual(report["backend"], "pi")
        self.assertIn("capabilities", report)
        self.assertIn("FIDELITY", report)


class PiEmitDeterminismTest(unittest.TestCase):
    """Emit twice -> byte-identical trees (pure function of the IR; no timestamps)."""

    def test_emit_twice_is_byte_identical(self):
        proj_a = tempfile.mkdtemp(prefix="pi-det-a-")
        proj_b = tempfile.mkdtemp(prefix="pi-det-b-")
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


class PiComparatorSelfTeethTest(unittest.TestCase):
    """A single-byte snapshot mutation surfaces EXACTLY one comparator failure.

    Negative control proving the byte-diff comparator is neither a pass-everything
    nor a fail-everything stub: an unmutated snapshot diffs clean, and flipping one
    byte of one file yields exactly one DIFF (and nothing else).
    """

    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-teeth-")
        _emit_core_overlay(cls.project)
        cls.tree = _read_tree(cls.project)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_unmutated_snapshot_is_empty_diff(self):
        self.assertEqual(
            [], _byte_diff(self.tree, self.tree),
            "an identical tree must produce an empty diff",
        )

    def test_single_byte_mutation_is_exactly_one_failure(self):
        target = _LOCK
        original = self.tree[target]
        # Flip the final content byte (before the trailing newline) deterministically.
        idx = len(original) - 2
        mutated_byte = bytes([original[idx] ^ 0x01])
        mutated = original[:idx] + mutated_byte + original[idx + 1 :]
        self.assertNotEqual(mutated, original, "mutation must change the bytes")

        current = dict(self.tree)
        current[target] = mutated
        failures = _byte_diff(self.tree, current)
        self.assertEqual(
            failures, [f"DIFF {target}"],
            f"a one-byte mutation must surface exactly one failure, got: {failures}",
        )

    def test_missing_file_is_exactly_one_failure(self):
        current = dict(self.tree)
        del current[_EXTENSION]
        failures = _byte_diff(self.tree, current)
        self.assertEqual(failures, [f"MISSING {_EXTENSION}"])


class PiEmitPurityTest(unittest.TestCase):
    """emit writes only under project_path; the real ~/.pi / ~/.config is untouched."""

    def test_emit_does_not_touch_real_pi_or_config(self):
        before_pi = _dir_fingerprint(_REAL_PI)
        before_cfg = _dir_fingerprint(_REAL_CONFIG)
        had_pi = os.path.isdir(_REAL_PI)
        project = tempfile.mkdtemp(prefix="pi-purity-")
        try:
            _emit_core_overlay(project)
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
            _dir_fingerprint(_REAL_PI), before_pi,
            "emit modified the real ~/.pi (it must be home-dir-free)",
        )
        self.assertEqual(
            _dir_fingerprint(_REAL_CONFIG), before_cfg,
            "emit modified the real ~/.config",
        )
        self.assertEqual(
            os.path.isdir(_REAL_PI), had_pi,
            "emit created/removed the real ~/.pi directory",
        )


class PiRefusalParityTest(unittest.TestCase):
    """Conflict cells refuse in the shared front-end; Pi emits nothing."""

    def test_conflict_cell_refuses_before_backend(self):
        project = tempfile.mkdtemp(prefix="pi-conflict-")
        try:
            result = ir.compose(
                _BASE, [matrix.CONFLICT_A, matrix.CONFLICT_B], project
            )
            self.assertIsNone(
                result.graph,
                "the known-conflict cell must refuse (graph=None) before any emit",
            )
            self.assertTrue(result.errors, "a refusal must carry errors")
            self.assertFalse(
                os.path.isfile(os.path.join(project, _EXTENSION)),
                "no Pi extension may be emitted for a refused cell",
            )
        finally:
            shutil.rmtree(project, ignore_errors=True)


_LOAD_HARNESS = r"""
const PKG = process.argv[2];
const projectRoot = process.argv[3];
const pkg = await import(PKG);
const { discoverAndLoadExtensions } = pkg;
const { extensions, errors } = await discoverAndLoadExtensions(
  [], projectRoot, projectRoot,
);
const ext = (extensions || []).find(
  (e) => e.path && e.path.endsWith("system2.ts"),
) || extensions[0];
const out = {
  errors: (errors || []).map((e) => (e && e.message) ? e.message : String(e)),
  loaded: !!ext,
  handlers: ext ? [...ext.handlers.keys()] : [],
  commands: ext ? [...ext.commands.keys()] : [],
};
process.stdout.write(JSON.stringify(out));
"""


class PiLoadValidityTest(unittest.TestCase):
    """Load leg — the SHIPPED extension loads under Pi with errors:[] (AC-P2).

    Path-parameterized: loads the committed ``distributions/pi/extensions/system2.ts``
    (the published bytes) when present, else the in-process emission. PASS-required when
    node/pi present; LOUD-SKIP (never a silent pass) when absent. Runs under a hermetic
    temp HOME + hermetic ``.pi``; asserts the real ``~/.pi`` is untouched.
    """

    def test_extension_loads_under_pi_or_loud_skip(self):
        if not (_NODE_BIN and _PI_BIN):
            self.skipTest(_LOUD_SKIP)
        self.assertTrue(
            _PI_PKG_ENTRY and os.path.isfile(_PI_PKG_ENTRY),
            f"Pi package entry not found at {_PI_PKG_ENTRY!r} "
            "(set PI_PKG_ENTRY or ensure @earendil-works/pi-coding-agent is "
            "installed in `npm root -g`)",
        )

        before_pi = _dir_fingerprint(_REAL_PI)
        had_pi = os.path.isdir(_REAL_PI)

        project = tempfile.mkdtemp(prefix="pi-load-")
        home = tempfile.mkdtemp(prefix="pi-load-home-")
        harness = os.path.join(home, "load_harness.mjs")
        try:
            # Validate the SHIPPED bytes: load the committed distributions/pi extension
            # when present, else the in-process emission (pre-commit fallback).
            materialize_pi_project(project)
            with open(harness, "w", encoding="utf-8") as fh:
                fh.write(_LOAD_HARNESS)

            env = _hermetic_env(home)
            completed = subprocess.run(
                [_NODE_BIN, harness, _PI_PKG_ENTRY, project],
                capture_output=True, text=True, env=env, timeout=120,
            )
            self.assertEqual(
                completed.returncode, 0,
                f"node load harness failed: exit {completed.returncode}\n"
                f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
            )
            result = json.loads(completed.stdout)

            self.assertEqual(
                result["errors"], [],
                f"Pi reported load errors for the generated extension: "
                f"{result['errors']}",
            )
            self.assertTrue(result["loaded"], "the extension was not loaded")
            for event in ("tool_call", "agent_end", "before_agent_start"):
                self.assertIn(
                    event, result["handlers"],
                    f"extension did not register the {event!r} handler; "
                    f"registered: {result['handlers']}",
                )
            self.assertIn(
                "/delegate", result["commands"],
                f"extension did not register the /delegate command; "
                f"registered: {result['commands']}",
            )
        finally:
            shutil.rmtree(project, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)

        # The load leg ran under a hermetic HOME: real ~/.pi untouched.
        self.assertEqual(
            _dir_fingerprint(_REAL_PI), before_pi,
            "the Pi load leg modified the real ~/.pi",
        )
        self.assertEqual(
            os.path.isdir(_REAL_PI), had_pi,
            "the Pi load leg created/removed the real ~/.pi directory",
        )


# ---------------------------------------------------------------------------
# D2/the recorded decision sync guard (K3, CC-036) — drift control for the derived-literal
# utility-skill builders (mirror of test_codex_honesty.py's CodexSyncGuardTest,
# J4). The 3x3 token list is duplicated across that file and this one, with this
# cross-reference comment (design.md Decision D2 / Public Interfaces 4a row 6;
# accepted duplication, recorded). Three NEW skills only — init/compose/doctor
# are not derived-literal adaptations of a Claude source and carry no
# sync-guard invariant tokens.
# ---------------------------------------------------------------------------

_SYNC_GUARD_TOKENS = {
    "codex": (
        "--ephemeral",
        "history.persistence=none",
        "Never pass the prompt unquoted",
    ),
    "gemini": (
        "agy -p",
        "--print-timeout",
        "Never pass the prompt unquoted",
    ),
    "stateless-loop": (
        "STATUS: CLEAN",
        "claude -p",
        "max_iterations",
    ),
}


def _source_skill_path(name):
    return os.path.join(_REPO_ROOT, "plugin", "skills", name, "SKILL.md")


def _emitted_skill_path(root, name):
    return os.path.join(root, ".pi", "skills", f"system2-{name}", "SKILL.md")


class PiSyncGuardTest(unittest.TestCase):
    """D2/the recorded decision: every invariant token survives in both the merged source and the
    emitted Pi skill, for the three NEW utility skills — the drift control for
    the tri-copy derived-literal adaptation (Decision D2)."""

    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-syncguard-")
        _emit_core_overlay(cls.project)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_invariant_tokens_present_in_source_and_emitted_skill(self):
        for name, tokens in _SYNC_GUARD_TOKENS.items():
            with open(_source_skill_path(name), encoding="utf-8") as fh:
                source_text = fh.read()
            with open(_emitted_skill_path(self.project, name), encoding="utf-8") as fh:
                emitted_text = fh.read()
            for token in tokens:
                with self.subTest(skill=name, token=token, surface="plugin/skills source"):
                    self.assertIn(
                        token, source_text,
                        f"{name}: invariant token {token!r} missing from the merged "
                        f"source plugin/skills/{name}/SKILL.md",
                    )
                with self.subTest(skill=name, token=token, surface="emitted Pi skill"):
                    self.assertIn(
                        token, emitted_text,
                        f"{name}: invariant token {token!r} missing from the emitted "
                        f".pi/skills/system2-{name}/SKILL.md",
                    )

    def test_guard_trips_if_a_token_is_dropped_from_the_emitted_copy(self):
        # Mutation self-test (teeth): prove the presence assertion would fail if an
        # invariant token were dropped from the emitted skill. Only a throwaway
        # in-memory copy is tampered; the real emission is never touched.
        name = "codex"
        token = _SYNC_GUARD_TOKENS[name][0]
        with open(_emitted_skill_path(self.project, name), encoding="utf-8") as fh:
            text = fh.read()
        tampered = text.replace(token, "[token removed]")
        self.assertNotEqual(tampered, text, "fixture guard: token was not present to remove")
        self.assertNotIn(token, tampered)


if __name__ == "__main__":
    unittest.main()
