"""CLI-contract goldens: the compiler ``system2`` CLI vs the FROZEN oracle."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# The dry-run preview embeds a live UTC ``<!-- Composed at: … -->`` timestamp (no prior lock exists to reuse it), the one genuinely non-deterministic byte in the CLI contract.
_COMPOSED_AT_RE = re.compile(r"<!-- Composed at: [0-9TZ:-]+ -->")

# The reused ``test-overlay`` source path is an absolute fixture path that varies by checkout location (the frozen golden baked the original author's path).
_OVERLAY_PATH_RE = re.compile(r"[^\s\"]*/evals/fixtures/test-overlay")
_OVERLAY_TOKEN = "<OVERLAY>"

from evals import oracle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_ROOT = os.path.dirname(_THIS_DIR)
CONTRACT_DIR = os.path.join(_THIS_DIR, "cli_contract")

# Portable plugin/fixture resolution (sibling layout or SYSTEM2_PLUGIN_ROOT).
BASE = oracle.PLUGIN_ROOT
TEST_OVERLAY = os.path.join(
    oracle.PLUGIN_REPO_ROOT, "evals", "fixtures", "test-overlay"
)
ANCHORFILE = os.path.join(_THIS_DIR, "fixtures", "anchorfile")
CONFLICT_A = os.path.join(_THIS_DIR, "fixtures", "conflict-a")
CONFLICT_B = os.path.join(_THIS_DIR, "fixtures", "conflict-b")

# Placeholders the harness substitutes per-cell so a cell's argv/expectations are
# independent of the (volatile) temp dirs created at run time.
_PROJ = "@PROJECT@"
_HOME = "@HOME@"


def _hermetic_env(home_dir):
    env = {"HOME": home_dir}
    for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


def _normalize(text, project_dir, home_dir):
    """Replace the volatile temp-project / temp-HOME prefixes with stable tokens."""
    out = text.replace(project_dir, "<PROJECT>").replace(home_dir, "<HOME>")
    out = _COMPOSED_AT_RE.sub("<!-- Composed at: <TS> -->", out)
    out = _OVERLAY_PATH_RE.sub(_OVERLAY_TOKEN, out)
    return out


class _Cell:
    """A single CLI-contract matrix cell."""

    def __init__(self, name, oracle_argv, compiler_argv, setup=None, kind="compose"):
        self.name = name
        self.oracle_argv = oracle_argv
        self.compiler_argv = compiler_argv
        self.setup = setup or []
        self.kind = kind


def _cells():
    OVL = TEST_OVERLAY
    AF = ANCHORFILE
    return [
        _Cell(
            "compose_text",
            ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "text"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--overlays", OVL, "--format", "text"],
        ),
        _Cell(
            "compose_json",
            ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--overlays", OVL, "--format", "json"],
        ),
        _Cell(
            "compose_dry_run_text",
            ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--dry-run", "--format", "text"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--overlays", OVL, "--dry-run", "--format", "text"],
        ),
        _Cell(
            "compose_conflict_refusal_json",
            ["--base", BASE, "--project", _PROJ, "--overlays", f"{CONFLICT_A},{CONFLICT_B}", "--format", "json"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--overlays", f"{CONFLICT_A},{CONFLICT_B}", "--format", "json"],
            kind="refusal",
        ),
        _Cell(
            "compose_missing_overlays_refusal",
            ["--base", BASE, "--project", _PROJ, "--format", "text"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ, "--format", "text"],
            kind="refusal",
        ),
        _Cell(
            "doctor_composed_text",
            ["--base", BASE, "--project", _PROJ, "--doctor", "--format", "text"],
            ["doctor", "--target", "claude-code", "--base", BASE, "--project", _PROJ, "--format", "text"],
            setup=[("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"])],
            kind="doctor",
        ),
        _Cell(
            "doctor_no_lock_json",
            ["--base", BASE, "--project", _PROJ, "--doctor", "--format", "json"],
            ["doctor", "--target", "claude-code", "--base", BASE, "--project", _PROJ, "--format", "json"],
            kind="doctor",
        ),
        _Cell(
            "doctor_stale_base_text",
            ["--base", BASE, "--project", _PROJ, "--doctor", "--format", "text"],
            ["doctor", "--target", "claude-code", "--base", BASE, "--project", _PROJ, "--format", "text"],
            setup=[
                ("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"]),
                ("mutate_lock_version", []),
            ],
            kind="doctor",
        ),
        _Cell(
            "uninstall_last_json",
            ["--base", BASE, "--project", _PROJ, "--uninstall", "test-overlay", "--format", "json"],
            ["uninstall", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--name", "test-overlay", "--format", "json"],
            setup=[("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"])],
            kind="uninstall",
        ),
        _Cell(
            "uninstall_one_of_n_json",
            ["--base", BASE, "--project", _PROJ, "--uninstall", "anchorfile", "--format", "json"],
            ["uninstall", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--name", "anchorfile", "--format", "json"],
            setup=[("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", f"{OVL},{AF}", "--format", "json"])],
            kind="uninstall",
        ),
        _Cell(
            "uninstall_not_installed_text",
            ["--base", BASE, "--project", _PROJ, "--uninstall", "no-such-overlay", "--format", "text"],
            ["uninstall", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--name", "no-such-overlay", "--format", "text"],
            setup=[("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"])],
            kind="uninstall",
        ),
        _Cell(
            "uninstall_no_lock_json",
            ["--base", BASE, "--project", _PROJ, "--uninstall", "test-overlay", "--format", "json"],
            ["uninstall", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--name", "test-overlay", "--format", "json"],
            kind="uninstall",
        ),
        _Cell(
            "uninstall_dry_run_text",
            ["--base", BASE, "--project", _PROJ, "--uninstall", "anchorfile", "--dry-run", "--format", "text"],
            ["uninstall", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--name", "anchorfile", "--dry-run", "--format", "text"],
            setup=[("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", f"{OVL},{AF}", "--format", "json"])],
            kind="uninstall",
        ),
        _Cell(
            "from_lock_recompose_json",
            ["--base", BASE, "--project", _PROJ, "--from-lock", "--format", "json"],
            ["from-lock", "--target", "claude-code", "--base", BASE, "--project", _PROJ, "--format", "json"],
            setup=[("oracle", ["--base", BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"])],
            kind="from-lock",
        ),
        _Cell(
            "from_lock_missing_text",
            ["--base", BASE, "--project", _PROJ, "--from-lock", "--format", "text"],
            ["from-lock", "--target", "claude-code", "--base", BASE, "--project", _PROJ, "--format", "text"],
            kind="from-lock",
        ),
        _Cell(
            "profile_create_json",
            ["--base", BASE, "--project", _PROJ, "--profile-op", "create",
             "--profile-name", "cell-prof", "--profile-paths", OVL, "--format", "json"],
            ["profile", "create", "cell-prof", "--paths", OVL, "--format", "json"],
            kind="profile",
        ),
        _Cell(
            "profile_dry_run_rejected_text",
            ["--base", BASE, "--project", _PROJ, "--profile-op", "delete",
             "--profile-name", "x", "--dry-run", "--format", "text"],
            ["profile", "delete", "x", "--dry-run", "--format", "text"],
            kind="profile",
        ),
    ]


def _run_oracle_setup(step, project_dir, env):
    name, argv = step
    if name == "mutate_lock_version":
        lp = os.path.join(project_dir, "spec", "overlay-manifest.lock")
        with open(lp, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        data["system2_version"] = "0.0.0-stale"
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        return
    resolved = [project_dir if a == _PROJ else a for a in argv]
    subprocess.run(
        [sys.executable, oracle.COMPOSER_PATH] + resolved,
        capture_output=True, text=True, env=env, cwd=os.path.dirname(oracle.COMPOSER_PATH),
    )


def _capture_oracle(cell):
    """Run a cell against the frozen oracle; return (stdout, stderr, exit_code)."""
    home = tempfile.mkdtemp(prefix="clic-ohome-")
    project = tempfile.mkdtemp(prefix="clic-oproj-")
    env = _hermetic_env(home)
    try:
        for step in cell.setup:
            _run_oracle_setup(step, project, env)
        argv = [project if a == _PROJ else (home if a == _HOME else a) for a in cell.oracle_argv]
        completed = subprocess.run(
            [sys.executable, oracle.COMPOSER_PATH] + argv,
            capture_output=True, text=True, env=env,
            cwd=os.path.dirname(oracle.COMPOSER_PATH),
        )
        return (
            _normalize(completed.stdout, project, home),
            _normalize(completed.stderr, project, home),
            completed.returncode,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def _capture_compiler(cell):
    """Run a cell against the compiler CLI (subprocess for hermetic HOME)."""
    home = tempfile.mkdtemp(prefix="clic-chome-")
    project = tempfile.mkdtemp(prefix="clic-cproj-")
    env = _hermetic_env(home)
    try:
        # Seed the project with the oracle before invoking the compiler CLI.
        for step in cell.setup:
            _run_oracle_setup(step, project, env)
        argv = [project if a == _PROJ else (home if a == _HOME else a) for a in cell.compiler_argv]
        completed = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['system2']+sys.argv[1:]; "
             "from system2_compiler import cli; raise SystemExit(cli.main())"] + argv,
            capture_output=True, text=True, env=env, cwd=_PKG_ROOT,
        )
        return (
            _normalize(completed.stdout, project, home),
            _normalize(completed.stderr, project, home),
            completed.returncode,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def _cell_dir(cell):
    return os.path.join(CONTRACT_DIR, cell.name)


def capture_all():
    """Materialize the frozen-oracle goldens into ``cli_contract/<cell>/`` (capture-only)."""
    oracle.verify_pin()
    os.makedirs(CONTRACT_DIR, exist_ok=True)
    for cell in _cells():
        stdout, stderr, code = _capture_oracle(cell)
        d = _cell_dir(cell)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "stdout.txt"), "w", encoding="utf-8") as fh:
            fh.write(stdout)
        with open(os.path.join(d, "stderr.txt"), "w", encoding="utf-8") as fh:
            fh.write(stderr)
        with open(os.path.join(d, "exit_code.txt"), "w", encoding="utf-8") as fh:
            fh.write(str(code) + "\n")
    return len(_cells())


def _read_golden(cell):
    d = _cell_dir(cell)
    with open(os.path.join(d, "stdout.txt"), "r", encoding="utf-8") as fh:
        stdout = _OVERLAY_PATH_RE.sub(_OVERLAY_TOKEN, fh.read())
    with open(os.path.join(d, "stderr.txt"), "r", encoding="utf-8") as fh:
        stderr = _OVERLAY_PATH_RE.sub(_OVERLAY_TOKEN, fh.read())
    with open(os.path.join(d, "exit_code.txt"), "r", encoding="utf-8") as fh:
        code = int(fh.read().strip())
    return stdout, stderr, code


class CliContractTest(unittest.TestCase):
    """Diff the compiler CLI against the frozen-oracle CLI-contract goldens."""

    @classmethod
    def setUpClass(cls):
        oracle.verify_pin()
        if not os.path.isdir(CONTRACT_DIR):
            raise unittest.SkipTest(
                "cli_contract goldens absent; run "
                "`python3 -m evals.test_cli_contract --capture` first"
            )

    def test_compiler_matches_frozen_oracle(self):
        for cell in _cells():
            with self.subTest(cell=cell.name):
                exp_out, exp_err, exp_code = _read_golden(cell)
                got_out, got_err, got_code = _capture_compiler(cell)
                self.assertEqual(
                    exp_code, got_code,
                    msg=f"[{cell.name}] exit code: oracle {exp_code} != compiler {got_code}",
                )
                self.assertEqual(
                    exp_out, got_out,
                    msg=f"[{cell.name}] stdout mismatch vs frozen oracle",
                )
                self.assertEqual(
                    exp_err, got_err,
                    msg=f"[{cell.name}] stderr mismatch vs frozen oracle",
                )

    def test_self_teeth_mutation_fails(self):
        """Mutating one golden byte makes the diff fail (the net has teeth)."""
        cell = _cells()[0]
        exp_out, _exp_err, _exp_code = _read_golden(cell)
        got_out, _got_err, _got_code = _capture_compiler(cell)
        # The compiler matches the unmutated golden; a one-byte mutation must not.
        self.assertEqual(exp_out, got_out)
        mutated = exp_out + "X" if exp_out else "X"
        self.assertNotEqual(mutated, got_out)


def _main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="CLI-contract goldens harness.")
    parser.add_argument(
        "--capture", action="store_true",
        help="Materialize the frozen-oracle goldens (the ONLY way they are written).",
    )
    args = parser.parse_args(argv)
    if args.capture:
        n = capture_all()
        print(f"captured {n} CLI-contract cell(s) into {CONTRACT_DIR}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
