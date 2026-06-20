"""Unit tests for profiles.py store layer: load / validate / atomic save."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import os, sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT_DIR = os.path.join(_REPO_ROOT, "plugin", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import profiles  # noqa: E402


def _store_md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


class _StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, ".system2", "profiles.json")
        self._capture_real_store()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        self._assert_real_store_untouched()

    def _capture_real_store(self):
        path = profiles.DEFAULT_STORE_PATH
        self._real_store_existed = os.path.exists(path)
        self._real_store_md5 = (
            _store_md5(path) if self._real_store_existed else None
        )

    def _assert_real_store_untouched(self):
        path = profiles.DEFAULT_STORE_PATH
        if not self._real_store_existed:
            self.assertFalse(
                os.path.exists(path),
                f"test created the real store at {path}",
            )
        else:
            self.assertEqual(
                self._real_store_md5,
                _store_md5(path),
                f"test modified the real store at {path}",
            )


class TestLoadStore(_StoreTestBase):
    def test_absent_file_returns_empty_store(self):
        store = profiles.load_store(self.store_path)
        self.assertEqual(
            store, {"schema_version": profiles.SCHEMA_VERSION, "profiles": {}}
        )

    def test_corrupt_json_raises_clear_error_no_traceback(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ]")
        try:
            profiles.load_store(self.store_path)
            self.fail("expected ProfileError")
        except profiles.ProfileError as exc:
            self.assertEqual(exc.exit_code, 1)
            self.assertIn("malformed", exc.message)
            self.assertIn("Fix or remove the file", exc.message)
            # The user-facing message is a single clean line, not a stack trace.
            self.assertNotIn("Traceback", exc.message)
            self.assertNotIn("\n", exc.message)

    def test_structural_violation_profiles_not_object(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "1.0.0", "profiles": []}, fh)
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_structural_violation_entry_missing_overlays(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "1.0.0", "profiles": {"x": {}}}, fh)
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("'x'", ctx.exception.message)

    def test_valid_store_loads(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        valid = {"schema_version": "1.0.0", "profiles": {"x": {"overlays": []}}}
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(valid, fh)
        self.assertEqual(profiles.load_store(self.store_path), valid)

    def test_injection_profile_name_rejected_without_reinjecting(self):
        # A hand-edited store with a newline-bearing key would let the spoofed
        # line surface through --list and unknown-profile errors. Reject it at
        # load, and never echo the raw name back (repr neutralizes the newline).
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        bad = {
            "schema_version": "1.0.0",
            "profiles": {"good\nERROR: injected": {"overlays": []}},
        }
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("malformed", ctx.exception.message)
        # Single clean line, no raw newline re-injected into the message.
        self.assertNotIn("\n", ctx.exception.message)
        self.assertNotIn("Traceback", ctx.exception.message)

    def test_simple_invalid_profile_name_rejected(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        bad = {
            "schema_version": "1.0.0",
            "profiles": {"Bad_Name": {"overlays": []}},
        }
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(bad, fh)
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_valid_kebab_name_is_not_a_false_positive(self):
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        valid = {
            "schema_version": "1.0.0",
            "profiles": {"my-stack-2": {"overlays": []}},
        }
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump(valid, fh)
        self.assertEqual(profiles.load_store(self.store_path), valid)


class TestSaveStoreAtomic(_StoreTestBase):
    def test_round_trip_save_then_load(self):
        store = {
            "schema_version": "1.0.0",
            "profiles": {"my-stack": {"overlays": [{"path": "/abs/x"}]}},
        }
        profiles.save_store_atomic(store, self.store_path)
        self.assertEqual(profiles.load_store(self.store_path), store)

    def test_save_creates_parent_dir(self):
        self.assertFalse(os.path.exists(os.path.dirname(self.store_path)))
        profiles.save_store_atomic(
            {"schema_version": "1.0.0", "profiles": {}}, self.store_path
        )
        self.assertTrue(os.path.exists(self.store_path))

    def test_mid_write_failure_preserves_prior_file_and_unlinks_temp(self):
        prior = {"schema_version": "1.0.0", "profiles": {"old": {"overlays": []}}}
        profiles.save_store_atomic(prior, self.store_path)

        store_dir = os.path.dirname(self.store_path)
        new = {"schema_version": "1.0.0", "profiles": {"new": {"overlays": []}}}
        with mock.patch(
            "profiles.os.replace", side_effect=OSError("disk full")
        ):
            with self.assertRaises(profiles.ProfileError) as ctx:
                profiles.save_store_atomic(new, self.store_path)
        self.assertEqual(ctx.exception.exit_code, 3)

        # Prior file intact; no leftover temp file in the store dir.
        self.assertEqual(profiles.load_store(self.store_path), prior)
        leftovers = [f for f in os.listdir(store_dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestNormalizePath(unittest.TestCase):
    def test_relative_becomes_absolute_against_base(self):
        self.assertEqual(profiles.normalize_path("a/b", "/X"), "/X/a/b")

    def test_tilde_expansion(self):
        home = os.path.expanduser("~")
        self.assertEqual(
            profiles.normalize_path("~/sub", "/ignored"),
            os.path.normpath(os.path.join(home, "sub")),
        )

    def test_dotdot_collapse_via_normpath(self):
        self.assertEqual(profiles.normalize_path("a/../b", "/X"), "/X/b")
        self.assertEqual(profiles.normalize_path("/X/a/../b", "/ignored"), "/X/b")

    def test_cross_base_resolvability_for_absolute_raw(self):
        from_one = profiles.normalize_path("/abs/overlay", "/base/one")
        from_two = profiles.normalize_path("/abs/overlay", "/base/two")
        self.assertEqual(from_one, from_two)
        self.assertEqual(from_one, "/abs/overlay")


class TestDedupPaths(unittest.TestCase):
    def test_dedup_preserves_first_seen_order(self):
        self.assertEqual(
            profiles.dedup_paths(["/a", "/b", "/a", "/c", "/b"]),
            ["/a", "/b", "/c"],
        )

    def test_dedup_of_already_unique_list_is_identity(self):
        unique = ["/a", "/b", "/c"]
        self.assertEqual(profiles.dedup_paths(unique), unique)


def _write_overlay(dir_path, name):
    """Create a minimal valid overlay dir with a system2.overlay.json name."""
    os.makedirs(dir_path, exist_ok=True)
    with open(
        os.path.join(dir_path, "system2.overlay.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump({"name": name}, fh)


class TestReadOverlayName(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_valid_manifest_returns_name(self):
        ov = os.path.join(self.tmpdir, "overlay-x")
        _write_overlay(ov, "overlay-x")
        self.assertEqual(profiles._read_overlay_name(ov), "overlay-x")

    def test_missing_dir_returns_none(self):
        self.assertIsNone(
            profiles._read_overlay_name(os.path.join(self.tmpdir, "nope"))
        )

    def test_missing_manifest_returns_none(self):
        ov = os.path.join(self.tmpdir, "empty")
        os.makedirs(ov, exist_ok=True)
        self.assertIsNone(profiles._read_overlay_name(ov))

    def test_bad_json_returns_none(self):
        ov = os.path.join(self.tmpdir, "bad")
        os.makedirs(ov, exist_ok=True)
        with open(
            os.path.join(ov, "system2.overlay.json"), "w", encoding="utf-8"
        ) as fh:
            fh.write("{ not json ]")
        self.assertIsNone(profiles._read_overlay_name(ov))

    def test_invalid_name_returns_none(self):
        ov = os.path.join(self.tmpdir, "uppercase")
        _write_overlay(ov, "Bad_Name")
        self.assertIsNone(profiles._read_overlay_name(ov))

    def test_missing_name_returns_none(self):
        ov = os.path.join(self.tmpdir, "noname")
        os.makedirs(ov, exist_ok=True)
        with open(
            os.path.join(ov, "system2.overlay.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump({"version": "1.0.0"}, fh)
        self.assertIsNone(profiles._read_overlay_name(ov))


class TestResolveProfile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        self.ov_x = os.path.join(self.tmpdir, "overlay-x")
        self.ov_y = os.path.join(self.tmpdir, "overlay-y")
        _write_overlay(self.ov_x, "overlay-x")
        _write_overlay(self.ov_y, "overlay-y")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _save(self, store):
        profiles.save_store_atomic(store, self.store_path)

    def test_unknown_profile_sets_unknown_no_raise(self):
        self._save({"schema_version": "1.0.0", "profiles": {}})
        result = profiles.resolve_profile("nope", self.store_path)
        self.assertTrue(result.unknown)
        self.assertEqual(result.ordered_paths, [])
        self.assertEqual(result.stale, [])

    def test_unknown_profile_on_absent_store(self):
        result = profiles.resolve_profile(
            "nope", os.path.join(self.tmpdir, "absent.json")
        )
        self.assertTrue(result.unknown)

    def test_known_profile_preserves_definition_order(self):
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [
                            {"path": self.ov_y},
                            {"path": self.ov_x},
                        ]
                    }
                },
            }
        )
        result = profiles.resolve_profile("stack", self.store_path)
        self.assertFalse(result.unknown)
        self.assertEqual(result.ordered_paths, [self.ov_y, self.ov_x])

    def test_all_valid_yields_empty_stale(self):
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [{"path": self.ov_x}, {"path": self.ov_y}]
                    }
                },
            }
        )
        result = profiles.resolve_profile("stack", self.store_path)
        self.assertEqual(result.stale, [])

    def test_missing_path_recorded_as_stale_with_its_path(self):
        missing = os.path.join(self.tmpdir, "gone")
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [{"path": self.ov_x}, {"path": missing}]
                    }
                },
            }
        )
        result = profiles.resolve_profile("stack", self.store_path)
        self.assertEqual(result.ordered_paths, [self.ov_x, missing])
        self.assertEqual(len(result.stale), 1)
        self.assertEqual(result.stale[0].path, missing)
        self.assertIn("missing", result.stale[0].reason)

    def test_path_without_valid_manifest_is_stale(self):
        nomanifest = os.path.join(self.tmpdir, "nomanifest")
        os.makedirs(nomanifest, exist_ok=True)
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {"overlays": [{"path": nomanifest}]}
                },
            }
        )
        result = profiles.resolve_profile("stack", self.store_path)
        self.assertEqual(len(result.stale), 1)
        self.assertEqual(result.stale[0].path, nomanifest)

    def test_resolution_does_not_raise_on_stale(self):
        missing = os.path.join(self.tmpdir, "gone")
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {"stack": {"overlays": [{"path": missing}]}},
            }
        )
        # stale_policy is plumbed but resolution never raises on stale.
        result = profiles.resolve_profile(
            "stack", self.store_path, stale_policy="hard_fail"
        )
        self.assertEqual(len(result.stale), 1)


class TestAvailableProfilesAndUnknownError(unittest.TestCase):
    def test_available_profiles_definition_order(self):
        store = {
            "schema_version": "1.0.0",
            "profiles": {"b": {"overlays": []}, "a": {"overlays": []}},
        }
        self.assertEqual(profiles.available_profiles(store), ["b", "a"])

    def test_available_profiles_empty(self):
        self.assertEqual(
            profiles.available_profiles(
                {"schema_version": "1.0.0", "profiles": {}}
            ),
            [],
        )

    def test_unknown_error_lists_available(self):
        err = profiles._unknown_profile_error("nope", ["a", "b", "c"])
        self.assertEqual(err["exit_code"], 1)
        self.assertEqual(
            err["text"],
            "ERROR: No such profile 'nope'. Available profiles: a, b, c.",
        )
        self.assertEqual(err["json"]["status"], "error")
        self.assertEqual(err["json"]["message"], "No such profile 'nope'.")
        self.assertEqual(err["json"]["available_profiles"], ["a", "b", "c"])

    def test_unknown_error_none_defined(self):
        err = profiles._unknown_profile_error("nope", [])
        self.assertEqual(
            err["text"],
            "ERROR: No such profile 'nope'. No profiles are defined yet.",
        )
        self.assertEqual(err["json"]["available_profiles"], [])


def _write_overlay_versioned(dir_path, name, version):
    os.makedirs(dir_path, exist_ok=True)
    with open(
        os.path.join(dir_path, "system2.overlay.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump({"name": name, "version": version}, fh)


class TestListProfiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_store_returns_empty_list(self):
        self.assertEqual(profiles.list_profiles(self.store_path), [])

    def test_absent_store_returns_empty_list(self):
        self.assertEqual(
            profiles.list_profiles(os.path.join(self.tmpdir, "absent.json")), []
        )

    def test_non_empty_preserves_definition_order(self):
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "b-stack": {"overlays": []},
                    "a-stack": {"overlays": []},
                },
            },
            self.store_path,
        )
        self.assertEqual(
            profiles.list_profiles(self.store_path), ["b-stack", "a-stack"]
        )


class TestInspectProfile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        self.ov_x = os.path.join(self.tmpdir, "overlay-x")
        _write_overlay_versioned(self.ov_x, "overlay-x", "1.2.0")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _save(self, store):
        profiles.save_store_atomic(store, self.store_path)

    def test_valid_profile_shows_resolved_name_and_version(self):
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {"stack": {"overlays": [{"path": self.ov_x}]}},
            }
        )
        result = profiles.inspect_profile("stack", self.store_path)
        self.assertEqual(result["name"], "stack")
        self.assertEqual(len(result["overlays"]), 1)
        record = result["overlays"][0]
        self.assertEqual(record["path"], self.ov_x)
        self.assertEqual(record["resolved_name"], "overlay-x")
        self.assertEqual(record["resolved_version"], "1.2.0")
        self.assertFalse(record["stale"])
        self.assertEqual(record["annotation"], "")

    def test_cached_fields_default_to_none_when_absent(self):
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {"stack": {"overlays": [{"path": self.ov_x}]}},
            }
        )
        record = profiles.inspect_profile("stack", self.store_path)["overlays"][0]
        self.assertIsNone(record["cached_name"])
        self.assertIsNone(record["cached_version"])

    def test_one_stale_path_is_annotated_not_raised(self):
        missing = os.path.join(self.tmpdir, "gone")
        self._save(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [{"path": self.ov_x}, {"path": missing}]
                    }
                },
            }
        )
        result = profiles.inspect_profile("stack", self.store_path)
        self.assertEqual(len(result["overlays"]), 2)
        valid, stale = result["overlays"]
        self.assertFalse(valid["stale"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["path"], missing)
        self.assertIn("unresolvable", stale["annotation"])
        self.assertIsNone(stale["resolved_name"])
        self.assertIsNone(stale["resolved_version"])

    def test_unknown_profile_raises_clean_profile_error(self):
        self._save({"schema_version": "1.0.0", "profiles": {}})
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.inspect_profile("nope", self.store_path)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("nope", ctx.exception.message)


class TestReadOnlyNoWrite(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        self.ov_x = os.path.join(self.tmpdir, "overlay-x")
        _write_overlay_versioned(self.ov_x, "overlay-x", "1.2.0")
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {"stack": {"overlays": [{"path": self.ov_x}]}},
            },
            self.store_path,
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _snapshot(self):
        with open(self.store_path, "rb") as fh:
            content = fh.read()
        return content, os.stat(self.store_path).st_mtime_ns

    def test_list_inspect_resolve_do_not_write_store(self):
        before = self._snapshot()
        profiles.list_profiles(self.store_path)
        profiles.inspect_profile("stack", self.store_path)
        profiles.resolve_profile("stack", self.store_path)
        self.assertEqual(self._snapshot(), before)


class TestCliNoMutationVerb(unittest.TestCase):
    def test_parser_has_no_mutation_options(self):
        # Introspect the argparse parser built by main(): it must expose the
        # read verbs and NO --create/--edit/--delete/--save mutation options.
        captured = {}
        real_parse = argparse.ArgumentParser.parse_args

        def _capture(self, *a, **k):
            captured["parser"] = self
            raise SystemExit(0)

        with mock.patch.object(argparse.ArgumentParser, "parse_args", _capture):
            with mock.patch.object(sys, "argv", ["profiles.py", "--list"]):
                with self.assertRaises(SystemExit):
                    profiles.main()

        option_strings = set()
        for action in captured["parser"]._actions:
            option_strings.update(action.option_strings)
        self.assertIn("--list", option_strings)
        self.assertIn("--inspect", option_strings)
        self.assertIn("--resolve", option_strings)
        for forbidden in ("--create", "--edit", "--delete", "--save"):
            self.assertNotIn(forbidden, option_strings)

    def test_help_text_exposes_no_mutation_verb(self):
        proc = subprocess.run(
            [sys.executable, profiles.__file__, "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        for verb in ("--list", "--inspect", "--resolve"):
            self.assertIn(verb, proc.stdout)
        for forbidden in ("--create", "--edit", "--delete", "--save"):
            self.assertNotIn(forbidden, proc.stdout)


class TestCliListRejectsSpoofedName(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="s2_listspoof_")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_list_over_injected_name_store_exits_clean_no_spoof(self):
        store_dir = os.path.join(self.home, ".system2")
        os.makedirs(store_dir, exist_ok=True)
        with open(os.path.join(store_dir, "profiles.json"), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schema_version": "1.0.0",
                    "profiles": {"good\nERROR: injected": {"overlays": []}},
                },
                fh,
            )
        env = dict(os.environ)
        env["HOME"] = self.home
        proc = subprocess.run(
            [sys.executable, profiles.__file__, "--list", "--format", "text"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        combined = proc.stdout + proc.stderr
        # The only ERROR line is the malformed-store message; the spoofed line
        # never surfaces on its own.
        self.assertNotIn("\nERROR: injected", combined)
        self.assertNotIn("Traceback", combined)


class TestActiveProfileForLock(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        self.project = os.path.join(self.tmpdir, "project")
        os.makedirs(os.path.join(self.project, "spec"), exist_ok=True)
        # Real (non-stale) overlay dirs shared by both the profile store and
        # the lock. A profile is "active" only when all its paths resolve, so
        # these must exist with a valid manifest.
        self.path_a = os.path.join(self.tmpdir, "overlay-a")
        self.path_b = os.path.join(self.tmpdir, "overlay-b")
        self.path_c = os.path.join(self.tmpdir, "overlay-c")
        _write_overlay(self.path_a, "overlay-a")
        _write_overlay(self.path_b, "overlay-b")
        _write_overlay(self.path_c, "overlay-c")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _save_profile(self, name, paths):
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    name: {"overlays": [{"path": p} for p in paths]}
                },
            },
            self.store_path,
        )

    def _write_lock(self, source_paths):
        lock = {
            "schema_version": "1.0.0",
            "overlays": [
                {"source_path": p, "name": "ov"} for p in source_paths
            ],
        }
        with open(
            os.path.join(self.project, "spec", "overlay-manifest.lock"),
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(lock, fh)

    def test_equal_sets_in_different_order_is_active(self):
        self._save_profile("stack", [self.path_a, self.path_b])
        self._write_lock([self.path_b, self.path_a])  # reversed order
        self.assertTrue(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_differing_sets_not_active(self):
        self._save_profile("stack", [self.path_a, self.path_b])
        self._write_lock([self.path_a, self.path_c])
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_no_lock_file_not_active(self):
        self._save_profile("stack", [self.path_a, self.path_b])
        # No lock written.
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_stale_path_not_active_even_when_set_equals_lock(self):
        # The profile's source-path set equals the lock exactly, but one path
        # is stale (no overlay dir), so the profile cannot activate -> not
        # active (a doomed recompose must not be offered).
        stale = os.path.join(self.tmpdir, "gone-overlay")
        self._save_profile("stack", [self.path_a, stale])
        self._write_lock([self.path_a, stale])
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_unknown_profile_not_active(self):
        self._write_lock([self.path_a, self.path_b])
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "nope", self.store_path
            )
        )

    def test_resolved_set_equal_but_lock_empty_not_active(self):
        # Profile has paths; lock present but with an empty overlays list, so
        # the lock set is empty -> not active (matches no-lock -> False).
        self._save_profile("stack", [self.path_a, self.path_b])
        self._write_lock([])
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_empty_profile_and_empty_lock_not_active(self):
        self._save_profile("stack", [])
        self._write_lock([])
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_unparseable_lock_treated_as_no_lock(self):
        self._save_profile("stack", [self.path_a])
        with open(
            os.path.join(self.project, "spec", "overlay-manifest.lock"),
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write("{ not valid json ]")
        self.assertFalse(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

    def test_comparison_writes_neither_lock_nor_store(self):
        self._save_profile("stack", [self.path_a, self.path_b])
        self._write_lock([self.path_b, self.path_a])
        lock_file = os.path.join(
            self.project, "spec", "overlay-manifest.lock"
        )

        def _snapshot(path):
            with open(path, "rb") as fh:
                return fh.read(), os.stat(path).st_mtime_ns

        store_before = _snapshot(self.store_path)
        lock_before = _snapshot(lock_file)

        self.assertTrue(
            profiles.active_profile_for_lock(
                self.project, "stack", self.store_path
            )
        )

        self.assertEqual(_snapshot(self.store_path), store_before)
        self.assertEqual(_snapshot(lock_file), lock_before)
        # Independence: no .system2/overlays.json is ever created.
        self.assertFalse(
            os.path.exists(
                os.path.join(self.project, ".system2", "overlays.json")
            )
        )


class _MutationTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, ".system2", "profiles.json")
        self.base_cwd = os.path.join(self.tmpdir, "cwd")
        os.makedirs(self.base_cwd, exist_ok=True)
        self.ov_x = os.path.join(self.tmpdir, "overlay-x")
        self.ov_y = os.path.join(self.tmpdir, "overlay-y")
        self.ov_w = os.path.join(self.tmpdir, "overlay-w")
        _write_overlay_versioned(self.ov_x, "overlay-x", "1.2.0")
        _write_overlay_versioned(self.ov_y, "overlay-y", "0.4.1")
        _write_overlay_versioned(self.ov_w, "overlay-w", "2.0.0")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _stored_overlays(self, name):
        store = profiles.load_store(self.store_path)
        return store["profiles"][name]["overlays"]

    def _stored_paths(self, name):
        return [o["path"] for o in self._stored_overlays(name)]


class TestCreateProfile(_MutationTestBase):
    def test_create_stores_paths_and_composes_nothing(self):
        summary = profiles.create_profile(
            "stack", [self.ov_x, self.ov_y], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x, self.ov_y])
        self.assertEqual(summary["name"], "stack")
        self.assertEqual(summary["store_path"], self.store_path)
        self.assertEqual(
            [o["name"] for o in summary["overlays"]], ["overlay-x", "overlay-y"]
        )
        # Composes nothing: no project artifact is created anywhere under tmpdir.
        self.assertFalse(
            os.path.exists(os.path.join(self.tmpdir, "CLAUDE.md"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.tmpdir, "spec", "overlay-manifest.lock"))
        )

    def test_create_captures_advisory_cache(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        overlay = self._stored_overlays("stack")[0]
        self.assertEqual(overlay["cached_name"], "overlay-x")
        self.assertEqual(overlay["cached_version"], "1.2.0")

    def test_create_stale_path_caches_none(self):
        missing = os.path.join(self.tmpdir, "gone")
        profiles.create_profile(
            "stack", [missing], self.base_cwd, store_path=self.store_path
        )
        overlay = self._stored_overlays("stack")[0]
        self.assertIsNone(overlay["cached_name"])
        self.assertIsNone(overlay["cached_version"])

    def test_create_zero_paths_usage_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.create_profile(
                "stack", [], self.base_cwd, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertFalse(os.path.exists(self.store_path))

    def test_create_bad_name_rejected(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.create_profile(
                "Bad_Name", [self.ov_x], self.base_cwd, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertFalse(os.path.exists(self.store_path))

    def test_create_overwrite_without_force_errors(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.create_profile(
                "stack", [self.ov_y], self.base_cwd, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("already exists", ctx.exception.message)
        # Original untouched.
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])

    def test_create_overwrite_with_force_succeeds(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        profiles.create_profile(
            "stack", [self.ov_y], self.base_cwd, force=True, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])

    def test_create_dedup_preserves_first_seen(self):
        profiles.create_profile(
            "stack",
            [self.ov_x, self.ov_y, self.ov_x],
            self.base_cwd,
            store_path=self.store_path,
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x, self.ov_y])

    def test_create_normalizes_relative_against_base_cwd(self):
        rel = os.path.relpath(self.ov_x, self.base_cwd)
        profiles.create_profile(
            "stack", [rel], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])

    def test_create_sets_timestamps(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        entry = profiles.load_store(self.store_path)["profiles"]["stack"]
        self.assertIn("created_at", entry)
        self.assertIn("updated_at", entry)
        self.assertTrue(entry["created_at"].endswith("Z"))

    def test_first_mutation_creates_store_dir_and_file(self):
        self.assertFalse(os.path.exists(os.path.dirname(self.store_path)))
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        self.assertTrue(os.path.exists(self.store_path))


class TestEditProfile(_MutationTestBase):
    def _create(self, name, paths):
        profiles.create_profile(name, paths, self.base_cwd, store_path=self.store_path)

    def test_edit_add_new_path(self):
        self._create("stack", [self.ov_x])
        profiles.edit_profile(
            "stack", [self.ov_y], [], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x, self.ov_y])

    def test_edit_add_existing_path_is_noop_on_set(self):
        self._create("stack", [self.ov_x, self.ov_y])
        profiles.edit_profile(
            "stack", [self.ov_x], [], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x, self.ov_y])

    def test_edit_remove_by_live_name(self):
        self._create("stack", [self.ov_x, self.ov_y])
        profiles.edit_profile(
            "stack", [], ["overlay-x"], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])

    def test_edit_remove_by_cached_name_when_path_stale(self):
        self._create("stack", [self.ov_x, self.ov_y])
        # Make ov_x stale AFTER capture so only the advisory cache identifies it.
        shutil.rmtree(self.ov_x)
        profiles.edit_profile(
            "stack", [], ["overlay-x"], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])

    def test_edit_remove_absent_name_errors_clearly(self):
        self._create("stack", [self.ov_x])
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.edit_profile(
                "stack", [], ["overlay-z"], self.base_cwd, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("not found in profile", ctx.exception.message)
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])

    def test_edit_remove_blocked_by_stale_path_errors_naming_path(self):
        # Build a profile whose entry has a stale path AND no cached name, so
        # the requested removal target cannot be confirmed.
        missing = os.path.join(self.tmpdir, "gone")
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [
                            {"path": missing, "cached_name": None, "cached_version": None}
                        ],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                },
            },
            self.store_path,
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.edit_profile(
                "stack", [], ["overlay-x"], self.base_cwd, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn(missing, ctx.exception.message)
        self.assertEqual(self._stored_paths("stack"), [missing])

    def test_edit_combined_add_and_remove_in_one_call(self):
        self._create("stack", [self.ov_x, self.ov_y])
        profiles.edit_profile(
            "stack",
            [self.ov_w],
            ["overlay-x"],
            self.base_cwd,
            store_path=self.store_path,
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y, self.ov_w])

    def test_edit_removing_last_overlay_rejected_store_unchanged(self):
        # Removing the only overlay would leave the profile empty; this must be
        # rejected (mirrors create's zero-path rejection) and must NOT save.
        self._create("stack", [self.ov_x])
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.edit_profile(
                "stack", [], ["overlay-x"], self.base_cwd,
                store_path=self.store_path,
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("no overlays", ctx.exception.message)
        # The stored profile is unchanged: its single overlay survives.
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])

    def test_edit_remove_last_but_add_new_nets_non_empty_succeeds(self):
        # Removing the only overlay while adding another nets a non-empty list,
        # so the edit is allowed.
        self._create("stack", [self.ov_x])
        profiles.edit_profile(
            "stack", [self.ov_y], ["overlay-x"], self.base_cwd,
            store_path=self.store_path,
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])

    def test_edit_unknown_profile_raises_clean_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.edit_profile(
                "nope", [self.ov_x], [], self.base_cwd, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("nope", ctx.exception.message)

    def test_edit_refreshes_updated_at(self):
        self._create("stack", [self.ov_x])
        before = profiles.load_store(self.store_path)["profiles"]["stack"]
        with mock.patch(
            "profiles._now_iso", return_value="2099-12-31T23:59:59Z"
        ):
            profiles.edit_profile(
                "stack", [self.ov_y], [], self.base_cwd, store_path=self.store_path
            )
        after = profiles.load_store(self.store_path)["profiles"]["stack"]
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["updated_at"], "2099-12-31T23:59:59Z")


class TestDeleteProfile(_MutationTestBase):
    def test_delete_removes_entry(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        summary = profiles.delete_profile("stack", store_path=self.store_path)
        self.assertEqual(summary["name"], "stack")
        self.assertEqual(profiles.list_profiles(self.store_path), [])

    def test_delete_unknown_raises_clean_error(self):
        profiles.save_store_atomic(
            {"schema_version": "1.0.0", "profiles": {}}, self.store_path
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.delete_profile("nope", store_path=self.store_path)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("nope", ctx.exception.message)

    def test_delete_touches_no_project_artifact(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        profiles.delete_profile("stack", store_path=self.store_path)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmpdir, ".system2", "overlays.json"))
        )


class TestSaveProfileFromLock(_MutationTestBase):
    def setUp(self):
        super().setUp()
        self.project = os.path.join(self.tmpdir, "project")
        os.makedirs(os.path.join(self.project, "spec"), exist_ok=True)
        self.lock_file = os.path.join(
            self.project, "spec", "overlay-manifest.lock"
        )

    def _write_lock(self, entries):
        with open(self.lock_file, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "1.0.0", "overlays": entries}, fh)

    def test_save_captures_exact_source_paths_in_order(self):
        self._write_lock(
            [
                {"source_path": self.ov_y, "name": "overlay-y", "version": "0.4.1"},
                {"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"},
            ]
        )
        summary = profiles.save_profile_from_lock(
            "stack", self.project, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y, self.ov_x])
        self.assertEqual(summary["store_path"], self.store_path)

    def test_relative_source_path_resolves_against_cwd_not_project(self):
        # A relative lock source_path must resolve the same way --from-lock
        # would: against the current working directory, NOT project_path.
        old_cwd = os.getcwd()
        cwd = os.path.join(self.tmpdir, "run-cwd")
        os.makedirs(cwd, exist_ok=True)
        os.chdir(cwd)
        try:
            self._write_lock([{"source_path": "sub/overlay-x", "name": "x"}])
            profiles.save_profile_from_lock(
                "stack", self.project, store_path=self.store_path
            )
            stored = self._stored_paths("stack")
            # Resolved against the live cwd (os.getcwd() may differ from `cwd`
            # by symlink resolution, e.g. /var -> /private/var on macOS).
            self.assertEqual(
                stored,
                [os.path.normpath(os.path.join(os.getcwd(), "sub/overlay-x"))],
            )
            self.assertNotEqual(
                stored,
                [os.path.normpath(os.path.join(self.project, "sub/overlay-x"))],
            )
        finally:
            os.chdir(old_cwd)

    def test_absolute_source_path_stored_unchanged(self):
        self._write_lock([{"source_path": self.ov_x, "name": "overlay-x"}])
        profiles.save_profile_from_lock(
            "stack", self.project, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])

    def test_save_captures_advisory_cache_from_lock(self):
        # Path is stale (no overlay dir) so cache must come from the lock entry.
        stale = os.path.join(self.tmpdir, "stale-ov")
        self._write_lock(
            [{"source_path": stale, "name": "overlay-x", "version": "9.9.9"}]
        )
        profiles.save_profile_from_lock(
            "stack", self.project, store_path=self.store_path
        )
        overlay = self._stored_overlays("stack")[0]
        self.assertEqual(overlay["cached_name"], "overlay-x")
        self.assertEqual(overlay["cached_version"], "9.9.9")

    def test_save_no_lock_errors(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.save_profile_from_lock(
                "stack", self.project, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("no current composition to capture", ctx.exception.message.lower())
        self.assertFalse(os.path.exists(self.store_path))

    def test_save_empty_lock_errors(self):
        self._write_lock([])
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.save_profile_from_lock(
                "stack", self.project, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_save_overwrite_without_force_errors(self):
        self._write_lock(
            [{"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"}]
        )
        profiles.save_profile_from_lock(
            "stack", self.project, store_path=self.store_path
        )
        self._write_lock(
            [{"source_path": self.ov_y, "name": "overlay-y", "version": "0.4.1"}]
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.save_profile_from_lock(
                "stack", self.project, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])

    def test_save_overwrite_with_force_succeeds(self):
        self._write_lock(
            [{"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"}]
        )
        profiles.save_profile_from_lock(
            "stack", self.project, store_path=self.store_path
        )
        self._write_lock(
            [{"source_path": self.ov_y, "name": "overlay-y", "version": "0.4.1"}]
        )
        profiles.save_profile_from_lock(
            "stack", self.project, force=True, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])

    def test_save_never_touches_overlays_json(self):
        self._write_lock(
            [{"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"}]
        )
        profiles.save_profile_from_lock(
            "stack", self.project, store_path=self.store_path
        )
        self.assertFalse(
            os.path.exists(
                os.path.join(self.project, ".system2", "overlays.json")
            )
        )


class TestMutationMidWriteFailure(_MutationTestBase):
    def test_mid_write_failure_preserves_pre_mutation_state(self):
        profiles.create_profile(
            "stack", [self.ov_x], self.base_cwd, store_path=self.store_path
        )
        prior = profiles.load_store(self.store_path)
        with mock.patch("profiles.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(profiles.ProfileError) as ctx:
                profiles.create_profile(
                    "other", [self.ov_y], self.base_cwd, store_path=self.store_path
                )
        self.assertEqual(ctx.exception.exit_code, 3)
        self.assertEqual(profiles.load_store(self.store_path), prior)
        store_dir = os.path.dirname(self.store_path)
        self.assertEqual(
            [f for f in os.listdir(store_dir) if f.endswith(".tmp")], []
        )


class TestProfileNameReSecurity(unittest.TestCase):
    """The profile-name validator must reject path-traversal, uppercase, empty,
    and other shapes before any stored name is used in filesystem operations.

    These assertions directly pin the validator against path-traversal and
    empty/edge inputs that would enable traversal or injection through a
    malformed stored value.
    """

    def test_re_rejects_path_traversal_and_separators(self):
        for bad in ("../etc", "..", "a/../b", "/abs/path", "a/b", "a\\b"):
            self.assertIsNone(
                profiles._PROFILE_NAME_RE.match(bad),
                f"name {bad!r} must be rejected (traversal/separator)",
            )

    def test_re_rejects_empty_and_dash_edges(self):
        for bad in ("", "-lead", "trail-", "a--b", "-", "--"):
            self.assertIsNone(
                profiles._PROFILE_NAME_RE.match(bad),
                f"name {bad!r} must be rejected (empty/dash-edge)",
            )

    def test_re_rejects_uppercase_and_punctuation(self):
        for bad in ("Up", "UPPER", "a.b", "a b", "a_b", "a!b", "a:b"):
            self.assertIsNone(
                profiles._PROFILE_NAME_RE.match(bad),
                f"name {bad!r} must be rejected (case/punct)",
            )

    def test_re_accepts_valid_kebab(self):
        for good in ("a", "a-b", "a-b-c", "overlay-x", "good-name-1", "x1-y2"):
            self.assertIsNotNone(
                profiles._PROFILE_NAME_RE.match(good),
                f"name {good!r} must be accepted",
            )


class TestMutationRejectsTraversalNames(_MutationTestBase):
    """create / save_from_lock must refuse path-traversal profile names
    (not just uppercase) before writing anything to the store.

    A traversal name like '../evil' is the security-relevant case, exercised
    here for both create and save_profile_from_lock.
    """

    def setUp(self):
        super().setUp()
        self.project = os.path.join(self.tmpdir, "project")
        os.makedirs(os.path.join(self.project, "spec"), exist_ok=True)
        with open(
            os.path.join(self.project, "spec", "overlay-manifest.lock"),
            "w",
            encoding="utf-8",
        ) as fh:
            json.dump(
                {
                    "schema_version": "1.0.0",
                    "overlays": [
                        {"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"}
                    ],
                },
                fh,
            )

    def test_create_rejects_traversal_name_and_writes_nothing(self):
        for bad in ("../evil", "a/b", "/abs", ""):
            with self.assertRaises(profiles.ProfileError) as ctx:
                profiles.create_profile(
                    bad, [self.ov_x], self.base_cwd, store_path=self.store_path
                )
            self.assertEqual(ctx.exception.exit_code, 1)
        self.assertFalse(os.path.exists(self.store_path))

    def test_save_from_lock_rejects_traversal_name_and_writes_nothing(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.save_profile_from_lock(
                "../evil", self.project, store_path=self.store_path
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertFalse(os.path.exists(self.store_path))


class TestManifestContentNotExecuted(unittest.TestCase):
    """Profile resolution must treat overlay manifest content as inert
    untrusted data and never execute it (no eval / dynamic import).

    Here a manifest whose 'name' is a Python payload string is parsed; if the
    value were ever eval'd it would create a sentinel file. We assert the
    sentinel is never created and the payload-shaped name is simply rejected by
    the name validator (returns None).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sentinel = os.path.join(self.tmpdir, "PWNED_SENTINEL")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_payload_name_is_inert_data_not_executed(self):
        ov = os.path.join(self.tmpdir, "ov")
        os.makedirs(ov, exist_ok=True)
        payload = f'__import__("os").system("touch {self.sentinel}")'
        with open(
            os.path.join(ov, "system2.overlay.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump({"name": payload, "version": payload}, fh)

        # The payload-shaped name fails the name validator -> None. The version
        # field is not name-validated, so it is returned verbatim as an INERT
        # string (data, never code). The load-bearing assertion is that neither
        # read executes the content: the sentinel file is never created.
        self.assertIsNone(profiles._read_overlay_name(ov))
        self.assertEqual(profiles._read_overlay_version(ov), payload)
        self.assertFalse(
            os.path.exists(self.sentinel),
            "manifest content must never be executed",
        )

    def test_non_dict_manifest_is_inert(self):
        # A top-level JSON array (not an object) must not crash or execute.
        ov = os.path.join(self.tmpdir, "arr")
        os.makedirs(ov, exist_ok=True)
        with open(
            os.path.join(ov, "system2.overlay.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(["not", "a", "dict"], fh)
        self.assertIsNone(profiles._read_overlay_name(ov))
        self.assertIsNone(profiles._read_overlay_version(ov))


class TestCorruptStorePropagatesThroughReadApi(unittest.TestCase):
    """A corrupt profiles.json surfaced through any consuming command must raise
    a clean ProfileError (exit 1) with no traceback and a single line.

    These assert the clean-error contract through the public read API entry
    points (list_profiles, inspect_profile, resolve_profile).
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        with open(self.store_path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json ]")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _assert_clean(self, exc):
        self.assertEqual(exc.exit_code, 1)
        self.assertIn("malformed", exc.message)
        self.assertNotIn("Traceback", exc.message)
        self.assertNotIn("\n", exc.message)

    def test_list_profiles_corrupt_store_clean_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.list_profiles(self.store_path)
        self._assert_clean(ctx.exception)

    def test_inspect_profile_corrupt_store_clean_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.inspect_profile("anything", self.store_path)
        self._assert_clean(ctx.exception)

    def test_resolve_profile_corrupt_store_clean_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.resolve_profile("anything", self.store_path)
        self._assert_clean(ctx.exception)


class TestEditAddNormalizesRelativePath(_MutationTestBase):
    """edit --add must normalize a relative path against base_cwd (matching
    create), so the stored path is absolute and dedups against an already-stored
    absolute form.
    """

    def _create(self, name, paths):
        profiles.create_profile(name, paths, self.base_cwd, store_path=self.store_path)

    def test_edit_add_relative_path_stored_absolute(self):
        self._create("stack", [self.ov_x])
        rel = os.path.relpath(self.ov_y, self.base_cwd)
        profiles.edit_profile(
            "stack", [rel], [], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x, self.ov_y])

    def test_edit_add_relative_form_of_existing_path_is_noop(self):
        # Adding the relative form of an already-stored absolute path must not
        # duplicate it: normalization makes them the same path.
        self._create("stack", [self.ov_x])
        rel = os.path.relpath(self.ov_x, self.base_cwd)
        profiles.edit_profile(
            "stack", [rel], [], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_x])


class TestSaveFromLockDedupAndOrder(_MutationTestBase):
    """save_profile_from_lock preserves lock order and dedups repeated
    source_path entries (first occurrence wins), keeping the advisory cache.
    """

    def setUp(self):
        super().setUp()
        self.project = os.path.join(self.tmpdir, "project")
        os.makedirs(os.path.join(self.project, "spec"), exist_ok=True)
        self.lock_file = os.path.join(
            self.project, "spec", "overlay-manifest.lock"
        )

    def _write_lock(self, entries):
        with open(self.lock_file, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "1.0.0", "overlays": entries}, fh)

    def test_duplicate_source_paths_deduped_first_wins(self):
        self._write_lock(
            [
                {"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"},
                {"source_path": self.ov_y, "name": "overlay-y", "version": "0.4.1"},
                {"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"},
            ]
        )
        profiles.save_profile_from_lock(
            "cap", self.project, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("cap"), [self.ov_x, self.ov_y])

    def test_order_preserved_with_advisory_cache(self):
        self._write_lock(
            [
                {"source_path": self.ov_y, "name": "overlay-y", "version": "0.4.1"},
                {"source_path": self.ov_x, "name": "overlay-x", "version": "1.2.0"},
            ]
        )
        profiles.save_profile_from_lock(
            "cap", self.project, store_path=self.store_path
        )
        overlays = self._stored_overlays("cap")
        self.assertEqual([o["path"] for o in overlays], [self.ov_y, self.ov_x])
        self.assertEqual(
            [o["cached_name"] for o in overlays], ["overlay-y", "overlay-x"]
        )


class TestInspectPreservesDefinitionOrder(unittest.TestCase):
    """inspect_profile lists overlays in stored definition order; resolution
    order is definition order while active-compare is set-insensitive (the
    latter asserted in TestActiveProfileForLock).

    This pins definition order across multiple valid entries.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        self.ov_x = os.path.join(self.tmpdir, "overlay-x")
        self.ov_y = os.path.join(self.tmpdir, "overlay-y")
        _write_overlay_versioned(self.ov_x, "overlay-x", "1.2.0")
        _write_overlay_versioned(self.ov_y, "overlay-y", "0.4.1")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_inspect_overlays_in_definition_order(self):
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [{"path": self.ov_y}, {"path": self.ov_x}]
                    }
                },
            },
            self.store_path,
        )
        result = profiles.inspect_profile("stack", self.store_path)
        self.assertEqual(
            [r["path"] for r in result["overlays"]], [self.ov_y, self.ov_x]
        )
        self.assertEqual(
            [r["resolved_name"] for r in result["overlays"]],
            ["overlay-y", "overlay-x"],
        )


