"""Communication policy delivery, kept separate from engineering contracts.

These are prompt-assembly controls, not claims about live-model writing quality.
See evals/communication/ for the scenario-based human review suite.
"""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from evals import capture, matrix, oracle
from system2_compiler import cli, ir
from system2_compiler.backends.claude_code import ClaudeCodeBackend
from system2_compiler.backends.pi import PiBackend
from system2_compiler.ir.build import _load_base_template, _section_text


_HEADING = "Human communication policy"
_REPO = Path(oracle.PLUGIN_REPO_ROOT)


class HumanCommunicationPolicyTest(unittest.TestCase):
    def setUp(self):
        self.template = _load_base_template(oracle.PLUGIN_ROOT)
        self.policy = _section_text(self.template, _HEADING).strip()

    def assertPolicy(self, text):
        self.assertTrue(self.policy, "canonical template must define the shared policy")
        self.assertEqual(1, text.count(self.policy))

    def assertPiPolicy(self, project):
        extension = (Path(project) / ".pi/extensions/system2.ts").read_text()
        literal = extension.split("const HUMAN_COMMUNICATION_POLICY = ", 1)[1].split(";\n", 1)[0]
        self.assertPolicy(json.loads(literal))
        self.assertEqual(1, extension.count("## Human communication policy"))

    def test_template_and_repository_instructions_share_one_policy(self):
        self.assertPolicy(self.template)
        self.assertPolicy((_REPO / "CLAUDE.md").read_text())
        for principle in (
            "understanding, not minimum length",
            "private chain-of-thought",
            "machine-readable",
            "approval",
            "Do not narrate routine tool calls",
            "conceptual question",
        ):
            self.assertIn(principle, self.policy)

    def test_claude_overlay_and_profile_paths_retain_policy_and_gates(self):
        for cell_name in ("core+overlay", "core+overlay+profile", "core+anchorfile"):
            with self.subTest(cell=cell_name), tempfile.TemporaryDirectory() as project:
                cell = matrix.get_cell(cell_name)
                home = capture._materialize_profile_home(cell) if cell.profile else tempfile.mkdtemp()
                self.addCleanup(shutil.rmtree, home)
                args = [
                    sys.executable, "-m", "system2_compiler.cli", "compile", "--target", "claude-code",
                    "--base", oracle.PLUGIN_ROOT, "--project", project, "--format", "json",
                ]
                args += ["--profile", cell.profile] if cell.profile else ["--overlays", ",".join(cell.overlays)]
                run = subprocess.run(
                    args, cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "HOME": home}, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(0, run.returncode, run.stderr + run.stdout)
                text = (Path(project) / "CLAUDE.md").read_text()
                self.assertPolicy(text)
                self.assertIn("Pause for explicit user approval at each gate", text)
                self.assertIn("Do not proceed to merge or deploy without approval", text)
                self.assertIn("Completion summary requirements (files changed, commands run, decisions, risks)", text)

    def test_every_claude_specialist_uses_shared_policy_without_copying_it(self):
        for path in sorted((_REPO / "plugin/agents").glob("*.md")):
            with self.subTest(role=path.stem):
                text = path.read_text()
                self.assertIn("shared Human communication policy", text)
                self.assertNotIn("<thinking>", text)
                self.assertNotIn("## Thinking Protocol", text)
                self.assertIn("Completion", text)
                if self.policy:
                    self.assertNotIn(self.policy, text)

    def test_pi_parent_and_isolated_roles_receive_policy_once(self):
        with tempfile.TemporaryDirectory() as project:
            result = ir.compose(oracle.PLUGIN_ROOT, [matrix.TEST_OVERLAY], project)
            self.assertIsNotNone(result.graph, result.errors)
            PiBackend().emit(result.graph, project)
            root = Path(project)
            system = (root / ".pi/SYSTEM.md").read_text()
            parent = (root / ".pi/prompts/orchestrator.md").read_text()
            self.assertPiPolicy(project)
            # One shared runtime policy, not redundant copies in SYSTEM and templates.
            self.assertNotIn("## Human communication policy", system + parent)
            self.assertIn("shared Human communication policy", system)
            self.assertIn("shared Human communication policy", parent)
            for role in result.graph.roles:
                with self.subTest(role=role.name):
                    child = (root / f".pi/prompts/role-{role.name}.md").read_text()
                    self.assertNotIn("## Human communication policy", child)
                    self.assertIn("shared Human communication policy", child)
                    self.assertNotIn("<thinking>", child)
                    self.assertIn("system2_child_complete", child)
                    self.assertIn("System2 derives changed paths, artifact hashes, and verification observations", child)
            self.assertIn("## TDD Verification Loop", (root / ".pi/prompts/role-executor.md").read_text())
            self.assertIn("Use EARS-style statements", (root / ".pi/prompts/role-requirements-engineer.md").read_text())
            self.assertIn("spec/interfaces.json", (root / ".pi/prompts/role-design-architect.md").read_text())

    def test_lock_recomposition_and_overlay_removal_preserve_policy(self):
        removable = str(Path(__file__).parent / "fixtures/lifecycle-removable")
        for target, backend_cls, prompt_path in (
            ("claude-code", ClaudeCodeBackend, "CLAUDE.md"),
            ("pi", PiBackend, ".pi/prompts/orchestrator.md"),
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as project:
                overlays = [matrix.TEST_OVERLAY, removable]
                result = ir.compose(oracle.PLUGIN_ROOT, overlays, project)
                self.assertIsNotNone(result.graph, result.errors)
                backend = backend_cls(base_path=oracle.PLUGIN_ROOT, compose_fn=ir.compose)
                backend.emit(result.graph, project)
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    code = cli.main([
                        "compile", "--target", target, "--base", oracle.PLUGIN_ROOT,
                        "--project", project, "--from-lock", "--format", "json",
                    ])
                self.assertEqual(0, code)
                if target == "pi":
                    self.assertPiPolicy(project)
                else:
                    self.assertPolicy((Path(project) / prompt_path).read_text())
                removed = backend.uninstall(project, "lifecycle-removable")
                self.assertFalse(removed.errors, removed.errors)
                if target == "pi":
                    self.assertPiPolicy(project)
                else:
                    self.assertPolicy((Path(project) / prompt_path).read_text())

    def test_manual_review_suite_covers_intents_and_semantic_checks(self):
        suite = json.loads((_REPO / "evals/communication/cases.json").read_text())
        self.assertIn("not captured", suite["example_provenance"])
        self.assertEqual(
            {"clarity", "naturalness", "causal_coherence", "technical_fidelity",
             "contextual_completeness", "information_efficiency"},
            set(suite["dimensions"]),
        )
        cases = suite["cases"]
        self.assertEqual(8, len(cases))
        self.assertEqual(
            {"architecture", "implementation_failure", "completed_change", "unfamiliar_concept",
             "design_recommendation", "clarification", "approval", "incomplete_work"},
            {case["id"] for case in cases},
        )
        for case in cases:
            with self.subTest(case=case["id"]):
                for field in ("role", "audience", "user", "evidence", "review_checks",
                              "failure_conditions", "baseline_style", "improved_example"):
                    self.assertTrue(case[field])
                self.assertNotEqual(case["baseline_style"], case["improved_example"])

    def test_orchestrator_no_longer_requires_visible_process_templates(self):
        self.assertNotIn("articulate your reasoning", self.template)
        self.assertNotIn("Present a summary in this format", self.template)
        self.assertNotIn("Post-execution agents to run:", self.template)
        self.assertIn("spec/post-execution-log.md", self.template)
        self.assertIn("Wait for user approval or override", self.template)
        self.assertIn("Current lease patterns", self.template)


if __name__ == "__main__":
    unittest.main()
