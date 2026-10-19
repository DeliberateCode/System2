"""Cohesive acceptance for the Pi UX issues observed in the tasker study.

This test uses Pi 0.85.1's real JSON agent loop and tool execution with a
fully deterministic, offline provider. It complements the opt-in live-model
study documented in docs/installation/pi.md without sending external traffic.
"""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest

from evals import oracle
from evals.pi_test_support import resolve_pi_pkg_entry
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend


_NODE = os.environ.get("NODE_BIN") or shutil.which("node")
_PI = os.environ.get("PI_BIN") or shutil.which("pi")
_PI_ENTRY = resolve_pi_pkg_entry(_PI)

_VERIFY = "python3 -B -m unittest discover -s tests -v"

_STUDY_FILES = {
    "spec/context.md": "# Context\n\nBuild a small standard-library Python task CLI.\n",
    "spec/requirements.md": "# Requirements\n\n- Add, list, complete, and delete tasks.\n- Persist atomically as JSON.\n",
    "spec/design.md": "# Design\n\nUse a package-level store and argparse CLI with atomic replacement.\n",
    "spec/tasks.md": "# Tasks\n\n1. Implement the store and CLI.\n2. Add 29 tests.\n3. Add README and .gitignore.\n",
    "tasker/__init__.py": '"""Tasker study fixture."""\n\nfrom .store import TaskStore\n\n__all__ = ["TaskStore"]\n',
    "tasker/__main__.py": "from .cli import main\n\nraise SystemExit(main())\n",
    "tasker/store.py": textwrap.dedent(
        '''\
        import json
        import os
        import tempfile
        from pathlib import Path


        class TaskStore:
            def __init__(self, path):
                self.path = Path(path)

            def _load(self):
                if not self.path.exists():
                    return []
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, list):
                    raise ValueError("task data must be a list")
                return value

            def _save(self, tasks):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=".tasker.", dir=self.path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(tasks, stream, sort_keys=True)
                        stream.write("\\n")
                    os.replace(temporary, self.path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)

            def add(self, text):
                tasks = self._load()
                identifier = max((task["id"] for task in tasks), default=0) + 1
                tasks.append({"id": identifier, "text": text, "complete": False})
                self._save(tasks)
                return identifier

            def list(self):
                return self._load()

            def complete(self, identifier):
                tasks = self._load()
                for task in tasks:
                    if task["id"] == identifier:
                        task["complete"] = True
                        self._save(tasks)
                        return
                raise KeyError(identifier)

            def delete(self, identifier):
                tasks = self._load()
                remaining = [task for task in tasks if task["id"] != identifier]
                if len(remaining) == len(tasks):
                    raise KeyError(identifier)
                self._save(remaining)
        '''
    ),
    "tasker/cli.py": textwrap.dedent(
        '''\
        import argparse
        import json
        import os

        from .store import TaskStore


        def main(argv=None):
            parser = argparse.ArgumentParser(prog="tasker")
            commands = parser.add_subparsers(dest="command", required=True)
            add = commands.add_parser("add")
            add.add_argument("text")
            commands.add_parser("list")
            for name in ("complete", "delete"):
                command = commands.add_parser(name)
                command.add_argument("identifier", type=int)
            args = parser.parse_args(argv)
            store = TaskStore(os.environ.get("TASKER_DATA_FILE", ".tasker.json"))
            if args.command == "add":
                print(store.add(args.text))
            elif args.command == "list":
                for task in store.list():
                    print(json.dumps(task, sort_keys=True))
            elif args.command == "complete":
                store.complete(args.identifier)
            else:
                store.delete(args.identifier)
            return 0
        '''
    ),
    "tests/test_tasker.py": textwrap.dedent(
        '''\
        import tempfile
        import unittest
        from pathlib import Path

        from tasker import TaskStore


        class TaskerTests(unittest.TestCase):
            def setUp(self):
                self.temporary = tempfile.TemporaryDirectory()
                self.addCleanup(self.temporary.cleanup)
                self.path = Path(self.temporary.name) / "tasks.json"
                self.store = TaskStore(self.path)

            def test_add_assigns_stable_ids(self):
                self.assertEqual(self.store.add("one"), 1)
                self.assertEqual(self.store.add("two"), 2)

            def test_complete_marks_task(self):
                identifier = self.store.add("one")
                self.store.complete(identifier)
                self.assertTrue(self.store.list()[0]["complete"])

            def test_delete_removes_task(self):
                identifier = self.store.add("one")
                self.store.delete(identifier)
                self.assertEqual(self.store.list(), [])

            def test_missing_identifier_fails(self):
                with self.assertRaises(KeyError):
                    self.store.complete(99)


        def _round_trip_case(index):
            def test(self):
                text = f"task-{index}"
                identifier = self.store.add(text)
                self.assertEqual(identifier, 1)
                self.assertEqual(self.store.list(), [{"id": 1, "text": text, "complete": False}])
            return test


        for _index in range(25):
            setattr(TaskerTests, f"test_round_trip_{_index:02d}", _round_trip_case(_index))
        '''
    ),
    "README.md": "# tasker\n\nRun with `python3 -m tasker`.\n",
    ".gitignore": "__pycache__/\n*.py[cod]\n.venv/\nvenv/\n.pytest_cache/\n.mypy_cache/\n.tasker.json\n..tasker.json.*.tmp\n",
    "pyproject.toml": "[project]\nname = \"tasker-study\"\nversion = \"0.0.0\"\nrequires-python = \">=3.9\"\n",
    "permission-boundaries.md": "# Permission boundaries\n\nSystem2 preflights role and human path authority before delegation.\n",
}

