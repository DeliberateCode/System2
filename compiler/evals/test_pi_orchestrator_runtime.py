"""Non-skipping Pi 0.85.1 runtime tests for parent/child orchestration."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from evals import oracle
from evals.pi_test_support import resolve_pi_pkg_entry
from evals.test_pi_package import _build_package
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend

_NODE = os.environ.get("NODE_BIN") or shutil.which("node")
_PI = os.environ.get("PI_BIN") or shutil.which("pi")
_PI_ENTRY = resolve_pi_pkg_entry(_PI)
_ROLES = [
    "repo-governor",
    "spec-coordinator",
    "requirements-engineer",
    "design-architect",
    "task-planner",
    "executor",
    "test-engineer",
    "security-sentinel",
    "eval-engineer",
    "docs-release",
    "code-reviewer",
    "postmortem-scribe",
    "mcp-toolsmith",
]

_PARENT_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const roles = JSON.parse(process.argv[4]);
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
const notices = [];
const statuses = [];
let activeTools = ["read", "bash", "edit", "write", "system2_request_gate_approval", "system2_delegate"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [...ext.tools.values()].map((item) => item.definition);
loaded.runtime.getCommands = () => [...ext.commands.entries()].map(([name]) => ({ name, source: "extension", sourceInfo: { origin: "project" } }));
loaded.runtime.sendUserMessage = () => {};
const ctx = {
  cwd: project,
  mode: "tui",
  hasUI: true,
  model: { provider: "test-provider", id: "test-model" },
  thinkingLevel: "off",
  sessionManager: { getBranch: () => entries },
  ui: {
    notify: (message, level) => notices.push({ message, level }),
    confirm: async () => true,
    setStatus: (key, value) => statuses.push({ key, value }),
  },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const before = await (ext.handlers.get("before_agent_start") || [])[0]({ systemPrompt: "BASE" }, ctx);
const gate = ext.tools.get("system2_request_gate_approval").definition;
const gateResult = await gate.execute("gate-0", { gate: 0, scopeSummary: "Bounded native acceptance", taskWriteScope: ["^spec/design\\.md$"], artifactPaths: ["evidence.txt"] }, undefined, undefined, ctx);
const approvalId = gateResult.details.decision.id;
const delegated = [];
for (const role of roles) {
  const contract = {
    role,
    task: `Read-only native acceptance for ${role}`,
    gateContext: { gate: 1, approvalId, constraints: ["Do not modify files"] },
    artifactInputs: ["evidence.txt"],
    acceptanceCriteria: ["Return one bounded report"],
    verificationCommands: [],
    taskWriteScope: role === "design-architect" ? ["^spec/design\\.md$"] : [],
    budget: { maxFiles: 0, maxChangedLines: 0 },
    timeoutSeconds: 30,
  };
  const result = await ext.tools.get("system2_delegate").definition.execute(
    `delegate-${role}`, contract, undefined, undefined, ctx,
  );
  delegated.push(result.details);
}
const after = await (ext.handlers.get("before_agent_start") || [])[0]({ systemPrompt: "BASE" }, ctx);
const parentWrite = await (ext.handlers.get("tool_call") || [])[0]({ toolName: "write", input: { path: "src/no.py", content: "x" } }, ctx);
await ext.commands.get("system2-status").handler("", ctx);
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  commands: [...ext.commands.keys()],
  tools: [...ext.tools.keys()],
  before, after, gateResult, delegated, entries, notices, statuses, activeTools,
  parentWrite,
}));
"""

