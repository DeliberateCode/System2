"""``system2 codex init`` — single global user-scope enforcement install."""

import io
import json
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from system2_compiler import cli
from system2_compiler.backends import codex as codex_init

_REFERENCE = codex_init.user_hooks_reference()
_GUARDS = ("system2-shell-guard.js", "system2-edit-guard.js", "system2-budget.js")


def _run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CodexInitFreshInstall(unittest.TestCase):
    def test_renders_absolute_command_and_copies_guards(self):
        with tempfile.TemporaryDirectory() as home:
            result = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            self.assertEqual(result["status"], "installed")

            hooks_json = os.path.join(home, "hooks.json")
            hooks_dir = os.path.join(home, "system2", "hooks")
            self.assertTrue(os.path.isfile(hooks_json))
            for g in _GUARDS:
                self.assertTrue(os.path.isfile(os.path.join(hooks_dir, g)),
                                f"missing guard {g}")

            config = json.loads(open(hooks_json, encoding="utf-8").read())
            cmd = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            # the resolved command is an ABSOLUTE node path, no placeholder left.
            self.assertNotIn("{{SYSTEM2_HOOKS_DIR}}", json.dumps(config))
            self.assertTrue(cmd.startswith(f"node {hooks_dir}"), cmd)
            self.assertTrue(os.path.isabs(cmd.split(" ", 1)[1].rsplit("/", 1)[0]))

    def test_guard_bytes_match_committed_reference(self):
        with tempfile.TemporaryDirectory() as home:
            codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            for g in _GUARDS:
                ref = open(os.path.join(_REFERENCE, "hooks", g), "rb").read()
                got = open(os.path.join(home, "system2", "hooks", g), "rb").read()
                self.assertEqual(ref, got)

    def test_prints_hooks_trust_instruction(self):
        with tempfile.TemporaryDirectory() as home:
            code, out, _err = _run_cli(
                ["codex", "init", "--codex-home", home, "--reference", _REFERENCE]
            )
            self.assertEqual(code, 0)
            self.assertIn("/hooks", out)
            self.assertIn("advisory-only", out)


class CodexInitIdempotent(unittest.TestCase):
    def test_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home:
            r1 = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            first = open(r1["hooks_json"], encoding="utf-8").read()
            r2 = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            second = open(r2["hooks_json"], encoding="utf-8").read()
            self.assertEqual(first, second)
            self.assertEqual(r2["status"], "installed")
            # No spurious backup created on the second (System2-owned) run.
            self.assertIsNone(r2["backup_path"])


class CodexInitModifiedSinceWrite(unittest.TestCase):
    """Install state proves what System2 wrote, not ownership of later user edits."""

    def _install_then_customize(self, home):
        result = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
        hooks_json = result["hooks_json"]
        with open(hooks_json, "rb") as fh:
            fresh = fh.read()
        with open(hooks_json, encoding="utf-8") as fh:
            config = json.load(fh)
        config["hooks"]["MyCustomEvent"] = [{"command": "echo custom"}]
        with open(hooks_json, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
            fh.write("\n")
        with open(hooks_json, "rb") as fh:
            customized = fh.read()
        return hooks_json, fresh, customized

    def test_rerun_refuses_modified_owned_hooks_without_force(self):
        with tempfile.TemporaryDirectory() as home:
            hooks_json, _fresh, customized = self._install_then_customize(home)

            result = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE,
            )
            self.assertEqual(result["status"], "refused")
            with open(hooks_json, "rb") as fh:
                self.assertEqual(fh.read(), customized)

    def test_force_rerun_backs_up_modified_owned_hooks(self):
        with tempfile.TemporaryDirectory() as home:
            hooks_json, fresh, customized = self._install_then_customize(home)

            result = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE, force=True,
            )
            self.assertEqual(result["status"], "installed")
            self.assertTrue(result["backup_path"])
            with open(result["backup_path"], "rb") as fh:
                self.assertEqual(fh.read(), customized)
            with open(hooks_json, "rb") as fh:
                self.assertEqual(fh.read(), fresh)

    def test_legacy_state_without_digest_allows_untouched_rerun(self):
        with tempfile.TemporaryDirectory() as home:
            result = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE,
            )
            state_path = os.path.join(home, "system2", "system2-install.json")
            with open(state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            self.assertIn("hooks_json_sha256", state)
            del state["hooks_json_sha256"]
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
                fh.write("\n")

            rerun = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE,
            )
            self.assertEqual(rerun["status"], "installed")
            self.assertEqual(rerun["hooks_json"], result["hooks_json"])


