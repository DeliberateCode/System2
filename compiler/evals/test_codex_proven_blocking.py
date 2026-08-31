"""Exercise generated Codex hooks end to end against bypass and fault corpora."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends.codex import CodexBackend
from evals import matrix, oracle

# The Pi proven-blocking corpus is the parity reference at normalized input.
from evals.test_pi_proven_blocking import (
    _ALLOW_CASES as _PI_ALLOW_CASES,
    _BLOCK_CASES as _PI_BLOCK_CASES,
    _ROLE_CASES as _PI_ROLE_CASES,
)

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY

_NODE_BIN = os.environ.get("NODE_BIN") or shutil.which("node")
_LOUD_SKIP = (
    "node not installed — Codex generated-hook block corpus SKIPPED (LOUD skip, "
    "not a silent pass; the CI skip-count-0 gate escalates this to a failure)"
)

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_CORPUS_PATH = os.path.join(_FIXTURES, "codex_corpus", "corpus.json")

_SHELL_HOOK = "system2-shell-guard.js"
_EDIT_HOOK = "system2-edit-guard.js"
_HOOK_FILE = {"shell": _SHELL_HOOK, "edit": _EDIT_HOOK}

# Mirror the backend caps so generated-hook drift remains observable.
_MAX_MATCH_LEN = 16384
_MAX_INPUT_BYTES = 1048576
_WATCHDOG_MS = 2000


# The CAPTURED codex-cli 0.142.5 PreToolUse event envelope (verbatim shape).
_CODEX_0_142_5_ENVELOPE = {
    "session_id": "sess_00000000",
    "turn_id": "turn_00000000",
    "transcript_path": "/tmp/codex-transcript.jsonl",
    "cwd": "/project",
    "hook_event_name": "PreToolUse",
    "model": "gpt-5.5",
    "permission_mode": "default",
    "tool_name": "Bash",
    "tool_use_id": "call_00000000",
}


def _codex_event(case_event):
    """Wrap a corpus event in the captured 0.142.5 PreToolUse envelope (case wins)."""
    return {**_CODEX_0_142_5_ENVELOPE, **case_event}


def _load_corpus():
    with open(_CORPUS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def _pi_corpus_names():
    """Every Pi proven-blocking corpus case name (block + allow + role cases)."""
    names = set()
    for row in _PI_BLOCK_CASES:
        names.add(row[0])
    for row in _PI_ALLOW_CASES:
        names.add(row[0])
    for row in _PI_ROLE_CASES:
        names.add(row[1])  # role cases key the name in slot 1
    return names


def _resolve_hooks_dir(tmp_project):
    """Return committed hooks when available, otherwise emit temporary hooks."""
    override = os.environ.get("SYSTEM2_CODEX_DIST")
    candidates = []
    if override:
        candidates.append(os.path.join(override, "user-hooks", "hooks"))
    # repo-root/distributions/codex/user-hooks/hooks (compiler/ -> repo root is one up).
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(repo_root)
    candidates.append(
        os.path.join(repo_root, "distributions", "codex", "user-hooks", "hooks")
    )
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, _SHELL_HOOK)):
            return cand, "committed"

    result = ir.compose(_BASE, [_TEST_OVERLAY], tmp_project)
    if result.graph is None:
        raise AssertionError(
            f"compose refused the core+overlay cell: {result.errors!r}"
        )
    CodexBackend().emit(result.graph, tmp_project)
    return os.path.join(tmp_project, "user-hooks", "hooks"), "emitted"


def _run_hook(hooks_dir, hook_kind, stdin_bytes, env_extra=None, timeout=30):
    """Spawn ``node <hook>.js`` with ``stdin_bytes`` (a str) on stdin; return the raw
    ``(returncode, stdout, stderr)``. Stdin is closed (EOF) — the normal case."""
    hook_path = os.path.join(hooks_dir, _HOOK_FILE[hook_kind])
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    completed = subprocess.run(
        [_NODE_BIN, hook_path],
        input=stdin_bytes,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _deny_of(obj):
    """The modern-schema deny reason if *obj* is a Codex ``permissionDecision:"deny"`` block, else None."""
    if not isinstance(obj, dict):
        return None
    hso = obj.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("permissionDecision") == "deny":
        return hso.get("permissionDecisionReason")
    return None


def _classify(returncode, stdout):
    """Map (exit code, stdout) to a decision: 'block' | 'allow' | 'error'."""
    if returncode == 2:
        return "block", None
    if returncode == 0:
        text = (stdout or "").strip()
        if not text:
            return "allow", None
        try:
            obj = json.loads(text)
        except ValueError:
            return "error", None
        reason = _deny_of(obj)
        if reason is not None:
            return "block", reason
        return "error", None
    return "error", None


class CodexProvenBlockingTest(unittest.TestCase):
    """End-to-end: the generated Node hooks block the whole bypass corpus."""

    @classmethod
    def setUpClass(cls):
        cls._skip = None
        cls._proc_error = None
        cls.results = {}
        cls.corpus = _load_corpus()
        if not _NODE_BIN:
            cls._skip = _LOUD_SKIP
            return
        cls.project = tempfile.mkdtemp(prefix="codex-block-")
        try:
            cls.hooks_dir, cls.hooks_source = _resolve_hooks_dir(cls.project)
        except Exception as exc:  # emission failure is a hard error, never a skip
            cls._proc_error = f"could not resolve/emit Codex hooks: {exc!r}"
            return

        for case in cls.corpus:
            raw = json.dumps(_codex_event(case["event"]))
            try:
                rc, out, err = _run_hook(
                    cls.hooks_dir, case["hook"], raw, case.get("env"),
                )
            except subprocess.SubprocessError as exc:
                cls._proc_error = (
                    f"node hook subprocess failed for case {case['name']!r}: {exc!r}"
                )
                return
            decision, reason = _classify(rc, out)
            cls.results[case["name"]] = {
                "decision": decision, "reason": reason,
                "rc": rc, "stdout": out, "stderr": err,
            }

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "project", None):
            shutil.rmtree(cls.project, ignore_errors=True)

    def setUp(self):
        if self._skip:
            self.skipTest(self._skip)
        if self._proc_error:
            self.fail(self._proc_error)

    def _cases(self, predicate):
        return [c for c in self.corpus if predicate(c)]

    def _assert_not_allow(self, out, ctx):
        """A fail-closed exit must never emit an allow."""
        text = (out or "").strip()
        if not text:
            return
        obj = json.loads(text)
        self.assertNotIn(
            "decision", obj, f"legacy top-level 'decision' key must be gone ({ctx})"
        )
        self.assertIsNotNone(
            _deny_of(obj),
            f"fail-closed stdout must be a modern deny, not an allow ({ctx}): {out!r}",
        )

    # -- Corpus outcome assertions ------------------------------------------

    def test_all_block_cases_block(self):
        """Every corpus block case is blocked (decision block OR exit-2 fail-closed),
        with the expected reason class — driven end-to-end through the real hook."""
        offenders = []
        for case in self._cases(lambda c: c["expect"] == "block"):
            res = self.results[case["name"]]
            if res["decision"] != "block":
                offenders.append(
                    f"{case['name']} (hook={case['hook']}): expected BLOCK, got "
                    f"{res['decision']} rc={res['rc']} stdout={res['stdout']!r} "
                    f"stderr={res['stderr']!r} — POSSIBLE REAL BYPASS in the "
                    f"event->normalization glue"
                )
                continue
            rc_want = case["reason_contains"]
            # A stdout decision carries the reason substring; a fail-closed exit-2 is
            # allowed to carry only the generic hook-error reason on stderr.
            if rc_want and res["rc"] == 0 and rc_want not in (res["reason"] or ""):
                offenders.append(
                    f"{case['name']}: blocked but reason {res['reason']!r} lacks "
                    f"{rc_want!r}"
                )
        self.assertFalse(
            offenders,
            "corpus block cases that did not block correctly:\n  "
            + "\n  ".join(offenders),
        )

    def test_all_allow_cases_allow(self):
        """The negative control: every benign case is ALLOWED (exit 0, no decision).
        If the gate blocked everything these would fail — proving it discriminates."""
        offenders = []
        for case in self._cases(lambda c: c["expect"] == "allow"):
            res = self.results[case["name"]]
            if res["decision"] != "allow":
                offenders.append(
                    f"{case['name']} (hook={case['hook']}): expected ALLOW, got "
                    f"{res['decision']} rc={res['rc']} stdout={res['stdout']!r} "
                    f"stderr={res['stderr']!r} — the gate over-blocks (not a real "
                    f"discriminating gate)"
                )
        self.assertFalse(
            offenders,
            "corpus allow cases that were blocked:\n  " + "\n  ".join(offenders),
        )

    def test_gate_discriminates_block_vs_allow(self):
        """Cross-cut: every block case blocked AND every allow case allowed. The single
        assertion that the gate is neither pass-everything nor block-everything."""
        blocks = self._cases(lambda c: c["expect"] == "block")
        allows = self._cases(lambda c: c["expect"] == "allow")
        self.assertTrue(blocks and allows, "corpus must contain both block and allow cases")
        all_blocked = all(self.results[c["name"]]["decision"] == "block" for c in blocks)
        none_over = all(self.results[c["name"]]["decision"] == "allow" for c in allows)
        self.assertTrue(
            all_blocked and none_over,
            "the generated hooks must block exactly the dangerous cases and allow the "
            "benign ones",
        )

    def test_block_json_is_modern_permission_decision_schema(self):
        """Policy blocks use the modern permissionDecision schema."""
        checked = 0
        for case in self._cases(lambda c: c["expect"] == "block"):
            res = self.results[case["name"]]
            if res["rc"] != 0:
                continue  # exit-2 fail-closed cases carry no stdout obligation here
            checked += 1
            obj = json.loads(res["stdout"])
            self.assertNotIn(
                "decision", obj,
                f"{case['name']}: legacy top-level 'decision' key must be gone",
            )
            hso = obj.get("hookSpecificOutput")
            self.assertIsInstance(
                hso, dict, f"{case['name']}: missing hookSpecificOutput"
            )
            self.assertEqual(
                hso.get("permissionDecision"), "deny",
                f"{case['name']}: permissionDecision must be 'deny'",
            )
            self.assertEqual(
                hso.get("hookEventName"), "PreToolUse",
                f"{case['name']}: hookEventName must be 'PreToolUse'",
            )
            self.assertEqual(
                hso.get("permissionDecisionReason"), res["reason"],
                f"{case['name']}: reason must live in permissionDecisionReason",
            )
        self.assertGreater(
            checked, 0, "no exit-0 block case exercised the modern-schema assertion"
        )

    def test_shell_string_and_argv_forms_both_block(self):
        """a dangerous shell call blocks whether Codex delivers it as a command
        STRING or an argv ARRAY (the array-join normalization glue under test)."""
        for name in ("dangerous_bash_string", "dangerous_bash_argv"):
            res = self.results[name]
            self.assertEqual(
                res["decision"], "block",
                f"{name}: `rm -rf /` slipped past the shell guard ({res})",
            )
        self.assertEqual(
            self.results["benign_bash_argv"]["decision"], "allow",
            "an argv-array benign command must still be allowed (argv normalization "
            "must not over-block)",
        )

    def test_multi_command_chains_block(self):
        """Dangerous commands remain blocked within shell command chains."""
        for name in ("chain_and_rm", "chain_semi_sudo_rm", "chain_pipe_rm",
                     "chain_pipe_curl_sh"):
            self.assertEqual(
                self.results[name]["decision"], "block",
                f"chain case {name} was not blocked ({self.results[name]})",
            )

    def test_heredoc_blocks(self):
        """Dangerous commands remain blocked inside heredocs."""
        self.assertEqual(
            self.results["heredoc_rm"]["decision"], "block",
            f"heredoc-embedded `rm -rf /` was not blocked ({self.results['heredoc_rm']})",
        )

    def test_dangerous_command_corpus_blocks(self):
        """The shell hook blocks the extended dangerous-command corpus."""
        for name in ("danger_mkfs", "danger_dd_of", "danger_drop_table",
                     "danger_delete_from_no_where", "danger_git_reset_hard",
                     "danger_git_push_force_main"):
            res = self.results[name]
            self.assertEqual(res["decision"], "block", f"{name} not blocked ({res})")
            self.assertIn("block-dangerous", res["reason"] or "")

    def test_event_key_variants_block(self):
        """the shell guard normalizes the command from `tool_input`, `arguments`,
        and the top-level `command` key alike — no event-key shape bypasses it."""
        for name in ("key_arguments_danger", "key_toplevel_danger"):
            self.assertEqual(
                self.results[name]["decision"], "block",
                f"event-key variant {name} bypassed the guard ({self.results[name]})",
            )

    def test_sensitive_path_corpus_blocks(self):
        """protect-sensitive fires end-to-end for .env, .ssh/, id_rsa, *.pem, and
        credentials via the edit guard, and for a slash-anchored .env via the shell
        guard."""
        for name in ("sens_env", "sens_ssh", "sens_id_rsa", "sens_pem",
                     "sens_credentials"):
            res = self.results[name]
            self.assertEqual(res["decision"], "block", f"{name} not blocked ({res})")
            self.assertIn("protect-sensitive", res["reason"] or "")
        shell = self.results["shell_sensitive_dotenv"]
        self.assertEqual(shell["decision"], "block")
        self.assertIn("protect-sensitive", shell["reason"] or "")

    def test_lease_enforcement_corpus(self):
        """enforce-lease end-to-end: off-scope (absolute + off-extension) blocked, in-scope allowed, `../` traversal fails closed, and an empty-scope (read-only) role's write fails closed."""
        for name in ("off_scope_write", "lease_offscope_ext", "traversal_write",
                     "traversal_py_write", "shell_write_redirect_offscope"):
            res = self.results[name]
            self.assertEqual(res["decision"], "block", f"{name} not blocked ({res})")
            self.assertIn("enforce-lease", res["reason"] or "")
        for name in ("in_scope_write", "lease_in_scope_ext"):
            self.assertEqual(
                self.results[name]["decision"], "allow",
                f"in-scope write {name} was blocked ({self.results[name]})",
            )

    def test_apply_patch_payload_variants(self):
        """Patch payload paths cannot bypass the edit guard."""
        self.assertEqual(self.results["patch_add_sensitive"]["decision"], "block")
        self.assertIn(
            "protect-sensitive",
            self.results["patch_add_sensitive"]["reason"] or "",
        )
        self.assertEqual(self.results["patch_update_offscope"]["decision"], "block")
        self.assertIn(
            "enforce-lease", self.results["patch_update_offscope"]["reason"] or "",
        )
        self.assertEqual(self.results["patch_unified_secrets"]["decision"], "block")
        self.assertIn(
            "protect-sensitive",
            self.results["patch_unified_secrets"]["reason"] or "",
        )

    def test_role_switch_lease(self):
        """Changing SYSTEM2_ACTIVE_ROLE updates the enforced write scope."""
        self.assertEqual(
            self.results["da_in_scope"]["decision"], "allow",
            "design-architect's in-scope write (spec/design.md) was blocked — the "
            "multi-line OR-joined allowlist is bricked",
        )
        self.assertEqual(self.results["da_off_scope"]["decision"], "block")
        self.assertIn("enforce-lease", self.results["da_off_scope"]["reason"] or "")
        self.assertEqual(
            self.results["reviewer_write_fail_closed"]["decision"], "block",
            "an empty-scope (read-only) role's write must fail CLOSED",
        )

    # -- Pi-corpus parity at the normalized-input boundary ------------------

    def test_pi_corpus_parity_zero_uncovered(self):
        """Every Pi proven-blocking corpus case has a Codex-event-shaped counterpart — enumerated with zero uncovered cases."""
        pi_names = _pi_corpus_names()
        covered = set()
        for case in self.corpus:
            for pin in case.get("pi_parity", []):
                covered.add(pin)
        # Every declared parity name must be a real Pi corpus case (no typos/drift).
        stray = covered - pi_names
        self.assertFalse(
            stray,
            f"corpus declares pi_parity names not in the Pi corpus (drift): {sorted(stray)}",
        )
        uncovered = pi_names - covered
        self.assertFalse(
            uncovered,
            "Pi corpus cases without a Codex-event-shaped counterpart: "
            f"{sorted(uncovered)}",
        )
        # And each covered Pi case's Codex counterpart(s) must actually have been
        # exercised end-to-end (present in the results), not merely declared.
        for case in self.corpus:
            if case.get("pi_parity"):
                self.assertIn(
                    case["name"], self.results,
                    f"parity case {case['name']} was declared but never run",
                )

    def test_no_block_case_silently_allowed(self):
        """Standing guard: NO corpus block case may resolve to 'allow'. A single silent
        allow is a real bypass and fails loudly with the exact case + event."""
        bypasses = []
        for case in self.corpus:
            if case["expect"] != "block":
                continue
            if self.results[case["name"]]["decision"] == "allow":
                bypasses.append((case["name"], case["hook"], case["event"]))
        self.assertFalse(
            bypasses,
            "REAL BYPASS — dangerous case(s) silently allowed by the generated hook:\n"
            + "\n".join(f"  {n} [{h}]: {json.dumps(e)}" for n, h, e in bypasses),
        )

    # --  fault cases (end-to-end, fail-closed) --------------------------

    def test_oversized_command_is_capped_and_decided(self):
        """a command longer than the match cap is CAPPED and STILL DECIDED — it
        blocks (never crash-open, never a silent allow)."""
        big = "echo " + ("a" * (_MAX_MATCH_LEN + 4096))
        rc, out, err = _run_hook(
            self.hooks_dir, "shell", json.dumps({"tool_input": {"command": big}}),
        )
        decision, reason = _classify(rc, out)
        self.assertEqual(
            decision, "block",
            f"oversized command was not decided-as-block (rc={rc} out={out!r} err={err!r})",
        )
        if rc == 0:
            self.assertIn("safe match length", reason or "")

    def test_oversized_stdin_fails_closed(self):
        """stdin above the hard input cap fails closed (exit 2), never allow."""
        payload = json.dumps({"tool_input": {"command": "x" * (_MAX_INPUT_BYTES + 2048)}})
        rc, out, err = _run_hook(self.hooks_dir, "shell", payload)
        self.assertEqual(
            rc, 2,
            f"oversized stdin did not fail closed with exit 2 (rc={rc} out={out!r})",
        )
        self._assert_not_allow(out, "oversized stdin")

    def test_watchdog_timeout_blocks(self):
        """a pathological/slow input (stdin held open, EOF never arrives) hits the
        watchdog and resolves to BLOCK (exit 2) — never a hang or silent allow."""
        hook_path = os.path.join(self.hooks_dir, _HOOK_FILE["shell"])
        proc = subprocess.Popen(
            [_NODE_BIN, hook_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=dict(os.environ),
        )
        try:
            # Write a partial (never-closed) event so the reader waits for more; the
            # watchdog (WATCHDOG_MS) must fire and fail closed well before this timeout.
            proc.stdin.write('{"tool_input": {"command": "rm -rf /"')
            proc.stdin.flush()
            out, err = proc.communicate(timeout=(_WATCHDOG_MS / 1000.0) + 8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail(
                "watchdog did not fire; the hook hung on never-closed stdin instead "
                "of failing closed"
            )
        self.assertEqual(
            proc.returncode, 2,
            f"watchdog did not resolve to fail-closed exit 2 (rc={proc.returncode} "
            f"out={out!r} err={err!r})",
        )
        self._assert_not_allow(out, "watchdog timeout")

    def test_malformed_json_fails_closed_in_both_hooks(self):
        """malformed JSON on stdin fails closed (exit 2 with a reason) on BOTH the
        shell and edit guards — a parse failure is never a silent allow."""
        for hook_kind in ("shell", "edit"):
            rc, out, err = _run_hook(self.hooks_dir, hook_kind, "not json {")
            self.assertEqual(
                rc, 2,
                f"{hook_kind} guard did not fail closed on malformed JSON "
                f"(rc={rc} out={out!r} err={err!r})",
            )
            self._assert_not_allow(out, f"malformed JSON ({hook_kind})")
            self.assertIn("system2-hook-error", err or "")

    def test_hooks_under_test_are_the_generated_node_scripts(self):
        """Sanity: the shell and edit guards exist as generated Node scripts at the
        resolved (committed or emitted) hooks dir, and record which source was used."""
        for name in (_SHELL_HOOK, _EDIT_HOOK):
            path = os.path.join(self.hooks_dir, name)
            self.assertTrue(os.path.isfile(path), f"missing generated hook: {path}")
            with open(path, "r", encoding="utf-8") as fh:
                head = fh.read(64)
            self.assertTrue(head.startswith("#!/usr/bin/env node"), f"{name} is not a Node script")
        self.assertIn(self.hooks_source, ("committed", "emitted"))


if __name__ == "__main__":
    unittest.main()
