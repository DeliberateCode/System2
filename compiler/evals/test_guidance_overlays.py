"""Overlay regressions must be visible in host instructions, not just locks."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from evals import matrix, oracle
from system2_compiler import cli, ir
from system2_compiler.backends.codex import CodexBackend
from system2_compiler.backends.pi import PiBackend


_TARGETS = (
    (CodexBackend, "skills/system2/SKILL.md", "skills"),
    (PiBackend, ".pi/prompts/system2.md", ".pi/skills"),
)
_APPLIED = {
    "orchestrator.principles": ["test-principle-1"],
    "orchestrator.gates.3.consultation": ["test-gate3-consultation"],
    "delegation.advisory_sources": ["test-advisory-1"],
    "agents.executor.prompt_sections.implementation_discipline": ["test-executor-discipline"],
    "spec.context.required_sections": ["test-spec-section"],
    "auxiliary_agents": ["test-scout"],
}


class GuidanceOverlayTest(unittest.TestCase):
    def run_cli(self, verb, target, project, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main([
                verb, "--target", target, "--base", oracle.PLUGIN_ROOT,
                "--project", str(project), "--format", "json", *args,
            ])
        self.assertEqual(0, code, stdout.getvalue() + stderr.getvalue())
        return json.loads(stdout.getvalue())["report"], stderr.getvalue()

    def test_all_six_supported_categories_change_emitted_guidance(self):
        for backend, prompt, skills in _TARGETS:
            with self.subTest(target=backend.name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "base"
                composed = root / "composed"
                source.mkdir()
                composed.mkdir()
                graph = ir.compose(oracle.PLUGIN_ROOT, [], str(source)).graph
                backend().emit(graph, str(source))
                report, warnings = self.run_cli(
                    "compile", backend.name, composed, "--overlays", matrix.TEST_OVERLAY,
                )
                self.assertEqual(_APPLIED, report["contributions_applied"])
                self.assertEqual({}, report["deferred"])
                self.assertEqual("", warnings)

                base_prompt, prompt_text = ((project / prompt).read_text() for project in (source, composed))
                for instruction in (
                    "Gate 3 consultation (pre-delegation)",
                    "Before approving the design, verify test coverage plan exists.",
                    "Test overlay principle: always validate inputs before processing.",
                    "Test Advisory Source",
                    "**Test Section**: A test required section for context",
                    "system2-role-test-scout",
                ):
                    self.assertNotIn(instruction, base_prompt)
                    self.assertIn(instruction, prompt_text)
                self.assertLess(prompt_text.index("Gate 3 (design)"), prompt_text.index("Gate 3 consultation"))
                self.assertLess(prompt_text.index("Gate 3 consultation"), prompt_text.index("Gate 4 (tasks)"))

                executor = Path(skills, "system2-role-executor", "SKILL.md")
                guidance = "Test executor discipline: enforce aggregate boundaries in all domain operations."
                self.assertNotIn(guidance, (source / executor).read_text())
                self.assertIn(guidance, (composed / executor).read_text())
                self.assertEqual(
                    (source / skills / "system2-role-test-engineer/SKILL.md").read_bytes(),
                    (composed / skills / "system2-role-test-engineer/SKILL.md").read_bytes(),
                )
                scout = Path(skills, "system2-role-test-scout", "SKILL.md")
                self.assertFalse((source / scout).exists())
                self.assertIn("You are a test scout agent.", (composed / scout).read_text())
                self.assertNotIn("\ntools:", (composed / scout).read_text())

    def test_host_configuration_is_deferred_in_compile_and_from_lock(self):
        for backend, prompt, _skills in _TARGETS:
            with self.subTest(target=backend.name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                overlay = root / "test-overlay"
                shutil.copytree(matrix.TEST_OVERLAY, overlay)
                manifest_path = overlay / "system2.overlay.json"
                manifest = json.loads(manifest_path.read_text())
                deferred_text = "Unapplied fixture permission"
                manifest["contributions"]["permissions"] = [
                    {"tool": "Read", "reason": deferred_text, "required_by": []},
                ]
                manifest_path.write_text(json.dumps(manifest))
                project = root / "project"
                project.mkdir()
                for verb, args in (
                    ("compile", ["--overlays", str(overlay)]), ("from-lock", []),
                ):
                    report, warnings = self.run_cli(verb, backend.name, project, *args)
                    self.assertEqual(_APPLIED, report["contributions_applied"])
                    self.assertEqual({"permissions": 1}, report["deferred"])
                    self.assertIn("does not apply these overlay contributions: permissions", warnings)
                    self.assertNotIn(deferred_text, (project / prompt).read_text())

    def test_overlay_removal_removes_auxiliary_skill_and_reports_surviving_guidance(self):
        removable = Path(__file__).parent / "fixtures/lifecycle-removable"
        for backend, prompt, skills in _TARGETS:
            with self.subTest(target=backend.name), tempfile.TemporaryDirectory() as project:
                args = ["--overlays", f"{matrix.TEST_OVERLAY},{removable}"]
                self.run_cli("compile", backend.name, project, *args)
                scout = Path(project, skills, "system2-role-test-scout/SKILL.md")
                self.assertTrue(scout.is_file())
                report, _warnings = self.run_cli(
                    "uninstall", backend.name, project, "--name", "lifecycle-removable", "--dry-run",
                )
                self.assertEqual(_APPLIED, report["contributions_applied"])
                self.run_cli("uninstall", backend.name, project, "--name", "test-overlay")
                self.assertFalse(scout.exists())
                self.assertNotIn("Gate 3 consultation", Path(project, prompt).read_text())
