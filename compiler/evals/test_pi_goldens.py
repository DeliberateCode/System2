"""Focused checks for the lightweight Pi guidance projection."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from evals import matrix, oracle
from system2_compiler import ir
from system2_compiler.backends.pi import PiBackend, _RUNTIME_BOUNDARY


_EXPECTED_ROLES = {
    "system2-role-code-reviewer",
    "system2-role-design-architect",
    "system2-role-docs-release",
    "system2-role-eval-engineer",
    "system2-role-executor",
    "system2-role-mcp-toolsmith",
    "system2-role-postmortem-scribe",
    "system2-role-repo-governor",
    "system2-role-requirements-engineer",
    "system2-role-security-sentinel",
    "system2-role-spec-coordinator",
    "system2-role-task-planner",
    "system2-role-test-engineer",
}


def _emit(project):
    result = ir.compose(oracle.PLUGIN_ROOT, [], project)
    if result.graph is None:
        raise AssertionError(result.errors)
    PiBackend().emit(result.graph, project)
    return result.graph


class PiProjectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-projection-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.graph = _emit(self.root)

    def test_emits_workflow_overlay_prompts_and_thirteen_role_skills(self):
        prompts = set(os.listdir(os.path.join(self.root, ".pi", "prompts")))
        self.assertEqual(prompts, {"system2.md", "system2-doctor.md", "system2-compose.md", "system2-profile.md"})

        skills_root = os.path.join(self.root, ".pi", "skills")
        roles = {
            name for name in os.listdir(skills_root)
            if os.path.isfile(os.path.join(skills_root, name, "SKILL.md"))
        }
        self.assertEqual(roles, _EXPECTED_ROLES)

    def test_orchestrator_is_a_plain_prompt_template(self):
        text = Path(self.root, ".pi", "prompts", "system2.md").read_text()
        self.assertIn("$ARGUMENTS", text)
        self.assertIn(_RUNTIME_BOUNDARY, text)
        self.assertIn('`subagent` in single mode with `agent: "system2-specialist"`', text)
        self.assertIn('`agentScope: "user"`', text)
        self.assertIn("absolute role `SKILL.md` path", text)
        self.assertIn("do not use chain mode across approval gates", text)
        self.assertIn("unavailable after automatic setup", text)
        self.assertIn("`system2_compiler_path`", text)
        self.assertNotIn("system2_delegate", text)

    def test_role_skill_preserves_the_canonical_contract(self):
        text = Path(
            self.root, ".pi", "skills", "system2-role-executor", "SKILL.md"
        ).read_text()
        self.assertIn("## TDD Verification Loop", text)
        self.assertIn("Pi controls active tools and project trust", text)
        self.assertNotIn("system2_child_complete", text)
        self.assertNotIn("Write scope:", text)

    def test_lock_is_advisory_and_owns_every_emitted_artifact(self):
        lock = json.loads(Path(self.root, "system2.pi.lock.json").read_text())
        self.assertEqual(lock["backend"], "pi")
        self.assertEqual(lock["enforcement"], "host-managed")
        self.assertEqual(lock["subagent_isolation"], "separate-process-context")
        self.assertEqual(lock["delegation_mode"], "pi-subagent-example")
        for capability in lock["capabilities"].values():
            self.assertEqual(capability["status"], "advisory")
            self.assertFalse(capability["enforced"])
            self.assertFalse(capability["gated"])

        owned = {entry["path"] for entry in lock["ownership"]["artifacts"]}
        actual = set()
        for directory, _subdirs, files in os.walk(self.root):
            for filename in files:
                actual.add(os.path.relpath(
                    os.path.join(directory, filename), self.root
                ).replace(os.sep, "/"))
        self.assertEqual(owned, actual)

    def test_plan_is_deterministic_and_non_mutating(self):
        with tempfile.TemporaryDirectory(prefix="pi-plan-") as project:
            result = ir.compose(oracle.PLUGIN_ROOT, [matrix.TEST_OVERLAY], project)
            self.assertIsNotNone(result.graph)
            backend = PiBackend()
            self.assertEqual(
                backend.plan(result.graph, project),
                backend.plan(result.graph, project),
            )
            self.assertEqual(os.listdir(project), [])


if __name__ == "__main__":
    unittest.main()
