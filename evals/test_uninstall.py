"""Tests for overlay uninstall feature.

Covers: _read_base_template, _compute_stale_artifacts, _uninstall_last_overlay,
_uninstall (argument validation, multi-overlay, last-overlay, output format).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

# Add scripts directory to path.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_DIR = os.path.join(_REPO_ROOT, "plugin", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import composer  # noqa: E402

_FIXTURE_DIR = os.path.join(_REPO_ROOT, "evals", "fixtures", "test-overlay")
_BASE_PATH = os.path.join(_REPO_ROOT, "plugin")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compose_and_write(project_dir, overlay_paths, base_path=_BASE_PATH):
    """Compose overlays and write outputs to *project_dir*.

    Returns the compose result dict.
    """
    result = composer.compose(base_path, overlay_paths, project_dir)
    assert result["errors"] == [], f"compose() returned errors: {result['errors']}"
    composer._write_outputs(
        project_dir,
        result["claude_md"],
        result["lock"],
        result["auxiliary_agents"],
        pending_content_copies=result.get("pending_content_copies", []),
        overlay_info_for_lock=result.get("overlay_info_for_lock", []),
        valid_anchors_by_agent=result.get("valid_anchors_by_agent"),
    )
    return result


def _create_minimal_overlay(parent_dir, name, version="1.0.0"):
    """Create a minimal valid overlay in *parent_dir*/<name>.

    Returns the overlay directory path.
    """
    overlay_dir = os.path.join(parent_dir, name)
    os.makedirs(overlay_dir, exist_ok=True)

    # Create a content file.
    contributions_dir = os.path.join(overlay_dir, "contributions", "orchestrator")
    os.makedirs(contributions_dir, exist_ok=True)
    with open(os.path.join(contributions_dir, "principles.md"), "w") as fh:
        fh.write(f"- {name} principle: always test thoroughly.\n")

    manifest = {
        "name": name,
        "version": version,
        "description": f"Minimal test overlay {name}",
        "schema_version": "1.0.0",
        "tags": ["test"],
        "compatibility": {
            "known_conflicts": [],
            "tested_with": [],
            "review_when_combined_with_tags": [],
        },
        "contributions": {
            "orchestrator": {
                "principles": [
                    {
                        "id": f"{name}-principle-1",
                        "content_file": "contributions/orchestrator/principles.md",
                        "after": None,
                    }
                ]
            }
        },
    }
    with open(os.path.join(overlay_dir, "system2.overlay.json"), "w") as fh:
        json.dump(manifest, fh)

    return overlay_dir


def _write_lock(project_dir, lock_data):
    """Write a lock file to project_dir/spec/overlay-manifest.lock."""
    spec_dir = os.path.join(project_dir, "spec")
    os.makedirs(spec_dir, exist_ok=True)
    lock_path = os.path.join(spec_dir, "overlay-manifest.lock")
    with open(lock_path, "w") as fh:
        json.dump(lock_data, fh)


def _snapshot_project(project_dir):
    """Return a dict of path -> content for all files under project_dir."""
    snapshot = {}
    for root, dirs, files in os.walk(project_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, project_dir)
            with open(fpath, "rb") as fh:
                snapshot[rel] = fh.read()
    return snapshot


# ---------------------------------------------------------------------------
# TestReadBaseTemplate
# ---------------------------------------------------------------------------

class TestReadBaseTemplate(unittest.TestCase):
    """Unit tests for _read_base_template()."""

    def test_reads_from_init_skill_template(self):
        """REQ-014: Reads the template block from the init skill SKILL.md."""
        init_skill_path = os.path.join(_BASE_PATH, "skills", "init", "SKILL.md")
        fallback_path = os.path.join(os.path.dirname(_BASE_PATH), "CLAUDE.md")
        result = composer._read_base_template(init_skill_path, fallback_path)
        self.assertTrue(len(result) > 100, "Template should be non-trivial")
        self.assertIn("## Operating principles", result)
        self.assertTrue(result.endswith("\n"), "Should end with exactly one newline")

    def test_falls_back_to_repo_claude_md(self):
        """When init skill path is invalid, falls back to the fallback file."""
        fallback_path = os.path.join(os.path.dirname(_BASE_PATH), "CLAUDE.md")
        result = composer._read_base_template(
            "/nonexistent/SKILL.md", fallback_path
        )
        self.assertTrue(len(result) > 100, "Fallback should produce content")
        self.assertIn("## Operating principles", result)

    def test_returns_empty_on_both_missing(self):
        """When both paths are invalid, returns empty string."""
        result = composer._read_base_template(
            "/nonexistent/SKILL.md", "/also/nonexistent/CLAUDE.md"
        )
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# TestComputeStaleArtifacts
# ---------------------------------------------------------------------------

class TestComputeStaleArtifacts(unittest.TestCase):
    """Unit tests for _compute_stale_artifacts()."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_stale_")

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_returns_overlay_dir_when_exists(self):
        """Returns the overlay's cached directory if it exists on disk."""
        overlay_dir = os.path.join(
            self.project_dir, ".system2", "overlays", "my-overlay"
        )
        os.makedirs(overlay_dir, exist_ok=True)
        lock_data = {
            "overlays": [],
            "contributions_applied": {},
        }
        result = composer._compute_stale_artifacts(
            self.project_dir, "my-overlay", lock_data
        )
        self.assertIn(overlay_dir, result)

    def test_returns_empty_when_nothing_exists(self):
        """Returns empty list when no artifacts exist on disk."""
        lock_data = {
            "overlays": [],
            "contributions_applied": {},
        }
        result = composer._compute_stale_artifacts(
            "/tmp/nonexistent-project-path-" + str(os.getpid()),
            "my-overlay",
            lock_data,
        )
        self.assertEqual(result, [])

    def test_validates_overlay_name(self):
        """Path traversal overlay names are handled safely (return empty)."""
        lock_data = {
            "overlays": [],
            "contributions_applied": {},
        }
        result = composer._compute_stale_artifacts(
            self.project_dir, "../etc", lock_data
        )
        self.assertEqual(result, [])

        result2 = composer._compute_stale_artifacts(
            self.project_dir, "../../passwd", lock_data
        )
        self.assertEqual(result2, [])


