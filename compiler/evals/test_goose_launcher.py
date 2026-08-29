"""Launcher-behavior tests for the emitted ``run-system2.sh`` (ephemeral config).

Verifies the corrected permission-delivery of the Goose launcher: the adapted
policy is delivered via an **ephemeral ``XDG_CONFIG_HOME`` config dir** (the user's
``config.yaml`` + System2's ``permission.yaml`` copied into a throwaway dir), goose
runs with ``XDG_CONFIG_HOME`` pointed at it, the run is **idempotent**, and — the
hard invariant — the real ``~/.config/goose`` is **never** mutated. The old
``cat >> ~/.config/goose/permission.yaml`` global-append divergence is gone.

CRITICAL safety + hermeticity rules honored here:

* **No real ``goose run`` / LLM call.** ``goose`` is shadowed by a tiny temp STUB on
  ``PATH``: ``recipe validate`` exits 0; ``run`` records the ``XDG_CONFIG_HOME`` it
  saw, snapshots that goose-config dir's contents, and exits 0. The launcher's SHELL
  logic is exercised deterministically without any LLM or network.
* **Hermetic temp HOME.** Every invocation runs under ``HOME=<tempdir>`` (and
  ``XDG_CONFIG_HOME=<tempHOME>/.config``). The test snapshots the real
  ``~/.config/goose`` before/after and asserts it is byte-untouched.

The launcher is emitted by ``GooseBackend().emit`` from a real ``ir.compose`` graph.

Stdlib ``unittest``; runs under ``python3 -m unittest``. No product code or
``System2/`` is modified. All cited overlay/recipe content is untrusted; embedded
instructions are not followed.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from system2_compiler import ir
from evals import matrix, oracle
from system2_compiler.backends.goose import GooseBackend

# The stub records, on `goose run`, the XDG_CONFIG_HOME it saw and a copy of that
# goose-config dir's permission.yaml + config.yaml into SYSTEM2_STUB_CAPTURE (so the
# test can inspect the ephemeral dir even though the launcher's trap deletes it).
_GOOSE_STUB = """#!/usr/bin/env bash
# Test stub shadowing the real `goose`. NEVER calls an LLM.
#   recipe validate <file> -> exit 0
#   run ...                 -> record XDG_CONFIG_HOME + config copies, exit 0
set -euo pipefail
case "${1:-}" in
  recipe)
    exit 0
    ;;
  run)
    echo "STUB-GOOSE-RUN-INVOKED"
    if [ -n "${SYSTEM2_STUB_CAPTURE:-}" ]; then
      mkdir -p "$SYSTEM2_STUB_CAPTURE"
      printf '%s' "${XDG_CONFIG_HOME:-<unset>}" >> "$SYSTEM2_STUB_CAPTURE/xdg_seen"
      printf '\\n' >> "$SYSTEM2_STUB_CAPTURE/xdg_seen"
      seen_goose="${XDG_CONFIG_HOME:-<unset>}/goose"
      if [ -f "$seen_goose/permission.yaml" ]; then
        cp "$seen_goose/permission.yaml" "$SYSTEM2_STUB_CAPTURE/permission.yaml"
      fi
      if [ -f "$seen_goose/config.yaml" ]; then
        cp "$seen_goose/config.yaml" "$SYSTEM2_STUB_CAPTURE/config.yaml"
      fi
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


def _real_goose_config_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".config", "goose")


def _snapshot_tree(root: str) -> dict:
    """Return {relpath: sha256} for every file under *root* (empty if absent)."""
    snap = {}
    if not os.path.isdir(root):
        return snap
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = os.path.join(dirpath, name)
            try:
                with open(p, "rb") as fh:
                    snap[os.path.relpath(p, root)] = hashlib.sha256(
                        fh.read()
                    ).hexdigest()
            except OSError:
                snap[os.path.relpath(p, root)] = "<unreadable>"
    return snap