class CodexInitPreexistingForeign(unittest.TestCase):
    def _seed_foreign(self, home):
        foreign = os.path.join(home, "hooks.json")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write('{"hooks": {"PreToolUse": [{"userAuthored": true}]}}\n')
        return foreign

    def test_refuses_without_force_and_does_not_clobber(self):
        with tempfile.TemporaryDirectory() as home:
            foreign = self._seed_foreign(home)
            before = open(foreign, encoding="utf-8").read()

            code, _out, err = _run_cli(
                ["codex", "init", "--codex-home", home, "--reference", _REFERENCE]
            )
            self.assertEqual(code, 3)
            self.assertIn("WARNING", err)
            # Untouched — no silent clobber.
            self.assertEqual(open(foreign, encoding="utf-8").read(), before)
            self.assertFalse(os.path.isdir(os.path.join(home, "system2")))

    def test_force_backs_up_then_installs(self):
        with tempfile.TemporaryDirectory() as home:
            foreign = self._seed_foreign(home)
            before = open(foreign, encoding="utf-8").read()

            result = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE, force=True,
            )
            self.assertEqual(result["status"], "installed")
            self.assertTrue(result["backup_path"])
            self.assertTrue(os.path.isfile(result["backup_path"]))
            self.assertEqual(open(result["backup_path"], encoding="utf-8").read(), before)
            # The live hooks.json is now the System2 config.
            self.assertIn("{{SYSTEM2_HOOKS_DIR}}",
                          open(os.path.join(_REFERENCE, "hooks.json.tmpl")).read())
            self.assertNotIn("userAuthored",
                             open(foreign, encoding="utf-8").read())


class CodexInitUninstall(unittest.TestCase):
    def test_uninstall_removes_only_system2_and_restores_backup(self):
        with tempfile.TemporaryDirectory() as home:
            foreign = os.path.join(home, "hooks.json")
            with open(foreign, "w", encoding="utf-8") as fh:
                fh.write('{"user": "hooks"}\n')
            before = open(foreign, encoding="utf-8").read()

            codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE, force=True)
            self.assertTrue(os.path.isdir(os.path.join(home, "system2", "hooks")))

            result = codex_init.codex_uninstall(codex_home=home)
            self.assertEqual(result["status"], "uninstalled")
            # Backup restored to the original user file.
            self.assertEqual(open(foreign, encoding="utf-8").read(), before)
            # System2 guard dir gone.
            self.assertFalse(os.path.isdir(os.path.join(home, "system2", "hooks")))

    def test_uninstall_fresh_install_removes_hooks_json(self):
        with tempfile.TemporaryDirectory() as home:
            codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            hooks_json = os.path.join(home, "hooks.json")
            self.assertTrue(os.path.isfile(hooks_json))

            result = codex_init.codex_uninstall(codex_home=home)
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse(os.path.isfile(hooks_json))
            self.assertIsNone(result["restored_backup"])

    def test_uninstall_refuses_hooks_json_modified_since_install(self):
        """A customized global config must not retain references to deleted hooks."""
        with tempfile.TemporaryDirectory() as home:
            installed = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE,
            )
            hooks_json = installed["hooks_json"]
            state_path = os.path.join(home, "system2", "system2-install.json")
            with open(hooks_json, encoding="utf-8") as fh:
                config = json.load(fh)
            config["hooks"]["MyCustomEvent"] = [{"command": "echo custom"}]
            with open(hooks_json, "w", encoding="utf-8") as fh:
                json.dump(config, fh, indent=2)
                fh.write("\n")
            with open(hooks_json, "rb") as fh:
                customized = fh.read()

            result = codex_init.codex_uninstall(codex_home=home)
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["removed"], [])
            self.assertIs(result["hooks_json_removed"], False)
            self.assertTrue(os.path.isfile(state_path))
            self.assertTrue(all(os.path.isfile(path) for path in installed["hook_files"]))
            with open(hooks_json, "rb") as fh:
                self.assertEqual(fh.read(), customized)

    def test_uninstall_noop_when_nothing_installed(self):
        with tempfile.TemporaryDirectory() as home:
            result = codex_init.codex_uninstall(codex_home=home)
            self.assertEqual(result["status"], "nothing")


