"""Tests for TASK-007: compose(), lock generation, content copying, atomic writes."""

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

# Add scripts directory to path.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_DIR = os.path.join(_REPO_ROOT, "plugin", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import composer  # noqa: E402


_FIXTURE_DIR = os.path.join(
    _REPO_ROOT, "evals", "fixtures", "test-overlay"
)
_BASE_PATH = os.path.join(_REPO_ROOT, "plugin")


class TestCopyOverlayContent(unittest.TestCase):
    """Tests for _copy_overlay_content."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_")
        with open(os.path.join(_FIXTURE_DIR, "system2.overlay.json")) as fh:
            self.manifest = json.load(fh)

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_copies_content_files_to_project_local(self):
        target_dir = os.path.join(
            self.project_dir, ".system2", "overlays", "test-overlay"
        )
        os.makedirs(target_dir, exist_ok=True)
        composer._copy_overlay_content(
            _FIXTURE_DIR, self.manifest, target_dir
        )
        self.assertTrue(os.path.isdir(target_dir))
        self.assertTrue(
            os.path.isfile(
                os.path.join(target_dir, "contributions", "orchestrator", "principles.md")
            )
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(target_dir, "contributions", "agents", "executor-discipline.md")
            )
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(target_dir, "contributions", "orchestrator", "gate-3-consultation.md")
            )
        )

    def test_copies_agent_files(self):
        target_dir = os.path.join(
            self.project_dir, ".system2", "overlays", "test-overlay"
        )
        os.makedirs(target_dir, exist_ok=True)
        composer._copy_overlay_content(
            _FIXTURE_DIR, self.manifest, target_dir
        )
        self.assertTrue(
            os.path.isfile(os.path.join(target_dir, "agents", "test-scout.md"))
        )

    def test_content_hash_is_deterministic(self):
        target1 = os.path.join(self.project_dir, "copy1")
        target2 = os.path.join(self.project_dir, "copy2")
        os.makedirs(target1)
        os.makedirs(target2)
        hash1 = composer._copy_overlay_content(
            _FIXTURE_DIR, self.manifest, target1
        )
        hash2 = composer._copy_overlay_content(
            _FIXTURE_DIR, self.manifest, target2
        )
        self.assertEqual(hash1, hash2)
        self.assertTrue(hash1.startswith("sha256:"))

    def test_content_hash_format(self):
        target_dir = os.path.join(self.project_dir, "hash_test")
        os.makedirs(target_dir)
        content_hash = composer._copy_overlay_content(
            _FIXTURE_DIR, self.manifest, target_dir
        )
        self.assertTrue(content_hash.startswith("sha256:"))
        hex_part = content_hash.split(":")[1]
        self.assertEqual(len(hex_part), 64)


class TestGenerateLock(unittest.TestCase):
    """Tests for _generate_lock."""

    def test_lock_has_required_fields(self):
        overlays = [
            {
                "name": "test-overlay",
                "version": "1.0.0",
                "source_path": "/tmp/test-overlay",
                "local_path": ".system2/overlays/test-overlay/",
                "manifest_hash": "sha256:abc123",
                "content_hash": "sha256:def456",
            }
        ]
        contributions_applied = {
            "orchestrator.principles": ["test-principle-1"],
        }
        warnings = []
        lock = composer._generate_lock(
            overlays, contributions_applied, warnings, "0.4.1"
        )
        self.assertIn("composed_at", lock)
        self.assertEqual(lock["system2_version"], "0.4.1")
        self.assertEqual(lock["schema_version"], "1.0.0")
        self.assertIn("overlays", lock)
        self.assertIn("contributions_applied", lock)
        self.assertIn("warnings", lock)

    def test_lock_overlay_entry_fields(self):
        overlays = [
            {
                "name": "test-overlay",
                "version": "1.0.0",
                "source_path": "/tmp/test-overlay",
                "local_path": ".system2/overlays/test-overlay/",
                "manifest_hash": "sha256:abc123",
                "content_hash": "sha256:def456",
            }
        ]
        lock = composer._generate_lock(overlays, {}, [], "0.4.1")
        entry = lock["overlays"][0]
        self.assertEqual(entry["name"], "test-overlay")
        self.assertEqual(entry["version"], "1.0.0")
        self.assertEqual(entry["source_path"], "/tmp/test-overlay")
        self.assertEqual(entry["local_path"], ".system2/overlays/test-overlay/")
        self.assertEqual(entry["manifest_hash"], "sha256:abc123")
        self.assertEqual(entry["content_hash"], "sha256:def456")

    def test_lock_timestamp_is_iso8601(self):
        lock = composer._generate_lock(
            [], {}, [], "0.4.1", timestamp="2026-06-02T12:00:00Z"
        )
        ts = lock["composed_at"]
        self.assertTrue(ts.endswith("Z"))
        self.assertEqual(ts, "2026-06-02T12:00:00Z")
        from datetime import datetime
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


class TestWriteOutputs(unittest.TestCase):
    """Tests for _write_outputs (atomic writes)."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_")
        os.makedirs(os.path.join(self.project_dir, "spec"), exist_ok=True)
        os.makedirs(os.path.join(self.project_dir, ".claude", "agents"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_writes_claude_md(self):
        claude_md = "# Test composed CLAUDE.md\n\nContent here.\n"
        lock = {"composed_at": "2026-01-01T00:00:00Z"}
        files = composer._write_outputs(
            self.project_dir, claude_md, lock, [], {}
        )
        claude_path = os.path.join(self.project_dir, "CLAUDE.md")
        self.assertIn(claude_path, files)
        self.assertTrue(os.path.isfile(claude_path))
        with open(claude_path) as f:
            self.assertEqual(f.read(), claude_md)

    def test_writes_lock_file(self):
        lock = {"composed_at": "2026-01-01T00:00:00Z", "overlays": []}
        files = composer._write_outputs(
            self.project_dir, "# CLAUDE.md", lock, [], {}
        )
        lock_path = os.path.join(self.project_dir, "spec", "overlay-manifest.lock")
        self.assertIn(lock_path, files)
        self.assertTrue(os.path.isfile(lock_path))
        with open(lock_path) as f:
            parsed = json.load(f)
        self.assertEqual(parsed["composed_at"], "2026-01-01T00:00:00Z")

    def test_writes_auxiliary_agent_files(self):
        aux = [
            {
                "name": "test-scout",
                "source_file": os.path.join(
                    _FIXTURE_DIR, "agents", "test-scout.md"
                ),
            }
        ]
        files = composer._write_outputs(
            self.project_dir, "# CLAUDE.md", {}, aux, {}
        )
        agent_path = os.path.join(
            self.project_dir, ".claude", "agents", "test-scout.md"
        )
        self.assertIn(agent_path, files)
        self.assertTrue(os.path.isfile(agent_path))

    def test_backup_and_restore_on_failure(self):
        """Verify that existing files are restored if a write fails."""
        # Write an existing CLAUDE.md
        claude_path = os.path.join(self.project_dir, "CLAUDE.md")
        original_content = "# Original CLAUDE.md\n"
        with open(claude_path, "w") as f:
            f.write(original_content)

        # Make the spec directory read-only to force a lock file write failure
        spec_dir = os.path.join(self.project_dir, "spec")
        os.makedirs(spec_dir, exist_ok=True)
        os.chmod(spec_dir, stat.S_IRUSR | stat.S_IXUSR)

        try:
            with self.assertRaises(OSError):
                composer._write_outputs(
                    self.project_dir,
                    "# New CLAUDE.md",
                    {"key": "value"},
                    [],
                    {},
                )
            # Original CLAUDE.md should be restored
            with open(claude_path) as f:
                self.assertEqual(f.read(), original_content)
        finally:
            os.chmod(spec_dir, stat.S_IRWXU)

    def test_idempotent_output(self):
        """Running _write_outputs twice produces identical files."""
        claude_md = "# Composed\n"
        lock = {"composed_at": "2026-01-01T00:00:00Z", "overlays": []}
        composer._write_outputs(self.project_dir, claude_md, lock, [], {})
        # Read first output
        with open(os.path.join(self.project_dir, "CLAUDE.md")) as f:
            first_claude = f.read()
        with open(os.path.join(self.project_dir, "spec", "overlay-manifest.lock")) as f:
            first_lock = f.read()
        # Write again
        composer._write_outputs(self.project_dir, claude_md, lock, [], {})
        with open(os.path.join(self.project_dir, "CLAUDE.md")) as f:
            second_claude = f.read()
        with open(os.path.join(self.project_dir, "spec", "overlay-manifest.lock")) as f:
            second_lock = f.read()
        self.assertEqual(first_claude, second_claude)
        self.assertEqual(first_lock, second_lock)


class TestCompose(unittest.TestCase):
    """Tests for compose() public API."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_")

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_compose_returns_expected_keys(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        for key in (
            "claude_md", "lock", "auxiliary_agents",
            "files_to_write", "report", "errors",
        ):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_compose_no_errors_for_valid_overlay(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        self.assertEqual(result["errors"], [])

    def test_compose_generates_claude_md(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        self.assertIn("COMPOSED", result["claude_md"])
        self.assertIn("test-overlay@1.0.0", result["claude_md"])

    def test_compose_generates_lock(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        lock = result["lock"]
        self.assertEqual(lock["schema_version"], "1.0.0")
        self.assertEqual(len(lock["overlays"]), 1)
        self.assertEqual(lock["overlays"][0]["name"], "test-overlay")

    def test_compose_copies_content_files(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        composer._write_outputs(
            self.project_dir,
            result["claude_md"],
            result["lock"],
            result["auxiliary_agents"],
            pending_content_copies=result.get("pending_content_copies", []),
            overlay_info_for_lock=result.get("overlay_info_for_lock", []),
        )
        local_path = os.path.join(
            self.project_dir, ".system2", "overlays", "test-overlay"
        )
        self.assertTrue(os.path.isdir(local_path))

    def test_compose_lists_auxiliary_agents(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        self.assertTrue(len(result["auxiliary_agents"]) > 0)
        names = [a["name"] for a in result["auxiliary_agents"]]
        self.assertIn("test-scout", names)

    def test_compose_idempotent(self):
        """Running compose twice produces identical claude_md and lock."""
        result1 = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        # Clean local copies so second run re-copies
        shutil.rmtree(
            os.path.join(self.project_dir, ".system2"), ignore_errors=True
        )
        result2 = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        # Timestamps differ but content_fingerprint and contributions match.
        self.assertEqual(
            result1["lock"]["content_fingerprint"],
            result2["lock"]["content_fingerprint"],
        )
        self.assertEqual(
            result1["lock"]["contributions_applied"],
            result2["lock"]["contributions_applied"],
        )

    def test_compose_reads_version_file(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        self.assertEqual(result["lock"]["system2_version"], "1.0.0")

    def test_compose_returns_files_to_write(self):
        result = composer.compose(
            _BASE_PATH, [_FIXTURE_DIR], self.project_dir
        )
        self.assertIn("files_to_write", result)
        file_names = [os.path.basename(f) for f in result["files_to_write"]]
        self.assertIn("CLAUDE.md", file_names)
        self.assertIn("overlay-manifest.lock", file_names)

    def test_compose_validation_errors(self):
        """compose() with invalid overlay returns errors."""
        bad_dir = tempfile.mkdtemp(prefix="s2test_bad_")
        try:
            # Write an invalid manifest (missing name)
            with open(os.path.join(bad_dir, "system2.overlay.json"), "w") as f:
                json.dump({"version": "1.0.0", "schema_version": "1.0.0", "contributions": {}}, f)
            result = composer.compose(_BASE_PATH, [bad_dir], self.project_dir)
            self.assertTrue(len(result["errors"]) > 0)
        finally:
            shutil.rmtree(bad_dir, ignore_errors=True)


class TestMainWiring(unittest.TestCase):
    """Tests for main() exit code behavior."""

    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="s2test_")

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_main_dry_run_exit_0(self):
        """--dry-run with valid overlay should exit 0."""
        rc = os.system(
            f'python3 "{os.path.join(_SCRIPT_DIR, "composer.py")}" '
            f'--base "{_BASE_PATH}" '
            f'--overlays "{_FIXTURE_DIR}" '
            f'--project "{self.project_dir}" '
            f'--dry-run --format json > /dev/null 2>&1'
        )
        self.assertEqual(rc >> 8, 0)

    def test_main_dry_run_no_files_written(self):
        """--dry-run should not write any files."""
        os.system(
            f'python3 "{os.path.join(_SCRIPT_DIR, "composer.py")}" '
            f'--base "{_BASE_PATH}" '
            f'--overlays "{_FIXTURE_DIR}" '
            f'--project "{self.project_dir}" '
            f'--dry-run --format json > /dev/null 2>&1'
        )
        self.assertFalse(
            os.path.isfile(os.path.join(self.project_dir, "CLAUDE.md"))
        )
        self.assertFalse(
            os.path.isfile(
                os.path.join(self.project_dir, "spec", "overlay-manifest.lock")
            )
        )

    def test_main_write_mode_creates_files(self):
        """Without --dry-run, files should be written."""
        rc = os.system(
            f'python3 "{os.path.join(_SCRIPT_DIR, "composer.py")}" '
            f'--base "{_BASE_PATH}" '
            f'--overlays "{_FIXTURE_DIR}" '
            f'--project "{self.project_dir}" '
            f'--format json > /dev/null 2>&1'
        )
        self.assertEqual(rc >> 8, 0)
        self.assertTrue(
            os.path.isfile(os.path.join(self.project_dir, "CLAUDE.md"))
        )
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.project_dir, "spec", "overlay-manifest.lock")
            )
        )

    def test_main_invalid_overlay_exit_1(self):
        """Invalid overlay should exit 1."""
        bad_dir = tempfile.mkdtemp(prefix="s2test_bad_")
        try:
            with open(os.path.join(bad_dir, "system2.overlay.json"), "w") as f:
                json.dump({"contributions": {}}, f)
            rc = os.system(
                f'python3 "{os.path.join(_SCRIPT_DIR, "composer.py")}" '
                f'--base "{_BASE_PATH}" '
                f'--overlays "{bad_dir}" '
                f'--project "{self.project_dir}" '
                f'--dry-run --format json > /dev/null 2>&1'
            )
            self.assertEqual(rc >> 8, 1)
        finally:
            shutil.rmtree(bad_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