# ---------------------------------------------------------------------------
# TestUninstallArgValidation
# ---------------------------------------------------------------------------

class TestUninstallArgValidation(unittest.TestCase):
    """Unit tests for _uninstall() input validation."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_uninst_")

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_invalid_overlay_name_rejected(self):
        """REQ-029: Path traversal names are rejected."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "../etc"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("Invalid overlay name", result["errors"][0])

    def test_no_lock_file_returns_error(self):
        """REQ-004: Missing lock file returns clear error."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "some-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("No lock file", result["errors"][0])

    def test_malformed_lock_file_returns_error(self):
        """REQ-005: Malformed JSON lock file returns clear error."""
        spec_dir = os.path.join(self.project_dir, "spec")
        os.makedirs(spec_dir, exist_ok=True)
        lock_path = os.path.join(spec_dir, "overlay-manifest.lock")
        with open(lock_path, "w") as fh:
            fh.write("this is not json{{{")
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "some-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("malformed", result["errors"][0].lower())

    def test_malformed_overlay_entry_returns_error(self):
        """REQ-026: Overlay entry missing 'name' field returns error."""
        lock_data = {
            "overlays": [{"version": "1.0.0"}],
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "some-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("name", result["errors"][0].lower())

    def test_overlay_not_in_lock_returns_error_with_installed_list(self):
        """REQ-006, REQ-019: Error lists installed overlay names."""
        lock_data = {
            "overlays": [
                {"name": "overlay-a", "version": "1.0.0", "source_path": "/tmp/a"},
            ],
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("not installed", result["errors"][0].lower())
        self.assertIn("overlay-a", result["errors"][0])

    def test_remaining_overlay_invalid_name_rejected(self):
        """REQ-029: Remaining overlay with invalid name is rejected."""
        lock_data = {
            "overlays": [
                {"name": "good-overlay", "version": "1.0.0", "source_path": "/tmp/a"},
                {"name": "BAD_NAME!", "version": "1.0.0", "source_path": "/tmp/b"},
            ],
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "good-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("invalid overlay name", result["errors"][0].lower())


# ---------------------------------------------------------------------------
# TestUninstallMultiOverlay
# ---------------------------------------------------------------------------

class TestUninstallMultiOverlay(unittest.TestCase):
    """Integration tests for multi-overlay uninstall."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_multi_")
        self.overlay_staging = tempfile.mkdtemp(prefix="s2test_ovs_")
        # Create second overlay programmatically.
        self.overlay_b_dir = _create_minimal_overlay(
            self.overlay_staging, "overlay-b", "2.0.0"
        )
        # Compose both overlays.
        _compose_and_write(
            self.project_dir,
            [_FIXTURE_DIR, self.overlay_b_dir],
        )

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.overlay_staging, ignore_errors=True)

    def test_multi_overlay_uninstall_dry_run_no_file_changes(self):
        """REQ-007: Dry-run mode does not modify any files."""
        before = _snapshot_project(self.project_dir)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b", dry_run=True
        )
        after = _snapshot_project(self.project_dir)
        self.assertEqual(result["errors"], [])
        self.assertEqual(before, after)

    def test_multi_overlay_uninstall_produces_correct_report(self):
        """REQ-008: Dry-run report has uninstall metadata."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b", dry_run=True
        )
        self.assertEqual(result["errors"], [])
        report = result["report"]
        self.assertIn("uninstall", report)
        uninstall_meta = report["uninstall"]
        self.assertEqual(uninstall_meta["removed"]["name"], "overlay-b")
        self.assertEqual(len(uninstall_meta["remaining"]), 1)
        self.assertEqual(
            uninstall_meta["remaining"][0]["name"], "test-overlay"
        )

    def test_multi_overlay_uninstall_write_mode(self):
        """REQ-011: Write mode rewrites CLAUDE.md, updates lock, removes stale."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b", dry_run=False
        )
        self.assertEqual(result["errors"], [])

        # Call _write_outputs for the multi-overlay result.
        composer._write_outputs(
            self.project_dir,
            result["claude_md"],
            result["lock"],
            result["auxiliary_agents"],
            pending_content_copies=result.get("pending_content_copies", []),
            overlay_info_for_lock=result.get("overlay_info_for_lock", []),
            valid_anchors_by_agent=result.get("valid_anchors_by_agent"),
        )

        # CLAUDE.md should reference test-overlay but not overlay-b.
        claude_path = os.path.join(self.project_dir, "CLAUDE.md")
        with open(claude_path) as fh:
            claude_content = fh.read()
        self.assertIn("test-overlay", claude_content)
        self.assertNotIn("overlay-b", claude_content)

        # Lock should have only one overlay.
        lock_path = os.path.join(
            self.project_dir, "spec", "overlay-manifest.lock"
        )
        self.assertTrue(os.path.isfile(lock_path))
        with open(lock_path) as fh:
            lock = json.load(fh)
        self.assertEqual(len(lock["overlays"]), 1)
        self.assertEqual(lock["overlays"][0]["name"], "test-overlay")

        # Overlay-b's cached dir should be removed by _write_outputs stale cleanup.
        overlay_b_cache = os.path.join(
            self.project_dir, ".system2", "overlays", "overlay-b"
        )
        self.assertFalse(os.path.isdir(overlay_b_cache))

        # test-overlay's cached dir should still exist.
        test_overlay_cache = os.path.join(
            self.project_dir, ".system2", "overlays", "test-overlay"
        )
        self.assertTrue(os.path.isdir(test_overlay_cache))

    def test_multi_overlay_uninstall_byte_identity(self):
        """REQ-012: Uninstalling A from A+B matches fresh compose of B only.

        Compares CLAUDE.md text content; lock metadata timestamps may differ.
        """
        # Uninstall overlay-b to leave test-overlay.
        result_uninstall = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b", dry_run=False
        )
        self.assertEqual(result_uninstall["errors"], [])
        composer._write_outputs(
            self.project_dir,
            result_uninstall["claude_md"],
            result_uninstall["lock"],
            result_uninstall["auxiliary_agents"],
            pending_content_copies=result_uninstall.get("pending_content_copies", []),
            overlay_info_for_lock=result_uninstall.get("overlay_info_for_lock", []),
            valid_anchors_by_agent=result_uninstall.get("valid_anchors_by_agent"),
        )

        with open(os.path.join(self.project_dir, "CLAUDE.md")) as fh:
            uninstall_claude = fh.read()

        # Fresh compose with test-overlay only in a separate project dir.
        fresh_dir = tempfile.mkdtemp(prefix="s2test_fresh_")
        try:
            result_fresh = composer.compose(
                _BASE_PATH, [_FIXTURE_DIR], fresh_dir
            )
            self.assertEqual(result_fresh["errors"], [])
            # Compare the composed text (excluding the timestamp line).
            uninstall_lines = uninstall_claude.split("\n")
            fresh_lines = result_fresh["claude_md"].split("\n")
            # Remove the timestamp line (line 2: <!-- Composed at: ... -->).
            uninstall_body = [
                l for l in uninstall_lines
                if not l.startswith("<!-- Composed at:")
            ]
            fresh_body = [
                l for l in fresh_lines
                if not l.startswith("<!-- Composed at:")
            ]
            self.assertEqual(uninstall_body, fresh_body)
        finally:
            shutil.rmtree(fresh_dir, ignore_errors=True)

    def test_multi_overlay_uninstall_missing_source_path_rollback(self):
        """REQ-016, REQ-017: Missing source_path causes error, files unchanged."""
        before = _snapshot_project(self.project_dir)

        # Manually corrupt the lock file to point overlay-b's remaining peer
        # (test-overlay) to a nonexistent source path. We uninstall overlay-b
        # so test-overlay is the "remaining" overlay whose source_path is needed.
        lock_path = os.path.join(
            self.project_dir, "spec", "overlay-manifest.lock"
        )
        with open(lock_path) as fh:
            lock_data = json.load(fh)
        for ov in lock_data["overlays"]:
            if ov["name"] == "test-overlay":
                ov["source_path"] = "/nonexistent/path/test-overlay"
        with open(lock_path, "w") as fh:
            json.dump(lock_data, fh)

        # Update snapshot to include the modified lock.
        before = _snapshot_project(self.project_dir)

        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b", dry_run=False
        )
        self.assertTrue(len(result["errors"]) > 0)

        # Files should be unchanged (compose() doesn't call _write_outputs on error).
        after = _snapshot_project(self.project_dir)
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# TestUninstallLastOverlay
# ---------------------------------------------------------------------------