_PROVIDER = r'''import fs from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";

export default function (pi: ExtensionAPI) {
  const spec = JSON.parse(fs.readFileSync(process.env.SYSTEM2_PI_CHILD_SPEC!, "utf8"));
  const fixture = JSON.parse(process.env.SYSTEM2_STUDY_FIXTURE!);
  let outputIndex = 0;
  let verificationIndex = 0;
  let sequence = 0;

  const emit = (model: any, calls: any[]) => {
    const stream = createAssistantMessageEventStream();
    queueMicrotask(() => {
      const output: any = {
        role: "assistant", content: [], api: model.api, provider: model.provider,
        model: model.id, stopReason: "pending", timestamp: Date.now(),
        usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      };
      stream.push({ type: "start", partial: output });
      calls.forEach((call, index) => {
        output.content.push(call);
        stream.push({ type: "toolcall_start", contentIndex: index, partial: output });
        stream.push({ type: "toolcall_end", contentIndex: index, toolCall: call, partial: output });
      });
      output.stopReason = "toolUse";
      stream.push({ type: "done", reason: "toolUse", message: output });
      stream.end();
    });
    return stream;
  };

  pi.registerProvider("system2-study", {
    name: "System2 offline study provider", baseUrl: "http://127.0.0.1/unused",
    apiKey: "offline-study", api: "openai-completions",
    models: [{ id: "deterministic", name: "Deterministic study model", reasoning: false,
      input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 64000, maxTokens: 4096 }],
    streamSimple(model: any) {
      sequence += 1;
      if (outputIndex < spec.contract.expectedOutputs.length) {
        const path = spec.contract.expectedOutputs[outputIndex++];
        return emit(model, [{
          type: "toolCall", id: `write-${sequence}`, name: "write",
          arguments: { path, content: fixture[path] },
        }]);
      }
      if (verificationIndex < spec.contract.verificationCommands.length) {
        const command = spec.contract.verificationCommands[verificationIndex++];
        return emit(model, [{
          type: "toolCall", id: `verify-${sequence}`, name: "bash", arguments: { command },
        }]);
      }
      return emit(model, [{
        type: "toolCall", id: `complete-${sequence}`, name: "system2_child_complete",
        arguments: { outcome: "complete", summary: `Completed ${spec.contract.role} study work`,
          blockers: [], risks: [], followUps: [] },
      }]);
    },
  });
}
'''