class TestSaveAtomicTempUnlinkOnMkstempPath(_StoreTestBase):
    """When the write fails (here at json.dump time, after mkstemp), the temp
    file is unlinked and any prior store file is preserved.

    This exercises the earlier failure window (during the fdopen/dump write) to
    confirm the temp is still cleaned up and the prior store survives.
    """

    def test_write_failure_before_replace_unlinks_temp_keeps_prior(self):
        prior = {"schema_version": "1.0.0", "profiles": {"old": {"overlays": []}}}
        profiles.save_store_atomic(prior, self.store_path)
        store_dir = os.path.dirname(self.store_path)

        new = {"schema_version": "1.0.0", "profiles": {"new": {"overlays": []}}}
        with mock.patch(
            "profiles.json.dump", side_effect=OSError("no space left on device")
        ):
            with self.assertRaises(profiles.ProfileError) as ctx:
                profiles.save_store_atomic(new, self.store_path)
        self.assertEqual(ctx.exception.exit_code, 3)

        self.assertEqual(profiles.load_store(self.store_path), prior)
        leftovers = [f for f in os.listdir(store_dir) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestNonDictOverlayElementsRejectedCleanly(unittest.TestCase):
    """A corrupt/attacker-controlled store whose ``overlays`` list contains
    non-dict elements must be rejected by load_store as a clean ProfileError
    (exit 1, single line, no traceback), and driving the read API over such a
    store must never raise an uncaught non-ProfileError exception.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")
        with open(self.store_path, "w", encoding="utf-8") as fh:
            fh.write('{"profiles":{"evil":{"overlays":["x",1,null]}}}')

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _assert_clean(self, exc):
        self.assertEqual(exc.exit_code, 1)
        self.assertIn("malformed", exc.message)
        self.assertNotIn("Traceback", exc.message)
        self.assertNotIn("\n", exc.message)

    def test_load_store_rejects_non_dict_overlay_elements(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self._assert_clean(ctx.exception)

    def test_inspect_over_corrupt_store_raises_only_profile_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.inspect_profile("evil", self.store_path)
        self._assert_clean(ctx.exception)

    def test_resolve_over_corrupt_store_raises_only_profile_error(self):
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.resolve_profile("evil", self.store_path)
        self._assert_clean(ctx.exception)

    def test_in_memory_store_with_non_dict_overlays_does_not_crash_read_api(self):
        # An in-memory store that never passes through _validate_store still
        # must not raise an uncaught AttributeError/TypeError from the read
        # API's overlay iteration (defense-in-depth skip of non-dict elements).
        store = {"schema_version": "1.0.0", "profiles": {"evil": {"overlays": ["x", 1, None]}}}
        self.assertEqual(profiles._summary("evil", store["profiles"]["evil"], "p"),
                         {"name": "evil", "overlays": [], "store_path": "p"})


class TestEditRemoveAllEntriesSharingName(_MutationTestBase):
    """edit --remove targets an overlay NAME, so when two DISTINCT stored paths
    both resolve to the same name, a single remove must drop BOTH entries.

    This pins the intended set-by-name removal semantic: removal is keyed on the
    resolved/cached name, not on a single path, so duplicate-named entries from
    two locations are removed together (no orphaned twin left behind).
    """

    def test_remove_by_name_drops_all_entries_with_that_name(self):
        # Two physically distinct overlay dirs whose manifests both name
        # themselves "overlay-x" (same logical overlay shipped from two paths).
        ov_x1 = os.path.join(self.tmpdir, "loc1", "overlay-x")
        ov_x2 = os.path.join(self.tmpdir, "loc2", "overlay-x")
        _write_overlay_versioned(ov_x1, "overlay-x", "1.0.0")
        _write_overlay_versioned(ov_x2, "overlay-x", "1.0.0")
        self.assertNotEqual(ov_x1, ov_x2)

        # Build the profile directly so both distinct paths are stored, each
        # resolving (via live manifest) to the same name "overlay-x", plus a
        # third unrelated overlay that must survive.
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [
                            {"path": ov_x1, "cached_name": "overlay-x",
                             "cached_version": "1.0.0"},
                            {"path": self.ov_y, "cached_name": "overlay-y",
                             "cached_version": "0.4.1"},
                            {"path": ov_x2, "cached_name": "overlay-x",
                             "cached_version": "1.0.0"},
                        ],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                },
            },
            self.store_path,
        )

        profiles.edit_profile(
            "stack", [], ["overlay-x"], self.base_cwd, store_path=self.store_path
        )

        # BOTH overlay-x entries (distinct paths) are gone; only overlay-y left.
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])

    def test_remove_by_cached_name_drops_all_stale_twins(self):
        # Same intent but via the advisory cache: two distinct stale paths both
        # cached as "overlay-x" must both be removed by a single remove.
        stale1 = os.path.join(self.tmpdir, "gone1")
        stale2 = os.path.join(self.tmpdir, "gone2")
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [
                            {"path": stale1, "cached_name": "overlay-x",
                             "cached_version": "1.0.0"},
                            {"path": self.ov_y, "cached_name": "overlay-y",
                             "cached_version": "0.4.1"},
                            {"path": stale2, "cached_name": "overlay-x",
                             "cached_version": "1.0.0"},
                        ],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                },
            },
            self.store_path,
        )
        profiles.edit_profile(
            "stack", [], ["overlay-x"], self.base_cwd, store_path=self.store_path
        )
        self.assertEqual(self._stored_paths("stack"), [self.ov_y])


class TestLoadStoreReadIoError(_StoreTestBase):
    """A present-but-unreadable store (read I/O / permission failure) must raise
    ProfileError with exit_code 3 and a clean single-line message (no traceback),
    distinct from the corrupt-content exit-1 contract.
    """

    def _is_root(self):
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def test_unreadable_store_raises_exit_3_clean_message(self):
        if self._is_root():
            self.skipTest("chmod 000 is bypassed by root; cannot simulate EACCES")
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as fh:
            json.dump({"schema_version": "1.0.0", "profiles": {}}, fh)
        os.chmod(self.store_path, 0o000)
        # If chmod 000 is still readable in this environment, the test cannot
        # exercise the I/O path; skip rather than assert a false negative.
        try:
            try:
                with open(self.store_path, "r", encoding="utf-8"):
                    pass
                readable = True
            except OSError:
                readable = False
            if readable:
                self.skipTest("chmod 000 store is still readable in this environment")

            with self.assertRaises(profiles.ProfileError) as ctx:
                profiles.load_store(self.store_path)
            self.assertEqual(ctx.exception.exit_code, 3)
            self.assertNotIn("Traceback", ctx.exception.message)
            self.assertNotIn("\n", ctx.exception.message)
            self.assertIn(self.store_path, ctx.exception.message)
        finally:
            # Restore perms so tearDown's rmtree can clean the tempdir.
            os.chmod(self.store_path, 0o600)


class TestUnknownProfileMessageHelper(unittest.TestCase):
    """The public unknown_profile_message helper returns plain text (no leading
    "ERROR: " prefix), lists available profiles when present, and uses the
    "No profiles are defined yet." variant when the store is empty.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_message_lists_available_profiles_no_error_prefix(self):
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "a-stack": {"overlays": []},
                    "b-stack": {"overlays": []},
                },
            },
            self.store_path,
        )
        msg = profiles.unknown_profile_message("nope", self.store_path)
        self.assertFalse(msg.startswith("ERROR: "))
        self.assertEqual(
            msg,
            "No such profile 'nope'. Available profiles: a-stack, b-stack.",
        )

    def test_message_empty_store_uses_none_defined_variant(self):
        profiles.save_store_atomic(
            {"schema_version": "1.0.0", "profiles": {}}, self.store_path
        )
        msg = profiles.unknown_profile_message("nope", self.store_path)
        self.assertFalse(msg.startswith("ERROR: "))
        self.assertEqual(
            msg, "No such profile 'nope'. No profiles are defined yet."
        )

    def test_message_absent_store_uses_none_defined_variant(self):
        msg = profiles.unknown_profile_message(
            "nope", os.path.join(self.tmpdir, "absent.json")
        )
        self.assertEqual(
            msg, "No such profile 'nope'. No profiles are defined yet."
        )


