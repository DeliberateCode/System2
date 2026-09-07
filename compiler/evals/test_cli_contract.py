"""CLI-contract goldens: the compiler ``system2`` CLI vs the FROZEN oracle."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# The dry-run preview embeds a live UTC ``<!-- Composed at: … -->`` timestamp
# (no prior lock exists to reuse it), the one genuinely non-deterministic byte
# in the CLI contract.  Match only the canonical UTC shape; calendar validity,
# capture-time proximity, and cross-stream consistency are checked separately.
_COMPOSED_AT_RE = re.compile(
    r"<!-- Composed at: (?P<timestamp>[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}Z) -->"
)
_TIMESTAMP_TOKEN = "<!-- Composed at: <TS> -->"
_TIMESTAMP_CAPTURE_SLOP_SECONDS = 5

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
INJECTION = os.path.join(_THIS_DIR, "fixtures", "injection")

# Placeholders the harness substitutes per-cell so a cell's argv/expectations are
# independent of the (volatile) temp dirs created at run time.
_PROJ = "@PROJECT@"
_HOME = "@HOME@"
_MISSING_BASE = "@MISSING_BASE@"


def _hermetic_env(home_dir):
    env = {"HOME": home_dir}
    for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


def _validated_runtime_timestamp(texts, capture_started, capture_finished):
    """Return one valid, near-capture timestamp shared by all output streams."""
    timestamps = [
        match.group("timestamp")
        for text in texts
        for match in _COMPOSED_AT_RE.finditer(text)
    ]
    if not timestamps:
        return None
    if len(set(timestamps)) != 1:
        raise AssertionError(
            f"runtime timestamps are internally inconsistent: {timestamps!r}"
        )

    timestamp = timestamps[0]
    try:
        instant = datetime.datetime.strptime(
            timestamp, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError as exc:
        raise AssertionError(f"invalid canonical UTC timestamp: {timestamp!r}") from exc

    earliest = capture_started - _TIMESTAMP_CAPTURE_SLOP_SECONDS
    latest = capture_finished + _TIMESTAMP_CAPTURE_SLOP_SECONDS
    if not earliest <= instant <= latest:
        raise AssertionError(
            f"runtime timestamp {timestamp!r} is outside capture window "
            f"[{capture_started!r}, {capture_finished!r}]"
        )
    return timestamp


def _normalize(text, project_dir, home_dir, runtime_timestamp=None):
    """Normalize exact runtime roots and one previously validated timestamp."""
    out = text.replace(project_dir, "<PROJECT>").replace(home_dir, "<HOME>")
    for path, token in (
        (TEST_OVERLAY, _OVERLAY_TOKEN),
        (INJECTION, "<INJECTION_OVERLAY>"),
        (BASE, "<BASE>"),
    ):
        out = out.replace(path, token)
    if runtime_timestamp is not None:
        exact = f"<!-- Composed at: {runtime_timestamp} -->"
        out = out.replace(exact, _TIMESTAMP_TOKEN)
    return out


def _normalize_capture(
    stdout, stderr, project_dir, home_dir, capture_started, capture_finished
):
    """Validate a subprocess timestamp jointly, then normalize both streams."""
    runtime_timestamp = _validated_runtime_timestamp(
        (stdout, stderr), capture_started, capture_finished
    )
    return (
        _normalize(stdout, project_dir, home_dir, runtime_timestamp),
        _normalize(stderr, project_dir, home_dir, runtime_timestamp),
    )


def _resolve_argv(argv, project, home):
    replacements = {
        _PROJ: project,
        _HOME: home,
        _MISSING_BASE: os.path.join(home, "missing-base"),
    }
    return [replacements.get(arg, arg) for arg in argv]


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
            "compose_io_refusal_json",
            ["--base", _MISSING_BASE, "--project", _PROJ, "--overlays", OVL, "--format", "json"],
            ["compile", "--target", "claude-code", "--base", _MISSING_BASE,
             "--project", _PROJ, "--overlays", OVL, "--format", "json"],
            kind="refusal",
        ),
        _Cell(
            "compose_injection_blocked_json",
            ["--base", BASE, "--project", _PROJ, "--overlays", INJECTION, "--format", "json"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--overlays", INJECTION, "--format", "json"],
            kind="refusal",
        ),
        _Cell(
            "compose_injection_acknowledged_json",
            ["--base", BASE, "--project", _PROJ, "--overlays", INJECTION,
             "--allow-injection", "--format", "json"],
            ["compile", "--target", "claude-code", "--base", BASE, "--project", _PROJ,
             "--overlays", INJECTION, "--allow-injection", "--format", "json"],
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
            "profile_missing_name_json",
            ["--base", BASE, "--project", _PROJ, "--profile-op", "create", "--format", "json"],
            ["profile", "create", "--format", "json"],
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
    resolved = _resolve_argv(argv, project_dir, env["HOME"])
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
        argv = _resolve_argv(cell.oracle_argv, project, home)
        capture_started = time.time()
        completed = subprocess.run(
            [sys.executable, oracle.COMPOSER_PATH] + argv,
            capture_output=True, text=True, env=env,
            cwd=os.path.dirname(oracle.COMPOSER_PATH),
        )
        capture_finished = time.time()
        stdout, stderr = _normalize_capture(
            completed.stdout, completed.stderr, project, home,
            capture_started, capture_finished,
        )
        return stdout, stderr, completed.returncode
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
        argv = _resolve_argv(cell.compiler_argv, project, home)
        capture_started = time.time()
        completed = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.argv=['system2']+sys.argv[1:]; "
             "from system2_compiler import cli; raise SystemExit(cli.main())"] + argv,
            capture_output=True, text=True, env=env, cwd=_PKG_ROOT,
        )
        capture_finished = time.time()
        stdout, stderr = _normalize_capture(
            completed.stdout, completed.stderr, project, home,
            capture_started, capture_finished,
        )
        return stdout, stderr, completed.returncode
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


def _compare_contract(expected, actual, label):
    for stream, wanted, got in zip(
        ("stdout", "stderr", "exit code"), expected, actual
    ):
        if wanted != got:
            raise AssertionError(
                f"[{label}] {stream} mismatch: expected {wanted!r}, got {got!r}"
            )


def _read_golden(cell):
    d = _cell_dir(cell)
    with open(os.path.join(d, "stdout.txt"), "r", encoding="utf-8") as fh:
        stdout = fh.read()
    with open(os.path.join(d, "stderr.txt"), "r", encoding="utf-8") as fh:
        stderr = fh.read()
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
                expected = _read_golden(cell)
                actual = _capture_compiler(cell)
                _compare_contract(expected, actual, cell.name)

    def test_normalization_replaces_only_exact_runtime_roots(self):
        exact = f'{{"path":"{TEST_OVERLAY}"}}'
        wrong = '{"path":"/wrong/evals/fixtures/test-overlay"}'
        normalized = _normalize(
            exact + "\n" + wrong + "\n/tmp/project with space/file",
            "/tmp/project with space", "/tmp/home",
        )
        self.assertIn('{"path":"<OVERLAY>"}', normalized)
        self.assertIn(wrong, normalized)
        self.assertIn("<PROJECT>/file", normalized)

    def test_timestamp_normalization_requires_valid_near_consistent_runtime_value(self):
        capture_started = 2_000_000_000.0
        capture_finished = capture_started + 1
        timestamp = datetime.datetime.fromtimestamp(
            capture_started, datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = f"preview\n<!-- Composed at: {timestamp} -->\n"
        stdout, stderr = _normalize_capture(
            raw, "", "/tmp/project", "/tmp/home",
            capture_started, capture_finished,
        )
        self.assertEqual(stdout, "preview\n<!-- Composed at: <TS> -->\n")
        self.assertEqual(stderr, "")

        malformed = "preview\n<!-- Composed at: ---- -->\n"
        malformed_actual = _normalize_capture(
            malformed, "", "/tmp/project", "/tmp/home",
            capture_started, capture_finished,
        ) + (0,)
        with self.assertRaises(AssertionError):
            _compare_contract((stdout, stderr, 0), malformed_actual, "malformed-ts")

        with self.assertRaisesRegex(AssertionError, "invalid canonical UTC"):
            _normalize_capture(
                "<!-- Composed at: 1999-99-99T99:99:99Z -->", "",
                "/tmp/project", "/tmp/home", capture_started, capture_finished,
            )
        for wrong in ("1999-01-01T00:00:00Z", "2099-01-01T00:00:00Z"):
            with self.subTest(wrong=wrong):
                with self.assertRaisesRegex(AssertionError, "outside capture window"):
                    _normalize_capture(
                        f"<!-- Composed at: {wrong} -->", "",
                        "/tmp/project", "/tmp/home",
                        capture_started, capture_finished,
                    )
        other = datetime.datetime.fromtimestamp(
            capture_started + 1, datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.assertRaisesRegex(AssertionError, "internally inconsistent"):
            _normalize_capture(
                raw, f"<!-- Composed at: {other} -->",
                "/tmp/project", "/tmp/home",
                capture_started, capture_finished,
            )

    def test_real_comparator_rejects_each_mutated_channel(self):
        cell = next(cell for cell in _cells() if cell.name == "compose_text")
        expected = _read_golden(cell)
        actual = _capture_compiler(cell)
        _compare_contract(expected, actual, cell.name)
        mutations = (
            (expected[0] + "X", expected[1], expected[2]),
            (expected[0], expected[1] + "X", expected[2]),
            (expected[0], expected[1], expected[2] + 1),
        )
        for channel, mutated in zip(("stdout", "stderr", "exit"), mutations):
            with self.subTest(channel=channel):
                with self.assertRaises(AssertionError):
                    _compare_contract(mutated, actual, cell.name)

    def test_new_capture_inputs_match_frozen_oracle_before_snapshot_capture(self):
        names = {
            "compose_io_refusal_json": 3,
            "compose_injection_blocked_json": 4,
            "compose_injection_acknowledged_json": 0,
            "profile_missing_name_json": 1,
        }
        by_name = {cell.name: cell for cell in _cells()}
        for name, exit_code in names.items():
            with self.subTest(cell=name):
                oracle_result = _capture_oracle(by_name[name])
                compiler_result = _capture_compiler(by_name[name])
                self.assertEqual(oracle_result[2], exit_code, oracle_result)
                _compare_contract(oracle_result, compiler_result, name)
        blocked = _capture_oracle(by_name["compose_injection_blocked_json"])
        acknowledged = _capture_oracle(by_name["compose_injection_acknowledged_json"])
        self.assertIn("WARNING:", blocked[1])
        self.assertIn("WARNING:", acknowledged[1])
        self.assertEqual(blocked[2], 4)
        self.assertEqual(acknowledged[2], 0)


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
