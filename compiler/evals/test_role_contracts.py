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
    "Execution order: test-engineer, code-reviewer (simplification), security-sentinel, "
    "eval-engineer, docs-release, code-reviewer",
    "Run test-engineer (always)",
    "Run security-sentinel (when changed path/content matches security patterns)",
    "Blocker policy: user-gate",
    "Blocker options: delegate-fix, override, abort",
    "Boomerang cap: 3",
    "Corrective-cycle cap: 3",
    "Classification: Local, Non-local",
    "Regression ledger fields:",
    "previously passing tests now failing",
    "changed-file summary (files modified since last green run)",
)

_FORBIDDEN_NEUTRAL_MECHANICS = (
    "CLAUDE_PLUGIN_ROOT",
    "SubagentStop",
    "plugin/allowlists/",
    ".task-budget.json",
    "change-budget-reporter.py",
    "system2:",
    "attempt_completion",
)

_CLAUDE_ROLE_REFERENCES = (
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/rules",
    ".claude/slop-catalog.md",
    "executor.regex",
    "Claude Code CLI",
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
                for forbidden in (
                    *_FORBIDDEN_NEUTRAL_MECHANICS,
                    *_CLAUDE_ROLE_REFERENCES,
                ):
                    self.assertNotIn(forbidden, contract)
        for name, excerpts in _ROLE_EXCERPTS.items():
            with self.subTest(role=name, control="semantics"):
                self.assertEqual([], _missing(roles[name].contract_text, excerpts))
        serialized = json.loads(self.graph.to_json())
        self.assertTrue(all(role["contract_text"] for role in serialized["roles"]))
        self.assertIn(
            "Pause for explicit user approval at each gate",
            serialized["gate_graph"]["approval_rule"],
        )
        neutral_workflow = json.dumps(
            {
                "post_execution": {
                    key: value
                    for key, value in serialized["post_execution"].items()
                    if key != "opaque_text"
                },
                "maintenance_loop": {
                    key: value
                    for key, value in serialized["maintenance_loop"].items()
                    if key != "opaque_text"
                },
            }
        )
        for forbidden in _FORBIDDEN_NEUTRAL_MECHANICS:
            self.assertNotIn(forbidden, neutral_workflow)

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
        for target, root, path_for in (
            (
                "codex",
                self.projects["codex"],
                lambda name: os.path.join(
                    "skills", f"system2-role-{name}", "SKILL.md"
                ),
            ),
            (
                "pi",
                self.projects["pi"],
                lambda name: os.path.join(".pi", "prompts", f"role-{name}.md"),
            ),
        ):
            for name in expected:
                with self.subTest(target=target, role=name, control="neutrality"):
                    artifact = _read(root, path_for(name))
                    for forbidden in (
                        *_FORBIDDEN_NEUTRAL_MECHANICS,
                        *_CLAUDE_ROLE_REFERENCES,
                    ):
                        self.assertNotIn(forbidden, artifact)
                    self.assertEqual(1, artifact.count("# System2 role:"))
                    self.assertNotIn("## Canonical role contract\n\n\n", artifact)

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
                for trigger in self.graph.post_execution.trigger_rules:
                    when = "always" if trigger.always else f"when {trigger.condition}"
                    self.assertIn(f"Run {trigger.agent} ({when})", artifact)
                for option in self.graph.post_execution.blocker_policy["options"]:
                    self.assertIn(option, artifact)
                for field in self.graph.maintenance_loop.regression_ledger_fields:
                    self.assertIn(field, artifact)
                for forbidden in _FORBIDDEN_NEUTRAL_MECHANICS:
                    self.assertNotIn(forbidden, artifact)
                self.assertEqual(1, artifact.count("# System2 orchestrator"))
        self.assertIn("Trust state (READ THIS FIRST", artifacts["codex"])
        self.assertIn("Enforcement on Pi (read this", artifacts["pi"])


class InvalidRoleContractTest(unittest.TestCase):
    def _compose_with_executor_source(self, mutation):
        source_parent = tempfile.mkdtemp(prefix="invalid-role-source-")
        project = tempfile.mkdtemp(prefix="invalid-role-project-")
        base = os.path.join(source_parent, "plugin")
        shutil.copytree(_BASE, base)
        role_path = os.path.join(base, "agents", "executor.md")
        mutation(role_path)
        try:
            result = ir.compose(base, [_TEST_OVERLAY], project)
            self.assertIsNone(result.graph)
            self.assertEqual([], result.files_to_write)
            self.assertTrue(result.errors)
            self.assertIn("executor", result.errors[0])
            self.assertEqual([], os.listdir(project))
            return result
        finally:
            shutil.rmtree(source_parent, ignore_errors=True)
            shutil.rmtree(project, ignore_errors=True)

    def test_missing_role_source_refuses_without_a_graph_or_write_plan(self):
        result = self._compose_with_executor_source(os.remove)
        self.assertIn("missing or unreadable", result.errors[0])

    def test_unreadable_role_source_refuses_without_a_raw_decode_error(self):
        def make_unreadable(path):
            with open(path, "wb") as fh:
                fh.write(b"\xff\xfe\x00")

        result = self._compose_with_executor_source(make_unreadable)
        self.assertIn("missing or unreadable", result.errors[0])
        self.assertNotIn("UnicodeDecodeError", result.errors[0])

    def test_empty_role_source_refuses_without_a_graph_or_write_plan(self):
        def empty(path):
            with open(path, "w", encoding="utf-8"):
                pass

        result = self._compose_with_executor_source(empty)
        self.assertIn("empty", result.errors[0])

    def test_unterminated_frontmatter_refuses_without_a_graph_or_write_plan(self):
        def unterminate(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("---\nname: executor\n")

        result = self._compose_with_executor_source(unterminate)
        self.assertIn("unterminated frontmatter", result.errors[0])


if __name__ == "__main__":
    unittest.main()