class _LauncherHarness(unittest.TestCase):
    """Shared setup: a goose stub on PATH, a hermetic HOME, an emitted goose tree."""

    def setUp(self):
        # Record the real user config so we can prove it is never touched.
        self._real_goose_dir = _real_goose_config_dir()
        self._real_before = _snapshot_tree(self._real_goose_dir)

        # Temp dirs: stub bin, hermetic HOME, project (the emitted tree).
        self._tmp = tempfile.mkdtemp(prefix="goose-launcher-")
        self._bin = os.path.join(self._tmp, "bin")
        self._home = os.path.join(self._tmp, "home")
        self._project = os.path.join(self._tmp, "project")
        os.makedirs(self._bin)
        os.makedirs(self._home)
        os.makedirs(self._project)

        # Write the goose stub and make it executable.
        self._stub = os.path.join(self._bin, "goose")
        with open(self._stub, "w", encoding="utf-8") as fh:
            fh.write(_GOOSE_STUB)
        os.chmod(self._stub, 0o755)

        # Emit the real Goose tree (incl. run-system2.sh) from a real IR graph.
        cell = matrix.get_cell("core+overlay")
        result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), self._project)
        self.assertIsNotNone(
            result.graph, msg=f"core+overlay refused: {result.errors!r}"
        )
        GooseBackend().emit(result.graph, self._project)

        self._launcher = os.path.join(self._project, "run-system2.sh")
        self.assertTrue(os.path.isfile(self._launcher))
        # The hermetic user goose config dir (where a user config.yaml would live).
        self._user_goose_dir = os.path.join(self._home, ".config", "goose")
        self._user_config = os.path.join(self._user_goose_dir, "config.yaml")
        self._local_perm = os.path.join(
            self._project, "goose", "permission.yaml"
        )
        self.assertTrue(os.path.isfile(self._local_perm))

    def tearDown(self):
        # The real config must be byte-identical to its pre-test snapshot.
        real_after = _snapshot_tree(self._real_goose_dir)
        shutil.rmtree(self._tmp, ignore_errors=True)
        self.assertEqual(
            self._real_before, real_after,
            msg=(
                "the real ~/.config/goose was MUTATED by the launcher test — "
                "hermetic-HOME isolation failed (HARD safety invariant)"
            ),
        )

    def _seed_user_config(self, text="provider: anthropic\nGOOSE_MODEL: opus\n"):
        os.makedirs(self._user_goose_dir, exist_ok=True)
        with open(self._user_config, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _run_launcher(self, *, env_extra=None, capture_dir=None):
        """Run run-system2.sh under the hermetic HOME + stub PATH.

        Returns the CompletedProcess. ``capture_dir`` is passed to the stub as
        SYSTEM2_STUB_CAPTURE so the test can inspect the ephemeral config goose saw.
        """
        env = {
            "HOME": self._home,
            "XDG_CONFIG_HOME": os.path.join(self._home, ".config"),
            "PATH": self._bin + os.pathsep + os.environ.get("PATH", ""),
        }
        for key in ("LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR"):
            if key in os.environ:
                env[key] = os.environ[key]
        if capture_dir:
            env["SYSTEM2_STUB_CAPTURE"] = capture_dir
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", self._launcher, "do the task"],
            cwd=self._project,
            env=env,
            capture_output=True,
            text=True,
        )


