"""Regression tests for repository-level evaluations."""

import importlib.util
import os
import tempfile
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "root_run_evals", Path(__file__).with_name("run_evals.py")
)
assert _SPEC and _SPEC.loader
run_evals = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_evals)


def test_requirement_identifier_guard_rejects_a_production_script():
    fd, planted_path = tempfile.mkstemp(
        prefix=".identifier-guard-",
        suffix=".py",
        dir=run_evals.REPO_ROOT / "plugin" / "scripts",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# REQ" + "-999\n")
        with mock.patch.object(run_evals, "record") as record:
            run_evals.check_no_requirement_ids_in_implementation_files()
    finally:
        os.unlink(planted_path)

    record.assert_called_once()
    assert record.call_args.args[2] is False
    assert os.path.basename(planted_path) in record.call_args.args[3]


def test_maintained_documentation_matches_current_pi_and_overlay_contracts():
    repo = run_evals.REPO_ROOT
    pi_install = (repo / "docs" / "installation" / "pi.md").read_text(
        encoding="utf-8"
    )
    compiler_readme = (repo / "compiler" / "README.md").read_text(
        encoding="utf-8"
    )
    overlay_guide = (
        repo / "docs" / "overlays" / "claude-code.md"
    ).read_text(encoding="utf-8")

    assert "0.85.1" in pi_install
    assert "caller-owned `AGENTS.md`" in pi_install
    assert "adapted/partial" in pi_install
    assert "pi install npm:" not in pi_install
    for target in ("claude-code", "codex", "pi"):
        segments = compiler_readme.split(f"--target {target}")[1:]
        example = next(
            (segment[:240] for segment in segments if "--base ../plugin" in segment[:240]),
            "",
        )
        assert example, f"{target} example uses the wrong base"
        assert "--overlays" in example, f"{target} source example is not runnable"
    assert "optional `.system2/overlays.json` input list" in overlay_guide
    assert "spec/overlay-manifest.lock" in overlay_guide
    assert "--remove overlay-a" in overlay_guide
    assert "--remove OverlayA" not in overlay_guide


def test_claude_agents_preserve_requirements_to_verification_traceability():
    expected_excerpts = {
        "requirements-engineer.md": (
            "Each requirement gets an ID: REQ-001, REQ-002, ...",
            "Traceability Matrix (Requirement -> Design Section -> Task IDs)",
        ),
        "design-architect.md": (
            "Verification Strategy (mapping to requirements and test strategy)",
        ),
        "task-planner.md": (
            "Task ID: TASK-001, TASK-002, ...",
            "Traceability (REQ IDs -> TASK IDs)",
        ),
        "executor.md": (
            "the packet's requirement IDs serve as valid citation authority",
        ),
        "test-engineer.md": ("Add tests that map directly to REQ IDs",),
        "eval-engineer.md": ("Traceability (REQ IDs -> eval cases)",),
        "code-reviewer.md": ("Spec alignment: satisfaction of REQ IDs and gaps",),
    }

    agents_dir = run_evals.REPO_ROOT / "plugin" / "agents"
    for filename, excerpts in expected_excerpts.items():
        content = (agents_dir / filename).read_text(encoding="utf-8")
        for excerpt in excerpts:
            assert excerpt in content, f"{filename} is missing {excerpt!r}"