class TestUninstallLastOverlay(unittest.TestCase):
    """Integration tests for last-overlay uninstall."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_last_")
        # Compose single overlay.
        _compose_and_write(self.project_dir, [_FIXTURE_DIR])

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_last_overlay_uninstall_dry_run(self):
        """REQ-007, REQ-008: Dry-run returns preview without file changes."""
        before = _snapshot_project(self.project_dir)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "test-overlay", dry_run=True
        )
        after = _snapshot_project(self.project_dir)
        self.assertEqual(result["errors"], [])
        self.assertEqual(before, after)
        # Result should contain the base template text.
        self.assertNotIn("<!-- COMPOSED:", result["claude_md"])
        self.assertIn("## Operating principles", result["claude_md"])

    def test_last_overlay_uninstall_write_mode(self):
        """REQ-013: Write mode restores base template, removes lock and cache."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "test-overlay", dry_run=False
        )
        self.assertEqual(result["errors"], [])

        # CLAUDE.md should be the base template (no COMPOSED header).
        claude_path = os.path.join(self.project_dir, "CLAUDE.md")
        self.assertTrue(os.path.isfile(claude_path))
        with open(claude_path) as fh:
            claude_content = fh.read()
        self.assertNotIn("<!-- COMPOSED:", claude_content)
        self.assertIn("## Operating principles", claude_content)

        # Lock file should be removed.
        lock_path = os.path.join(
            self.project_dir, "spec", "overlay-manifest.lock"
        )
        self.assertFalse(os.path.isfile(lock_path))

        # Overlay cache dir should be removed.
        overlay_cache = os.path.join(
            self.project_dir, ".system2", "overlays", "test-overlay"
        )
        self.assertFalse(os.path.isdir(overlay_cache))

        # Agent file should be removed.
        agent_path = os.path.join(
            self.project_dir, ".claude", "agents", "test-scout.md"
        )
        self.assertFalse(os.path.isfile(agent_path))

    def test_last_overlay_uninstall_removes_empty_parent_dir(self):
        """DQ-1: Empty .system2/overlays/ parent is removed after last uninstall."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "test-overlay", dry_run=False
        )
        self.assertEqual(result["errors"], [])
        overlays_parent = os.path.join(
            self.project_dir, ".system2", "overlays"
        )
        self.assertFalse(os.path.isdir(overlays_parent))

    def test_last_overlay_uninstall_missing_base_template_aborts(self):
        """REQ-015: Missing base template returns error, no files changed."""
        before = _snapshot_project(self.project_dir)

        # Call _uninstall_last_overlay directly with a bogus base_path so
        # _read_base_template cannot find any template file.
        lock_path = os.path.join(
            self.project_dir, "spec", "overlay-manifest.lock"
        )
        with open(lock_path) as fh:
            lock_data = json.load(fh)
        overlay_entry = lock_data["overlays"][0]

        result = composer._uninstall_last_overlay(
            "/nonexistent/base",
            self.project_dir,
            overlay_entry,
            lock_data,
            dry_run=False,
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("Cannot read base CLAUDE.md", result["errors"][0])

        # Files should be unchanged.
        after = _snapshot_project(self.project_dir)
        self.assertEqual(before, after)

    def test_last_overlay_base_template_matches_compose_source(self):
        """REQ-014: Template from _read_base_template matches what compose uses."""
        init_skill_path = os.path.join(
            _BASE_PATH, "skills", "init", "SKILL.md"
        )
        repo_claude_path = os.path.join(
            os.path.dirname(_BASE_PATH), "CLAUDE.md"
        )
        template = composer._read_base_template(init_skill_path, repo_claude_path)

        # Read the same init skill file and extract the template block
        # using the same logic as compose() (lines 3239-3248).
        with open(init_skill_path, "r", encoding="utf-8") as fh:
            skill_content = fh.read()
        begin = skill_content.find("---BEGIN TEMPLATE---")
        end = skill_content.find("---END TEMPLATE---")
        self.assertNotEqual(begin, -1)
        self.assertNotEqual(end, -1)
        begin += len("---BEGIN TEMPLATE---\n")
        compose_template = skill_content[begin:end].rstrip("\n") + "\n"

        self.assertEqual(template, compose_template)


# ---------------------------------------------------------------------------
# TestUninstallOutputFormat
# ---------------------------------------------------------------------------

class TestUninstallOutputFormat(unittest.TestCase):
    """Tests for uninstall result format compliance."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_fmt_")
        _compose_and_write(self.project_dir, [_FIXTURE_DIR])

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_success_report_contains_required_elements(self):
        """REQ-032: Success result has all four required report elements."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "test-overlay", dry_run=False
        )
        self.assertEqual(result["errors"], [])
        report = result["report"]
        self.assertIn("uninstall", report)
        uninstall_meta = report["uninstall"]
        # Element 1: removed overlay.
        self.assertIn("removed", uninstall_meta)
        self.assertEqual(uninstall_meta["removed"]["name"], "test-overlay")
        # Element 2: remaining overlays.
        self.assertIn("remaining", uninstall_meta)
        self.assertIsInstance(uninstall_meta["remaining"], list)
        # Element 3: files to write (via files_to_write in report or result).
        self.assertIn("files_to_write", result)
        # Element 4: artifacts removed.
        self.assertIn("artifacts_removed", uninstall_meta)

    def test_error_report_contains_required_elements(self):
        """REQ-033: Error result has error message and empty state fields."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "../bad-name"
        )
        self.assertTrue(len(result["errors"]) > 0)
        # Error shape consistency.
        self.assertEqual(result["claude_md"], "")
        self.assertEqual(result["lock"], {})
        self.assertEqual(result["auxiliary_agents"], [])
        self.assertEqual(result["files_to_write"], [])