_FAILURE_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
let activeTools = ["read", "bash", "edit", "write", "system2_request_gate_approval", "system2_delegate"];
let duplicateTopology = false;
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [...ext.tools.values()].map((item) => item.definition);
loaded.runtime.getCommands = () => {
  const commands = [...ext.commands.entries()].map(([name]) => ({ name, source: "extension" }));
  if (duplicateTopology) commands.push({ name: "system2-source-marker:2", source: "package" });
  return commands;
};
const ctx = {
  cwd: project, mode: "tui", hasUI: true,
  model: { provider: "test-provider", id: "test-model" }, thinkingLevel: "off",
  sessionManager: { getBranch: () => entries },
  ui: { notify: () => {}, confirm: async () => true, setStatus: () => {} },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const delegate = ext.tools.get("system2_delegate").definition;
const contract = {
  role: "code-reviewer", task: "Review failure recovery",
  gateContext: { gate: 1, approvalId: "pending", constraints: [] },
  artifactInputs: [], acceptanceCriteria: ["Return one report"], verificationCommands: [],
  taskWriteScope: [], budget: { maxFiles: 0, maxChangedLines: 0 }, timeoutSeconds: 30,
};
let beforeGateError;
try { await delegate.execute("before-gate", contract, undefined, undefined, ctx); }
catch (error) { beforeGateError = error.message; }
const gate = ext.tools.get("system2_request_gate_approval").definition;
let noUiError;
try { await gate.execute("no-ui", { gate: 0, scopeSummary: "must fail", taskWriteScope: [], artifactPaths: [] }, undefined, undefined, { ...ctx, hasUI: false }); }
catch (error) { noUiError = error.message; }
const approval = await gate.execute("gate", { gate: 0, scopeSummary: "failure test", taskWriteScope: [], artifactPaths: [] }, undefined, undefined, ctx);
contract.gateContext.approvalId = approval.details.decision.id;
let malformedError;
try { await delegate.execute("malformed", { ...contract, unexpected: true }, undefined, undefined, ctx); }
catch (error) { malformedError = error.message; }
let unsafeScopeError;
try { await delegate.execute("unsafe-scope", { ...contract, taskWriteScope: ["^(a+)+$"] }, undefined, undefined, ctx); }
catch (error) { unsafeScopeError = error.message; }
let unapprovedScopeError;
try { await delegate.execute("unapproved-scope", { ...contract, role: "executor", taskWriteScope: ["^src/.*$"] }, undefined, undefined, ctx); }
catch (error) { unapprovedScopeError = error.message; }
let unapprovedArtifactError;
try { await delegate.execute("unapproved-artifact", { ...contract, artifactInputs: ["evidence.txt"] }, undefined, undefined, ctx); }
catch (error) { unapprovedArtifactError = error.message; }
duplicateTopology = true;
let duplicateError;
try { await delegate.execute("duplicate", contract, undefined, undefined, ctx); }
catch (error) { duplicateError = error.message; }
duplicateTopology = false;
let processError;
try { await delegate.execute("fails", contract, undefined, undefined, ctx); }
catch (error) { processError = error.message; }
const stateAfterFailure = entries[entries.length - 1].data;
const recovered = await delegate.execute("recovers", contract, undefined, undefined, ctx);
const stateAfterRecovery = entries[entries.length - 1].data;
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  beforeGateError, noUiError, malformedError, unsafeScopeError, unapprovedScopeError, unapprovedArtifactError, duplicateError, processError,
  stateAfterFailure, recovered, stateAfterRecovery, entries,
}));
"""

_RESTORE_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const entries = JSON.parse(process.argv[4]);
const shouldRecover = process.argv[5] === "recover";
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const notices = [];
let activeTools = ["read", "bash", "edit", "write", "system2_request_gate_approval", "system2_delegate"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [...ext.tools.values()].map((item) => item.definition);
loaded.runtime.getCommands = () => [...ext.commands.entries()].map(([name]) => ({ name, source: "extension", sourceInfo: { origin: "project" } }));
const ctx = {
  cwd: project, mode: "tui", hasUI: true,
  sessionManager: { getBranch: () => entries },
  ui: {
    notify: (message, level) => notices.push({ message, level }),
    confirm: async () => true,
    setStatus: () => {},
  },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const before = await (ext.handlers.get("before_agent_start") || [])[0]({ systemPrompt: "BASE" }, ctx);
await ext.commands.get("system2-status").handler("", ctx);
const statusBefore = JSON.parse(notices[notices.length - 1].message);
const parentWrite = await (ext.handlers.get("tool_call") || [])[0]({ toolName: "write", input: { path: "spec/context.md", content: "x" } }, ctx);
if (shouldRecover) await ext.commands.get("system2-recover").handler("", ctx);
await ext.commands.get("system2-status").handler("", ctx);
const statusAfter = JSON.parse(notices[notices.length - 1].message);
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  before, statusBefore, statusAfter, parentWrite, entries, notices,
}));
"""

