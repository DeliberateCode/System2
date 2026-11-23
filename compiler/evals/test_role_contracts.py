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
    "existence alone is not evidence of approval",
    "start at the earliest gate that lacks approval",
    "if an artifact changed since approval",
    "the user's decision and the approved scope or artifact version",
    "only after Gate 4 approval or an explicit user override",
    "A completed tool call or closed thread alone does not prove success.",
    "If executor status is not success, do not trigger post-execution",
    "test-engineer: Always trigger",
    "security-sentinel: Trigger if any changed file path or content matches security patterns",
    "ask user to choose:",
    "Explain the proposed verification and review plan",
    "Wait for user approval or override",
    "test-engineer -> simplification (code-reviewer in simplification mode) -> "
    "security-sentinel -> eval-engineer -> docs-release -> code-reviewer",
    "Append completion summary to `spec/post-execution-log.md`",
    "write approved entries to `.claude/slop-catalog.md`",
    "Delegate fixes to executor, then re-run this agent",
    "Override and proceed to next agent",
    "Abort the workflow",
    "Track boomerang count per agent; if count reaches 3, halt and escalate",
    "Read `spec/post-execution-log.md` to aggregate completion summaries",
    "agents run, agents skipped, user overrides, boomerang cycles, timing",
    "Do not proceed to merge or deploy without approval",
    "**Local**: regression is confined to file(s) currently being edited",
    "**Non-local**: regression is in files not touched by the current task",
    "Record a regression ledger in `spec/regression-ledger.md`",
    "in corrective mode to produce a bounded corrective requirement packet",
    "**Amendment**: corrective requirements refine existing design decisions",
    "**Invalidation**: corrective requirements contradict or obsolete existing design decisions",
    "refresh `spec/design.md` and `spec/tasks.md`",
    "Re-enter executor only after the corrective packet is approved",
    "After **3** corrective cycles without convergence",
    "Do not reset the counter unless the user explicitly approves a scope change or the verification suite passes cleanly",
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
    "executor.regex",
    "Claude Code",
)

_REPO_GOVERNOR_NEUTRAL_SECURITY_POLICY = (
    "Sensitive and large-artifact access policy",
    "using the active harness's documented native mechanism",
    "Do not guess configuration syntax.",
    "report that native access controls are unsupported.",
)