_HARNESS = r'''const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
const messages = [];
const confirmations = [];
let activeTools = ["read", "grep", "find", "ls", "bash", "write", "edit",
  "system2_status", "system2_delegate", "system2_request_gate_approval", "system2_request_gate_skip"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.sendMessage = (message, options) => messages.push({ message, options });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [
  ...["read", "grep", "find", "ls", "bash", "write", "edit"].map((name) => ({ name })),
  ...[...ext.tools.values()].map((item) => item.definition),
];
loaded.runtime.getCommands = () => [...ext.commands.entries()].map(([name]) => ({ name, source: "extension" }));
const ctx = {
  cwd: project, mode: "tui", hasUI: true,
  model: { provider: "system2-study", id: "deterministic" }, thinkingLevel: "off",
  sessionManager: { getBranch: () => entries },
  ui: {
    notify: () => {}, setStatus: () => {}, select: async () => "maintenance",
    confirm: async (title, body) => { confirmations.push({ title, body }); return true; },
  },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) {
  await handler({ type: "session_start", reason: "startup" }, ctx);
}
const gate = ext.tools.get("system2_request_gate_approval").definition;
const skip = ext.tools.get("system2_request_gate_skip").definition;
const delegateTool = ext.tools.get("system2_delegate").definition;
const delegated = [];
const delegate = async (label, role, expectedOutputs = [], verificationCommands = [], contextInputs = []) => {
  const result = await delegateTool.execute(label, {
    role, objective: `${label} for the tasker study`, contextInputs, expectedOutputs,
    acceptanceCriteria: [`Complete ${label}`], verificationCommands,
    budget: { maxFiles: expectedOutputs.length, maxChangedLines: 1000 }, timeoutSeconds: 60,
  }, undefined, undefined, ctx);
  delegated.push({ label, role, details: result.details });
  return result;
};
const allowedPaths = ["spec/**", "tasker/**", "tests/**", "README.md", ".gitignore", "pyproject.toml", "permission-boundaries.md"];
await gate.execute("gate-0", { gate: 0, scopeSummary: "Build tasker", allowedPaths, artifactPaths: [] }, undefined, undefined, ctx);
await delegate("repo-survey", "repo-governor", [], [], ["study-context.txt"]);
await delegate("context", "spec-coordinator", ["spec/context.md"]);
await gate.execute("gate-1", { gate: 1, scopeSummary: "Approve context", allowedPaths, artifactPaths: ["spec/context.md"] }, undefined, undefined, ctx);
await delegate("requirements", "requirements-engineer", ["spec/requirements.md"], [], ["spec/context.md"]);
await gate.execute("gate-2", { gate: 2, scopeSummary: "Approve requirements", allowedPaths, artifactPaths: ["spec/context.md", "spec/requirements.md"] }, undefined, undefined, ctx);
await delegate("design", "design-architect", ["spec/design.md"], [], ["study-context.txt", "spec/requirements.md"]);
await gate.execute("gate-3", { gate: 3, scopeSummary: "Approve design", allowedPaths, artifactPaths: ["spec/context.md", "spec/requirements.md", "spec/design.md"] }, undefined, undefined, ctx);
await delegate("tasks", "task-planner", ["spec/tasks.md"], [], ["spec/design.md"]);
const implementationOutputs = ["tasker/__init__.py", "tasker/__main__.py", "tasker/cli.py", "tasker/store.py", "tests/test_tasker.py", "README.md", ".gitignore", "pyproject.toml"];
await gate.execute("gate-4", { gate: 4, scopeSummary: "Approve task plan", allowedPaths,
  artifactPaths: ["spec/context.md", "spec/requirements.md", "spec/design.md", "spec/tasks.md"],
  plannedOutputs: implementationOutputs }, undefined, undefined, ctx);
await delegate("implementation", "executor", implementationOutputs, ["python3 -B -m unittest discover -s tests -v"], ["spec/tasks.md"]);
const parentGate = (ext.handlers.get("tool_call") || [])[0];
const parentVerificationAllowed = (await parentGate({ toolName: "bash", input: { command: "python3 -B -m unittest discover -s tests -v" } }, ctx)) === undefined;
const parentBash = pkg.createBashTool(project);
const parentVerification = parentVerificationAllowed
  ? await parentBash.execute("parent-verification", { command: "python3 -B -m unittest discover -s tests -v" })
  : null;
await delegate("verification", "test-engineer", [], ["python3 -B -m unittest discover -s tests -v"], ["spec/tasks.md"]);
await delegate("simplification", "code-reviewer");
await delegate("security", "security-sentinel");
await delegate("documentation", "docs-release");
await delegate("final-review", "code-reviewer");
const releaseArtifacts = ["spec/context.md", "spec/requirements.md", "spec/design.md", "spec/tasks.md", ...implementationOutputs];
await gate.execute("gate-5", { gate: 5, scopeSummary: "Ship tasker", allowedPaths, artifactPaths: releaseArtifacts }, undefined, undefined, ctx);
const fullStatus = (await ext.tools.get("system2_status").definition.execute("full-status", {}, undefined, undefined, ctx)).details;
await ext.commands.get("system2-new").handler("maintenance", ctx);
await gate.execute("maintenance-gate-0", { gate: 0, scopeSummary: "Document permission behavior",
  allowedPaths: ["permission-boundaries.md"], artifactPaths: [] }, undefined, undefined, ctx);
await delegate("permission-doc", "docs-release", ["permission-boundaries.md"]);
await skip.execute("maintenance-skip", { throughGate: 4, reason: "Human selected a bounded documentation-only maintenance workflow" }, undefined, undefined, ctx);
await gate.execute("maintenance-gate-5", { gate: 5, scopeSummary: "Ship permission documentation",
  allowedPaths: ["permission-boundaries.md"], artifactPaths: ["permission-boundaries.md"] }, undefined, undefined, ctx);
const finalStatus = (await ext.tools.get("system2_status").definition.execute("final-status", {}, undefined, undefined, ctx)).details;
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)), delegated,
  parentVerificationAllowed, parentVerification, fullStatus, finalStatus, entries, messages, confirmations,
}));
'''