class CodexInitSeam(unittest.TestCase):
    def test_env_codex_home_seam(self):
        with tempfile.TemporaryDirectory() as home:
            old = os.environ.get("CODEX_HOME")
            os.environ["CODEX_HOME"] = home
            try:
                resolved = codex_init.resolve_codex_home()
            finally:
                if old is None:
                    os.environ.pop("CODEX_HOME", None)
                else:
                    os.environ["CODEX_HOME"] = old
            self.assertEqual(resolved, os.path.abspath(home))

    def test_reference_is_not_modified(self):
        before = {
            p: open(os.path.join(_REFERENCE, p), "rb").read()
            for p in ("hooks.json.tmpl",) + tuple(os.path.join("hooks", g) for g in _GUARDS)
        }
        with tempfile.TemporaryDirectory() as home:
            codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE, force=False)
        for p, content in before.items():
            self.assertEqual(open(os.path.join(_REFERENCE, p), "rb").read(), content)


class CodexInitEscapeSafe(unittest.TestCase):
    """S-Esc: a home path with a space/quote must yield VALID JSON + a correct
    ``node <path>`` invocation (never a broken string-splice)."""

    def test_spaced_and_quoted_home_roundtrips(self):
        with tempfile.TemporaryDirectory() as base:
            home = os.path.join(base, "co dex ho'me")  # space AND single quote
            os.makedirs(home)
            result = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            self.assertEqual(result["status"], "installed")

            hooks_json = os.path.join(home, "hooks.json")
            # Parses as valid JSON despite the space/quote in the resolved path.
            config = json.loads(open(hooks_json, encoding="utf-8").read())
            self.assertNotIn("{{SYSTEM2_HOOKS_DIR}}", json.dumps(config))

            cmd = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            # The command is `node <shell-quoted-abs-path>`; it round-trips via
            # shlex.split back to the exact on-disk guard path.
            argv = shlex.split(cmd)
            self.assertEqual(argv[0], "node")
            expected = os.path.join(home, "system2", "hooks", "system2-shell-guard.js")
            self.assertEqual(argv[1], expected)
            self.assertTrue(os.path.isfile(argv[1]), argv[1])


class CodexInitRecovery(unittest.TestCase):
    """Test recovery from incomplete or corrupted installs."""

    def _bak_files(self, home):
        return [f for f in os.listdir(home)
                if f.startswith("hooks.json.") and f.endswith(".bak")]

    def test_missing_state_recognized_as_system2_owned(self):
        with tempfile.TemporaryDirectory() as home:
            codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            os.unlink(os.path.join(home, "system2", "system2-install.json"))
            # Re-run WITHOUT --force must NOT refuse or back up (still owned).
            result = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            self.assertEqual(result["status"], "installed")
            self.assertFalse(result["preexisting_foreign"])
            self.assertIsNone(result["backup_path"])
            self.assertEqual(self._bak_files(home), [])

    def test_corrupt_state_recognized_as_system2_owned(self):
        with tempfile.TemporaryDirectory() as home:
            codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            with open(os.path.join(home, "system2", "system2-install.json"), "w") as fh:
                fh.write("{ this is not valid json")
            result = codex_init.codex_init(codex_home=home, reference_dir=_REFERENCE)
            self.assertEqual(result["status"], "installed")
            self.assertFalse(result["preexisting_foreign"])
            self.assertEqual(self._bak_files(home), [])