class TestLoadStoreRejectsNonStringPath(unittest.TestCase):
    """A store overlay with a null or missing 'path' is corruption (the mutation
    API only ever writes string paths). load_store must reject it as a clean
    malformed-store ProfileError (exit 1, no traceback) rather than letting the
    defensive skip silently compose a base-only project.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store_path = os.path.join(self.tmpdir, "profiles.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _assert_clean(self, exc):
        self.assertEqual(exc.exit_code, 1)
        self.assertIn("malformed", exc.message)
        self.assertIn("path", exc.message)
        self.assertIn("Fix or remove the file", exc.message)
        self.assertNotIn("Traceback", exc.message)
        self.assertNotIn("\n", exc.message)

    def _write(self, raw):
        with open(self.store_path, "w", encoding="utf-8") as fh:
            fh.write(raw)

    def test_null_path_overlay_rejected(self):
        self._write(
            '{"schema_version":"1.0.0",'
            '"profiles":{"bad":{"overlays":[{"path":null}]}}}'
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self._assert_clean(ctx.exception)

    def test_missing_path_overlay_rejected(self):
        self._write(
            '{"schema_version":"1.0.0",'
            '"profiles":{"bad":{"overlays":[{}]}}}'
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self._assert_clean(ctx.exception)

    def test_empty_path_overlay_rejected(self):
        self._write(
            '{"schema_version":"1.0.0",'
            '"profiles":{"bad":{"overlays":[{"path":""}]}}}'
        )
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(self.store_path)
        self._assert_clean(ctx.exception)


class TestNonUtf8InputDegradesCleanly(unittest.TestCase):
    """Non-UTF-8 (invalid byte) content on untrusted inputs must not raise an
    uncaught UnicodeDecodeError (a ValueError, not OSError/JSONDecodeError).
    Manifests degrade to None; a non-UTF-8 store is rejected as malformed.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_bad_manifest(self):
        ov = os.path.join(self.tmpdir, "ov")
        os.makedirs(ov, exist_ok=True)
        with open(os.path.join(ov, "system2.overlay.json"), "wb") as fh:
            fh.write(b"\xff")
        return ov

    def test_read_overlay_name_returns_none(self):
        ov = self._write_bad_manifest()
        self.assertIsNone(profiles._read_overlay_name(ov))

    def test_read_overlay_version_returns_none(self):
        ov = self._write_bad_manifest()
        self.assertIsNone(profiles._read_overlay_version(ov))

    def test_resolve_over_bad_manifest_marks_stale_no_traceback(self):
        ov = self._write_bad_manifest()
        store_path = os.path.join(self.tmpdir, "profiles.json")
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {"p": {"overlays": [{"path": ov}]}},
            },
            store_path,
        )
        result = profiles.resolve_profile("p", store_path)
        self.assertEqual(result.ordered_paths, [ov])
        self.assertEqual([s.path for s in result.stale], [ov])

    def test_inspect_over_bad_manifest_marks_stale_no_traceback(self):
        ov = self._write_bad_manifest()
        store_path = os.path.join(self.tmpdir, "profiles.json")
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {"p": {"overlays": [{"path": ov}]}},
            },
            store_path,
        )
        result = profiles.inspect_profile("p", store_path)
        self.assertTrue(result["overlays"][0]["stale"])

    def test_non_utf8_store_rejected_exit_1_no_traceback(self):
        store_path = os.path.join(self.tmpdir, "profiles.json")
        with open(store_path, "wb") as fh:
            fh.write(b"\xff\xfe\x00")
        with self.assertRaises(profiles.ProfileError) as ctx:
            profiles.load_store(store_path)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn("malformed", ctx.exception.message)
        self.assertNotIn("Traceback", ctx.exception.message)


