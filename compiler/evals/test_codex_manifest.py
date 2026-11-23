"""Focused Codex plugin projection checks."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from evals import matrix, oracle
from system2_compiler import ir
from system2_compiler.backends.codex import CodexBackend


_EXPECTED_SKILLS = {
    "system2",
    "system2-doctor",
    "system2-compose",
    "system2-profile",
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
    CodexBackend().emit(result.graph, project)


def _read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


class CodexManifestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-plugin-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        _emit(self.root)

    def test_manifest_points_only_to_skills(self):
        manifest = _read_json(os.path.join(self.root, ".codex-plugin", "plugin.json"))
        self.assertEqual(manifest["name"], "system2")
        self.assertEqual(manifest["skills"], "./skills")
        self.assertNotIn("hooks", manifest)
        self.assertNotIn("mcpServers", manifest)

    def test_marketplace_selects_the_codex_distribution(self):
        marketplace = _read_json(os.path.join(
            oracle.PLUGIN_REPO_ROOT, ".agents", "plugins", "marketplace.json"
        ))
        self.assertEqual(marketplace["name"], "system2-marketplace")
        self.assertEqual(len(marketplace["plugins"]), 1)
        self.assertEqual(marketplace["plugins"][0]["source"], "./distributions/codex")

    def test_emits_workflow_and_overlay_commands_with_thirteen_roles(self):
        skills_root = os.path.join(self.root, "skills")
        names = {
            name for name in os.listdir(skills_root)
            if os.path.isfile(os.path.join(skills_root, name, "SKILL.md"))
        }
        self.assertEqual(names, _EXPECTED_SKILLS)
        for name in names:
            with self.subTest(skill=name):
                text = Path(skills_root, name, "SKILL.md").read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"))
                self.assertIn(f"name: {name}\n", text)

    def test_lock_is_advisory_and_owns_every_emitted_artifact(self):
        lock = _read_json(os.path.join(self.root, "system2.codex.lock.json"))
        self.assertEqual(lock["backend"], "codex")
        self.assertEqual(lock["enforcement"], "host-managed")
        self.assertEqual(lock["subagent_isolation"], "separate-thread-context")
        self.assertEqual(lock["delegation_mode"], "native-subagents")
        self.assertTrue(lock["capabilities"])
        for name, capability in lock["capabilities"].items():
            with self.subTest(capability=name):
                self.assertEqual(capability["status"], "advisory")
                self.assertFalse(capability["enforced"])
                self.assertFalse(capability["gated"])

        owned = {entry["path"] for entry in lock["ownership"]["artifacts"]}
        actual = set()
        for directory, _subdirs, files in os.walk(self.root):
            for filename in files:
                rel = os.path.relpath(os.path.join(directory, filename), self.root)
                actual.add(rel.replace(os.sep, "/"))
        self.assertEqual(owned, actual)

    def test_plan_is_deterministic_and_non_mutating(self):
        with tempfile.TemporaryDirectory(prefix="codex-plan-") as project:
            result = ir.compose(oracle.PLUGIN_ROOT, [matrix.TEST_OVERLAY], project)
            self.assertIsNotNone(result.graph)
            backend = CodexBackend()
            first = backend.plan(result.graph, project)
            second = backend.plan(result.graph, project)
            self.assertEqual(first, second)
            self.assertEqual(os.listdir(project), [])


if __name__ == "__main__":
    unittest.main()