# ---------------------------------------------------------------------------
# TestEndToEndUninstallWorkflow
# ---------------------------------------------------------------------------

class TestEndToEndUninstallWorkflow(unittest.TestCase):
    """Full compose-two, uninstall-one, uninstall-last workflow.

    Category: missing coverage.
    This test exercises the complete lifecycle:
    compose two overlays, dry-run uninstall, write uninstall first,
    verify intermediate state, uninstall last, verify base state.
    """

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_e2e_")
        self.overlay_staging = tempfile.mkdtemp(prefix="s2test_e2e_ovs_")
        self.alpha_dir = _create_minimal_overlay(
            self.overlay_staging, "overlay-alpha", "1.0.0"
        )
        self.beta_dir = _create_minimal_overlay(
            self.overlay_staging, "overlay-beta", "2.0.0"
        )

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.overlay_staging, ignore_errors=True)

    def test_full_uninstall_lifecycle(self):
        """Compose two overlays, uninstall one, uninstall last, verify all states."""
        project = self.project_dir
        claude_path = os.path.join(project, "CLAUDE.md")
        lock_path = os.path.join(project, "spec", "overlay-manifest.lock")

        # -- Phase 1: Compose both overlays --
        _compose_and_write(project, [self.alpha_dir, self.beta_dir])

        with open(claude_path) as fh:
            composed = fh.read()
        self.assertIn("<!-- COMPOSED:", composed)
        self.assertIn("overlay-alpha", composed)
        self.assertIn("overlay-beta", composed)

        with open(lock_path) as fh:
            lock = json.load(fh)
        self.assertEqual(len(lock["overlays"]), 2)

        alpha_cache = os.path.join(
            project, ".system2", "overlays", "overlay-alpha"
        )
        beta_cache = os.path.join(
            project, ".system2", "overlays", "overlay-beta"
        )
        self.assertTrue(os.path.isdir(alpha_cache))
        self.assertTrue(os.path.isdir(beta_cache))

        # -- Phase 2: Dry-run uninstall overlay-alpha --
        before = _snapshot_project(project)
        dryrun = composer._uninstall(
            _BASE_PATH, project, "overlay-alpha", dry_run=True
        )
        self.assertEqual(dryrun["errors"], [])
        after = _snapshot_project(project)
        self.assertEqual(before, after, "Dry-run must not change files")

        # Verify dry-run result shape.
        self.assertIn("uninstall", dryrun["report"])
        self.assertEqual(
            dryrun["report"]["uninstall"]["removed"]["name"], "overlay-alpha"
        )
        self.assertEqual(len(dryrun["report"]["uninstall"]["remaining"]), 1)
        self.assertEqual(
            dryrun["report"]["uninstall"]["remaining"][0]["name"],
            "overlay-beta",
        )

        # -- Phase 3: Write-mode uninstall overlay-alpha --
        result_a = composer._uninstall(
            _BASE_PATH, project, "overlay-alpha", dry_run=False
        )
        self.assertEqual(result_a["errors"], [])
        composer._write_outputs(
            project,
            result_a["claude_md"],
            result_a["lock"],
            result_a["auxiliary_agents"],
            pending_content_copies=result_a.get("pending_content_copies", []),
            overlay_info_for_lock=result_a.get("overlay_info_for_lock", []),
            valid_anchors_by_agent=result_a.get("valid_anchors_by_agent"),
        )

        with open(claude_path) as fh:
            after_alpha = fh.read()
        self.assertIn("overlay-beta", after_alpha)
        self.assertNotIn("overlay-alpha", after_alpha)

        with open(lock_path) as fh:
            lock2 = json.load(fh)
        self.assertEqual(len(lock2["overlays"]), 1)
        self.assertEqual(lock2["overlays"][0]["name"], "overlay-beta")

        self.assertFalse(os.path.isdir(alpha_cache))
        self.assertTrue(os.path.isdir(beta_cache))

        # -- Phase 4: Uninstall overlay-beta (last overlay) --
        result_b = composer._uninstall(
            _BASE_PATH, project, "overlay-beta", dry_run=False
        )
        self.assertEqual(result_b["errors"], [])

        with open(claude_path) as fh:
            base_state = fh.read()
        self.assertNotIn("<!-- COMPOSED:", base_state)
        self.assertIn("## Operating principles", base_state)

        self.assertFalse(os.path.isfile(lock_path))
        self.assertFalse(os.path.isdir(
            os.path.join(project, ".system2", "overlays")
        ))


