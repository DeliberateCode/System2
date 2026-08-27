"""Golden runner + byte-diff comparator (Phase 0 oracle driver + Phase 1 compiler driver).

For each matrix cell, re-run the chosen driver into a throwaway project and byte-diff
every produced artifact against its captured snapshot under ``evals/goldens/<cell>/``.

- ``--driver oracle`` (default): re-runs the frozen ``composer.py`` as a subprocess
  (the Phase 0 cross-check / rollout-backout path).
- ``--driver compiler``: runs the in-process compiler (``ir.compose`` then
  ``ClaudeCodeBackend().emit``) per cell. This is the DoD-1 keystone: the seam-cut
  compose→emit path must be byte-identical to the frozen TASK-006 baseline (REQ-014).

Both drivers seed the prior golden lock first so ``composed_at`` is reused
(idempotency), and both compare under the same per-artifact-class comparison policy
(``byte-identical`` this cycle).

A clean run vs the unmodified oracle yields an empty diff.

The per-artifact-class comparison policy is loaded from ``comparison_policy.json``
(``CLAUDE.md``, ``agents``, ``lock``, ``warnings``). The default mode is
``byte-identical``; selecting ``semantic-equivalent`` for a class WITHOUT a non-empty
``justification`` is rejected at load (REQ-005). This cycle ships every class
``byte-identical``.

A normal run NEVER rewrites snapshots (REQ-007). Only an explicit ``--rebaseline`` flag
re-materializes the baseline (delegating to ``capture.capture_all``).

The oracle is invoked ONLY as a subprocess via ``oracle.invoke_oracle``; this module never
imports ``composer``/``profiles``/``hook_security``.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

from evals import capture, matrix, oracle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GOLDENS_DIR = os.path.join(_THIS_DIR, "goldens")
POLICY_PATH = os.path.join(_THIS_DIR, "comparison_policy.json")

# The lock's overlay ``source_path`` is the ABSOLUTE on-disk fixture path, which varies
# by checkout location (the frozen baseline baked the original author's path). Normalize
# any ``…/evals/fixtures/<name>`` source path to a stable token on BOTH the produced lock
# and the frozen baseline so the byte-comparison is checkout-independent. The content
# hashes that actually pin overlay bytes are unaffected (REQ-035 byte fidelity intact).
_FIXTURE_PATH_RE = re.compile(rb'("source_path":\s*")[^"]*/evals/fixtures/')
_FIXTURE_TOKEN = rb"\1<FIXTURES>/"


def _normalize_lock_paths(lock_bytes: bytes) -> bytes:
    return _FIXTURE_PATH_RE.sub(_FIXTURE_TOKEN, lock_bytes)

_VALID_MODES = ("byte-identical", "semantic-equivalent")
_ARTIFACT_CLASSES = ("CLAUDE.md", "agents", "lock", "warnings")


class PolicyError(ValueError):
    """Raised when ``comparison_policy.json`` is structurally invalid or under-justified."""


def _validate_entry(label: str, entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise PolicyError(f"comparison policy {label!r} must be an object")
    mode = entry.get("mode")
    if mode not in _VALID_MODES:
        raise PolicyError(
            f"comparison policy {label!r} has invalid mode {mode!r}; expected one of {_VALID_MODES}"
        )
    justification = entry.get("justification")
    if mode == "semantic-equivalent":
        if not isinstance(justification, str) or not justification.strip():
            raise PolicyError(
                f"comparison policy {label!r} selects 'semantic-equivalent' without a non-empty "
                "justification; rejected (REQ-005)"
            )
    return {"mode": mode, "justification": justification}


def load_policy(policy_path: str = POLICY_PATH) -> dict:
    """Load + validate the comparison policy. Rejects under-justified semantic modes."""
    with open(policy_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    default = _validate_entry("default", raw.get("default", {"mode": "byte-identical", "justification": None}))
    classes = {}
    for name, entry in raw.get("classes", {}).items():
        classes[name] = _validate_entry(f"classes.{name}", entry)
    return {"default": default, "classes": classes}


def policy_for(policy: dict, artifact_class: str) -> dict:
    return policy["classes"].get(artifact_class, policy["default"])


_OVERLAY_CONTENT_PREFIX = os.path.join(".system2", "overlays") + os.sep


def _classify(rel_path: str) -> str:
    if rel_path == "CLAUDE.md":
        return "CLAUDE.md"
    if rel_path.startswith(os.path.join(".claude", "agents") + os.sep):
        return "agents"
    if rel_path == os.path.join("spec", "overlay-manifest.lock"):
        return "lock"
    if rel_path.startswith(_OVERLAY_CONTENT_PREFIX):
        return "overlay-content"
    return "warnings"


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _compare_bytes(label: str, expected: bytes, actual: bytes) -> "str | None":
    """Return a failure message if bytes differ, else None (byte-identical mode)."""
    if expected == actual:
        return None
    return (
        f"{label}: byte mismatch (expected {len(expected)} bytes, got {len(actual)} bytes)"
    )


_DEGRADATION_STATUS_ENUM = ("native", "adapted", "advisory", "unsupported")


def _compare_lock(
    label: str, expected: bytes, actual: bytes, *, require_report: bool
) -> list:
    """Structural-additive lock comparison applied UNIFORMLY to both drivers (REQ-035).

    Parse the produced lock, remove the additive ``degradation_report`` key if
    present, re-serialize with the canonical ``json.dumps(stripped, indent=2) + "\\n"``
    formatting, and byte-compare THAT to the frozen baseline lock. The frozen baseline
    never carried the report, so:

    - oracle driver: no ``degradation_report`` -> strip is a no-op -> exact byte match
      against the immutable baseline (the comparator change does not break the oracle
      path).
    - compiler driver: the report is removed, and the remaining keys/values must be
      byte-identical to the baseline -> the ONLY lock delta is the additive report
      (REQ-035 additive-only).

    When *require_report* is True (compiler driver), additionally assert the
    ``degradation_report`` IS present and complete (non-empty, every entry carries a
    valid four-value ``status`` + a ``mechanism``); for claude-code every status is
    ``native``.
    """
    failures: list = []
    try:
        produced = json.loads(actual.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{label}: produced lock is not parseable JSON: {exc!r}"]

    report = produced.pop("degradation_report", None)
    stripped = (json.dumps(produced, indent=2) + "\n").encode("utf-8")
    expected = _normalize_lock_paths(expected)
    stripped = _normalize_lock_paths(stripped)
    msg = _compare_bytes(label, expected, stripped)
    if msg:
        failures.append(msg + " (after stripping additive degradation_report)")

    if require_report:
        if not isinstance(report, dict):
            failures.append(f"{label}: missing additive degradation_report (REQ-032)")
            return failures
        caps = report.get("capabilities")
        if not isinstance(caps, dict) or not caps:
            failures.append(
                f"{label}: degradation_report.capabilities is empty (REQ-033)"
            )
            return failures
        for cap, entry in caps.items():
            status = (entry or {}).get("status")
            if status not in _DEGRADATION_STATUS_ENUM:
                failures.append(
                    f"{label}: degradation_report[{cap!r}].status {status!r} not in "
                    f"{_DEGRADATION_STATUS_ENUM} (REQ-036)"
                )
            if not (entry or {}).get("mechanism"):
                failures.append(
                    f"{label}: degradation_report[{cap!r}] has no mechanism (REQ-037)"
                )
            if status != "native":
                failures.append(
                    f"{label}: claude-code degradation_report[{cap!r}].status is "
                    f"{status!r}, expected 'native' (REQ-034)"
                )
    return failures


def _rerun_composed(cell: "matrix.Cell", cell_dir: str):
    """Re-run the oracle for a composed cell, seeding the prior golden lock for determinism."""
    project_dir = tempfile.mkdtemp(prefix=f"rerun-{cell.name}-")
    home = None
    capture._seed_prior_lock(project_dir, cell_dir)
    if cell.profile is not None:
        home = capture._materialize_profile_home(cell)
    run = oracle.invoke_oracle(
        base=None,
        overlays=list(cell.overlays),
        project=project_dir,
        profile=cell.profile,
        home=home,
    )
    return run, project_dir, home


def _diff_composed(cell: "matrix.Cell", cell_dir: str, policy: dict) -> list:
    failures = []
    run, project_dir, home = _rerun_composed(cell, cell_dir)
    try:
        if run.exit_code != 0:
            failures.append(
                f"[{cell.name}] oracle re-run exited {run.exit_code} for a composed cell; "
                f"stderr={run.stderr!r}"
            )
            return failures

        # Compare each snapshot file against the freshly produced artifact.
        for snap_path in _iter_snapshot_files(cell_dir):
            rel = os.path.relpath(snap_path, cell_dir)
            if rel == "warnings.txt":
                actual = run.stderr.encode("utf-8")
            else:
                produced = os.path.join(project_dir, rel)
                if not os.path.isfile(produced):
                    failures.append(f"[{cell.name}] missing produced artifact: {rel}")
                    continue
                actual = _read_bytes(produced)
            cls = "warnings" if rel == "warnings.txt" else _classify(rel)
            mode = policy_for(policy, cls)["mode"]
            expected = _read_bytes(snap_path)
            if mode == "byte-identical":
                if cls == "lock":
                    # Structural-additive lock comparison (REQ-035). The oracle lock
                    # has no degradation_report, so the strip is a no-op and this is
                    # an exact match against the immutable baseline.
                    failures.extend(
                        _compare_lock(
                            f"[{cell.name}] {rel}", expected, actual,
                            require_report=False,
                        )
                    )
                else:
                    msg = _compare_bytes(f"[{cell.name}] {rel}", expected, actual)
                    if msg:
                        failures.append(msg)
            # semantic-equivalent is unused this cycle; reaching here would require a
            # justified policy entry. Byte-identical is the only shipped mode.
        oracle.cleanup_run(run)
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
    return failures


# ---------------------------------------------------------------------------
# Compiler driver (in-process ir.compose -> ClaudeCodeBackend().emit)
# ---------------------------------------------------------------------------

def _compiler_compose_emit(cell: "matrix.Cell", cell_dir: str):
    """Run the in-process compiler for a composed cell into a temp project.

    Seeds the prior golden lock first (so a matching fingerprint reuses
    ``composed_at``). For the profile cell, materializes a hermetic HOME store and
    reloads ``ir.profiles`` so its store path resolves into that HOME (matching how
    the oracle subprocess gets a hermetic HOME), then composes by profile name.
    Returns (written_paths, project_dir, home_dir|None).
    """
    import importlib

    project_dir = tempfile.mkdtemp(prefix=f"compiler-{cell.name}-")
    capture._seed_prior_lock(project_dir, cell_dir)

    home = None
    saved_home = os.environ.get("HOME")
    try:
        from system2_compiler import ir
        from system2_compiler.backends.claude_code import ClaudeCodeBackend

        profile = cell.profile
        if profile is not None:
            home = capture._materialize_profile_home(cell)
            os.environ["HOME"] = home
            # Rebind resolve_profile's default store path to the hermetic HOME.
            importlib.reload(ir.profiles)

        result = ir.compose(
            oracle.PLUGIN_ROOT,
            list(cell.overlays),
            project_dir,
            profile=profile,
        )
        if result.graph is None:
            raise RuntimeError(
                f"cell {cell.name!r}: compiler refused a composed cell: "
                f"{result.errors!r}"
            )
        written = ClaudeCodeBackend().emit(result.graph, project_dir)
        return written, project_dir, home
    finally:
        if profile is not None:
            if saved_home is not None:
                os.environ["HOME"] = saved_home
            else:
                os.environ.pop("HOME", None)
            importlib.reload(ir.profiles)


def _diff_composed_compiler(cell: "matrix.Cell", cell_dir: str, policy: dict) -> list:
    failures = []
    project_dir = None
    home = None
    try:
        _written, project_dir, home = _compiler_compose_emit(cell, cell_dir)
        for snap_path in _iter_snapshot_files(cell_dir):
            rel = os.path.relpath(snap_path, cell_dir)
            if rel == "warnings.txt":
                # The compiler renders warnings via cli._emit_stderr_warnings; the
                # frozen baseline warnings.txt is the oracle stderr. Compare the
                # compiler-rendered stream captured in-process.
                actual = _render_compiler_warnings(cell, cell_dir).encode("utf-8")
            else:
                produced = os.path.join(project_dir, rel)
                if not os.path.isfile(produced):
                    failures.append(f"[{cell.name}] missing produced artifact: {rel}")
                    continue
                actual = _read_bytes(produced)
            cls = "warnings" if rel == "warnings.txt" else _classify(rel)
            mode = policy_for(policy, cls)["mode"]
            expected = _read_bytes(snap_path)
            if mode == "byte-identical":
                if cls == "lock":
                    # Structural-additive lock comparison (REQ-035): strip the
                    # additive degradation_report, byte-match the remainder to the
                    # immutable baseline, and assert the report is present+complete.
                    failures.extend(
                        _compare_lock(
                            f"[{cell.name}] {rel}", expected, actual,
                            require_report=True,
                        )
                    )
                else:
                    msg = _compare_bytes(f"[{cell.name}] {rel}", expected, actual)
                    if msg:
                        failures.append(msg)
        failures.extend(_extra_overlay_content(cell, cell_dir, project_dir))
    except Exception as exc:  # noqa: BLE001 — surface as a failure, not a crash
        failures.append(f"[{cell.name}] compiler driver error: {exc!r}")
    finally:
        if project_dir is not None:
            shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
    return failures


def _extra_overlay_content(cell: "matrix.Cell", cell_dir: str, project_dir: str) -> list:
    """Flag produced ``.system2/overlays/`` files absent from the frozen snapshot.

    Snapshot-file iteration only catches missing/mismatched files; an EXTRA copied
    file (e.g. an unknown-anchor content_file the backend wrongly collects) would slip
    through. This reconciles the produced overlay-content tree against the golden so a
    spurious copy is a hard failure (blocker B1).
    """
    failures = []
    produced_root = os.path.join(project_dir, ".system2", "overlays")
    snapshot_root = os.path.join(cell_dir, ".system2", "overlays")
    # Only reconcile cells whose frozen baseline actually pins the overlay-content
    # tree. Pre-existing cells captured before overlay-content snapshotting carry no
    # such tree and are left untouched (their byte-fidelity is gated by the lock).
    if not os.path.isdir(produced_root) or not os.path.isdir(snapshot_root):
        return failures
    for root, _, names in os.walk(produced_root):
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(root, name), project_dir)
            if not os.path.isfile(os.path.join(cell_dir, rel)):
                failures.append(
                    f"[{cell.name}] extra overlay-content file copied (not in baseline): {rel}"
                )
    return failures


def _render_compiler_warnings(cell: "matrix.Cell", cell_dir: str) -> str:
    """Render the neutral stderr warning stream the CLI would emit for *cell*.

    Re-composes in-process (no emit) and feeds the report through the relocated
    ``cli._emit_stderr_warnings`` via an in-memory stderr, producing the exact
    bytes the CLI writes — the parity surface vs the oracle baseline (REQ-046).
    """
    import importlib
    import io

    from system2_compiler import cli

    project_dir = tempfile.mkdtemp(prefix=f"warn-{cell.name}-")
    capture._seed_prior_lock(project_dir, cell_dir)
    home = None
    saved_home = os.environ.get("HOME")
    saved_stderr = sys.stderr
    try:
        from system2_compiler import ir
        profile = cell.profile
        if profile is not None:
            home = capture._materialize_profile_home(cell)
            os.environ["HOME"] = home
            importlib.reload(ir.profiles)
        result = ir.compose(
            oracle.PLUGIN_ROOT, list(cell.overlays), project_dir, profile=profile,
        )
        buf = io.StringIO()
        sys.stderr = buf
        if result.graph is not None:
            cli._emit_stderr_warnings(result.report)
        return buf.getvalue()
    finally:
        sys.stderr = saved_stderr
        if profile is not None:
            if saved_home is not None:
                os.environ["HOME"] = saved_home
            else:
                os.environ.pop("HOME", None)
            importlib.reload(ir.profiles)
        shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)


def _diff_refusal_compiler(cell: "matrix.Cell", cell_dir: str, policy: dict) -> list:
    """Compiler-driver refusal check: compose returns graph=None + errors.

    Compares the compiler's refusal exit-code classification and refusal text to the
    frozen oracle baseline (REQ-021). The oracle emits its refusal as a JSON report on
    stdout; the CLI mirrors that JSON shape, so refusal.txt is compared against the
    CLI's stdout JSON.
    """
    import io

    from system2_compiler import cli

    failures = []
    project_dir = tempfile.mkdtemp(prefix=f"compiler-{cell.name}-")
    saved_stdout = sys.stdout
    try:
        argv = [
            "--target", "claude-code",
            "--overlays", ",".join(cell.overlays),
            "--base", oracle.PLUGIN_ROOT,
            "--project", project_dir,
            "--format", "json",
        ]
        buf = io.StringIO()
        sys.stdout = buf
        code = cli.main(argv)
        sys.stdout = saved_stdout
        stdout = buf.getvalue()

        expected_exit = _read_bytes(os.path.join(cell_dir, "exit_code.txt"))
        actual_exit = (str(code) + "\n").encode("utf-8")
        if expected_exit != actual_exit:
            failures.append(
                f"[{cell.name}] exit_code mismatch: expected {expected_exit!r}, got {actual_exit!r}"
            )
        expected_refusal = _read_bytes(os.path.join(cell_dir, "refusal.txt"))
        msg = _compare_bytes(
            f"[{cell.name}] refusal.txt", expected_refusal, stdout.encode("utf-8")
        )
        if msg:
            failures.append(msg)
    finally:
        sys.stdout = saved_stdout
        shutil.rmtree(project_dir, ignore_errors=True)
    return failures


def _diff_refusal(cell: "matrix.Cell", cell_dir: str, policy: dict) -> list:
    failures = []
    project_dir = tempfile.mkdtemp(prefix=f"rerun-{cell.name}-")
    try:
        run = oracle.invoke_oracle(
            base=None,
            overlays=list(cell.overlays),
            project=project_dir,
            profile=cell.profile,
        )
        if run.exit_code == 0:
            failures.append(f"[{cell.name}] expected refusal (non-zero exit) but oracle exited 0")
            return failures

        expected_exit = _read_bytes(os.path.join(cell_dir, "exit_code.txt"))
        actual_exit = (str(run.exit_code) + "\n").encode("utf-8")
        if expected_exit != actual_exit:
            failures.append(
                f"[{cell.name}] exit_code mismatch: expected {expected_exit!r}, got {actual_exit!r}"
            )

        expected_refusal = _read_bytes(os.path.join(cell_dir, "refusal.txt"))
        actual_refusal = (run.stdout if run.stdout else run.stderr).encode("utf-8")
        msg = _compare_bytes(f"[{cell.name}] refusal.txt", expected_refusal, actual_refusal)
        if msg:
            failures.append(msg)

        expected_warn = _read_bytes(os.path.join(cell_dir, "warnings.txt"))
        msg = _compare_bytes(f"[{cell.name}] warnings.txt", expected_warn, run.stderr.encode("utf-8"))
        if msg:
            failures.append(msg)
        oracle.cleanup_run(run)
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
    return failures


def _diff_core(cell_dir: str) -> list:
    """Re-verify the core cell's static inventory invariant by recomputing references."""
    failures = []
    base_snap = os.path.join(cell_dir, "base_template.md")
    if not os.path.isfile(base_snap):
        return [f"[core] missing base_template.md"]
    current_base = capture._read_base_template().encode("utf-8")
    if _read_bytes(base_snap) != current_base:
        failures.append("[core] base_template.md: drift vs the live base CLAUDE.md template")

    ref_path = os.path.join(cell_dir, "structural_goldens.json")
    if not os.path.isfile(ref_path):
        return failures + ["[core] missing structural_goldens.json"]
    with open(ref_path, "r", encoding="utf-8") as fh:
        record = json.load(fh)
    for entry in record.get("structural_goldens", []):
        abs_path = os.path.join(capture._WORKSPACE_ROOT, entry["path"])
        if not os.path.isfile(abs_path):
            failures.append(f"[core] referenced structural golden missing: {entry['path']}")
            continue
        if capture._sha256_file(abs_path) != entry["sha256"]:
            failures.append(f"[core] structural golden drift: {entry['path']}")
    return failures