class CodexInitSymlinkGuard(unittest.TestCase):
    """never write THROUGH a symlinked hooks.json."""

    def test_force_over_symlink_does_not_write_through(self):
        with tempfile.TemporaryDirectory() as home:
            target = os.path.join(home, "real-user-hooks.json")
            with open(target, "w") as fh:
                fh.write('{"user":"authored"}\n')
            link = os.path.join(home, "hooks.json")
            os.symlink(target, link)

            result = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE, force=True,
            )
            self.assertEqual(result["status"], "installed")
            # The symlink was replaced by a real file; the target was NOT written through.
            self.assertFalse(os.path.islink(link))
            self.assertEqual(open(target).read(), '{"user":"authored"}\n')
            self.assertIn("system2", open(link).read())


class CodexInitDryRunWritesNothing(unittest.TestCase):
    """S-Tests: --dry-run must create nothing under the codex home."""

    def test_api_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            result = codex_init.codex_init(
                codex_home=home, reference_dir=_REFERENCE, dry_run=True,
            )
            self.assertEqual(result["status"], "dry_run")
            self.assertEqual(os.listdir(home), [])

    def test_cli_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            code, _out, _err = _run_cli([
                "codex", "init", "--codex-home", home,
                "--reference", _REFERENCE, "--dry-run",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(os.listdir(home), [])


class CodexInitBadReference(unittest.TestCase):
    """a missing/invalid --reference is a clean CLI error, not a traceback."""

    def test_missing_reference_is_clean_cli_error(self):
        with tempfile.TemporaryDirectory() as home:
            bad = os.path.join(home, "does-not-exist")
            code, _out, err = _run_cli([
                "codex", "init", "--codex-home", home, "--reference", bad,
            ])
            self.assertNotEqual(code, 0)
            self.assertIn("error", err.lower())
            self.assertNotIn("Traceback", err)
            self.assertEqual(os.listdir(home), [])


class CodexUninstallHardening(unittest.TestCase):
    """uninstall refuses path-traversal hook_files and out-of-home backups."""

    def _write_state(self, home, hook_files, backup_path):
        state_dir = os.path.join(home, "system2")
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "system2-install.json"), "w") as fh:
            json.dump({"owned": True, "hooks_json": os.path.join(home, "hooks.json"),
                       "hook_files": hook_files, "backup_path": backup_path}, fh)

    def test_traversal_hook_file_entries_are_skipped(self):
        with tempfile.TemporaryDirectory() as home:
            outside = os.path.join(home, "outside-secret")
            with open(outside, "w") as fh:
                fh.write("do not touch\n")
            # A tampered state pointing hook_files up-and-out of hooks_dir.
            self._write_state(home, ["../../outside-secret", "system2-shell-guard.js"],
                              backup_path=None)
            result = codex_init.codex_uninstall(codex_home=home)
            self.assertEqual(result["status"], "uninstalled")
            # The traversal target was NOT removed.
            self.assertTrue(os.path.isfile(outside))
            self.assertNotIn(outside, result["removed"])

    def test_out_of_home_backup_is_not_restored(self):
        with tempfile.TemporaryDirectory() as base:
            home = os.path.join(base, "home")
            os.makedirs(home)
            # A backup path OUTSIDE codex_home must never be moved onto hooks.json.
            evil_backup = os.path.join(base, "evil.bak")
            with open(evil_backup, "w") as fh:
                fh.write('{"evil":"payload"}\n')
            # codex_uninstall now content-signature-checks
            with open(os.path.join(home, "hooks.json"), "w") as fh:
                fh.write('{"hooks":{"PreToolUse":[{"hooks":[{"command":'
                          '"node /home/.codex/system2/hooks/system2-shell-guard.js"}]}]}}\n')
            self._write_state(home, ["system2-shell-guard.js"], backup_path=evil_backup)

            result = codex_init.codex_uninstall(codex_home=home)
            self.assertEqual(result["status"], "uninstalled")
            self.assertIsNone(result["restored_backup"])
            # The out-of-home backup was left in place, not moved into the home.
            self.assertTrue(os.path.isfile(evil_backup))
            self.assertFalse(os.path.isfile(os.path.join(home, "hooks.json")))


if __name__ == "__main__":
    unittest.main()