_REPO_GOVERNOR_CLAUDE_PERMISSION_RESIDUE = (
    "Claude Code",
    "permissions.deny",
    "Read(",
    "Edit(",
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
        result = ir.compose(_BASE, [], cls.projects["codex"])
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
            "pi": lambda name: os.path.join(
                ".pi", "skills", f"system2-role-{name}", "SKILL.md"
            ),
        }
        for target, path_for in role_paths.items():
            for name, excerpts in _ROLE_EXCERPTS.items():
                with self.subTest(target=target, role=name):
                    artifact = _read(self.projects[target], path_for(name))
                    self.assertEqual([], _missing(artifact, excerpts))
                    self.assertIn("final completion response", artifact)
                    self.assertIn("Do not spawn another subagent, approve gates", artifact)

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
        pi_skills = os.listdir(os.path.join(self.projects["pi"], ".pi", "skills"))
        pi_prefix = "system2-role-"
        pi_roles = {
            name[len(pi_prefix) :]
            for name in pi_skills
            if name.startswith(pi_prefix)
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
                lambda name: os.path.join(
                    ".pi", "skills", f"system2-role-{name}", "SKILL.md"
                ),
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

    def test_repo_governor_uses_harness_neutral_security_policy(self):
        artifacts = {
            "codex": _read(
                self.projects["codex"],
                os.path.join(
                    "skills", "system2-role-repo-governor", "SKILL.md"
                ),
            ),
            "pi": _read(
                self.projects["pi"],
                os.path.join(
                    ".pi", "skills", "system2-role-repo-governor", "SKILL.md"
                ),
            ),
        }
        for target, artifact in artifacts.items():
            with self.subTest(target=target):
                self.assertEqual(
                    [], _missing(artifact, _REPO_GOVERNOR_NEUTRAL_SECURITY_POLICY)
                )
                for residue in _REPO_GOVERNOR_CLAUDE_PERMISSION_RESIDUE:
                    self.assertNotIn(residue, artifact)

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
            "pi": _read(
                self.projects["pi"], os.path.join(".pi", "prompts", "system2.md")
            ),
        }
        for target, artifact in artifacts.items():
            with self.subTest(target=target):
                self.assertEqual([], _missing(artifact, _ORCHESTRATOR_EXCERPTS))
                self.assertIn("Keep gate decisions and user communication in the parent session", artifact)
                self.assertIn("before dispatching the next dependent role", artifact)
                self.assertLess(artifact.index("Wait for user approval or override"),
                                artifact.index("Execute triggered agents sequentially"))
                for field in self.graph.maintenance_loop.regression_ledger_fields:
                    self.assertIn(field, artifact)
                # Codex qualifies plugin skills with its own namespace, not the
                # unqualified Claude subagent references in the source template.
                body = artifact.replace("$system2:system2", "$system2")
                for forbidden in _FORBIDDEN_NEUTRAL_MECHANICS:
                    self.assertNotIn(forbidden, body)
                self.assertEqual(1, artifact.count("# System2 orchestrator"))
        self.assertIn("## Runtime boundary", artifacts["codex"])
        self.assertIn("## Runtime boundary", artifacts["pi"])
        self.assertIn("System2 adds no custom enforcement or permission layer", artifacts["pi"])

    def test_gate_responsibilities_and_full_handoff_match_the_canonical_template(self):
        canonical = self.graph.base_template.text
        contract = canonical[canonical.index("## Delegation contract"):canonical.index("## Post-Execution Workflow")].strip()
        for target, prompt, prefix, role_ref in (
            ("codex", "skills/system2/SKILL.md", "skills", "$system2:system2-role-"),
            ("pi", ".pi/prompts/system2.md", ".pi/skills", "system2-role-"),
        ):
            with self.subTest(target=target):
                artifact = _read(self.projects[target], prompt)
                self.assertIn(contract, artifact)
                for gate, spec, role in (
                    (1, "context", "spec-coordinator"),
                    (2, "requirements", "requirements-engineer"),
                    (3, "design", "design-architect"),
                    (4, "tasks", "task-planner"),
                ):
                    path = f"spec/{spec}.md"
                    self.assertIn(
                        f"- Gate {gate} ({spec}): approve {path}\n"
                        f"  - Delegate drafting or revision of `{path}` to `{role_ref}{role}`; "
                        "the parent reviews it before requesting approval.", artifact,
                    )
                self.assertIn("`: final review", artifact[artifact.index("Available role skills"):])
                executor = _read(self.projects[target], f"{prefix}/system2-role-executor/SKILL.md")
                for field in ("status: success", "status: blockers", "status: failure",
                              "files_changed", "tests_added", "test_outcomes"):
                    self.assertIn(f"`{field}`", executor)
                scope = next(role.write_scope for role in self.graph.roles if role.name == "executor")
                self.assertTrue(scope)
                self.assertIn(f"```regex\n{scope}\n```", executor)
                for role in ("code-reviewer", "executor"):
                    skill = _read(self.projects[target], f"{prefix}/system2-role-{role}/SKILL.md")
                    self.assertIn("`.claude/slop-catalog.md`", skill)
                self.assertLess(artifact.index("Before dispatching an approved task"),
                                artifact.index("## Post-Execution Workflow"))
                self.assertIn("check the actual changes against the delegated scope", artifact)
                self.assertIn("do not revert caller changes", artifact)

    def test_doctors_check_approval_and_verification_evidence_without_mutation(self):
        for target, prompt in (
            ("codex", "skills/system2-doctor/SKILL.md"),
            ("pi", ".pi/prompts/system2-doctor.md"),
        ):
            with self.subTest(target=target):
                artifact = _read(self.projects[target], prompt)
                for excerpt in (
                    "without editing it or starting a specialist",
                    "approved in their current form",
                    "spec/post-execution-log.md",
                    "spec/regression-ledger.md",
                    "Do not infer missing approvals",
                ):
                    self.assertIn(excerpt, artifact)


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
