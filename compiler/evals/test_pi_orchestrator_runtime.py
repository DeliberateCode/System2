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
const confirmations = [];
let approveParentWrite = true;
let activeTools = ["read", "bash", "edit", "write", "system2_request_gate_approval", "system2_delegate", "system2_status"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [
  ...["read", "grep", "find", "ls", "bash", "write", "edit"].map((name) => ({ name })),
  ...[...ext.tools.values()].map((item) => item.definition),
];
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
    confirm: async (title, body) => {
      confirmations.push({ title, body });
      return title.startsWith("Approve one parent Orchestrator") ? approveParentWrite : true;
    },
    setStatus: (key, value) => statuses.push({ key, value }),
  },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const before = await (ext.handlers.get("before_agent_start") || [])[0]({ systemPrompt: "BASE" }, ctx);
const gate = ext.tools.get("system2_request_gate_approval").definition;
let regexGateError;
try { await gate.execute("regex-gate", { gate: 0, scopeSummary: "reject regex syntax", allowedPaths: ["^spec/.*$"], artifactPaths: [] }, undefined, undefined, ctx); }
catch (error) { regexGateError = error.message; }
const gateResult = await gate.execute("gate-0", { gate: 0, scopeSummary: "Bounded native acceptance", allowedPaths: [`${project}/spec/**`], artifactPaths: ["evidence.txt"] }, undefined, undefined, ctx);
const delegated = [];
for (const role of roles) {
  const intent = {
    role,
    objective: `Read-only native acceptance for ${role}`,
    contextInputs: role === "design-architect" ? [`${project}/context.txt`] : [],
    expectedOutputs: role === "design-architect" ? [`${project}/spec/design.md`] : [],
    acceptanceCriteria: ["Return one bounded report"],
    verificationCommands: [],
    ...(role === "repo-governor" ? {} : {
      budget: { maxFiles: 0, maxChangedLines: 0 }, timeoutSeconds: 30,
    }),
  };
  const result = await ext.tools.get("system2_delegate").definition.execute(
    `delegate-${role}`, intent, undefined, undefined, ctx,
  );
  delegated.push(result.details);
}
const after = await (ext.handlers.get("before_agent_start") || [])[0]({ systemPrompt: "BASE" }, ctx);
const parentToolCall = (ext.handlers.get("tool_call") || [])[0];
const parentBashAllowed = (await parentToolCall({ toolName: "bash", input: { command: "python3 -B -m unittest --help" } }, ctx)) === undefined;
const parentDangerous = await parentToolCall({ toolName: "bash", input: { command: "rm -rf /" } }, ctx);
const parentSensitive = await parentToolCall({ toolName: "bash", input: { command: "cat .env" } }, ctx);
const parentShellWriteAllowed = (await parentToolCall({ toolName: "bash", input: { command: "printf x > notes.txt" } }, ctx)) === undefined;
const parentWriteAllowed = (await parentToolCall({ toolName: "write", input: { path: "README.md", content: "x" } }, ctx)) === undefined;
approveParentWrite = false;
const parentWriteRejected = await parentToolCall({ toolName: "edit", input: { path: "README.md", oldText: "x", newText: "y" } }, ctx);
const parentWriteNoUi = await parentToolCall({ toolName: "write", input: { path: "README.md", content: "x" } }, { ...ctx, hasUI: false });
const parentProtected = await parentToolCall({ toolName: "write", input: { path: ".pi/SYSTEM.md", content: "x" } }, ctx);
const modelStatus = await ext.tools.get("system2_status").definition.execute("status", {}, undefined, undefined, ctx);
await ext.commands.get("system2-status").handler("", ctx);
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  commands: [...ext.commands.keys()],
  tools: [...ext.tools.keys()],
  before, after, regexGateError, gateResult, delegated, entries, notices, statuses, confirmations, activeTools,
  parentBashAllowed, parentDangerous, parentSensitive, parentShellWriteAllowed, parentWriteAllowed, parentWriteRejected, parentWriteNoUi, parentProtected, modelStatus,
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
const intent = {
  role: "code-reviewer", objective: "Review failure recovery", expectedOutputs: [],
  acceptanceCriteria: ["Return one report"], verificationCommands: [],
  budget: { maxFiles: 0, maxChangedLines: 0 }, timeoutSeconds: 30,
};
let beforeGateError;
try { await delegate.execute("before-gate", intent, undefined, undefined, ctx); }
catch (error) { beforeGateError = error.message; }
const gate = ext.tools.get("system2_request_gate_approval").definition;
let noUiError;
try { await gate.execute("no-ui", { gate: 0, scopeSummary: "must fail", allowedPaths: [], artifactPaths: [] }, undefined, undefined, { ...ctx, hasUI: false }); }
catch (error) { noUiError = error.message; }
await gate.execute("gate", { gate: 0, scopeSummary: "failure test", allowedPaths: ["spec/*.md", "permission-boundaries.md", "orphan.bin"], artifactPaths: [] }, undefined, undefined, ctx);
let malformedError;
try { await delegate.execute("malformed", { ...intent, unexpected: true }, undefined, undefined, ctx); }
catch (error) { malformedError = error.message; }
let legacyGateContextError;
try { await delegate.execute("legacy-gate", { ...intent, gateContext: { gate: 1, approvalId: "legacy", constraints: [] } }, undefined, undefined, ctx); }
catch (error) { legacyGateContextError = error.message; }
let unauthorizedOutputError;
try { await delegate.execute("unauthorized-output", { ...intent, role: "executor", expectedOutputs: ["src/no.py"] }, undefined, undefined, ctx); }
catch (error) { unauthorizedOutputError = error.message; }
let nestedOutputError;
try { await delegate.execute("nested-output", { ...intent, role: "design-architect", expectedOutputs: ["spec/nested/design.md"] }, undefined, undefined, ctx); }
catch (error) { nestedOutputError = error.message; }
let roleMismatchError;
try { await delegate.execute("role-mismatch", { ...intent, role: "executor", expectedOutputs: ["permission-boundaries.md"] }, undefined, undefined, ctx); }
catch (error) { roleMismatchError = error.message; }
let noRoleError;
try { await delegate.execute("no-role", { ...intent, role: "executor", expectedOutputs: ["orphan.bin"] }, undefined, undefined, ctx); }
catch (error) { noRoleError = error.message; }
let protectedOutputError;
try { await delegate.execute("protected-output", { ...intent, role: "executor", expectedOutputs: [".pi/extensions/no.ts"] }, undefined, undefined, ctx); }
catch (error) { protectedOutputError = error.message; }
let outsideAbsoluteError;
try { await delegate.execute("outside-absolute", { ...intent, role: "executor", expectedOutputs: ["/tmp/system2-outside.py"] }, undefined, undefined, ctx); }
catch (error) { outsideAbsoluteError = error.message; }
let legacyArtifactInputsError;
try { await delegate.execute("legacy-artifacts", { ...intent, artifactInputs: ["evidence.txt"] }, undefined, undefined, ctx); }
catch (error) { legacyArtifactInputsError = error.message; }
let sensitiveContextError;
try { await delegate.execute("sensitive-context", { ...intent, contextInputs: [".env"] }, undefined, undefined, ctx); }
catch (error) { sensitiveContextError = error.message; }
let outsideContextError;
try { await delegate.execute("outside-context", { ...intent, contextInputs: ["/tmp/system2-outside-context.txt"] }, undefined, undefined, ctx); }
catch (error) { outsideContextError = error.message; }
let aliasedSensitiveContextError;
try { await delegate.execute("aliased-sensitive-context", { ...intent, contextInputs: ["context-alias.txt"] }, undefined, undefined, ctx); }
catch (error) { aliasedSensitiveContextError = error.message; }
let noBashVerificationError;
try {
  await delegate.execute("no-bash-verification", {
    ...intent, role: "design-architect", expectedOutputs: ["spec/design.md"],
    verificationCommands: ["python3 -B -m unittest"],
  }, undefined, undefined, ctx);
} catch (error) { noBashVerificationError = error.message; }
duplicateTopology = true;
let duplicateError;
try { await delegate.execute("duplicate", intent, undefined, undefined, ctx); }
catch (error) { duplicateError = error.message; }
duplicateTopology = false;
const processResult = await delegate.execute("fails", intent, undefined, undefined, ctx);
const stateAfterFailure = entries[entries.length - 1].data;
const recovered = await delegate.execute("recovers", intent, undefined, undefined, ctx);
const stateAfterRecovery = entries[entries.length - 1].data;
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  beforeGateError, noUiError, malformedError, legacyGateContextError, unauthorizedOutputError, nestedOutputError, roleMismatchError, noRoleError, protectedOutputError, outsideAbsoluteError, legacyArtifactInputsError, sensitiveContextError, outsideContextError, aliasedSensitiveContextError, noBashVerificationError, duplicateError, processResult,
  stateAfterFailure, recovered, stateAfterRecovery, entries,
}));
"""

_RESTORE_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const entries = JSON.parse(process.argv[4]);
const restoreMode = process.argv[5];
const shouldRecover = restoreMode === "recover";
const restoreEvent = restoreMode === "tree" ? "session_tree" : "session_start";
if (restoreMode === "tamper-archive") {
  const archive = entries.find((entry) => entry.customType === "system2-workflow-archive");
  if (archive) archive.data.workflow.kind = "tampered";
}
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
for (const handler of ext.handlers.get(restoreEvent) || []) await handler({ type: restoreEvent, reason: restoreEvent === "session_tree" ? "fork" : "startup" }, ctx);
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
const detached = await toolCall({ toolName: "bash", input: { command: "setsid sh -c 'echo late > spec/design.md'" } }, ctx);
const siblingCtx = {
  ...ctx,
  sessionManager: { getBranch: () => [{ type: "message", message: { role: "assistant", content: [
    { type: "toolCall", name: "read", id: "one" },
    { type: "toolCall", name: "system2_child_complete", id: "two" },
  ] } }] },
};
const sibling = await toolCall({ toolName: "system2_child_complete", input: {} }, siblingCtx);
const complete = ext.tools.get("system2_child_complete").definition;
const claim = {
  outcome: "complete", summary: "Reviewed design contract",
  blockers: [], risks: [], followUps: [],
};
const completion = await complete.execute("complete", claim, undefined, undefined, ctx);
let duplicateError = null;
try { await complete.execute("complete-again", claim, undefined, undefined, ctx); }
catch (error) { duplicateError = error.message; }
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  tools: [...ext.tools.keys()], commands: [...ext.commands.keys()], activeTools,
  prompt, inScope: inScope ?? null, offScope, detached, sibling, completion, duplicateError,
}));
"""

