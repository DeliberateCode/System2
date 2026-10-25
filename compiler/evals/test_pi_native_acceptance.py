"""Non-skipping native acceptance against the caller-provided Pi runtime."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from evals import matrix, oracle
from evals.pi_test_support import resolve_pi_pkg_entry
from evals.test_pi_package import _build_package
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend

_PI_BIN = os.environ.get("PI_BIN") or shutil.which("pi")
_NODE_BIN = os.environ.get("NODE_BIN") or shutil.which("node")


def _emit(project):
    result = ir.compose(oracle.PLUGIN_ROOT, [matrix.TEST_OVERLAY], project)
    if result.graph is None:
        raise AssertionError(result.errors)
    PiBackend().emit(result.graph, project)


def _env(home, agent_dir):
    env = {
        "HOME": home,
        "PATH": os.environ.get("PATH", ""),
        "PI_CODING_AGENT_DIR": agent_dir,
        "PI_OFFLINE": "1",
        "PI_SKIP_VERSION_CHECK": "1",
    }
    for key in ("LANG", "LC_ALL", "LC_CTYPE"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


class _RpcClient:
    def __init__(self, cwd, env, session_dir):
        self.proc = subprocess.Popen(
            [
                _PI_BIN,
                "--mode",
                "rpc",
                "--approve",
                "--offline",
                "--session-dir",
                session_dir,
            ],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._next_id = 0
        self.events = []

    def request(self, command):
        self._next_id += 1
        request_id = f"req-{self._next_id}"
        command = {"id": request_id, **command}
        self.proc.stdin.write(json.dumps(command) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read()
                raise AssertionError(
                    f"Pi RPC ended before {request_id}; rc={self.proc.poll()} stderr={stderr!r}"
                )
            event = json.loads(line)
            self.events.append(event)
            if event.get("type") == "response" and event.get("id") == request_id:
                if not event.get("success"):
                    raise AssertionError(f"Pi RPC request failed: {event}")
                return event

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()


class PiNativeAcceptanceTest(unittest.TestCase):
    """Drive public Pi command dispatch and documented lifecycle APIs."""

    @classmethod
    def setUpClass(cls):
        if not _PI_BIN or not _NODE_BIN or not resolve_pi_pkg_entry(_PI_BIN):
            raise AssertionError(
                "Pi native acceptance requires installed Node and Pi; skipping is not allowed"
            )
        version = subprocess.run(
            [_PI_BIN, "--version"], capture_output=True, text=True, timeout=30
        )
        if version.returncode != 0 or not version.stdout.strip():
            raise AssertionError(
                "Pi native acceptance could not identify the caller-provided Pi runtime: "
                + version.stderr.strip()
            )
        cls.pi_version = version.stdout.strip()

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="pi-native-acceptance-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.project = os.path.join(self.temp, "project")
        self.home = os.path.join(self.temp, "home")
        self.agent_dir = os.path.join(self.home, ".pi", "agent")
        self.session_dir = os.path.join(self.temp, "sessions")
        os.makedirs(self.project)
        os.makedirs(self.agent_dir)
        os.makedirs(self.session_dir)
        _emit(self.project)

    def test_rpc_exposes_human_status_without_switching_parent_role(self):
        client = _RpcClient(
            self.project, _env(self.home, self.agent_dir), self.session_dir
        )
        self.addCleanup(client.close)

        commands = client.request({"type": "get_commands"})["data"]["commands"]
        names = {command["name"] for command in commands}
        self.assertIn("delegate", names)
        self.assertIn("system2-status", names)
        self.assertIn("system2-history", names)
        self.assertIn("system2-new", names)
        self.assertIn("system2-resume", names)
        self.assertIn("system2-recover", names)
        self.assertNotIn("/delegate", names)

        client.request({"type": "prompt", "message": "/system2-status"})
        entries = client.request({"type": "get_entries"})["data"]["entries"]
        self.assertFalse(
            any(entry.get("customType") == "system2-role" for entry in entries)
        )
        notifications = [
            event for event in client.events
            if event.get("type") == "extension_ui_request"
            and event.get("method") == "notify"
        ]
        status = json.loads(notifications[-1]["message"])
        self.assertEqual(status["mode"], "orchestrator")
        self.assertTrue(status["healthy"])
        self.assertTrue(os.path.isabs(status["childExecutable"]))
        self.assertNotIn("unavailable", status["childExecutable"])
        self.assertIn("system2_status", status["modelControlTools"])
        self.assertIn("system2_request_gate_skip", status["modelControlTools"])
        for tool in ("bash", "write", "edit", "system2_status"):
            self.assertIn(tool, status["activeTools"])
        # Status is a human command and never requires a model/network turn.
        self.assertFalse(
            any(event.get("type") == "agent_start" for event in client.events)
        )

    def test_rpc_discovers_and_invokes_package_init(self):
        package = os.path.join(self.temp, "package")
        package_project = os.path.join(self.temp, "package-project")
        os.makedirs(package_project)
        _build_package(package)
        with open(
            os.path.join(self.agent_dir, "settings.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump({"packages": [package], "defaultProjectTrust": "always"}, fh)

        client = _RpcClient(
            package_project, _env(self.home, self.agent_dir), self.session_dir
        )
        self.addCleanup(client.close)
        commands = client.request({"type": "get_commands"})["data"]["commands"]
        by_name = {command["name"]: command for command in commands}
        self.assertIn("delegate", by_name)
        self.assertIn("system2-init", by_name)
        self.assertEqual(
            by_name["system2-init"].get("sourceInfo", {}).get("origin"), "package"
        )
        agents = os.path.join(package_project, "AGENTS.md")
        with open(agents, "wb") as fh:
            fh.write(b"CALLER OWNED\n")
        client.request({"type": "prompt", "message": "/system2-init"})
        self.assertTrue(os.path.isfile(os.path.join(package_project, ".pi", "SYSTEM.md")))
        with open(agents, "rb") as fh:
            self.assertEqual(fh.read(), b"CALLER OWNED\n")

    def test_sdk_reload_reconstructs_branch_workflow_and_parent_prompt(self):
        harness = os.path.join(self.temp, "lifecycle.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                r'''
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
const PKG = process.argv[2];
const project = process.argv[3];
const agentDir = process.argv[4];
const pkg = await import(PKG);
const settings = pkg.SettingsManager.create(project, agentDir);
settings.setProjectTrusted(true);
await settings.reload();
const loader = new pkg.DefaultResourceLoader({ cwd: project, agentDir, settingsManager: settings });
await loader.reload();
const sm = pkg.SessionManager.inMemory(project);
const { session } = await pkg.createAgentSession({
  cwd: project,
  agentDir,
  settingsManager: settings,
  resourceLoader: loader,
  sessionManager: sm,
});
const errors = [];
await session.bindExtensions({ mode: "rpc", onError: (error) => errors.push(error) });
const commands = session.extensionRunner.getRegisteredCommands().map((c) => c.invocationName);
const tools = session.getAllTools().map((tool) => tool.name);
async function emitBeforeAgentStart() {
  const runner = session.extensionRunner;
  if (runner.emitBeforeAgentStart.length <= 2) {
    const result = await runner.emitBeforeAgentStart(
      { prompt: "test", images: [], messageType: "user" },
      { customPrompt: "BASE", cwd: project },
    );
    return {
      systemPrompt: result.systemPromptOptions.forceSystemPrompt ?? "BASE",
    };
  }
  return runner.emitBeforeAgentStart(
    "test", undefined, "BASE", { cwd: project },
  );
}
const before = await emitBeforeAgentStart();
sm.appendCustomEntry("system2-workflow-state", {
  schemaVersion: 2,
  mode: "orchestrator",
  generation: 1,
  transitionId: "native-gate-0",
  provenance: {
    orchestratorPromptDigest: createHash("sha256").update(fs.readFileSync(path.join(project, ".pi", "SYSTEM.md"))).digest("hex"),
    extensionDigest: createHash("sha256").update(fs.readFileSync(path.join(project, ".pi", "extensions", "system2.ts"))).digest("hex"),
  },
  workflow: { id: "native-workflow", sequence: 1, kind: "full", startedAt: "2026-01-01T00:00:00.000Z" },
  gate: {
    current: 1,
    complete: false,
    decisions: [{
      id: "native-human-approval",
      gate: 0,
      decision: "approved",
      scopeSummary: "native acceptance",
      allowedPaths: [],
      plannedOutputs: [],
      artifacts: [],
      decidedAt: "2026-01-01T00:00:00.000Z",
      source: "tui",
    }],
  },
  orchestration: {
    infrastructureFailureStreak: 0,
    infrastructureCircuitOpen: false,
    circuitOpenedAt: null,
    lastInfrastructureFailure: null,
    resumeCount: 0,
    lastResumedAt: null,
  },
  activeDelegation: null,
  lastDelegation: null,
});
await session.reload();
const after = await emitBeforeAgentStart();
const restoredBlock = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "restored", toolName: "write",
  input: { path: "spec/context.md", content: "x" },
});
const nativeDanger = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "danger", toolName: "bash",
  input: { command: "rm -rf /" },
});
const nativeSensitive = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "sensitive", toolName: "bash",
  input: { command: "cat .env" },
});
const nativeBenign = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "benign", toolName: "bash",
  input: { command: "python3 -B -m unittest --help" },
});
sm.appendCustomEntry("system2-workflow-state", {
  schemaVersion: 2, mode: "orchestrator", generation: 99,
});
await session.reload();
const malformedBlock = await session.extensionRunner.emitToolCall({
  type: "tool_call", toolCallId: "malformed", toolName: "write",
  input: { path: "spec/design.md", content: "x" },
});
const malformedPrompt = await emitBeforeAgentStart();
process.stdout.write(JSON.stringify({
  before, after, restoredBlock, nativeDanger, nativeSensitive,
  nativeBenign: nativeBenign ?? null,
  malformedBlock, malformedPrompt,
  commands, tools, errors, entries: sm.getEntries(),
}));
'''
            )
        completed = subprocess.run(
            [_NODE_BIN, harness, resolve_pi_pkg_entry(_PI_BIN), self.project, self.agent_dir],
            env=_env(self.home, self.agent_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["errors"], [], result)
        self.assertIn("delegate", result["commands"], result)
        self.assertIn("system2-status", result["commands"], result)
        self.assertIn("system2-history", result["commands"], result)
        self.assertIn("system2-new", result["commands"], result)
        self.assertIn("system2-resume", result["commands"], result)
        self.assertIn("system2_delegate", result["tools"], result)
        self.assertIn("system2_request_gate_approval", result["tools"], result)
        self.assertIn("system2_request_gate_skip", result["tools"], result)
        self.assertIn("system2_status", result["tools"], result)
        for key in ("before", "after"):
            self.assertIn(
                "SYSTEM2 PARENT AUTHORITY: mode=orchestrator", result[key]["systemPrompt"], result
            )
            self.assertNotIn("System2 role: design-architect", result[key]["systemPrompt"], result)
        self.assertIn("current gate: 0", result["before"]["systemPrompt"])
        self.assertIn("current gate: 1", result["after"]["systemPrompt"])
        self.assertTrue(result["restoredBlock"]["block"])
        self.assertIn("Orchestrator", result["restoredBlock"]["reason"])
        self.assertTrue(result["nativeDanger"]["block"])
        self.assertIn("block-dangerous", result["nativeDanger"]["reason"])
        self.assertTrue(result["nativeSensitive"]["block"])
        self.assertIn("protect-sensitive", result["nativeSensitive"]["reason"])
        self.assertIsNone(result["nativeBenign"])
        self.assertTrue(result["malformedBlock"]["block"])
        self.assertIn("RECOVERY REQUIRED", result["malformedPrompt"]["systemPrompt"])


if __name__ == "__main__":
    unittest.main()
