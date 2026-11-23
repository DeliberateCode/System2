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


def test_maintained_documentation_balances_harnesses_and_current_contracts():
    repo = run_evals.REPO_ROOT
    readme = (repo / "README.md").read_text(encoding="utf-8")
    installation_index = (repo / "docs" / "installation.md").read_text(
        encoding="utf-8"
    )
    installation_guides = {
        harness: (repo / "docs" / "installation" / filename).read_text(
            encoding="utf-8"
        )
        for harness, filename in (
            ("Claude Code", "claude-code.md"),
            ("Codex", "codex.md"),
            ("Pi", "pi.md"),
        )
    }
    pi_install = installation_guides["Pi"]
    compiler_readme = (repo / "compiler" / "README.md").read_text(
        encoding="utf-8"
    )
    ci_workflow = (repo / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    overlay_guide = (
        repo / "docs" / "overlays" / "claude-code.md"
    ).read_text(encoding="utf-8")

    assert "all three support the same gates, roles, and project artifacts" in readme
    assert "equally viable" in installation_index
    for harness, guide in installation_guides.items():
        for heading in ("## Install", "## Run", "## Runtime boundary", "## Update", "## Remove"):
            assert heading in guide, f"{harness} guide is missing {heading}"
    combined_user_docs = "\n".join(
        [readme, installation_index, *installation_guides.values()]
    ).lower()
    for unequal_claim in (
        "reference harness",
        "reference implementation",
        "reference channel",
        "current reference",
        "primary harness",
    ):
        assert unequal_claim not in combined_user_docs
    assert "pi install git:github.com/DeliberateCode/System2" in pi_install
    assert "/system2 <your task>" in pi_install
    assert "does not install an extension, hooks, or a permission layer" in pi_install
    assert "0.85.1" not in pi_install
    assert "PI_CI_VERSION" not in ci_workflow
    assert "python -m unittest discover" in ci_workflow
    for target in ("claude-code", "codex", "pi"):
        assert f"`{target}`" in compiler_readme
    assert "All targets preserve the same workflow, gates, and role contracts" in compiler_readme
    assert "--base ../plugin" in compiler_readme
    assert "--overlays" in compiler_readme
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