class PiStudyAcceptanceTest(unittest.TestCase):
    """Reproduce the original tasker journey without manual repair or test handoff."""

    @classmethod
    def setUpClass(cls):
        if not _NODE or not _PI or not _PI_ENTRY:
            raise AssertionError("Pi study acceptance requires node and Pi 0.85.1; skipping is forbidden")
        version = subprocess.run(
            [_PI, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        if version != "0.85.1":
            raise AssertionError(f"Pi study acceptance requires 0.85.1, found {version!r}")

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="pi-tasker-study-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.project = os.path.join(self.temp, "project")
        self.home = os.path.join(self.temp, "home")
        os.makedirs(self.project)
        os.makedirs(self.home)
        result = ir.compose(oracle.PLUGIN_ROOT, [], self.project)
        self.assertIsNotNone(result.graph, result.errors)
        PiBackend().emit(result.graph, self.project)
        with open(os.path.join(self.project, "study-context.txt"), "w", encoding="utf-8") as stream:
            stream.write("Ordinary repository context that is not gate evidence.\n")

    def _environment(self, provider, record):
        wrapper = os.path.join(self.temp, "pi-study-wrapper")
        wrapper_source = f'''#!/usr/bin/env python3
import json, os
spec = json.load(open(os.environ["SYSTEM2_PI_CHILD_SPEC"], encoding="utf-8"))
with open(os.environ["SYSTEM2_STUDY_RECORD"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(spec, sort_keys=True) + "\\n")
args = {json.dumps(_PI)}
argv = [args, *os.sys.argv[1:-1], "-e", {json.dumps(provider)}, os.sys.argv[-1]]
os.execv(args, argv)
'''
        with open(wrapper, "w", encoding="utf-8") as stream:
            stream.write(wrapper_source)
        os.chmod(wrapper, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        env = {
            "HOME": self.home,
            "PATH": os.environ.get("PATH", ""),
            "PI_CODING_AGENT_DIR": os.path.join(self.home, ".pi", "agent"),
            "PI_OFFLINE": "1",
            "PI_SKIP_VERSION_CHECK": "1",
            "SYSTEM2_PI_BIN": wrapper,
            "SYSTEM2_STUDY_FIXTURE": json.dumps(_STUDY_FILES),
            "SYSTEM2_STUDY_RECORD": record,
        }
        for key in ("LANG", "LC_ALL", "LC_CTYPE"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        return env

    def test_tasker_study_completes_without_manual_files_or_test_handoff(self):
        provider = os.path.join(self.temp, "study-provider.ts")
        harness = os.path.join(self.temp, "study-harness.mjs")
        record = os.path.join(self.temp, "child-specs.jsonl")
        with open(provider, "w", encoding="utf-8") as stream:
            stream.write(_PROVIDER)
        with open(harness, "w", encoding="utf-8") as stream:
            stream.write(_HARNESS)
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            cwd=self.project,
            env=self._environment(provider, record),
            capture_output=True,
            text=True,
            timeout=240,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)

        self.assertEqual(len(result["delegated"]), 12)
        for delegation in result["delegated"]:
            self.assertEqual(delegation["details"]["status"], "complete", delegation)
            self.assertIsNone(delegation["details"]["failure"], delegation)
        self.assertTrue(result["parentVerificationAllowed"])
        parent_output = "".join(
            item.get("text", "") for item in result["parentVerification"]["content"]
            if item.get("type") == "text"
        )
        self.assertIn("Ran 29 tests", parent_output)
        self.assertIn("OK", parent_output)
        self.assertEqual(result["fullStatus"]["operationalState"], "completed_review")
        self.assertEqual(result["finalStatus"]["operationalState"], "completed_review")
        self.assertEqual(result["finalStatus"]["workflow"]["kind"], "maintenance")
        self.assertEqual(len(result["finalStatus"]["workflowHistory"]), 1)
        self.assertEqual(result["finalStatus"]["workflowHistory"][0]["outcome"], "completed")

        for path in (*_STUDY_FILES.keys(),):
            self.assertTrue(os.path.isfile(os.path.join(self.project, path)), path)
        with open(os.path.join(self.project, ".gitignore"), encoding="utf-8") as stream:
            self.assertIn("__pycache__/", stream.read())

        verified = subprocess.run(
            _VERIFY.split(), cwd=self.project, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIn("Ran 29 tests", verified.stderr)
        self.assertIn("OK", verified.stderr)
        leftovers = []
        for root, directories, files in os.walk(self.project):
            directories[:] = [item for item in directories if item != ".pi"]
            leftovers.extend(
                os.path.relpath(os.path.join(root, item), self.project)
                for item in files
                if item.endswith((".pyc", ".pyo")) or item == ".tasker.json"
            )
        self.assertEqual(leftovers, [])

        gate_messages = [
            item for item in result["messages"]
            if item["message"].get("customType") == "system2-gate-decision"
        ]
        self.assertEqual(len(gate_messages), 12)
        self.assertTrue(all(item["message"]["display"] for item in gate_messages))
        self.assertTrue(all("Human" in item["message"]["content"] for item in gate_messages))
        explicit_gate_prompts = [
            item for item in result["confirmations"]
            if item["title"].startswith("HUMAN APPROVAL REQUIRED")
        ]
        self.assertEqual(len(explicit_gate_prompts), 8)

        with open(record, encoding="utf-8") as stream:
            specs = [json.loads(line) for line in stream if line.strip()]
        self.assertEqual(len(specs), 12)
        design = next(spec for spec in specs if spec["contract"]["role"] == "design-architect")
        self.assertIn("study-context.txt", design["contract"]["contextInputs"])
        self.assertNotIn("study-context.txt", design["contract"]["gateEvidence"])
        executor = next(spec for spec in specs if spec["contract"]["role"] == "executor")
        self.assertIn(".gitignore", executor["contract"]["expectedOutputs"])
        self.assertEqual(executor["contract"]["taskWriteScope"], [
            "^tasker/__init__\\.py$", "^tasker/__main__\\.py$", "^tasker/cli\\.py$",
            "^tasker/store\\.py$", "^tests/test_tasker\\.py$", "^README\\.md$",
            "^\\.gitignore$", "^pyproject\\.toml$",
        ])
        permission = next(spec for spec in specs if spec["contract"]["expectedOutputs"] == ["permission-boundaries.md"])
        self.assertEqual(permission["contract"]["role"], "docs-release")


if __name__ == "__main__":
    unittest.main()