_CHILD_VERIFICATION_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
let activeTools = ["read", "bash", "edit", "write"];
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
const ctx = {
  cwd: project, mode: "json", hasUI: false,
  sessionManager: { getBranch: () => [] },
  ui: { setStatus: () => {}, notify: () => {} },
};
for (const handler of ext.handlers.get("session_start") || []) {
  await handler({ type: "session_start", reason: "startup" }, ctx);
}
const complete = ext.tools.get("system2_child_complete").definition;
const claim = {
  outcome: "complete", summary: "Verified implementation",
  blockers: [], risks: [], followUps: [],
};
let missingError;
try { await complete.execute("missing", claim, undefined, undefined, ctx); }
catch (error) { missingError = error.message; }
for (const handler of ext.handlers.get("tool_result") || []) {
  await handler({
    type: "tool_result", toolCallId: "verify", toolName: "bash",
    input: { command: "python3 -B -m unittest" }, content: [],
    details: undefined, isError: false,
  }, ctx);
}
const completion = await complete.execute("complete", claim, undefined, undefined, ctx);
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  missingError, completion, activeTools,
}));
"""

_RESILIENCE_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
let activeTools = ["read", "bash", "edit", "write", "system2_request_gate_approval", "system2_delegate"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [...ext.tools.values()].map((item) => item.definition);
loaded.runtime.getCommands = () => [...ext.commands.entries()].map(([name]) => ({ name, source: "extension" }));
const ctx = {
  cwd: project, mode: "tui", hasUI: true,
  model: { provider: "test-provider", id: "test-model" }, thinkingLevel: "off",
  sessionManager: { getBranch: () => entries },
  ui: { notify: () => {}, confirm: async () => true, setStatus: () => {} },
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const gate = ext.tools.get("system2_request_gate_approval").definition;
const approvedPaths = (process.env.RESILIENCE_MODE === "orphan" || process.env.RESILIENCE_MODE === "excluded")
  ? ["spec/**"] : ["spec/design.md"];
await gate.execute("gate", {
  gate: 0, scopeSummary: "resilient completion", allowedPaths: approvedPaths, artifactPaths: [],
}, undefined, undefined, ctx);
const intent = {
  role: "executor", objective: "Create the design file",
  expectedOutputs: process.env.RESILIENCE_MODE === "orphan"
    ? ["spec/design.md", "spec/interfaces.json"] : ["spec/design.md"],
  acceptanceCriteria: ["Create spec/design.md"], verificationCommands: ["python3 -B -m unittest"],
  budget: { maxFiles: process.env.RESILIENCE_MODE === "orphan" ? 3 : 1, maxChangedLines: process.env.RESILIENCE_MODE === "budget" ? 0 : 20 },
  timeoutSeconds: process.env.RESILIENCE_MODE === "timeout" ? 10 : 30,
};
const result = await ext.tools.get("system2_delegate").definition.execute(
  "resilient", intent, undefined, undefined, ctx,
);
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  result, state: entries[entries.length - 1].data,
}));
"""

