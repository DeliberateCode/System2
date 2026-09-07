"""Semantic controls for canonical role and workflow projection."""

import json
import os
import shutil
import tempfile
import unittest

from evals import matrix, oracle
from system2_compiler import ir
from system2_compiler.backends.codex import CodexBackend
from system2_compiler.backends.pi import PiBackend

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY

_ROLE_EXCERPTS = {
    "requirements-engineer": (
        "Use EARS-style statements.",
        "Each requirement gets an ID: REQ-001, REQ-002",
        "Traceability Matrix (Requirement -> Design Section -> Task IDs)",
    ),
    "task-planner": (
        "Task ID: TASK-001, TASK-002",
        "Traceability (REQ IDs -> TASK IDs)",
    ),
    "executor": (
        "## TDD Verification Loop",
        "bias toward small, reviewable changes and strong tests",
        "corrective requirement packet",
    ),
    "test-engineer": (
        "Classify: flaky vs deterministic vs environment.",
        "Verification summary must include:",
    ),
    "eval-engineer": (
        "Golden Dataset strategy (case authoring, review, versioning)",
        "corrective cycle count (should remain under the cap of 3)",
    ),
    "security-sentinel": (
        "Abuse Cases (at least 5 realistic misuse scenarios)",
        "Require human-in-the-loop gates for irreversible actions.",
    ),
    "code-reviewer": (
        "Surface-area delta:",
        "Future-change probe:",
    ),
}

_ORCHESTRATOR_EXCERPTS = (
    "Pause for explicit user approval at each gate unless the user says to skip gates.",
    "Parse executor's completion summary for `files_changed`, `tests_added`, `test_outcomes`",
    "system2:test-engineer -> simplification (code-reviewer in simplification mode) "
    "-> system2:security-sentinel -> system2:eval-engineer -> "
    "system2:docs-release -> system2:code-reviewer",
    "(a) Delegate fixes to executor, then re-run this agent",
    "(b) Override and proceed to next agent",
    "(c) Abort the workflow",
    "Read `spec/post-execution-log.md` to aggregate completion summaries",
    "previously passing tests now failing",
    "changed-file summary (files modified since last green run)",
    "After **3** corrective cycles without convergence",
)


def _missing(text, excerpts):
    return [excerpt for excerpt in excerpts if excerpt not in text]


def _read(root, relative):
    with open(os.path.join(root, relative), encoding="utf-8") as fh:
        return fh.read()


class CanonicalRoleContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projects = {
            "codex": tempfile.mkdtemp(prefix="role-contract-codex-"),
            "pi": tempfile.mkdtemp(prefix="role-contract-pi-"),
        }
        result = ir.compose(_BASE, [_TEST_OVERLAY], cls.projects["codex"])
        if result.graph is None:
            raise AssertionError(f"compose refused role-contract cell: {result.errors!r}")
        cls.graph = result.graph
        CodexBackend().emit(cls.graph, cls.projects["codex"])
        PiBackend().emit(cls.graph, cls.projects["pi"])

    @classmethod
    def tearDownClass(cls):
        for project in cls.projects.values():
            shutil.rmtree(project, ignore_errors=True)

    def test_ir_carries_frontmatter_free_serializable_contracts(self):
        roles = {role.name: role for role in self.graph.roles}
        self.assertEqual(set(_ROLE_EXCERPTS), set(_ROLE_EXCERPTS) & set(roles))
        for name, role in roles.items():
            with self.subTest(role=name, control="frontmatter"):
                contract = role.contract_text
                self.assertTrue(contract)
                self.assertFalse(contract.startswith("---"))
                self.assertNotIn("\ntools:\n", contract)
                self.assertNotIn("\nhooks:\n", contract)
                self.assertNotIn("\nmodel:", contract)
                self.assertNotIn("CLAUDE_PLUGIN_ROOT", contract)
        for name, excerpts in _ROLE_EXCERPTS.items():
            with self.subTest(role=name, control="semantics"):
                self.assertEqual([], _missing(roles[name].contract_text, excerpts))
        serialized = json.loads(self.graph.to_json())
        self.assertTrue(all(role["contract_text"] for role in serialized["roles"]))
        self.assertIn(
            "Pause for explicit user approval at each gate",
            serialized["gate_graph"]["approval_rule"],
        )

    def test_codex_and_pi_roles_preserve_distinctive_semantics(self):
        role_paths = {
            "codex": lambda name: os.path.join(
                "skills", f"system2-role-{name}", "SKILL.md"
            ),
            "pi": lambda name: os.path.join(".pi", "prompts", f"role-{name}.md"),
        }
        for target, path_for in role_paths.items():
            for name, excerpts in _ROLE_EXCERPTS.items():
                with self.subTest(target=target, role=name):
                    artifact = _read(self.projects[target], path_for(name))
                    self.assertEqual([], _missing(artifact, excerpts))
                    self.assertNotIn("attempt_completion", artifact)
                    self.assertIn("final completion response", artifact)

    def test_emitted_role_inventories_are_exactly_thirteen(self):
        expected = set(self.graph.delegation_contract.preferred_order)
        self.assertEqual(13, len(expected))

        codex_skills = os.listdir(os.path.join(self.projects["codex"], "skills"))
        codex_prefix = "system2-role-"
        codex_roles = {
            name[len(codex_prefix) :]
            for name in codex_skills
            if name.startswith(codex_prefix)
        }
        pi_prompts = os.listdir(os.path.join(self.projects["pi"], ".pi", "prompts"))
        pi_roles = {
            name[len("role-") : -len(".md")]
            for name in pi_prompts
            if name.startswith("role-") and name.endswith(".md")
        }
        self.assertEqual(expected, codex_roles)
        self.assertEqual(expected, pi_roles)

    def test_generic_role_stub_fails_the_semantic_control(self):
        generic_stub = (
            "You are the System2 requirements-engineer agent. "
            "Operate within your gate role and write scope."
        )
        self.assertEqual(
            list(_ROLE_EXCERPTS["requirements-engineer"]),
            _missing(generic_stub, _ROLE_EXCERPTS["requirements-engineer"]),
        )

    def test_orchestrators_render_complete_canonical_workflow(self):
        artifacts = {
            "codex": _read(
                self.projects["codex"],
                os.path.join("skills", "system2", "SKILL.md"),
            ),
            "pi": _read(self.projects["pi"], os.path.join(".pi", "SYSTEM.md")),
        }
        for target, artifact in artifacts.items():
            with self.subTest(target=target):
                self.assertEqual([], _missing(artifact, _ORCHESTRATOR_EXCERPTS))
                self.assertNotIn("attempt_completion", artifact)
                self.assertIn("final completion response", artifact)
        self.assertIn("Trust state (READ THIS FIRST", artifacts["codex"])
        self.assertIn("Enforcement on Pi (read this", artifacts["pi"])


if __name__ == "__main__":
    unittest.main()