# ---------------------------------------------------------------------------
# TestUninstallCoverageGaps
# ---------------------------------------------------------------------------

class TestUninstallCoverageGaps(unittest.TestCase):
    """Tests covering requirements gaps identified during verification.

    Category: missing coverage.
    """

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_gaps_")
        self.overlay_staging = tempfile.mkdtemp(prefix="s2test_gaps_ovs_")

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)
        shutil.rmtree(self.overlay_staging, ignore_errors=True)

    # -- REQ-018, REQ-027: Last-overlay rollback on I/O failure --

    def test_last_overlay_rollback_on_write_failure(self):
        """REQ-018, REQ-027: I/O error during last-overlay write triggers rollback.

        Simulates a write failure by making CLAUDE.md's parent directory
        read-only after backup, then verifies all files are restored.
        """
        _compose_and_write(self.project_dir, [_FIXTURE_DIR])
        before = _snapshot_project(self.project_dir)

        lock_path = os.path.join(
            self.project_dir, "spec", "overlay-manifest.lock"
        )
        with open(lock_path) as fh:
            lock_data = json.load(fh)
        overlay_entry = lock_data["overlays"][0]

        # Patch os.replace to fail during the atomic write step.
        call_count = [0]
        original_replace = os.replace

        def failing_replace(src, dst):
            call_count[0] += 1
            # Let the CLAUDE.md write fail (first os.replace call).
            if call_count[0] == 1:
                # Remove the tmp file so it doesn't leak.
                if os.path.exists(src):
                    os.unlink(src)
                raise OSError("Simulated I/O failure on CLAUDE.md write")
            return original_replace(src, dst)

        with unittest.mock.patch('os.replace', side_effect=failing_replace):
            with self.assertRaises(OSError) as ctx:
                composer._uninstall_last_overlay(
                    _BASE_PATH,
                    self.project_dir,
                    overlay_entry,
                    lock_data,
                    dry_run=False,
                )
            self.assertIn("Simulated", str(ctx.exception))

        # All files should be restored to pre-uninstall state.
        after = _snapshot_project(self.project_dir)
        self.assertEqual(before, after, "Rollback must restore all files")

    # -- REQ-017: Remediation message in error output --

    def test_multi_overlay_missing_source_path_error_includes_remediation(self):
        """REQ-017: Error message includes remediation suggestion."""
        overlay_b = _create_minimal_overlay(
            self.overlay_staging, "overlay-b", "2.0.0"
        )
        _compose_and_write(
            self.project_dir, [_FIXTURE_DIR, overlay_b]
        )

        # Corrupt the remaining overlay's source_path.
        lock_path = os.path.join(
            self.project_dir, "spec", "overlay-manifest.lock"
        )
        with open(lock_path) as fh:
            lock_data = json.load(fh)
        for ov in lock_data["overlays"]:
            if ov["name"] == "test-overlay":
                ov["source_path"] = "/nonexistent/path/test-overlay"
        with open(lock_path, "w") as fh:
            json.dump(lock_data, fh)

        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "overlay-b", dry_run=False
        )
        self.assertTrue(len(result["errors"]) > 0)
        # REQ-017: Error must include remediation advice.
        errors_text = " ".join(result["errors"])
        self.assertTrue(
            "remediation" in errors_text.lower()
            or "restore" in errors_text.lower()
            or "re-compose" in errors_text.lower()
            or "--from-lock" in errors_text,
            f"Error must include remediation advice: {result['errors']}",
        )

    # -- REQ-020: File path names rejected --

    def test_file_path_name_rejected(self):
        """REQ-020: Overlay name that looks like a file path is rejected."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "/path/to/overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("Invalid overlay name", result["errors"][0])

    def test_absolute_path_name_rejected(self):
        """REQ-020: Absolute path overlay name is rejected."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "/etc/passwd"
        )
        self.assertTrue(len(result["errors"]) > 0)

    def test_uppercase_name_rejected(self):
        """REQ-020: Non-kebab-case name with uppercase is rejected."""
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "MyOverlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("Invalid overlay name", result["errors"][0])

    # -- REQ-025: Remaining overlay with empty source_path --

    def test_remaining_overlay_empty_source_path_returns_error(self):
        """REQ-025: Remaining overlay with empty source_path returns error."""
        lock_data = {
            "overlays": [
                {"name": "good-overlay", "version": "1.0.0", "source_path": "/tmp/a"},
                {"name": "no-source", "version": "1.0.0", "source_path": ""},
            ],
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "good-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("no source_path", result["errors"][0].lower())

    def test_remaining_overlay_missing_source_path_key_returns_error(self):
        """REQ-025: Remaining overlay missing source_path key returns error."""
        lock_data = {
            "overlays": [
                {"name": "good-overlay", "version": "1.0.0", "source_path": "/tmp/a"},
                {"name": "no-source", "version": "1.0.0"},
            ],
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "good-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("no source_path", result["errors"][0].lower())

    # -- REQ-002: Mutual exclusion (CLI-level) --

    def test_mutual_exclusion_uninstall_with_overlays(self):
        """REQ-002: --uninstall with --overlays exits with error."""
        import subprocess
        script = os.path.join(_SCRIPT_DIR, "composer.py")
        proc = subprocess.run(
            [
                sys.executable, script,
                "--base", _BASE_PATH,
                "--project", self.project_dir,
                "--uninstall", "some-overlay",
                "--overlays", "/tmp/fake-overlay",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("mutually exclusive", combined.lower())

    def test_mutual_exclusion_uninstall_with_from_lock(self):
        """REQ-002: --uninstall with --from-lock exits with error."""
        import subprocess
        script = os.path.join(_SCRIPT_DIR, "composer.py")
        proc = subprocess.run(
            [
                sys.executable, script,
                "--base", _BASE_PATH,
                "--project", self.project_dir,
                "--uninstall", "some-overlay",
                "--from-lock",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn("mutually exclusive", combined.lower())

    # -- Edge case: empty overlays array in lock file --

    def test_empty_overlays_array_in_lock(self):
        """Edge case: lock file exists but overlays array is empty."""
        lock_data = {
            "overlays": [],
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "some-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("not installed", result["errors"][0].lower())

    # -- Edge case: lock file overlays is not a list --

    def test_overlays_not_a_list_in_lock(self):
        """Edge case: lock file overlays field is a string, not a list."""
        lock_data = {
            "overlays": "not-a-list",
            "contributions_applied": {},
        }
        _write_lock(self.project_dir, lock_data)
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "some-overlay"
        )
        self.assertTrue(len(result["errors"]) > 0)
        self.assertIn("not a list", result["errors"][0].lower())

    # -- REQ-032: Last-overlay success report has all elements --

    def test_last_overlay_success_report_all_elements(self):
        """REQ-032: Last-overlay success report has removed, remaining, artifacts."""
        _compose_and_write(self.project_dir, [_FIXTURE_DIR])
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "test-overlay", dry_run=True
        )
        self.assertEqual(result["errors"], [])
        report = result["report"]
        self.assertIn("uninstall", report)
        meta = report["uninstall"]
        # Element 1: removed overlay.
        self.assertEqual(meta["removed"]["name"], "test-overlay")
        self.assertIn("version", meta["removed"])
        # Element 2: remaining overlays.
        self.assertEqual(meta["remaining"], [])
        # Element 3: files_to_write exists in result.
        self.assertIn("files_to_write", result)
        self.assertTrue(len(result["files_to_write"]) > 0)
        # Element 4: artifacts_removed.
        self.assertIn("artifacts_removed", meta)
        self.assertIsInstance(meta["artifacts_removed"], list)

    # -- REQ-033: Error report shape on rollback-relevant errors --

    def test_error_report_shape_on_lock_parse_failure(self):
        """REQ-033: Error result on lock parse failure has correct shape."""
        spec_dir = os.path.join(self.project_dir, "spec")
        os.makedirs(spec_dir, exist_ok=True)
        lock_path = os.path.join(spec_dir, "overlay-manifest.lock")
        with open(lock_path, "w") as fh:
            fh.write("{invalid json")
        result = composer._uninstall(
            _BASE_PATH, self.project_dir, "some-overlay"
        )
        # Verify error shape: errors populated, empty outputs.
        self.assertTrue(len(result["errors"]) > 0)
        self.assertEqual(result["claude_md"], "")
        self.assertEqual(result["lock"], {})
        self.assertEqual(result["auxiliary_agents"], [])
        self.assertEqual(result["files_to_write"], [])


if __name__ == "__main__":
    unittest.main()
