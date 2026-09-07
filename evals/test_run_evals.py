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
