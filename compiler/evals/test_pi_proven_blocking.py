"""Prove Pi blocking behavior with synthetic ``tool_call`` events and no LLM."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from evals.test_pi_goldens import materialize_pi_project

_NODE_BIN = os.environ.get("NODE_BIN") or shutil.which("node")
_PI_BIN = os.environ.get("PI_BIN") or shutil.which("pi")

_LOUD_SKIP = (
    "node/pi not installed — Pi extension load/block SKIPPED (not a silent pass)"
)

_PI_PKG_REL = os.path.join(
    "@earendil-works", "pi-coding-agent", "dist", "index.js"
)


def _resolve_pi_pkg_entry():
    """Resolve the Pi package's dist entry portably (no hard-coded $HOME path)."""
    override = os.environ.get("PI_PKG_ENTRY")
    if override and os.path.isfile(override):
        return override

    roots = []
    npm = shutil.which("npm")
    if npm:
        try:
            out = subprocess.run(
                [npm, "root", "-g"], capture_output=True, text=True, timeout=30
            )
            if out.returncode == 0 and out.stdout.strip():
                roots.append(out.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    if _PI_BIN:
        # <prefix>/bin/pi  ->  <prefix>/lib/node_modules
        prefix = os.path.dirname(os.path.dirname(os.path.realpath(_PI_BIN)))
        roots.append(os.path.join(prefix, "lib", "node_modules"))
        roots.append(os.path.join(prefix, "node_modules"))

    for root in roots:
        cand = os.path.join(root, _PI_PKG_REL)
        if os.path.isfile(cand):
            return cand
    return None


_PI_PKG_ENTRY = _resolve_pi_pkg_entry()

_REAL_PI = os.path.join(os.path.expanduser("~"), ".pi")
_EXTENSION = os.path.join(".pi", "extensions", "system2.ts")

# The synthetic event scenarios fired at the real handler.
_BLOCK_CASES = (
    ("off_scope_write", "write", {"path": "/etc/passwd", "content": "x"}, "enforce-lease"),
    ("dangerous_bash", "bash", {"command": "rm -rf /"}, "block-dangerous"),
    ("sensitive_read", "read", {"path": ".env"}, "protect-sensitive"),
    # Dangerous-command flag and whitespace variants.
    ("danger_rm_fr", "bash", {"command": "rm -fr /"}, "block-dangerous"),
    ("danger_rm_double_space", "bash", {"command": "rm  -rf /"}, "block-dangerous"),
    ("danger_rm_separated", "bash", {"command": "rm -r -f /"}, "block-dangerous"),
    ("danger_rm_long_flags", "bash", {"command": "rm --recursive --force /"}, "block-dangerous"),
    ("danger_sudo_rm", "bash", {"command": "sudo  rm -rf /etc"}, "block-dangerous"),
    ("danger_chmod_777", "bash", {"command": "chmod -R  777 /srv"}, "block-dangerous"),
    ("danger_git_push_f", "bash", {"command": "git push -f origin main"}, "block-dangerous"),
    # Traversal paths must block regardless of suffix.
    ("traversal_write", "write", {"path": "../../etc/cron.d/x.sh", "content": "x"}, "enforce-lease"),
    ("traversal_py_write", "write", {"path": "../secret.py", "content": "x"}, "enforce-lease"),
)
_ALLOW_CASES = (
    ("in_scope_write", "write", {"path": "src/main.py", "content": "x"}),
    ("benign_bash", "bash", {"command": "ls -la"}),
    ("ordinary_read", "read", {"path": "README.md"}),
    ("benign_rm_file", "bash", {"command": "rm file.txt"}),
)

# Role-switched cases: /delegate to a role, then fire a write.
_ROLE_CASES = (
    ("design-architect", "da_in_scope", "write", {"path": "spec/design.md", "content": "x"}, False),
    ("design-architect", "da_off_scope", "write", {"path": "src/x.py", "content": "x"}, True),
    ("code-reviewer", "reviewer_write_fail_closed", "write", {"path": "src/x.py", "content": "x"}, True),
)


def _hermetic_env(home):
    env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
    for k in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["PI_CONFIG_DIR"] = os.path.join(home, ".pi")
    return env


def _dir_fingerprint(path):
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


# The synthetic-event harness.
_BLOCK_HARNESS = r"""
const PKG = process.argv[2];
const projectRoot = process.argv[3];
const blockCases = JSON.parse(process.argv[4]);
const allowCases = JSON.parse(process.argv[5]);
const roleCases = JSON.parse(process.argv[6]);

const pkg = await import(PKG);
const { discoverAndLoadExtensions } = pkg;
const { extensions, errors } = await discoverAndLoadExtensions(
  [], projectRoot, projectRoot,
);
const loadErrors = (errors || []).map(
  (e) => (e && e.message) ? e.message : String(e),
);
const ext = (extensions || []).find(
  (e) => e.path && e.path.endsWith("system2.ts"),
) || extensions[0];

const out = { loadErrors, blocks: {}, allows: {}, roleBlocks: {}, delegate: {}, seams: {} };

if (!ext) {
  out.fatal = "extension did not load";
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}

const toolCallList = ext.handlers.get("tool_call") || [];
const toolCall = toolCallList[0];
out.seams.toolCallRegistered = !!toolCall;
out.seams.agentEndRegistered = (ext.handlers.get("agent_end") || []).length > 0;

// Fire the blocking cases.
for (const c of blockCases) {
  const r = await toolCall({ toolName: c.toolName, input: c.input });
  out.blocks[c.name] = {
    block: !!(r && r.block),
    reason: (r && r.reason) || null,
  };
}
// Fire the allowed cases (the negative control).
for (const c of allowCases) {
  const r = await toolCall({ toolName: c.toolName, input: c.input });
  out.allows[c.name] = { block: !!(r && r.block), raw: r === undefined ? null : r };
}

// /delegate dispatcher: valid + unknown role.
const delegate = ext.commands.get("/delegate");
out.seams.delegateRegistered = !!(delegate && delegate.handler);
if (delegate && delegate.handler) {
  let notes = [];
  const ctx = { ui: { notify: (msg, level) => notes.push({ level, msg }) } };
  await delegate.handler("executor", ctx);
  out.delegate.valid = notes.slice();
  notes = [];
  await delegate.handler("no-such-role", ctx);
  out.delegate.unknown = notes.slice();

  // Role-switched lease cases: /delegate flips activeRole, then fire a write at
  // the same tool_call handler and observe block/allow. Proves the multi-line-scope
  // role (design-architect) allows its in-scope write and blocks an off-scope one,
  // and that an empty-scope (read-only) role's write fails closed.
  for (const c of roleCases) {
    await delegate.handler(c.role, ctx);
    const r = await toolCall({ toolName: c.toolName, input: c.input });
    out.roleBlocks[c.name] = {
      block: !!(r && r.block),
      reason: (r && r.reason) || null,
    };
  }
  // Restore the default active role so later seams observe a clean state.
  await delegate.handler("executor", ctx);
}

// The before_agent_start injection seam survives and augments the prompt.
const basList = ext.handlers.get("before_agent_start") || [];
if (basList.length) {
  const r = await basList[0]({ systemPrompt: "ORIGINAL_PROMPT" });
  out.seams.beforeAgentStart = (r && typeof r.systemPrompt === "string")
    ? r.systemPrompt : null;
}

process.stdout.write(JSON.stringify(out));
"""


class PiProvenBlockingTest(unittest.TestCase):
    """Synthetic tool_call events prove the native gate blocks (no LLM)."""

    @classmethod
    def setUpClass(cls):
        cls._skip = None
        if not (_NODE_BIN and _PI_BIN):
            cls._skip = _LOUD_SKIP
            return
        if not _PI_PKG_ENTRY:
            cls._skip = (
                "Pi package entry could not be resolved (npm root -g / pi binary / "
                "PI_PKG_ENTRY all unresolvable) — Pi extension block SKIPPED "
                "(LOUD skip, not a silent pass)"
            )
            return

        cls._before_pi = _dir_fingerprint(_REAL_PI)
        cls._had_pi = os.path.isdir(_REAL_PI)
        cls.project = tempfile.mkdtemp(prefix="pi-block-")
        cls.home = tempfile.mkdtemp(prefix="pi-block-home-")
        # Drive the corpus through the SHIPPED gate: reconstruct the project from the
        cls.project_source = materialize_pi_project(cls.project)

        block_arg = json.dumps([
            {"name": n, "toolName": t, "input": i} for (n, t, i, _r) in _BLOCK_CASES
        ])
        allow_arg = json.dumps([
            {"name": n, "toolName": t, "input": i} for (n, t, i) in _ALLOW_CASES
        ])
        role_arg = json.dumps([
            {"role": role, "name": n, "toolName": t, "input": i}
            for (role, n, t, i, _b) in _ROLE_CASES
        ])
        harness = os.path.join(cls.home, "block_harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_BLOCK_HARNESS)

        env = _hermetic_env(cls.home)
        completed = subprocess.run(
            [
                _NODE_BIN, harness, _PI_PKG_ENTRY, cls.project,
                block_arg, allow_arg, role_arg,
            ],
            capture_output=True, text=True, env=env, timeout=120,
        )
        cls.completed = completed
        if completed.returncode != 0:
            cls._skip = None
            cls.result = None
            cls._proc_error = (
                f"node block harness failed: exit {completed.returncode}\n"
                f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}"
            )
        else:
            cls._proc_error = None
            cls.result = json.loads(completed.stdout)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "project", None):
            shutil.rmtree(cls.project, ignore_errors=True)
        if getattr(cls, "home", None):
            shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        if self._skip:
            self.skipTest(self._skip)
        if self._proc_error:
            self.fail(self._proc_error)

    def test_no_load_errors(self):
        self.assertEqual(
            self.result["loadErrors"], [],
            f"Pi reported load errors: {self.result['loadErrors']}",
        )
        self.assertNotIn("fatal", self.result, self.result.get("fatal"))
        self.assertTrue(self.result["seams"]["toolCallRegistered"])

    def test_off_scope_write_is_blocked(self):
        case = self.result["blocks"]["off_scope_write"]
        self.assertTrue(
            case["block"],
            "an off-write_scope write (/etc/passwd) was NOT blocked — the lease "
            "gate has no teeth",
        )
        self.assertIsNotNone(case["reason"], "a block must carry a lease reason")
        self.assertIn("enforce-lease", case["reason"])

    def test_dangerous_bash_is_blocked(self):
        case = self.result["blocks"]["dangerous_bash"]
        self.assertTrue(case["block"], "`rm -rf /` was NOT blocked")
        self.assertIn("block-dangerous", case["reason"])

    def test_sensitive_read_is_blocked(self):
        case = self.result["blocks"]["sensitive_read"]
        self.assertTrue(case["block"], "a `.env` read was NOT blocked")
        self.assertIn("protect-sensitive", case["reason"])

    def test_allowed_cases_are_not_blocked(self):
        # The negative control: if the gate blocked everything these would FAIL.
        for name in ("in_scope_write", "benign_bash", "ordinary_read"):
            case = self.result["allows"][name]
            self.assertFalse(
                case["block"],
                f"the allowed case {name!r} was blocked — the gate is a "
                "block-everything stub, not a real discriminating gate",
            )

    def test_gate_discriminates_block_vs_allow(self):
        # Ensure the gate discriminates between blocked and allowed cases.
        all_blocked = all(
            self.result["blocks"][n]["block"] for (n, *_r) in _BLOCK_CASES
        )
        none_allowed_blocked = not any(
            self.result["allows"][n]["block"] for (n, *_r) in _ALLOW_CASES
        )
        self.assertTrue(
            all_blocked and none_allowed_blocked,
            "the gate must block exactly the dangerous cases and allow the benign "
            f"ones; blocks={self.result['blocks']} allows={self.result['allows']}",
        )

    def test_obfuscated_dangerous_commands_are_blocked(self):
        # Every supported flag permutation must block.
        for name in (
            "danger_rm_fr",
            "danger_rm_double_space",
            "danger_rm_separated",
            "danger_rm_long_flags",
            "danger_sudo_rm",
            "danger_chmod_777",
            "danger_git_push_f",
        ):
            case = self.result["blocks"][name]
            self.assertTrue(
                case["block"],
                f"obfuscated dangerous command {name!r} was not blocked",
            )
            self.assertIn("block-dangerous", case["reason"])

    def test_traversal_writes_are_blocked(self):
        for name in ("traversal_write", "traversal_py_write"):
            case = self.result["blocks"][name]
            self.assertTrue(
                case["block"],
                f"traversal write {name!r} escaped the lease",
            )
            self.assertIn("enforce-lease", case["reason"])

    def test_multiline_scope_role_allows_in_scope_blocks_off_scope(self):
        # Multiline scopes allow matching paths and block non-matches.
        in_scope = self.result["roleBlocks"]["da_in_scope"]
        self.assertFalse(
            in_scope["block"],
            "design-architect's in-scope write (spec/design.md) was blocked",
        )
        off_scope = self.result["roleBlocks"]["da_off_scope"]
        self.assertTrue(
            off_scope["block"],
            "design-architect's off-scope write (src/x.py) was NOT blocked",
        )
        self.assertIn("enforce-lease", off_scope["reason"])

    def test_empty_scope_role_write_fails_closed(self):
        # a write by a read-only role (code-reviewer, empty write_scope) must be
        # BLOCKED (fail-closed), not allowed.
        case = self.result["roleBlocks"]["reviewer_write_fail_closed"]
        self.assertTrue(
            case["block"],
            "an empty-scope (read-only) role's write was NOT blocked — the lease "
            "must fail CLOSED for unscoped writers",
        )
        self.assertIn("enforce-lease", case["reason"])

    def test_delegate_accepts_valid_role(self):
        notes = self.result["delegate"]["valid"]
        self.assertTrue(notes, "/delegate to a valid role produced no notification")
        self.assertEqual(notes[0]["level"], "info")
        self.assertIn("executor", notes[0]["msg"])

    def test_delegate_rejects_unknown_role(self):
        notes = self.result["delegate"]["unknown"]
        self.assertTrue(notes, "/delegate to an unknown role produced no notification")
        self.assertEqual(
            notes[0]["level"], "error",
            "an unknown role must be rejected with an error notification",
        )
        self.assertIn("Unknown role", notes[0]["msg"])

    def test_injection_seam_survives_and_augments_prompt(self):
        # before_agent_start is the surviving context-injection seam.
        augmented = self.result["seams"]["beforeAgentStart"]
        self.assertIsNotNone(augmented, "before_agent_start did not augment the prompt")
        self.assertIn("ORIGINAL_PROMPT", augmented, "the seam dropped the base prompt")
        self.assertIn(".pi/SYSTEM.md", augmented, "the seam did not inject System2 context")

    def test_subagent_isolation_reported_honestly(self):
        # /delegate is an in-session role switch, not an isolated sub-session, so the honest report value is `adapted`.
        with open(
            os.path.join(self.project, "system2.pi.lock.json"), encoding="utf-8"
        ) as fh:
            report = json.load(fh)
        self.assertEqual(
            report["subagent_isolation"], "adapted",
            "the report must honestly call /delegate isolation `adapted` (an "
            "in-session role switch, not a native isolated sub-session)",
        )

    def test_real_pi_untouched(self):
        self.assertEqual(
            _dir_fingerprint(_REAL_PI), self._before_pi,
            "the proven-blocking leg modified the real ~/.pi",
        )
        self.assertEqual(
            os.path.isdir(_REAL_PI), self._had_pi,
            "the proven-blocking leg created/removed the real ~/.pi directory",
        )


if __name__ == "__main__":
    unittest.main()