_CIRCUIT_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const fs = await import("node:fs");
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
const notices = [];
const confirmations = [];
let activeTools = ["read", "bash", "edit", "write", "system2_request_gate_approval", "system2_delegate", "system2_status"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [...ext.tools.values()].map((item) => item.definition);
loaded.runtime.getCommands = () => [...ext.commands.entries()].map(([name]) => ({ name, source: "extension" }));
const ctx = {
  cwd: project, mode: "tui", hasUI: true,
  model: { provider: "test-provider", id: "test-model" }, thinkingLevel: "off",
  sessionManager: { getBranch: () => entries },
  ui: {
    notify: (message, level) => notices.push({ message, level }),
    confirm: async (title, body) => { confirmations.push({ title, body }); return true; },
    setStatus: () => {},
  },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const gate = ext.tools.get("system2_request_gate_approval").definition;
await gate.execute("gate", {
  gate: 0, scopeSummary: "circuit breaker", allowedPaths: ["spec/design.md"], artifactPaths: [],
}, undefined, undefined, ctx);
const delegate = ext.tools.get("system2_delegate").definition;
const intent = {
  role: "design-architect", objective: "Create the design file",
  expectedOutputs: ["spec/design.md"], acceptanceCriteria: ["Create spec/design.md"],
  verificationCommands: [], budget: { maxFiles: 1, maxChangedLines: 20 }, timeoutSeconds: 30,
};
const failures = [];
for (let attempt = 0; attempt < 3; attempt++) failures.push((await delegate.execute(`failure-${attempt}`, intent, undefined, undefined, ctx)).details);
const stateAtCircuit = entries[entries.length - 1].data;
const statusAtCircuit = (await ext.tools.get("system2_status").definition.execute("status", {}, undefined, undefined, ctx)).details;
let circuitError;
try { await delegate.execute("blocked-by-circuit", intent, undefined, undefined, ctx); }
catch (error) { circuitError = error.message; }
await ext.commands.get("system2-resume").handler("", ctx);
const stateAfterResume = entries[entries.length - 1].data;
await ext.commands.get("system2-resume").handler("", ctx);
const alreadyClosedNotice = notices.at(-1);
const postResume = await delegate.execute("post-resume", intent, undefined, undefined, ctx);
const finalState = entries[entries.length - 1].data;
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  commands: [...ext.commands.keys()], failures, stateAtCircuit, statusAtCircuit, circuitError,
  confirmations, notices, stateAfterResume, alreadyClosedNotice, postResume, finalState, entries,
  fileStillPresent: fs.existsSync(`${project}/spec/design.md`),
}));
"""

_PHASE8_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
const messages = [];
const notices = [];
const confirmations = [];
const confirmationAnswers = [];
const selections = ["Start a small maintenance workflow"];
let activeTools = ["read", "bash", "edit", "write", "system2_status", "system2_request_gate_approval", "system2_request_gate_skip", "system2_delegate"];
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
  sessionManager: { getBranch: () => entries },
  ui: {
    notify: (message, level) => notices.push({ message, level }),
    setStatus: () => {},
    confirm: async (title, body) => { confirmations.push({ title, body }); return confirmationAnswers.length ? confirmationAnswers.shift() : true; },
    select: async (title, options) => {
      const answer = selections.shift();
      notices.push({ message: JSON.stringify({ title, options, answer }), level: "select" });
      return answer;
    },
  },
  waitForIdle: async () => {},
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const entriesBeforeEarlyNew = entries.length;
await ext.commands.get("system2-new").handler("full", ctx);
const earlyNewStateEntries = entries.length - entriesBeforeEarlyNew;
const gate = ext.tools.get("system2_request_gate_approval").definition;
const skip = ext.tools.get("system2_request_gate_skip").definition;
let skipGateZeroError;
try {
  await skip.execute("skip-gate-0", { throughGate: 1, reason: "must retain scope authority" }, undefined, undefined, ctx);
} catch (error) { skipGateZeroError = error.message; }
const paths = ["spec/**", "src/**", "docs/**"];
const gate0 = await gate.execute("gate-0", {
  gate: 0, scopeSummary: "Approve bounded project work", allowedPaths: paths,
  artifactPaths: ["evidence.txt"],
}, undefined, undefined, ctx);
const skipped = await skip.execute("skip-1-4", {
  throughGate: 4,
  reason: "Human explicitly chose an abbreviated maintenance path for this study",
}, undefined, undefined, ctx);
const gate5 = await gate.execute("gate-5", {
  gate: 5, scopeSummary: "Ship reviewed work", allowedPaths: paths,
  artifactPaths: ["evidence.txt"],
}, undefined, undefined, ctx);
const completeState = entries.filter((entry) => entry.customType === "system2-workflow-state").at(-1).data;
const completeStatus = (await ext.tools.get("system2_status").definition.execute("complete-status", {}, undefined, undefined, ctx)).details;
const entriesBeforeCompleteResume = entries.length;
await ext.commands.get("system2-resume").handler("", ctx);
const completeResumeStateEntries = entries.length - entriesBeforeCompleteResume;
const completeResumeNotice = notices.at(-1);
const parentToolCall = (ext.handlers.get("tool_call") || [])[0];
const completeWrite = await parentToolCall({ toolName: "write", input: { path: "docs/follow-up.md", content: "x" } }, ctx);
let completeDelegationError;
try {
  await ext.tools.get("system2_delegate").definition.execute("complete-delegate", {
    role: "code-reviewer", objective: "Review after completion", expectedOutputs: [],
    acceptanceCriteria: ["Return a review"], verificationCommands: [],
  }, undefined, undefined, ctx);
} catch (error) { completeDelegationError = error.message; }
const inputHandler = (ext.handlers.get("input") || [])[0];
const inputResult = await inputHandler({
  type: "input", text: "Please document one follow-up", source: "interactive",
}, ctx);
const maintenanceState = entries.filter((entry) => entry.customType === "system2-workflow-state").at(-1).data;
const archivesAfterInput = entries.filter((entry) => entry.customType === "system2-workflow-archive");
await ext.commands.get("system2-new").handler("full", ctx);
const fullState = entries.filter((entry) => entry.customType === "system2-workflow-state").at(-1).data;
const archivesAfterCommand = entries.filter((entry) => entry.customType === "system2-workflow-archive");
confirmationAnswers.push(false);
const rejectedGate = await gate.execute("gate-0-rejected", {
  gate: 0, scopeSummary: "Reject this proposed replacement scope", allowedPaths: ["src/**"], artifactPaths: [],
}, undefined, undefined, ctx);
await ext.commands.get("system2-history").handler("", ctx);
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  commands: [...ext.commands.keys()], tools: [...ext.tools.keys()], earlyNewStateEntries,
  gate0, skipGateZeroError, skipped, gate5, rejectedGate, completeState, completeStatus, completeResumeStateEntries, completeResumeNotice, completeWrite, completeDelegationError, inputResult, maintenanceState, fullState,
  archivesAfterInput, archivesAfterCommand, messages, notices, confirmations, entries,
}));
"""