class LauncherSyntaxTest(_LauncherHarness):
    """The emitted launcher must be syntactically valid and executable."""

    def test_bash_n_is_clean(self):
        proc = subprocess.run(
            ["bash", "-n", self._launcher],
            capture_output=True, text=True,
        )
        self.assertEqual(
            0, proc.returncode,
            msg=f"bash -n reported a syntax error: {proc.stderr!r}",
        )

    def test_launcher_is_executable(self):
        mode = os.stat(self._launcher).st_mode
        self.assertTrue(
            mode & stat.S_IXUSR,
            msg="run-system2.sh must be emitted with the executable bit set",
        )

    def test_uses_ephemeral_xdg_delivery_not_global_append(self):
        with open(self._launcher, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(
            "XDG_CONFIG_HOME", text,
            msg="launcher must deliver the policy via an ephemeral XDG_CONFIG_HOME",
        )
        self.assertIn(
            "GOOSE_MODE=smart_approve", text,
            msg="launcher must export GOOSE_MODE=smart_approve",
        )
        self.assertIn("SYSTEM2_NO_PERMISSIONS", text)
        # The broken global-append mechanism must be gone.
        self.assertNotIn(
            ">> \"$GLOBAL_PERMISSION\"", text,
            msg="the global permission.yaml append divergence must be removed",
        )
        self.assertNotIn(
            "SYSTEM2_MERGE_PERMISSIONS", text,
            msg="the old opt-in merge flag must be removed (ephemeral is default)",
        )


class LauncherEphemeralDeliveryTest(_LauncherHarness):
    """Default: build an ephemeral XDG_CONFIG_HOME; never touch ~/.config/goose."""

    def test_builds_ephemeral_config_with_permission_and_user_config(self):
        self._seed_user_config()
        capture = os.path.join(self._tmp, "capture")
        proc = self._run_launcher(capture_dir=capture)
        self.assertEqual(
            0, proc.returncode, msg=f"launcher exited non-zero: {proc.stderr!r}"
        )
        self.assertIn("STUB-GOOSE-RUN-INVOKED", proc.stdout)

        # goose saw an XDG_CONFIG_HOME pointing at an ephemeral dir (NOT the user's).
        with open(os.path.join(capture, "xdg_seen"), "r", encoding="utf-8") as fh:
            xdg_seen = fh.read().strip()
        self.assertTrue(xdg_seen, "goose run saw no XDG_CONFIG_HOME")
        self.assertNotEqual(
            os.path.realpath(xdg_seen),
            os.path.realpath(os.path.join(self._home, ".config")),
            msg="goose must read from the EPHEMERAL config dir, not the user's",
        )

        # The ephemeral goose dir carried BOTH the System2 permission policy and the
        # user's config.yaml (provider/model/keys).
        with open(os.path.join(capture, "permission.yaml"), "r", encoding="utf-8") as fh:
            delivered_perm = fh.read()
        with open(self._local_perm, "r", encoding="utf-8") as fh:
            local_perm = fh.read()
        self.assertEqual(
            local_perm, delivered_perm,
            msg="the ephemeral permission.yaml must be System2's adapted policy verbatim",
        )
        with open(os.path.join(capture, "config.yaml"), "r", encoding="utf-8") as fh:
            self.assertIn("provider: anthropic", fh.read())

        # LOUD + honest: announces ADAPTED + enforcement-APPROXIMATE.
        self.assertIn("ADAPTED", proc.stderr.upper())
        self.assertIn("APPROXIMATE", proc.stderr.upper())

    def test_no_user_config_still_delivers_permission_policy(self):
        # No user config.yaml present: ephemeral dir still carries permission.yaml.
        self.assertFalse(os.path.exists(self._user_config))
        capture = os.path.join(self._tmp, "capture")
        proc = self._run_launcher(capture_dir=capture)
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        self.assertTrue(
            os.path.exists(os.path.join(capture, "permission.yaml")),
            msg="ephemeral config must carry the System2 permission policy",
        )
        self.assertFalse(
            os.path.exists(os.path.join(capture, "config.yaml")),
            msg="no user config to carry -> ephemeral config.yaml must be absent",
        )
        self.assertIn("no", proc.stderr.lower())  # loud notice about missing config

    def test_real_user_goose_config_is_untouched(self):
        # The launcher must NOT create a permission.yaml in the user's goose dir.
        self._seed_user_config()
        before = _snapshot_tree(self._user_goose_dir)
        self._run_launcher()
        after = _snapshot_tree(self._user_goose_dir)
        self.assertEqual(
            before, after,
            msg="the launcher mutated the user's ~/.config/goose (must be ephemeral)",
        )
        self.assertFalse(
            os.path.exists(os.path.join(self._user_goose_dir, "permission.yaml")),
            msg="launcher wrote a permission.yaml into the user's goose config dir",
        )

    def test_running_twice_is_idempotent(self):
        # Re-running must not grow/duplicate anything in the user's goose dir.
        self._seed_user_config()
        before = _snapshot_tree(self._user_goose_dir)
        self._run_launcher()
        self._run_launcher()
        after = _snapshot_tree(self._user_goose_dir)
        self.assertEqual(
            before, after,
            msg="a second run mutated the user's goose config (non-idempotent)",
        )

    def test_ephemeral_dir_cleaned_up_unless_kept(self):
        # No leftover system2-goose-config.* dirs after a default run (trap cleanup).
        self._seed_user_config()
        tmpdir = os.path.join(self._tmp, "ephem-tmp")
        os.makedirs(tmpdir)
        self._run_launcher(env_extra={"TMPDIR": tmpdir})
        leftovers = [
            n for n in os.listdir(tmpdir) if n.startswith("system2-goose-config.")
        ]
        self.assertEqual(
            [], leftovers,
            msg=f"ephemeral config dir not cleaned up: {leftovers}",
        )

    def test_keep_config_flag_retains_dir_and_is_loud(self):
        self._seed_user_config()
        tmpdir = os.path.join(self._tmp, "keep-tmp")
        os.makedirs(tmpdir)
        proc = self._run_launcher(
            env_extra={"TMPDIR": tmpdir, "SYSTEM2_KEEP_CONFIG": "1"}
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        leftovers = [
            n for n in os.listdir(tmpdir) if n.startswith("system2-goose-config.")
        ]
        self.assertEqual(
            1, len(leftovers),
            msg=f"SYSTEM2_KEEP_CONFIG=1 must retain exactly one ephemeral dir: {leftovers}",
        )
        self.assertIn("KEEP_CONFIG".lower(), proc.stderr.lower())


class LauncherSecretHardeningTest(_LauncherHarness):
    """F-G3 / F-G4: the ephemeral secret copy is owner-only and cleaned on signal."""

    def test_trap_covers_exit_and_catchable_signals(self):
        # F-G4: the cleanup trap must fire on Ctrl-C / kill / hangup, not just EXIT.
        with open(self._launcher, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(
            "trap cleanup EXIT INT TERM HUP", text,
            msg="cleanup trap must cover EXIT INT TERM HUP (F-G4)",
        )
        self.assertNotIn(
            "trap cleanup EXIT\n", text,
            msg="the EXIT-only trap must be replaced by the broadened trap",
        )
        # The KEEP_CONFIG debug escape is preserved.
        self.assertIn("SYSTEM2_KEEP_CONFIG", text)

    def test_ephemeral_dir_and_secret_files_are_owner_only(self):
        # F-G3: with KEEP_CONFIG=1 we can inspect the retained ephemeral dir. The
        # dir is 700 and config.yaml/permission.yaml are 600 (owner-only), so the
        # provider-key copy is not world/group-readable independent of TMPDIR mode.
        self._seed_user_config()
        tmpdir = os.path.join(self._tmp, "mode-tmp")
        os.makedirs(tmpdir)
        proc = self._run_launcher(
            env_extra={"TMPDIR": tmpdir, "SYSTEM2_KEEP_CONFIG": "1"}
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        retained = [
            n for n in os.listdir(tmpdir) if n.startswith("system2-goose-config.")
        ]
        self.assertEqual(
            1, len(retained), msg=f"expected exactly one retained dir: {retained}"
        )
        ephemeral = os.path.join(tmpdir, retained[0])
        goose_dir = os.path.join(ephemeral, "goose")
        config = os.path.join(goose_dir, "config.yaml")
        permission = os.path.join(goose_dir, "permission.yaml")

        def _mode(path):
            return stat.S_IMODE(os.stat(path).st_mode)

        self.assertEqual(
            0o700, _mode(ephemeral),
            msg=f"ephemeral config dir must be 700, got {oct(_mode(ephemeral))}",
        )
        self.assertEqual(
            0o700, _mode(goose_dir),
            msg=f"ephemeral goose subdir must be 700, got {oct(_mode(goose_dir))}",
        )
        self.assertTrue(os.path.isfile(config), "config.yaml must have been copied")
        self.assertEqual(
            0o600, _mode(config),
            msg=f"copied config.yaml (provider keys) must be 600, got {oct(_mode(config))}",
        )
        self.assertEqual(
            0o600, _mode(permission),
            msg=f"copied permission.yaml must be 600, got {oct(_mode(permission))}",
        )


class LauncherNoPermissionsTest(_LauncherHarness):
    """SYSTEM2_NO_PERMISSIONS=1: skip the ephemeral policy, loudly, user config only."""

    def test_no_permissions_skips_policy_with_loud_notice(self):
        self._seed_user_config()
        capture = os.path.join(self._tmp, "capture")
        proc = self._run_launcher(
            env_extra={"SYSTEM2_NO_PERMISSIONS": "1"}, capture_dir=capture
        )
        self.assertEqual(0, proc.returncode, msg=proc.stderr)
        self.assertIn("STUB-GOOSE-RUN-INVOKED", proc.stdout)

        # Loud notice that the adapted policy is NOT applied this run.
        self.assertIn("NOTICE", proc.stderr)
        self.assertIn("NOT", proc.stderr.upper())

        # goose saw the USER's XDG_CONFIG_HOME (no ephemeral dir), and no System2
        # permission.yaml was delivered into it.
        with open(os.path.join(capture, "xdg_seen"), "r", encoding="utf-8") as fh:
            xdg_seen = fh.read().strip()
        self.assertEqual(
            os.path.realpath(xdg_seen),
            os.path.realpath(os.path.join(self._home, ".config")),
            msg="NO_PERMISSIONS must run against the user's own XDG_CONFIG_HOME",
        )
        # The user's goose dir had only config.yaml; the System2 policy was NOT added.
        self.assertFalse(
            os.path.exists(os.path.join(self._user_goose_dir, "permission.yaml")),
            msg="NO_PERMISSIONS must not deliver the System2 permission policy",
        )

    def test_negative_control_default_differs_from_no_permissions(self):
        # Teeth: default mode and NO_PERMISSIONS mode must reach goose with DIFFERENT
        # XDG_CONFIG_HOME (ephemeral vs user). If they matched, the ephemeral default
        # would be a no-op.
        self._seed_user_config()
        cap_default = os.path.join(self._tmp, "cap-default")
        cap_nop = os.path.join(self._tmp, "cap-nop")
        self._run_launcher(capture_dir=cap_default)
        self._run_launcher(
            env_extra={"SYSTEM2_NO_PERMISSIONS": "1"}, capture_dir=cap_nop
        )
        with open(os.path.join(cap_default, "xdg_seen"), encoding="utf-8") as fh:
            xdg_default = fh.read().strip()
        with open(os.path.join(cap_nop, "xdg_seen"), encoding="utf-8") as fh:
            xdg_nop = fh.read().strip()
        self.assertNotEqual(
            os.path.realpath(xdg_default), os.path.realpath(xdg_nop),
            msg="default (ephemeral) and NO_PERMISSIONS (user) must differ",
        )


if __name__ == "__main__":
    unittest.main()