_CHILD_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
let activeTools = ["read", "bash", "edit", "write"];
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
const emptyBranch = { getBranch: () => [] };
const ctx = {
  cwd: project, mode: "json", hasUI: false,
  sessionManager: emptyBranch,
  ui: { setStatus: () => {}, notify: () => {} },
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const prompt = await (ext.handlers.get("before_agent_start") || [])[0]({ systemPrompt: "BASE" }, ctx);
const toolCall = (ext.handlers.get("tool_call") || [])[0];
const inScope = await toolCall({ toolName: "write", input: { path: "spec/design.md", content: "x" } }, ctx);
const offScope = await toolCall({ toolName: "write", input: { path: "src/no.py", content: "x" } }, ctx);
const siblingCtx = {
  ...ctx,
  sessionManager: { getBranch: () => [{ type: "message", message: { role: "assistant", content: [
    { type: "toolCall", name: "read", id: "one" },
    { type: "toolCall", name: "system2_child_complete", id: "two" },
  ] } }] },
};
const sibling = await toolCall({ toolName: "system2_child_complete", input: {} }, siblingCtx);
const complete = ext.tools.get("system2_child_complete").definition;
const report = {
  outcome: "complete", summary: "Reviewed design contract", artifacts: [], verification: [],
  risks: [], followUps: [], filesTouched: [], linesAdded: 0, linesRemoved: 0,
};
const completion = await complete.execute("complete", report, undefined, undefined, ctx);
let duplicateError = null;
try { await complete.execute("complete-again", report, undefined, undefined, ctx); }
catch (error) { duplicateError = error.message; }
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  tools: [...ext.tools.keys()], commands: [...ext.commands.keys()], activeTools,
  prompt, inScope: inScope ?? null, offScope, sibling, completion, duplicateError,
}));
"""


class PiOrchestratorRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _NODE or not _PI or not _PI_ENTRY:
            raise AssertionError(
                "Pi orchestrator runtime acceptance requires installed node and Pi; skipping is forbidden"
            )
        version = subprocess.run(
            [_PI, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        if version != "0.85.1":
            raise AssertionError(f"Pi 0.85.1 required, found {version!r}")

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="pi-orchestrator-runtime-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.project = os.path.join(self.temp, "project")
        self.home = os.path.join(self.temp, "home")
        self.bin_dir = os.path.join(self.temp, "bin")
        os.makedirs(self.project)
        os.makedirs(self.home)
        os.makedirs(self.bin_dir)
        result = ir.compose(oracle.PLUGIN_ROOT, [], self.project)
        self.assertIsNotNone(result.graph, result.errors)
        PiBackend().emit(result.graph, self.project)

    def _env(self):
        env = {
            "HOME": self.home,
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
            "PI_CODING_AGENT_DIR": os.path.join(self.home, ".pi", "agent"),
            "PI_OFFLINE": "1",
            "PI_SKIP_VERSION_CHECK": "1",
        }
        for key in ("LANG", "LC_ALL", "LC_CTYPE"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        fake_pi = os.path.join(self.bin_dir, "pi")
        if os.path.isfile(fake_pi):
            env["SYSTEM2_PI_BIN"] = os.path.realpath(fake_pi)
        return env

    def _run_restore(self, entries, recover=False):
        harness = os.path.join(self.temp, f"restore-{len(os.listdir(self.temp))}.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_RESTORE_HARNESS)
        completed = subprocess.run(
            [
                _NODE,
                harness,
                _PI_ENTRY,
                self.project,
                json.dumps(entries),
                "recover" if recover else "status",
            ],
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        return result

    def _design_child_spec(self, extension=None, role_prompt=None):
        extension = extension or os.path.join(
            self.project, ".pi", "extensions", "system2.ts"
        )
        role_prompt = role_prompt or os.path.join(
            self.project, ".pi", "prompts", "role-design-architect.md"
        )
        with open(extension, "rb") as fh:
            extension_digest = hashlib.sha256(fh.read()).hexdigest()
        with open(role_prompt, "rb") as fh:
            prompt_digest = hashlib.sha256(fh.read()).hexdigest()
        contract = {
            "role": "design-architect",
            "task": "Review the design artifact",
            "gateContext": {"gate": 1, "approvalId": "human-test", "constraints": []},
            "artifactInputs": [],
            "acceptanceCriteria": ["Return one bounded report"],
            "verificationCommands": [],
            "taskWriteScope": [r"^spec/design\.md$"],
            "budget": {"maxFiles": 1, "maxChangedLines": 10},
            "timeoutSeconds": 30,
        }
        contract_bytes = json.dumps(
            {"contract": contract, "artifactInputs": []},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return {
            "schemaVersion": 1,
            "id": "child-native-test",
            "nonce": "nonce-native-test",
            "parentPid": os.getpid(),
            "projectRoot": os.path.realpath(self.project),
            "extensionDigest": extension_digest,
            "rolePromptDigest": prompt_digest,
            "roleWriteScope": (
                r"(?:^spec/design\.md$)|(?:^spec/interfaces\.json$)|"
                r"(?:^spec/module-boundaries\.json$)"
            ),
            "tools": ["read", "edit", "write", "grep", "find"],
            "artifactInputs": [],
            "contractDigest": hashlib.sha256(contract_bytes).hexdigest(),
            "contract": contract,
        }

    def _write_child_spec(self, spec):
        spec_dir = os.path.join(self.temp, f"child-spec-{len(os.listdir(self.temp))}")
        os.makedirs(spec_dir, mode=0o700)
        spec_path = os.path.join(spec_dir, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        os.chmod(spec_path, 0o600)
        return spec_path

    def _write_fake_pi(self, record_path):
        fake = os.path.join(self.bin_dir, "pi")
        script = r'''#!/usr/bin/env node
const fs = require("node:fs");
const crypto = require("node:crypto");
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : (value && typeof value === "object") ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const specPath = process.env.SYSTEM2_PI_CHILD_SPEC;
const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
const stat = fs.statSync(specPath);
fs.appendFileSync(process.env.FAKE_PI_RECORD, JSON.stringify({
  pid: process.pid,
  argv: process.argv.slice(2),
  spec,
  specMode: stat.mode & 0o777,
}) + "\n");
const report = {
  outcome: "complete",
  summary: `Native fake child completed ${spec.contract.role}`,
  artifacts: [], verification: [], risks: [], followUps: [],
  filesTouched: [], linesAdded: 0, linesRemoved: 0,
};
process.stdout.write(JSON.stringify({
  type: "tool_execution_end", toolCallId: "child-complete",
  toolName: "system2_child_complete", isError: false,
  result: { content: [{ type: "text", text: "complete" }], details: {
    schemaVersion: 1, delegationId: spec.id, nonce: spec.nonce, role: spec.contract.role,
    contractDigest: spec.contractDigest, rolePromptDigest: spec.rolePromptDigest,
    reportDigest: crypto.createHash("sha256").update(canonical(report)).digest("hex"), report,
  } },
}) + "\n");
'''
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(fake, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return fake

    def _write_flaky_pi(self, record_path):
        fake = os.path.join(self.bin_dir, "pi")
        script = r'''#!/usr/bin/env node
const fs = require("node:fs");
const crypto = require("node:crypto");
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : (value && typeof value === "object") ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const specPath = process.env.SYSTEM2_PI_CHILD_SPEC;
const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
fs.appendFileSync(process.env.FAKE_PI_RECORD, JSON.stringify({ specPath, id: spec.id, pid: process.pid }) + "\n");
const marker = process.env.FAKE_PI_RECORD + ".failed";
if (!fs.existsSync(marker)) {
  fs.writeFileSync(marker, "failed once\n");
  process.stderr.write("intentional child failure\n");
  process.exit(7);
}
const report = {
  outcome: "complete", summary: "Recovered on retry", artifacts: [], verification: [],
  risks: [], followUps: [], filesTouched: [], linesAdded: 0, linesRemoved: 0,
};
process.stdout.write(JSON.stringify({
  type: "tool_execution_end", toolCallId: "child-complete",
  toolName: "system2_child_complete", isError: false,
  result: { details: {
    schemaVersion: 1, delegationId: spec.id, nonce: spec.nonce, role: spec.contract.role,
    contractDigest: spec.contractDigest, rolePromptDigest: spec.rolePromptDigest,
    reportDigest: crypto.createHash("sha256").update(canonical(report)).digest("hex"), report,
  }, content: [{ type: "text", text: "done" }] },
}) + "\n");
'''
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(fake, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def test_parent_stays_orchestrator_and_dispatches_all_13_processes(self):
        record_path = os.path.join(self.temp, "child-records.jsonl")
        with open(os.path.join(self.project, "evidence.txt"), "wb") as fh:
            fh.write(b"provenance-bound evidence\n")
        self._write_fake_pi(record_path)
        harness = os.path.join(self.temp, "parent.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_PARENT_HARNESS)
        env = self._env()
        env["FAKE_PI_RECORD"] = record_path
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project, json.dumps(_ROLES)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        self.assertEqual(
            set(result["tools"]),
            {"system2_delegate", "system2_request_gate_approval"},
        )
        self.assertIn("system2-status", result["commands"])
        self.assertIn("system2-recover", result["commands"])
        self.assertIn("SYSTEM2 PARENT AUTHORITY: mode=orchestrator", result["before"]["systemPrompt"])
        self.assertIn("SYSTEM2 PARENT AUTHORITY: mode=orchestrator", result["after"]["systemPrompt"])
        self.assertNotIn("System2 role:", result["after"]["systemPrompt"])
        self.assertTrue(result["parentWrite"]["block"])
        self.assertEqual(result["gateResult"]["details"]["decision"]["source"], "tui")
        self.assertEqual(
            result["gateResult"]["details"]["decision"]["taskWriteScope"],
            [r"^spec/design\.md$"],
        )
        artifact = result["gateResult"]["details"]["decision"]["artifacts"][0]
        self.assertEqual(artifact["status"], "present")
        self.assertEqual(
            artifact["sha256"], hashlib.sha256(b"provenance-bound evidence\n").hexdigest()
        )
        self.assertEqual(len(result["delegated"]), 13)
        self.assertEqual({item["role"] for item in result["delegated"]}, set(_ROLES))
        self.assertTrue(all(item["independent_agent"] for item in result["delegated"]))
        self.assertTrue(all(item["isolation"]["process"] for item in result["delegated"]))
        self.assertTrue(all(not item["isolation"]["filesystem"] for item in result["delegated"]))
        state_entries = [
            entry for entry in result["entries"]
            if entry["customType"] == "system2-workflow-state"
        ]
        self.assertEqual([entry["data"]["generation"] for entry in state_entries], list(range(1, 28)))
        self.assertIsNone(state_entries[-1]["data"]["activeDelegation"])
        self.assertEqual(state_entries[-1]["data"]["lastDelegation"]["role"], "mcp-toolsmith")
        with open(record_path, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(records), 13)
        self.assertEqual({record["spec"]["contract"]["role"] for record in records}, set(_ROLES))
        design_record = next(record for record in records if record["spec"]["contract"]["role"] == "design-architect")
        self.assertEqual(design_record["spec"]["contract"]["taskWriteScope"], [r"^spec/design\.md$"])
        self.assertEqual(len({record["pid"] for record in records}), 13)
        for record in records:
            self.assertEqual(record["specMode"], 0o600)
            self.assertIn("--no-session", record["argv"])
            self.assertIn("--no-extensions", record["argv"])
            self.assertIn("--mode", record["argv"])
            tools_arg = record["argv"][record["argv"].index("--tools") + 1].split(",")
            self.assertIn("system2_child_complete", tools_arg)
            self.assertNotIn("system2_delegate", tools_arg)
            self.assertEqual(record["spec"]["tools"].count("system2_delegate"), 0)
            self.assertEqual(len(record["spec"]["rolePromptDigest"]), 64)
            self.assertEqual(len(record["spec"]["contractDigest"]), 64)
            self.assertEqual(record["spec"]["artifactInputs"], [{
                "path": "evidence.txt",
                "sha256": hashlib.sha256(b"provenance-bound evidence\n").hexdigest(),
            }])

        # A fresh Node/Pi loader process reconstructs the authoritative branch state.
        restored = self._run_restore(result["entries"])
        self.assertTrue(restored["statusBefore"]["healthy"])
        self.assertEqual(restored["statusBefore"]["generation"], 27)
        self.assertEqual(restored["statusBefore"]["lastDelegation"]["role"], "mcp-toolsmith")
        self.assertIn("mode=orchestrator", restored["before"]["systemPrompt"])

        # A forked branch sees only its own ancestral transitions.
        forked = self._run_restore(result["entries"][:3])
        self.assertEqual(forked["statusBefore"]["generation"], 3)
        self.assertEqual(forked["statusBefore"]["lastDelegation"]["role"], "repo-governor")

        # Restart during a child run fails closed until explicit human recovery.
        interrupted = self._run_restore(result["entries"][:2], recover=True)
        self.assertFalse(interrupted["statusBefore"]["healthy"])
        self.assertIn("interrupted delegation", interrupted["statusBefore"]["healthDiagnostic"])
        self.assertTrue(interrupted["parentWrite"]["block"])
        self.assertTrue(interrupted["statusAfter"]["healthy"])
        self.assertEqual(interrupted["statusAfter"]["generation"], 1)
        self.assertEqual(interrupted["statusAfter"]["gate"]["current"], 0)
        self.assertTrue(any(entry["customType"] == "system2-workflow-recovery" for entry in interrupted["entries"]))

        malformed_entries = result["entries"][:3] + [{
            "type": "custom",
            "customType": "system2-workflow-state",
            "data": {"schemaVersion": 1, "mode": "orchestrator", "generation": 99},
        }]
        malformed = self._run_restore(malformed_entries)
        self.assertFalse(malformed["statusBefore"]["healthy"])
        self.assertIn("invalid workflow state", malformed["statusBefore"]["healthDiagnostic"])

    def test_tampered_child_authority_fails_closed(self):
        spec = self._design_child_spec()
        spec["extensionDigest"] = "0" * 64
        spec_path = self._write_child_spec(spec)
        harness = os.path.join(self.temp, "tampered-child.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_CHILD_HARNESS)
        env = self._env()
        env["SYSTEM2_PI_CHILD_SPEC"] = spec_path
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            cwd=self.project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid System2 child authority", completed.stderr)
        self.assertIn("extension digest mismatch", completed.stderr)

    def test_contract_topology_and_subprocess_failures_fail_closed_then_retry(self):
        record_path = os.path.join(self.temp, "flaky-records.jsonl")
        with open(os.path.join(self.project, "evidence.txt"), "wb") as fh:
            fh.write(b"not approved by this gate\n")
        self._write_flaky_pi(record_path)
        harness = os.path.join(self.temp, "failure.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_FAILURE_HARNESS)
        env = self._env()
        env["FAKE_PI_RECORD"] = record_path
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        self.assertIn("Gate 0", result["beforeGateError"])
        self.assertIn("interactive human TUI", result["noUiError"])
        self.assertIn("incomplete or unknown", result["malformedError"])
        self.assertIn("safe anchored path-regex subset", result["unsafeScopeError"])
        self.assertIn("not approved by the human gate", result["unapprovedScopeError"])
        self.assertIn("not bound to the latest human-approved digest", result["unapprovedArtifactError"])
        self.assertIn("ambiguous System2 extension topology", result["duplicateError"])
        self.assertIn("Untrusted child diagnostics were withheld", result["processError"])
        self.assertNotIn("intentional child failure", result["processError"])
        self.assertIn("untrusted diagnostic content withheld", result["stateAfterFailure"]["lastDelegation"]["diagnostic"])
        self.assertNotIn("intentional child failure", result["stateAfterFailure"]["lastDelegation"]["diagnostic"])
        self.assertIsNone(result["stateAfterFailure"]["activeDelegation"])
        self.assertEqual(result["stateAfterFailure"]["lastDelegation"]["status"], "failed")
        self.assertIsNone(result["stateAfterRecovery"]["activeDelegation"])
        self.assertEqual(result["stateAfterRecovery"]["lastDelegation"]["status"], "complete")
        self.assertEqual(result["recovered"]["details"]["report"]["summary"], "Recovered on retry")
        with open(record_path, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["pid"], records[1]["pid"])
        self.assertTrue(all(not os.path.exists(record["specPath"]) for record in records))

    def test_child_mode_has_bounded_authority_and_terminating_report(self):
        spec_path = self._write_child_spec(self._design_child_spec())
        harness = os.path.join(self.temp, "child.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_CHILD_HARNESS)
        env = self._env()
        env["SYSTEM2_PI_CHILD_SPEC"] = spec_path
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            cwd=self.project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        self.assertEqual(result["tools"], ["system2_child_complete"])
        self.assertNotIn("delegate", result["commands"])
        self.assertEqual(
            set(result["activeTools"]),
            {"read", "edit", "write", "grep", "find", "system2_child_complete"},
        )
        self.assertIn("SYSTEM2 ISOLATED SPECIALIST AUTHORITY", result["prompt"]["systemPrompt"])
        self.assertIn("System2 role: design-architect", result["prompt"]["systemPrompt"])
        self.assertIsNone(result["inScope"])
        self.assertTrue(result["offScope"]["block"])
        self.assertTrue(result["sibling"]["block"])
        self.assertTrue(result["completion"]["terminate"])
        self.assertEqual(result["completion"]["details"]["nonce"], "nonce-native-test")
        self.assertEqual(result["completion"]["details"]["report"]["outcome"], "complete")
        self.assertIn("duplicate", result["duplicateError"])

    def test_real_pi_json_package_child_executes_terminating_completion_tool(self):
        package = os.path.join(self.temp, "package")
        _build_package(package)
        extension = os.path.join(package, "extensions", "system2.ts")
        role_prompt = os.path.join(
            package, "prompts", "role-design-architect.md"
        )
        spec_path = self._write_child_spec(
            self._design_child_spec(extension=extension, role_prompt=role_prompt)
        )
        provider = os.path.join(self.temp, "deterministic-provider.ts")
        with open(provider, "w", encoding="utf-8") as fh:
            fh.write(
                r'''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";

export default function (pi: ExtensionAPI) {
  pi.registerProvider("system2-test", {
    name: "System2 deterministic acceptance provider",
    baseUrl: "http://127.0.0.1/unused",
    apiKey: "native-test-key",
    api: "openai-completions",
    models: [{
      id: "deterministic", name: "Deterministic", reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 32000, maxTokens: 1024,
    }],
    streamSimple(model) {
      const stream = createAssistantMessageEventStream();
      queueMicrotask(() => {
        const output = {
          role: "assistant",
          content: [],
          api: model.api,
          provider: model.provider,
          model: model.id,
          usage: {
            input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
          },
          stopReason: "pending",
          timestamp: Date.now(),
        };
        stream.push({ type: "start", partial: output });
        const toolCall = {
          type: "toolCall", id: "native-child-complete", name: "system2_child_complete",
          arguments: {
            outcome: "complete", summary: "Real Pi child completed deterministically",
            artifacts: [], verification: [], risks: [], followUps: [], filesTouched: [],
            linesAdded: 0, linesRemoved: 0,
          },
        };
        output.content.push(toolCall);
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: output });
        output.stopReason = "toolUse";
        stream.push({ type: "done", reason: "toolUse", message: output });
        stream.end();
      });
      return stream;
    },
  });
}
'''
            )
        env = self._env()
        env["SYSTEM2_PI_CHILD_SPEC"] = spec_path
        try:
            completed = subprocess.run(
                [
                    _PI,
                "--mode",
                "json",
                "--no-session",
                "--no-extensions",
                "-e",
                extension,
                "-e",
                provider,
                "--tools",
                "read,edit,write,grep,find,system2_child_complete",
                "--provider",
                "system2-test",
                "--model",
                "deterministic",
                    "Execute the supplied System2 child contract.",
                ],
                cwd=self.project,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired as error:
            self.fail(f"real Pi child timed out; stdout={error.stdout!r} stderr={error.stderr!r}")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        events = [json.loads(line) for line in completed.stdout.splitlines() if line]
        completions = [
            event
            for event in events
            if event.get("type") == "tool_execution_end"
            and event.get("toolName") == "system2_child_complete"
            and not event.get("isError")
        ]
        self.assertEqual(len(completions), 1, events)
        details = completions[0]["result"]["details"]
        self.assertEqual(details["delegationId"], "child-native-test")
        self.assertEqual(details["role"], "design-architect")
        self.assertEqual(details["report"]["outcome"], "complete")
        self.assertTrue(completions[0]["result"]["terminate"])


if __name__ == "__main__":
    unittest.main()