def _iter_snapshot_files(cell_dir: str):
    for root, _, files in os.walk(cell_dir):
        for name in sorted(files):
            yield os.path.join(root, name)


def run_goldens(
    goldens_dir: str = DEFAULT_GOLDENS_DIR,
    policy_path: str = POLICY_PATH,
    driver: str = "oracle",
) -> list:
    """Run every cell and return the list of failure messages (empty == green).

    ``driver`` selects ``oracle`` (subprocess cross-check) or ``compiler``
    (in-process ``ir.compose`` -> ``ClaudeCodeBackend().emit`` — the DoD-1 gate).
    The ``core`` inventory-invariant cell is driver-independent.
    """
    oracle.verify_pin()
    policy = load_policy(policy_path)
    matrix.assert_complete(goldens_dir)
    failures = []
    for cell in matrix.all_cells():
        cell_dir = cell.snapshot_dir(goldens_dir)
        if cell.name == "core":
            failures.extend(_diff_core(cell_dir))
        elif cell.refusal:
            if driver == "compiler":
                failures.extend(_diff_refusal_compiler(cell, cell_dir, policy))
            else:
                failures.extend(_diff_refusal(cell, cell_dir, policy))
        else:
            if driver == "compiler":
                failures.extend(_diff_composed_compiler(cell, cell_dir, policy))
            else:
                failures.extend(_diff_composed(cell, cell_dir, policy))
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 0 golden byte-diff comparator.")
    parser.add_argument(
        "--rebaseline",
        action="store_true",
        help="Re-materialize the baseline from the oracle (the ONLY way snapshots are rewritten).",
    )
    parser.add_argument("--goldens-dir", default=DEFAULT_GOLDENS_DIR)
    parser.add_argument(
        "--driver",
        choices=["oracle", "compiler"],
        default="oracle",
        help="oracle: frozen subprocess cross-check; compiler: in-process compose->emit (DoD-1).",
    )
    args = parser.parse_args(argv)

    if args.rebaseline:
        capture.capture_all(goldens_dir=args.goldens_dir, rebaseline=True)
        print(f"rebaselined {len(matrix.all_cells())} cell(s) into {args.goldens_dir}")
        return 0

    failures = run_goldens(goldens_dir=args.goldens_dir, driver=args.driver)
    if failures:
        print(f"GOLDEN DIFF FAILURES (driver={args.driver}):", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        print(f"{len(failures)} failing artifact(s)", file=sys.stderr)
        return 1
    print(
        f"GOLDENS GREEN (driver={args.driver}): {len(matrix.all_cells())} cell(s) "
        f"empty-diff vs the frozen baseline"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