_GATE4_HARNESS = r"""
const PKG = process.argv[2];
const project = process.argv[3];
const pkg = await import(PKG);
const loaded = await pkg.discoverAndLoadExtensions([], project, project);
const ext = loaded.extensions.find((candidate) => candidate.path.endsWith("system2.ts"));
const entries = [];
const confirmations = [];
let activeTools = ["read", "system2_request_gate_approval", "system2_delegate"];
loaded.runtime.appendEntry = (customType, data) => entries.push({ type: "custom", customType, data });
loaded.runtime.getActiveTools = () => [...activeTools];
loaded.runtime.setActiveTools = (names) => { activeTools = [...names]; };
loaded.runtime.getAllTools = () => [...ext.tools.values()].map((item) => item.definition);
loaded.runtime.getCommands = () => [...ext.commands.entries()].map(([name]) => ({ name, source: "extension" }));
const ctx = {
  cwd: project, mode: "tui", hasUI: true,
  sessionManager: { getBranch: () => entries },
  ui: {
    notify: () => {}, setStatus: () => {},
    confirm: async (title, body) => { confirmations.push({ title, body }); return true; },
  },
};
for (const handler of ext.handlers.get("session_start") || []) await handler({ type: "session_start", reason: "startup" }, ctx);
const gate = ext.tools.get("system2_request_gate_approval").definition;
const allowedPaths = ["spec/**", "src/**", "docs/**", ".gitignore", "permission-boundaries.md", "orphan.bin"];
for (let number = 0; number < 4; number++) {
  await gate.execute(`gate-${number}`, {
    gate: number, scopeSummary: `approve ${number}`, allowedPaths, artifactPaths: [],
  }, undefined, undefined, ctx);
}
let missingOutputsError;
try {
  await gate.execute("gate-4-missing", {
    gate: 4, scopeSummary: "missing outputs", allowedPaths, artifactPaths: [],
  }, undefined, undefined, ctx);
} catch (error) { missingOutputsError = error.message; }
let impossibleOutputError;
try {
  await gate.execute("gate-4-impossible", {
    gate: 4, scopeSummary: "impossible output", allowedPaths, artifactPaths: [],
    plannedOutputs: ["orphan.bin"],
  }, undefined, undefined, ctx);
} catch (error) { impossibleOutputError = error.message; }
const gate4 = await gate.execute("gate-4", {
  gate: 4, scopeSummary: "role-covered task plan", allowedPaths, artifactPaths: [],
  plannedOutputs: [".gitignore", "permission-boundaries.md", "src/app.py"],
}, undefined, undefined, ctx);
let unplannedDelegationError;
try {
  await ext.tools.get("system2_delegate").definition.execute("unplanned", {
    role: "docs-release", objective: "Create an unplanned guide",
    expectedOutputs: ["docs/extra.md"], acceptanceCriteria: ["Create the guide"],
    verificationCommands: [],
  }, undefined, undefined, ctx);
} catch (error) { unplannedDelegationError = error.message; }
process.stdout.write(JSON.stringify({
  loadErrors: loaded.errors.map((error) => error.message || String(error)),
  missingOutputsError, impossibleOutputError, unplannedDelegationError, gate4, confirmations,
  state: entries[entries.length - 1].data,
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
        with open(os.path.join(self.project, "context.txt"), "wb") as fh:
            fh.write(b"ordinary repository context\n")
        with open(os.path.join(self.project, "evidence.txt"), "wb") as fh:
            fh.write(b"provenance-bound evidence\n")

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

    def _run_restore(self, entries, recover=False, mode=None):
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
                mode or ("recover" if recover else "status"),
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
        context_digest = hashlib.sha256(b"ordinary repository context\n").hexdigest()
        evidence_digest = hashlib.sha256(b"provenance-bound evidence\n").hexdigest()
        context_inputs = [{"path": "context.txt", "sha256": context_digest}]
        gate_evidence = [{"path": "evidence.txt", "sha256": evidence_digest}]
        contract = {
            "role": "design-architect",
            "task": "Review the design artifact",
            "gateContext": {"gate": 1, "approvalId": "human-test", "constraints": []},
            "contextInputs": ["context.txt"],
            "gateEvidence": ["evidence.txt"],
            "expectedOutputs": ["spec/design.md"],
            "acceptanceCriteria": ["Return one bounded report"],
            "verificationCommands": [],
            "taskWriteScope": [r"^spec/design\.md$"],
            "budget": {"maxFiles": 1, "maxChangedLines": 10},
            "timeoutSeconds": 30,
        }
        contract_bytes = json.dumps(
            {"contract": contract, "contextInputs": context_inputs, "gateEvidence": gate_evidence},
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
            "contextInputs": context_inputs,
            "gateEvidence": gate_evidence,
            "contractDigest": hashlib.sha256(contract_bytes).hexdigest(),
            "contract": contract,
        }

    def _executor_child_spec_with_verification(self):
        spec = self._design_child_spec()
        role_prompt = os.path.join(
            self.project, ".pi", "prompts", "role-executor.md"
        )
        with open(role_prompt, "rb") as fh:
            spec["rolePromptDigest"] = hashlib.sha256(fh.read()).hexdigest()
        graph = ir.compose(oracle.PLUGIN_ROOT, [], self.project).graph
        role = next(item for item in graph.roles if item.name == "executor")
        spec["roleWriteScope"] = role.write_scope
        spec["tools"] = ["read", "edit", "write", "grep", "find", "bash"]
        spec["contract"]["role"] = "executor"
        spec["contract"]["task"] = "Implement and verify the design artifact"
        spec["contract"]["verificationCommands"] = ["python3 -B -m unittest"]
        contract_bytes = json.dumps(
            {
                "contract": spec["contract"],
                "contextInputs": spec["contextInputs"],
                "gateEvidence": spec["gateEvidence"],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        spec["contractDigest"] = hashlib.sha256(contract_bytes).hexdigest()
        return spec

    def _write_child_spec(self, spec):
        spec_dir = os.path.join(self.temp, f"child-spec-{len(os.listdir(self.temp))}")
        os.makedirs(spec_dir, mode=0o700)
        spec_path = os.path.join(spec_dir, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        os.chmod(spec_path, 0o600)
        return spec_path

    def _run_child_spec(self, spec, name):
        spec_path = self._write_child_spec(spec)
        harness = os.path.join(self.temp, f"{name}.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_CHILD_HARNESS)
        env = self._env()
        env["SYSTEM2_PI_CHILD_SPEC"] = spec_path
        return subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            cwd=self.project,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

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
const claim = {
  outcome: "complete",
  summary: `Native fake child completed ${spec.contract.role}`,
  blockers: [], risks: [], followUps: [],
};
process.stdout.write(JSON.stringify({
  type: "tool_execution_end", toolCallId: "child-complete",
  toolName: "system2_child_complete", isError: false,
  result: { content: [{ type: "text", text: "complete" }], details: {
    schemaVersion: 1, delegationId: spec.id, nonce: spec.nonce, role: spec.contract.role,
    contractDigest: spec.contractDigest, rolePromptDigest: spec.rolePromptDigest,
    claimDigest: crypto.createHash("sha256").update(canonical(claim)).digest("hex"), claim,
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
const claim = {
  outcome: "complete", summary: "Recovered on retry",
  blockers: [], risks: [], followUps: [],
};
process.stdout.write(JSON.stringify({
  type: "tool_execution_end", toolCallId: "child-complete",
  toolName: "system2_child_complete", isError: false,
  result: { details: {
    schemaVersion: 1, delegationId: spec.id, nonce: spec.nonce, role: spec.contract.role,
    contractDigest: spec.contractDigest, rolePromptDigest: spec.rolePromptDigest,
    claimDigest: crypto.createHash("sha256").update(canonical(claim)).digest("hex"), claim,
  }, content: [{ type: "text", text: "done" }] },
}) + "\n");
'''
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(fake, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def _write_resilient_pi(self, mode):
        fake = os.path.join(self.bin_dir, "pi")
        script = r'''#!/usr/bin/env node
const fs = require("node:fs");
const crypto = require("node:crypto");
const childProcess = require("node:child_process");
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : (value && typeof value === "object") ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const spec = JSON.parse(fs.readFileSync(process.env.SYSTEM2_PI_CHILD_SPEC, "utf8"));
const changedDirectory = "spec";
const changedPath = process.env.RESILIENCE_MODE === "excluded" ? "spec/interfaces.json" : "spec/design.md";
fs.mkdirSync(changedDirectory, { recursive: true });
const changedBody = process.env.RESILIENCE_MODE === "process-failure"
  ? `# ${process.env.RESILIENCE_MODE} ${process.pid}\n`
  : `# ${process.env.RESILIENCE_MODE}\n`;
fs.writeFileSync(changedPath, process.env.RESILIENCE_MODE === "binary" ? Buffer.from([0, 1, 2]) : changedBody);
const claim = {
  outcome: "complete", summary: "Completed after a recoverable report error",
  blockers: [], risks: [], followUps: [],
};
if (process.env.RESILIENCE_MODE !== "verification-missing") {
  process.stdout.write(JSON.stringify({
    type: "tool_execution_start", toolCallId: "verify", toolName: "bash",
    args: { command: "python3 -B -m unittest" },
  }) + "\n");
  process.stdout.write(JSON.stringify({
    type: "tool_execution_end", toolCallId: "verify", toolName: "bash", isError: process.env.RESILIENCE_MODE === "verification-failure",
    result: { content: [{ type: "text", text: "untrusted test output" }] },
  }) + "\n");
}
const accepted = {
  type: "tool_execution_end", toolCallId: "accepted", toolName: "system2_child_complete", isError: false,
  result: { details: {
    schemaVersion: 1, delegationId: spec.id, nonce: spec.nonce, role: spec.contract.role,
    contractDigest: spec.contractDigest, rolePromptDigest: spec.rolePromptDigest,
    claimDigest: crypto.createHash("sha256").update(canonical(claim)).digest("hex"), claim,
  }, content: [{ type: "text", text: "done" }] },
};
if (process.env.RESILIENCE_MODE === "retry") {
  process.stdout.write(JSON.stringify({
    type: "tool_execution_end", toolCallId: "rejected", toolName: "system2_child_complete",
    isError: true, result: { content: [{ type: "text", text: "invalid completion fields" }] },
  }) + "\n");
  process.stdout.write(JSON.stringify(accepted) + "\n");
} else if (process.env.RESILIENCE_MODE === "rejected") {
  for (let attempt = 1; attempt <= 3; attempt++) process.stdout.write(JSON.stringify({
    type: "tool_execution_end", toolCallId: `rejected-${attempt}`, toolName: "system2_child_complete",
    isError: true, result: { content: [{ type: "text", text: "untrusted validation failure" }] },
  }) + "\n");
} else if (process.env.RESILIENCE_MODE === "orphan") {
  const descendant = childProcess.spawn(process.execPath, ["-e", `
    const fs = require("fs");
    process.on("SIGTERM", () => {});
    setTimeout(() => fs.writeFileSync("spec/interfaces.json", "{}\\n"), 500);
    setTimeout(() => {}, 5000);
  `], { cwd: process.cwd(), stdio: "ignore" });
  descendant.unref();
  setTimeout(() => process.stdout.write(JSON.stringify(accepted) + "\n"), 300);
} else if (process.env.RESILIENCE_MODE === "budget" || process.env.RESILIENCE_MODE === "binary" || process.env.RESILIENCE_MODE === "excluded" || process.env.RESILIENCE_MODE === "verification-failure" || process.env.RESILIENCE_MODE === "verification-missing") {
  process.stdout.write(JSON.stringify(accepted) + "\n");
} else if (process.env.RESILIENCE_MODE === "timeout") {
  setTimeout(() => {}, 60000);
} else if (process.env.RESILIENCE_MODE === "no-completion") {
  process.stdout.write(JSON.stringify({
    type: "message_end",
    message: { role: "assistant", content: [{ type: "text", text: "I created the design, but omitted the completion call." }] },
  }) + "\n");
} else {
  process.stderr.write("provider emitted a secret diagnostic that must stay withheld\n");
  process.exit(7);
}
'''
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(fake, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return mode

    def _run_resilience_case(self, mode):
        self._write_resilient_pi(mode)
        harness = os.path.join(self.temp, f"resilience-{mode}.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_RESILIENCE_HARNESS)
        env = self._env()
        env["RESILIENCE_MODE"] = mode
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
        return result

    def test_gate_audit_skips_and_repeatable_workflow_cycles(self):
        harness = os.path.join(self.temp, "phase8.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_PHASE8_HARNESS)
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        self.assertEqual(result["earlyNewStateEntries"], 0)
        self.assertTrue(any("approve Gate 0 first" in item["message"] for item in result["notices"]))
        self.assertIn("Gate 0 establishes write authority and cannot be skipped", result["skipGateZeroError"])
        self.assertEqual(result["completeState"]["gate"]["complete"], True)
        self.assertEqual(result["completeStatus"]["operationalState"], "completed_review")
        self.assertEqual(result["completeResumeStateEntries"], 0)
        self.assertIn("completed workflow is read-only", result["completeResumeNotice"]["message"])
        self.assertTrue(result["completeWrite"]["block"])
        self.assertIn("read-only review mode", result["completeWrite"]["reason"])
        self.assertIn("start a new full or maintenance workflow", result["completeDelegationError"])
        self.assertEqual(
            [decision["decision"] for decision in result["completeState"]["gate"]["decisions"]],
            ["approved", "skipped", "skipped", "skipped", "skipped", "approved"],
        )
        gate_messages = [
            item for item in result["messages"]
            if item["message"]["customType"] == "system2-gate-decision"
        ]
        self.assertEqual(len(gate_messages), 7)
        self.assertTrue(all(item["message"]["display"] for item in gate_messages))
        self.assertTrue(all(item["options"] == {"triggerTurn": False} for item in gate_messages))
        self.assertIn("selected Approve in the interactive TUI", gate_messages[0]["message"]["content"])
        self.assertIn("explicitly skipped", gate_messages[1]["message"]["content"])
        self.assertIn("selected Reject in the interactive TUI", gate_messages[-1]["message"]["content"])
        self.assertEqual(result["rejectedGate"]["details"]["decision"]["decision"], "rejected")
        self.assertEqual(result["inputResult"], {"action": "continue"})
        self.assertEqual(result["maintenanceState"]["workflow"]["kind"], "maintenance")
        self.assertEqual(result["maintenanceState"]["workflow"]["sequence"], 2)
        self.assertEqual(result["maintenanceState"]["gate"], {"current": 0, "complete": False, "decisions": []})
        self.assertEqual(len(result["archivesAfterInput"]), 1)
        archived_complete = result["archivesAfterInput"][0]["data"]
        self.assertEqual(archived_complete["outcome"], "completed")
        self.assertEqual(len(archived_complete["gate"]["decisions"]), 6)
        self.assertEqual(archived_complete["gate"]["decisions"][0]["artifacts"][0]["status"], "present")
        self.assertEqual(result["fullState"]["workflow"]["kind"], "full")
        self.assertEqual(result["fullState"]["workflow"]["sequence"], 3)
        self.assertEqual(len(result["archivesAfterCommand"]), 2)
        self.assertEqual(result["archivesAfterCommand"][1]["data"]["outcome"], "abandoned")
        self.assertIn("system2-new", result["commands"])
        self.assertIn("system2-history", result["commands"])
        self.assertIn("system2_request_gate_skip", result["tools"])
        self.assertTrue(any("HUMAN APPROVAL REQUIRED" in item["title"] for item in result["confirmations"]))
        restored = self._run_restore(result["entries"])
        self.assertEqual(restored["statusBefore"]["workflow"]["sequence"], 3)
        self.assertEqual(len(restored["statusBefore"]["workflowHistory"]), 2)
        self.assertEqual(restored["statusBefore"]["workflowHistory"][0]["outcome"], "completed")
        self.assertEqual(restored["statusBefore"]["workflowHistory"][1]["outcome"], "abandoned")
        restored_tree = self._run_restore(result["entries"], mode="tree")
        self.assertEqual(restored_tree["statusBefore"]["workflow"]["sequence"], 3)
        tampered_archive = self._run_restore(result["entries"], mode="tamper-archive")
        self.assertFalse(tampered_archive["statusBefore"]["healthy"])
        self.assertIn("archive", tampered_archive["statusBefore"]["healthDiagnostic"])
        self.assertTrue(tampered_archive["parentWrite"]["block"])

    def test_gate_four_surfaces_role_coverage_and_refuses_impossible_plans(self):
        harness = os.path.join(self.temp, "gate4.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_GATE4_HARNESS)
        env = self._env()
        env["SYSTEM2_PI_BIN"] = os.path.realpath(_PI)
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        self.assertIn("requires concrete plannedOutputs", result["missingOutputsError"])
        self.assertIn("no configured System2 role", result["impossibleOutputError"])
        self.assertIn("absent from the human-approved Gate 4 task plan", result["unplannedDelegationError"])
        coverage = {
            item["path"]: item["compatibleRoles"]
            for item in result["gate4"]["details"]["roleCoverage"]
        }
        self.assertIn("executor", coverage[".gitignore"])
        self.assertEqual(coverage["permission-boundaries.md"], ["docs-release"])
        self.assertIn("executor", coverage["src/app.py"])
        self.assertEqual(
            result["gate4"]["details"]["decision"]["plannedOutputs"],
            [".gitignore", "permission-boundaries.md", "src/app.py"],
        )
        self.assertIn("Planned output role coverage", result["confirmations"][-1]["body"])
        self.assertIn(".gitignore -> executor", result["confirmations"][-1]["body"])
        self.assertIn("permission-boundaries.md -> docs-release", result["confirmations"][-1]["body"])
        self.assertEqual(result["state"]["gate"]["current"], 5)

    def test_parent_stays_orchestrator_and_dispatches_all_13_processes(self):
        record_path = os.path.join(self.temp, "child-records.jsonl")
        with open(os.path.join(self.project, "evidence.txt"), "wb") as fh:
            fh.write(b"provenance-bound evidence\n")
        with open(os.path.join(self.project, "context.txt"), "wb") as fh:
            fh.write(b"ordinary repository context\n")
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
            {"system2_delegate", "system2_request_gate_approval", "system2_request_gate_skip", "system2_status"},
        )
        self.assertIn("system2-status", result["commands"])
        self.assertIn("system2-history", result["commands"])
        self.assertIn("system2-new", result["commands"])
        self.assertIn("system2-resume", result["commands"])
        self.assertIn("system2-recover", result["commands"])
        self.assertIn("SYSTEM2 PARENT AUTHORITY: mode=orchestrator", result["before"]["systemPrompt"])
        self.assertIn("SYSTEM2 PARENT AUTHORITY: mode=orchestrator", result["after"]["systemPrompt"])
        self.assertNotIn("System2 role:", result["after"]["systemPrompt"])
        self.assertTrue(result["parentBashAllowed"])
        self.assertTrue(result["parentDangerous"]["block"])
        self.assertIn("block-dangerous", result["parentDangerous"]["reason"])
        self.assertTrue(result["parentSensitive"]["block"])
        self.assertIn("protect-sensitive", result["parentSensitive"]["reason"])
        self.assertTrue(result["parentShellWriteAllowed"])
        self.assertTrue(result["parentWriteAllowed"])
        self.assertTrue(result["parentWriteRejected"]["block"])
        self.assertIn("rejected", result["parentWriteRejected"]["reason"])
        self.assertTrue(result["parentWriteNoUi"]["block"])
        self.assertIn("interactive human approval", result["parentWriteNoUi"]["reason"])
        self.assertTrue(result["parentProtected"]["block"])
        self.assertIn("protected System2 path", result["parentProtected"]["reason"])
        self.assertIn("bash", result["activeTools"])
        self.assertIn("write", result["activeTools"])
        self.assertIn("edit", result["activeTools"])
        self.assertIn("system2_status", result["activeTools"])
        self.assertEqual(result["modelStatus"]["details"]["mode"], "orchestrator")
        self.assertEqual(result["modelStatus"]["details"]["gate"]["decisionCount"], 1)
        self.assertNotIn("decisions", result["modelStatus"]["details"]["gate"])
        overrides = [entry for entry in result["entries"] if entry["customType"] == "system2-parent-write-override"]
        self.assertEqual([entry["data"]["decision"] for entry in overrides], ["approved", "approved", "rejected"])
        self.assertEqual(overrides[0]["data"]["operation"], "bash")
        self.assertEqual(overrides[0]["data"]["paths"], ["notes.txt"])
        self.assertRegex(overrides[0]["data"]["requestDigest"], r"^[a-f0-9]{64}$")
        self.assertIn("not a regex", result["regexGateError"])
        self.assertEqual(result["gateResult"]["details"]["decision"]["source"], "tui")
        self.assertEqual(
            result["gateResult"]["details"]["decision"]["allowedPaths"],
            ["spec/**"],
        )
        artifact = result["gateResult"]["details"]["decision"]["artifacts"][0]
        self.assertEqual(artifact["status"], "present")
        self.assertEqual([item["path"] for item in result["gateResult"]["details"]["decision"]["artifacts"]], ["evidence.txt"])
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
        defaulted_record = next(record for record in records if record["spec"]["contract"]["role"] == "repo-governor")
        self.assertEqual(defaulted_record["spec"]["contract"]["budget"], {"maxFiles": 0, "maxChangedLines": 1000000})
        self.assertEqual(defaulted_record["spec"]["contract"]["timeoutSeconds"], 900)
        self.assertEqual(design_record["spec"]["contract"]["expectedOutputs"], ["spec/design.md"])
        self.assertEqual(design_record["spec"]["contract"]["taskWriteScope"], [r"^spec/design\.md$"])
        self.assertEqual(design_record["spec"]["contract"]["gateContext"]["gate"], 1)
        self.assertEqual(design_record["spec"]["contract"]["gateContext"]["approvalId"], result["gateResult"]["details"]["decision"]["id"])
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
            self.assertEqual(record["spec"]["gateEvidence"], [{
                "path": "evidence.txt",
                "sha256": hashlib.sha256(b"provenance-bound evidence\n").hexdigest(),
            }])
            expected_context = [{
                "path": "context.txt",
                "sha256": hashlib.sha256(b"ordinary repository context\n").hexdigest(),
            }] if record["spec"]["contract"]["role"] == "design-architect" else []
            self.assertEqual(record["spec"]["contextInputs"], expected_context)
            self.assertEqual(record["spec"]["contract"]["contextInputs"], [item["path"] for item in expected_context])
            self.assertEqual(record["spec"]["contract"]["gateEvidence"], ["evidence.txt"])

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
        completed = self._run_child_spec(spec, "tampered-child")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("invalid System2 child authority", completed.stderr)
        self.assertIn("extension digest mismatch", completed.stderr)

    def test_context_input_is_launch_bound_without_gate_approval(self):
        spec = self._design_child_spec()
        with open(os.path.join(self.project, "context.txt"), "wb") as fh:
            fh.write(b"changed after dispatch\n")
        completed = self._run_child_spec(spec, "changed-context-child")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("context input changed after dispatch: context.txt", completed.stderr)

    def test_human_approved_gate_evidence_remains_digest_bound(self):
        spec = self._design_child_spec()
        with open(os.path.join(self.project, "evidence.txt"), "wb") as fh:
            fh.write(b"changed after human approval\n")
        completed = self._run_child_spec(spec, "changed-evidence-child")
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("gate evidence digest mismatch: evidence.txt", completed.stderr)

    def test_contract_topology_and_subprocess_failures_fail_closed_then_retry(self):
        record_path = os.path.join(self.temp, "flaky-records.jsonl")
        with open(os.path.join(self.project, "evidence.txt"), "wb") as fh:
            fh.write(b"not approved by this gate\n")
        with open(os.path.join(self.project, ".env"), "wb") as fh:
            fh.write(b"SECRET=must-not-be-snapshotted\n")
        os.symlink(".env", os.path.join(self.project, "context-alias.txt"))
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
        self.assertIn("incomplete or unknown", result["legacyGateContextError"])
        self.assertIn("not covered by approved paths", result["unauthorizedOutputError"])
        self.assertIn("not covered by approved paths", result["nestedOutputError"])
        self.assertIn("delegation role preflight failed", result["roleMismatchError"])
        self.assertIn("use docs-release", result["roleMismatchError"])
        self.assertIn("no configured System2 role", result["noRoleError"])
        self.assertIn("protected System2 path", result["protectedOutputError"])
        self.assertIn("outside the project", result["outsideAbsoluteError"])
        self.assertIn("incomplete or unknown", result["legacyArtifactInputsError"])
        self.assertIn("protect-sensitive", result["sensitiveContextError"])
        self.assertIn("outside the project", result["outsideContextError"])
        self.assertIn("protect-sensitive", result["aliasedSensitiveContextError"])
        self.assertIn("delegation verification preflight failed", result["noBashVerificationError"])
        self.assertIn("design-architect has no Bash tool", result["noBashVerificationError"])
        self.assertIn("run those checks independently in the parent", result["noBashVerificationError"])
        self.assertIn("ambiguous System2 extension topology", result["duplicateError"])
        self.assertEqual(result["processResult"]["details"]["status"], "failed")
        self.assertEqual(result["processResult"]["details"]["failure"]["code"], "PROCESS_EXIT")
        self.assertEqual(result["processResult"]["details"]["failure"]["domain"], "infrastructure")
        self.assertFalse(result["processResult"]["details"]["failure"]["countsTowardCorrectiveCycle"])
        self.assertNotIn("intentional child failure", json.dumps(result["processResult"]))
        self.assertIn("Untrusted diagnostic content withheld", result["stateAfterFailure"]["lastDelegation"]["diagnostic"])
        self.assertNotIn("intentional child failure", result["stateAfterFailure"]["lastDelegation"]["diagnostic"])
        self.assertIsNone(result["stateAfterFailure"]["activeDelegation"])
        self.assertEqual(result["stateAfterFailure"]["lastDelegation"]["status"], "failed")
        self.assertEqual(result["stateAfterFailure"]["orchestration"]["infrastructureFailureStreak"], 1)
        self.assertIsNone(result["stateAfterRecovery"]["activeDelegation"])
        self.assertEqual(result["stateAfterRecovery"]["orchestration"]["infrastructureFailureStreak"], 0)
        self.assertEqual(result["stateAfterRecovery"]["lastDelegation"]["status"], "complete")
        self.assertEqual(result["recovered"]["details"]["report"]["summary"], "Recovered on retry")
        with open(record_path, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0]["pid"], records[1]["pid"])
        self.assertTrue(all(not os.path.exists(record["specPath"]) for record in records))

    def test_infrastructure_circuit_separates_retries_and_requires_human_resume(self):
        self._write_resilient_pi("process-failure")
        harness = os.path.join(self.temp, "circuit.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_CIRCUIT_HARNESS)
        env = self._env()
        env["RESILIENCE_MODE"] = "process-failure"
        completed = subprocess.run(
            [_NODE, harness, _PI_ENTRY, self.project],
            env=env, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["loadErrors"], [], result)
        self.assertIn("system2-resume", result["commands"])
        self.assertEqual(len(result["failures"]), 3)
        self.assertTrue(all(item["failure"]["domain"] == "infrastructure" for item in result["failures"]))
        self.assertTrue(all(not item["failure"]["countsTowardCorrectiveCycle"] for item in result["failures"]))
        reliability = result["stateAtCircuit"]["orchestration"]
        self.assertEqual(reliability["infrastructureFailureStreak"], 3)
        self.assertTrue(reliability["infrastructureCircuitOpen"])
        self.assertEqual(result["statusAtCircuit"]["operationalState"], "circuit_open")
        self.assertFalse(result["statusAtCircuit"]["healthy"])
        self.assertIn("circuit is open", result["circuitError"])
        self.assertIn("do not increment the application corrective/boomerang count", " ".join(result["statusAtCircuit"]["recommendedActions"]))
        resume_prompt = next(item for item in result["confirmations"] if item["title"].startswith("Resume System2"))
        self.assertIn("spec/design.md", resume_prompt["body"])
        self.assertIn("will never do so automatically", resume_prompt["body"])
        self.assertTrue(result["fileStillPresent"])
        self.assertEqual(result["stateAfterResume"]["orchestration"]["infrastructureFailureStreak"], 0)
        self.assertFalse(result["stateAfterResume"]["orchestration"]["infrastructureCircuitOpen"])
        self.assertEqual(result["stateAfterResume"]["orchestration"]["resumeCount"], 1)
        self.assertIn("already closed", result["alreadyClosedNotice"]["message"])
        self.assertIn("no resume is needed", result["alreadyClosedNotice"]["message"])
        self.assertEqual(result["postResume"]["details"]["failure"]["code"], "PROCESS_EXIT")
        self.assertEqual(result["finalState"]["orchestration"]["infrastructureFailureStreak"], 1)
        restored = self._run_restore(result["entries"])
        self.assertEqual(restored["statusBefore"]["orchestration"]["infrastructureFailureStreak"], 1)
        self.assertEqual(restored["statusBefore"]["orchestration"]["resumeCount"], 1)

    def test_child_completion_rejects_missing_exact_verification_then_accepts_observed_command(self):
        spec_path = self._write_child_spec(self._executor_child_spec_with_verification())
        harness = os.path.join(self.temp, "child-verification.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_CHILD_VERIFICATION_HARNESS)
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
        self.assertEqual(result["loadErrors"], [])
        self.assertIn("not observed verbatim", result["missingError"])
        self.assertIn("run each as a separate Bash call", result["missingError"])
        self.assertTrue(result["completion"]["terminate"])

    def test_rejected_completion_call_can_be_corrected_without_killing_child(self):
        result = self._run_resilience_case("retry")
        report = result["result"]["details"]["report"]
        self.assertEqual(result["state"]["lastDelegation"]["status"], "complete")
        self.assertEqual(report["completion"]["rejectedAttempts"], 1)
        self.assertEqual(report["filesTouched"], ["spec/design.md"])
        self.assertEqual(report["artifacts"][0]["sha256"], hashlib.sha256(b"# retry\n").hexdigest())
        self.assertEqual(report["evidence"]["source"], "project-snapshot")
        self.assertEqual(report["verification"], [{
            "command": "python3 -B -m unittest",
            "status": "passed",
            "evidence": "Observed a successful bash tool result; untrusted command output was withheld.",
        }])

    def test_completion_rejection_limit_returns_actionable_failure(self):
        result = self._run_resilience_case("rejected")
        details = result["result"]["details"]
        self.assertEqual(details["status"], "failed")
        self.assertEqual(details["failure"]["code"], "COMPLETION_REJECTED")
        self.assertEqual(details["report"]["completion"]["rejectedAttempts"], 3)
        self.assertEqual(details["report"]["filesTouched"], ["spec/design.md"])
        self.assertNotIn("untrusted validation failure", json.dumps(result))

    def test_missing_completion_returns_incomplete_report_with_observed_changes(self):
        result = self._run_resilience_case("no-completion")
        report = result["result"]["details"]["report"]
        self.assertEqual(result["state"]["lastDelegation"]["status"], "incomplete")
        self.assertEqual(result["result"]["details"]["failure"]["code"], "NO_COMPLETION")
        self.assertEqual(result["result"]["details"]["failure"]["domain"], "infrastructure")
        self.assertEqual(result["state"]["orchestration"]["infrastructureFailureStreak"], 1)
        self.assertEqual(report["filesTouched"], ["spec/design.md"])
        self.assertIn("omitted the completion call", report["summary"])
        self.assertNotIn("invalid child completion report", json.dumps(result))

    def test_budget_uses_observed_changes_not_model_reported_totals(self):
        result = self._run_resilience_case("budget")
        details = result["result"]["details"]
        self.assertEqual(details["status"], "failed")
        self.assertEqual(details["failure"]["code"], "SCOPE_VIOLATION")
        self.assertEqual(details["failure"]["domain"], "authorization")
        self.assertFalse(details["failure"]["countsTowardCorrectiveCycle"])
        self.assertEqual(details["report"]["linesAdded"], 1)
        self.assertEqual(details["report"]["filesTouched"], ["spec/design.md"])
        self.assertEqual(result["state"]["orchestration"]["infrastructureFailureStreak"], 0)

    def test_application_verification_failure_counts_only_toward_corrective_cycle(self):
        result = self._run_resilience_case("verification-failure")
        failure = result["result"]["details"]["failure"]
        self.assertEqual(failure["code"], "VERIFICATION_FAILED")
        self.assertEqual(failure["domain"], "application")
        self.assertTrue(failure["countsTowardCorrectiveCycle"])
        self.assertFalse(failure["countsTowardInfrastructureCircuit"])
        self.assertEqual(result["state"]["orchestration"]["infrastructureFailureStreak"], 0)

    def test_missing_contracted_verification_is_contract_failure_not_infrastructure(self):
        result = self._run_resilience_case("verification-missing")
        details = result["result"]["details"]
        failure = details["failure"]
        self.assertEqual(details["status"], "incomplete")
        self.assertEqual(failure["code"], "VERIFICATION_NOT_RUN")
        self.assertEqual(failure["domain"], "contract")
        self.assertFalse(failure["countsTowardCorrectiveCycle"])
        self.assertFalse(failure["countsTowardInfrastructureCircuit"])
        self.assertEqual(result["state"]["orchestration"]["infrastructureFailureStreak"], 0)
        self.assertEqual(details["report"]["verification"][0]["status"], "not_run")
        self.assertIn("Verify independently in the parent", " ".join(details["recommendedActions"]))

    def test_uncomputable_binary_evidence_cannot_produce_complete_outcome(self):
        result = self._run_resilience_case("binary")
        details = result["result"]["details"]
        self.assertEqual(details["status"], "incomplete")
        self.assertEqual(details["failure"]["code"], "EVIDENCE_FAILURE")
        self.assertFalse(details["report"]["evidence"]["complete"])
        self.assertIsNone(details["report"]["linesAdded"])

    def test_task_scoped_excluded_directory_change_is_not_invisible(self):
        result = self._run_resilience_case("excluded")
        details = result["result"]["details"]
        self.assertEqual(details["status"], "failed")
        self.assertEqual(details["failure"]["code"], "SCOPE_VIOLATION")
        self.assertEqual(details["failure"]["domain"], "authorization")
        self.assertEqual(details["report"]["filesTouched"], ["spec/interfaces.json"])

    def test_descendant_is_quiesced_before_after_snapshot(self):
        result = self._run_resilience_case("orphan")
        details = result["result"]["details"]
        self.assertEqual(details["status"], "complete")
        self.assertIn("spec/interfaces.json", details["report"]["filesTouched"])

    def test_timeout_returns_safe_category_and_partial_change_inventory(self):
        result = self._run_resilience_case("timeout")
        details = result["result"]["details"]
        self.assertEqual(details["status"], "failed")
        self.assertEqual(details["failure"]["code"], "TIMEOUT")
        self.assertEqual(details["report"]["filesTouched"], ["spec/design.md"])

    def test_process_failure_returns_safe_category_and_partial_change_inventory(self):
        result = self._run_resilience_case("process-failure")
        details = result["result"]["details"]
        self.assertEqual(result["state"]["lastDelegation"]["status"], "failed")
        self.assertEqual(details["failure"]["code"], "PROCESS_EXIT")
        self.assertEqual(details["report"]["filesTouched"], ["spec/design.md"])
        self.assertNotIn("secret diagnostic", json.dumps(result))
        self.assertRegex(details["failure"]["diagnosticDigest"], r"^[a-f0-9]{64}$")

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
        self.assertTrue(result["detached"]["block"])
        self.assertIn("process detachment", result["detached"]["reason"])
        self.assertTrue(result["sibling"]["block"])
        self.assertTrue(result["completion"]["terminate"])
        self.assertEqual(result["completion"]["details"]["nonce"], "nonce-native-test")
        self.assertEqual(result["completion"]["details"]["claim"]["outcome"], "complete")
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
  let invocation = 0;
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
      invocation += 1;
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
          arguments: invocation === 1 ? {
            outcome: "complete", summary: "First claim intentionally omits blockers",
            risks: [], followUps: [],
          } : {
            outcome: "complete", summary: "Real Pi child completed deterministically",
            blockers: [], risks: [], followUps: [],
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
        rejected = [
            event
            for event in events
            if event.get("type") == "tool_execution_end"
            and event.get("toolName") == "system2_child_complete"
            and event.get("isError")
        ]
        self.assertEqual(len(rejected), 1, events)
        self.assertEqual(len(completions), 1, events)
        details = completions[0]["result"]["details"]
        self.assertEqual(details["delegationId"], "child-native-test")
        self.assertEqual(details["role"], "design-architect")
        self.assertEqual(details["claim"]["outcome"], "complete")
        self.assertTrue(completions[0]["result"]["terminate"])


if __name__ == "__main__":
    unittest.main()
