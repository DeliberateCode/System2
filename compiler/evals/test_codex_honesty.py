"""Keep the Codex projection small and honest about its runtime boundary."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from evals import oracle
from system2_compiler import ir
from system2_compiler.backends import codex
from system2_compiler.backends.codex import CodexBackend


def _emit(project):
    result = ir.compose(oracle.PLUGIN_ROOT, [], project)
    if result.graph is None:
        raise AssertionError(result.errors)
    CodexBackend().emit(result.graph, project)


class CodexHonestyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-honesty-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        _emit(self.root)

    def test_distribution_contains_no_system2_runtime_or_hooks(self):
        paths = []
        for directory, subdirs, files in os.walk(self.root):
            paths.extend(os.path.relpath(os.path.join(directory, name), self.root)
                         for name in subdirs + files)
        lowered = "\n".join(paths).lower()
        self.assertNotIn("hook", lowered)
        self.assertFalse(any(path.endswith((".js", ".py")) for path in paths))

    def test_orchestrator_states_the_runtime_boundary_once_and_plainly(self):
        path = os.path.join(self.root, "skills", "system2", "SKILL.md")
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn(codex._RUNTIME_BOUNDARY, text)
        self.assertIn("does not install hooks or modify Codex configuration", text)
        self.assertIn("not isolated subagent execution", text)
        self.assertNotIn("pending native acceptance", text.lower())
        self.assertNotIn("system2 codex init", text)

    def test_role_skill_is_plain_guidance(self):
        path = os.path.join(
            self.root, "skills", "system2-role-executor", "SKILL.md"
        )
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("Codex controls permissions and approvals", text)
        self.assertNotIn("Write scope:", text)
        self.assertNotIn("## Operating guidance", text)

    def test_manifest_and_lock_do_not_overclaim(self):
        manifest = json.loads(Path(
            self.root, ".codex-plugin", "plugin.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["description"],
            "Deliberate, spec-driven engineering workflows for Codex.",
        )

        lock = json.loads(Path(
            self.root, "system2.codex.lock.json"
        ).read_text(encoding="utf-8"))
        self.assertIn(codex._RUNTIME_BOUNDARY, lock["FIDELITY"])
        for capability in lock["capabilities"].values():
            self.assertEqual(capability["status"], "advisory")
            self.assertIs(capability["enforced"], False)
            self.assertIs(capability["gated"], False)


if __name__ == "__main__":
    unittest.main()
