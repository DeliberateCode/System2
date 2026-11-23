import { lstat, mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { getAgentDir, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { discoverAgents } from "./upstream/agents.ts";
import registerSubagent from "./upstream/index.ts";

// One stable agent entry point for all roles, including project-composed ones.
const agent = "system2-specialist";
const definition = `---
name: ${agent}
description: Run one delegated System2 role using the skill supplied in the handoff.
---
Read the role SKILL.md identified in the handoff before acting. If it is missing or unreadable, report that blocker without attempting the task. Follow the role and the handoff limits. Return the completion summary to the orchestrator. Do not delegate further or advance gates.
`;

export default function (pi: ExtensionAPI) {
  const compilerPath = fileURLToPath(new URL("../compiler/run.py", import.meta.url));
  pi.registerTool({
    name: "system2_compiler_path",
    label: "System2 compiler path",
    description: "Locate the compiler bundled with System2 for overlay and profile commands. This tool does not run it.",
    parameters: Type.Object({}),
    async execute() {
      return { content: [{ type: "text", text: compilerPath }], details: {} };
    },
  });

  async function setup(ctx: ExtensionContext, reportSuccess: boolean) {
    if (!pi.getActiveTools().includes("subagent")) {
      if (reportSuccess) ctx.ui.notify("The subagent tool is inactive. Check your Pi tool and extension settings.", "warning");
      return;
    }
    const schema = pi.getAllTools().find((tool) => tool.name === "subagent")?.parameters?.properties;
    if (!schema?.agent || !schema.task || !schema.agentScope) {
      ctx.ui.notify("The configured subagent tool does not match the official example's single-agent interface.", "warning");
      return;
    }
    const dir = join(getAgentDir(), "agents");
    const file = join(dir, `${agent}.md`);
    try {
      await mkdir(dir, { recursive: true });
      if (!(await lstat(dir)).isDirectory()) throw new Error(`Not a local directory: ${dir}`);
      const existing = discoverAgents(ctx.cwd, "user").agents.find((candidate) => candidate.name === agent);
      if (!existing) await writeFile(file, definition, { flag: "wx", mode: 0o600 });
      else if (existing.filePath !== file || !(await lstat(file)).isFile() || (await readFile(file, "utf8")) !== definition) {
        throw new Error(`Not replacing an existing ${agent} definition: ${existing.filePath}`);
      }
      if (reportSuccess) ctx.ui.notify("System2 subagents are ready. Start with /system2 <task>.", "info");
    } catch (error) {
      ctx.ui.notify(`System2 setup stopped: ${String(error)}`, "error");
    }
  }

  pi.on("session_start", async (_event, ctx) => {
    // Other extensions register their tools before session_start. Do not replace one.
    if (!pi.getAllTools().some((tool) => tool.name === "subagent")) registerSubagent(pi);
    await setup(ctx, false);
  });

  pi.registerCommand("system2-setup", {
    description: "Check or retry automatic System2 subagent setup.",
    handler: (_args, ctx) => setup(ctx, true),
  });
}
