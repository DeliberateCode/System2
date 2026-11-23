// Verify package discovery, setup, and the official subagent process boundary without a model.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

// The official example starts the current entry point in a fresh process. Act
// as a deterministic child and inspect the actual handoff and system prompt file.
if (process.argv[2] === "--mode") {
  const args = process.argv.slice(2);
  assert.deepEqual(args.slice(0, 4), ["--mode", "json", "-p", "--no-session"]);
  const carrier = fs.readFileSync(args[args.indexOf("--append-system-prompt") + 1], "utf8");
  assert.match(carrier, /Read the role SKILL.md identified in the handoff/);
  const [task, skillPath] = args.at(-1).split("\n");
  assert.equal(task, "Task: Read this role skill and report its verification procedure:");
  const skill = fs.readFileSync(skillPath, "utf8");
  assert.match(skill, /System2 role: executor/);
  assert.match(skill, /Human communication policy/);
  assert.match(skill, /Test executor discipline/);
  assert.equal(path.basename(process.cwd()), "project");
  console.log(JSON.stringify({ type: "message_end", message: {
    role: "assistant", content: [{ type: "text", text: `Specialist completed in process ${process.pid}` }],
    usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, cost: { total: 0 }, totalTokens: 2 },
    stopReason: "end",
  } }));
  process.exit(0);
}

const [entry, packageRoot, legacyRoot, scratch] = process.argv.slice(2);
const { createAgentSession, DefaultResourceLoader, SessionManager, SettingsManager } = await import(pathToFileURL(entry).href);
const agentDir = path.join(scratch, "agent");
const project = path.join(scratch, "project");
const archive = path.join(scratch, "preview-backup");
const payload = path.join(legacyRoot, "distributions/pi/payload/project");
for (const dir of [agentDir, archive, path.join(project, ".pi/extensions"), path.join(project, "spec")]) {
  fs.mkdirSync(dir, { recursive: true });
}
fs.writeFileSync(path.join(agentDir, "settings.json"), JSON.stringify({ packages: [packageRoot] }));
const userFiles = { "AGENTS.md": "# Caller-owned instructions\n", "spec/context.md": "# Existing spec\n" };
for (const [file, body] of Object.entries(userFiles)) fs.writeFileSync(path.join(project, file), body);

const settings = SettingsManager.create(project, agentDir);
settings.setProjectTrusted(true);
await settings.reload();
const loader = new DefaultResourceLoader({ cwd: project, agentDir, settingsManager: settings });

async function loadPackage() {
  await loader.reload();
  const { extensions, errors } = loader.getExtensions();
  const { skills, diagnostics: skillDiagnostics } = loader.getSkills();
  const { prompts, diagnostics: promptDiagnostics } = loader.getPrompts();
  assert.deepEqual([...errors, ...skillDiagnostics, ...promptDiagnostics], []);
  assert.equal(extensions.length, 1);
  assert.match(extensions[0].path, /subagents\/index\.ts$/);
  assert.deepEqual(prompts.map((prompt) => prompt.name).sort(), ["system2", "system2-compose", "system2-doctor", "system2-profile"]);
  assert.equal(skills.length, 13);
  assert.ok(skills.every((skill) => skill.name.startsWith("system2-role-")));
  assert.match(prompts.find((prompt) => prompt.name === "system2").content, /spec\/post-execution-log\.md/);
}

await loadPackage();
assert.equal(loader.getSystemPrompt(), undefined);

const systemFile = ".pi/SYSTEM.md";
const lock = "system2.pi.lock.json";
const extension = ".pi/extensions/system2.ts";
for (const file of [systemFile, lock]) fs.copyFileSync(path.join(payload, file), path.join(project, file));
fs.appendFileSync(path.join(project, systemFile), "\nPreserve this user edit.\n");
await loadPackage();
assert.match(loader.getSystemPrompt(), /system2_delegate/);

