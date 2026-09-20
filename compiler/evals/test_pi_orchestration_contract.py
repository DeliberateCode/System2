"""Contract tests for Pi's persistent-orchestrator subprocess delegation."""

import json
import os
import shutil
import tempfile
import unittest

from evals import oracle
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend


_EXPECTED_ROLE_TOOLS = {
    "code-reviewer": ["Read", "Grep", "Glob", "Bash"],
    "design-architect": ["Read", "Edit", "Write", "Grep", "Glob"],
    "docs-release": ["Read", "Edit", "Write", "Grep", "Glob"],
    "eval-engineer": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "executor": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "mcp-toolsmith": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "postmortem-scribe": ["Read", "Edit", "Write", "Grep", "Glob"],
    "repo-governor": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "requirements-engineer": ["Read", "Edit", "Write", "Grep", "Glob"],
    "security-sentinel": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
    "spec-coordinator": ["Read", "Edit", "Write", "Grep", "Glob"],
    "task-planner": ["Read", "Edit", "Write", "Grep", "Glob"],
    "test-engineer": ["Read", "Edit", "Write", "Grep", "Glob", "Bash"],
}


class PiOrchestrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = tempfile.mkdtemp(prefix="pi-orchestration-contract-")
        result = ir.compose(oracle.PLUGIN_ROOT, [], cls.project)
        if result.graph is None:
            raise AssertionError(result.errors)
        cls.graph = result.graph
        PiBackend().emit(cls.graph, cls.project)
        with open(
            os.path.join(cls.project, ".pi", "extensions", "system2.ts"),
            encoding="utf-8",
        ) as fh:
            cls.extension = fh.read()
        with open(
            os.path.join(cls.project, ".pi", "SYSTEM.md"), encoding="utf-8"
        ) as fh:
            cls.system_prompt = fh.read()
        with open(
            os.path.join(cls.project, "system2.pi.lock.json"), encoding="utf-8"
        ) as fh:
            cls.lock = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.project, ignore_errors=True)

    def test_ir_preserves_canonical_role_tools(self):
        actual = {role.name: role.tools for role in self.graph.roles}
        self.assertEqual(actual, _EXPECTED_ROLE_TOOLS)

    def test_unknown_canonical_role_tool_fails_compilation(self):
        role = next(role for role in self.graph.roles if role.name == "executor")
        role.tools.append("UnknownHarnessTool")
        try:
            with tempfile.TemporaryDirectory(prefix="pi-unknown-tool-") as project:
                with self.assertRaisesRegex(ValueError, "no explicit tool projection"):
                    PiBackend().emit(self.graph, project)
        finally:
            role.tools.pop()

    def test_fresh_parent_is_orchestrator_not_specialist(self):
        self.assertIn("You are the persistent System2 Orchestrator", self.system_prompt)
        self.assertNotIn("delegate to the 13 roles via `/delegate", self.system_prompt)
        self.assertNotIn("READ_ONLY_DEFAULT_ROLE", self.extension)
        self.assertNotIn("let activeRole", self.extension)
        self.assertIn('mode: "orchestrator"', self.extension)

    def test_model_control_tools_and_human_commands_are_distinct(self):
        for name in (
            "system2_delegate",
            "system2_request_gate_approval",
            "system2_child_complete",
        ):
            self.assertIn(f'name: "{name}"', self.extension)
        for name in ("delegate", "system2-status", "system2-recover"):
            self.assertIn(f'pi.registerCommand("{name}"', self.extension)
        self.assertIn("spawn(", self.extension)
        self.assertIn('"--no-session"', self.extension)
        self.assertIn('"--no-extensions"', self.extension)
        self.assertIn("independent_agent: true", self.extension)
        self.assertIn("SYSTEM2_PI_BIN", self.extension)
        self.assertNotIn('command: "pi"', self.extension)

    def test_lock_describes_proven_isolation_dimensions(self):
        self.assertEqual(self.lock["delegation_mode"], "pi_subprocess")
        self.assertTrue(self.lock["independent_agent"])
        self.assertEqual(
            self.lock["isolation"],
            {
                "process": True,
                "session": True,
                "context": True,
                "filesystem": False,
                "credentials": False,
            },
        )
        self.assertEqual(self.lock["subagent_isolation"], "adapted")

    def test_role_prompt_and_task_lease_are_child_authority(self):
        self.assertIn("ROLE_PROMPT_DIGESTS", self.extension)
        self.assertIn("ROLE_TOOLS", self.extension)
        self.assertIn("roleWriteScope", self.extension)
        self.assertIn("taskWriteScope", self.extension)
        self.assertIn("effective scope is their intersection", self.extension)

    def test_state_is_branch_local_versioned_and_custom_entry_authoritative(self):
        self.assertIn('const WORKFLOW_ENTRY = "system2-workflow-state"', self.extension)
        self.assertIn("schemaVersion: 1", self.extension)
        self.assertIn("ctx.sessionManager.getBranch()", self.extension)
        self.assertIn("pi.appendEntry(WORKFLOW_ENTRY", self.extension)
        self.assertIn('pi.on("session_tree"', self.extension)
        self.assertIn("interrupted", self.extension)


if __name__ == "__main__":
    unittest.main()