class TestEditAddReplacesRemovedName(_MutationTestBase):
    """edit --add <new-path> --remove <SameName> must NET to a replacement:
    removes apply to the pre-add overlay set, then adds are appended. The freshly
    added overlay (whose manifest name collides with the removed name) survives.
    """

    def test_add_new_path_remove_same_name_yields_replacement(self):
        stale_old = os.path.join(self.tmpdir, "old", "missing")
        new_ov = os.path.join(self.tmpdir, "new", "overlay-x")
        _write_overlay_versioned(new_ov, "overlay-x", "2.0.0")
        profiles.save_store_atomic(
            {
                "schema_version": "1.0.0",
                "profiles": {
                    "stack": {
                        "overlays": [
                            {"path": stale_old, "cached_name": "overlay-x",
                             "cached_version": "1.0.0"},
                        ],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                },
            },
            self.store_path,
        )
        profiles.edit_profile(
            "stack", [new_ov], ["overlay-x"], self.base_cwd,
            store_path=self.store_path,
        )
        self.assertEqual(self._stored_paths("stack"), [new_ov])


class TestCliErrorFormatHonored(unittest.TestCase):
    """The read-only CLI must emit JSON on errors under --format json and keep
    the plain stderr line under --format text."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="s2_errfmt_")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home
        return subprocess.run(
            [sys.executable, profiles.__file__, *args],
            capture_output=True, text=True, env=env,
        )

    def test_inspect_missing_json_error_is_single_json_object(self):
        proc = self._run("--inspect", "nope", "--format", "json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["message"])

    def test_resolve_missing_json_error_is_single_json_object(self):
        proc = self._run("--resolve", "nope", "--format", "json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["message"])

    def test_inspect_missing_text_error_unchanged_on_stderr(self):
        proc = self._run("--inspect", "nope", "--format", "text")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip())
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