// Exercise the documented archival procedure without loading the retired extension.
fs.copyFileSync(path.join(legacyRoot, "distributions/pi/extensions/system2.ts"), path.join(project, extension));
for (const file of [systemFile, lock, extension]) {
  const original = path.join(project, file);
  const backup = path.join(archive, file);
  const before = fs.readFileSync(original);
  fs.mkdirSync(path.dirname(backup), { recursive: true });
  fs.renameSync(original, backup);
  assert.deepEqual(fs.readFileSync(backup), before);
}
await loadPackage();
assert.equal(loader.getSystemPrompt(), undefined);
for (const [file, body] of Object.entries(userFiles)) assert.equal(fs.readFileSync(path.join(project, file), "utf8"), body);
assert.ok(loader.getAgentsFiles().agentsFiles.some((file) => file.path === path.join(project, "AGENTS.md")));
const composedSkill = path.join(project, ".system2/pi/.pi/skills/system2-role-executor/SKILL.md");
const carrierPath = path.join(agentDir, "agents/system2-specialist.md");
const upstream = path.resolve(path.dirname(entry), "../examples/extensions/subagent/index.ts");
for (const existing of [false, true]) {
  const resources = new DefaultResourceLoader({
    cwd: project, agentDir, settingsManager: settings,
    additionalExtensionPaths: existing ? [upstream] : [],
  });
  await resources.reload();
  assert.deepEqual(resources.getExtensions().errors, []);
  const { session } = await createAgentSession({
    cwd: project, agentDir, settingsManager: settings,
    resourceLoader: resources, sessionManager: SessionManager.inMemory(project),
  });
  try {
    const notifications = [];
    await session.bindExtensions({
      uiContext: { notify: (message, level) => notifications.push({ message, level }) },
      onError: (error) => assert.fail(JSON.stringify(error)),
    });
    const tools = session.getAllTools().filter((tool) => tool.name === "subagent");
    assert.equal(tools.length, 1);
    assert.equal(tools[0].sourceInfo.path, existing ? upstream : path.join(packageRoot, "distributions/pi/subagents/index.ts"));
    assert.ok(session.getActiveToolNames().includes("subagent"));

    // A first session is enough; no setup command or model call is needed.
    assert.deepEqual(notifications, []);
    const carrier = fs.readFileSync(carrierPath, "utf8");
    assert.match(carrier, /name: system2-specialist/);
    const compilerPathTool = session.getToolDefinition("system2_compiler_path");
    const location = await compilerPathTool.execute("test-path", {});
    const compiler = location.content[0].text;
    assert.equal(compiler, path.join(packageRoot, "distributions/pi/compiler/run.py"));
    if (!existing) {
      execFileSync("python3", ["-ISB", compiler, "compile", "--overlays",
        path.join(packageRoot, "evals/fixtures/test-overlay")], { cwd: project });
      await loadPackage(); // No duplicate commands/skills after composition.
    }
    const subagent = session.getToolDefinition("subagent");
    const result = await subagent.execute("test-delegation", {
      agent: "system2-specialist", agentScope: "user",
      task: `Read this role skill and report its verification procedure:\n${composedSkill}`,
    }, undefined, undefined, { cwd: project });
    assert.equal(result.details.isError, false, JSON.stringify(result.content));
    const childPid = Number(result.content[0].text.match(/^Specialist completed in process (\d+)$/)?.[1]);
    assert.ok(childPid > 0 && childPid !== process.pid);

    if (!existing) {
      fs.unlinkSync(carrierPath); // Cover a fresh install with an existing subagent tool, too.
    } else {
      fs.appendFileSync(carrierPath, "\nCaller edit.\n");
      const { session: nextSession } = await createAgentSession({
        cwd: project, agentDir, settingsManager: settings,
        resourceLoader: resources, sessionManager: SessionManager.inMemory(project),
      });
      try {
        await nextSession.bindExtensions({
          uiContext: { notify: (message, level) => notifications.push({ message, level }) },
        });
        assert.match(notifications.at(-1).message, /Not replacing an existing/);
        assert.equal(fs.readFileSync(carrierPath, "utf8"), carrier + "\nCaller edit.\n");
      } finally {
        nextSession.dispose();
      }
    }
  } finally {
    session.dispose();
  }
}
console.log("Pi: 13 role skills, 4 prompts, setup reuses an existing subagent tool or loads the bundled example; separate child process verified.");
