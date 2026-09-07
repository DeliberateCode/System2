"""Golden runner and byte-diff comparator for oracle and compiler output."""

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

_REPO_ROOT_BYTES = os.fsencode(os.path.abspath(oracle.PLUGIN_REPO_ROOT))
_REPO_ROOT_TOKEN = b"<REPO_ROOT>"
_VALID_MODES = ("byte-identical",)
_ARTIFACT_CLASSES = (
    "base-template", "structural", "CLAUDE.md", "agents", "lock",
    "overlay-content", "warnings", "refusal", "exit-code",
)
_TIMESTAMP_RE = re.compile(rb"<!-- Composed at: ([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) -->")


def _normalize_produced_lock_paths(lock_bytes: bytes, cell: "matrix.Cell") -> bytes:
    lock = json.loads(lock_bytes.decode("utf-8"))
    actual = tuple(overlay.get("source_path") for overlay in lock.get("overlays", []))
    expected = matrix.resolved_overlay_sources(cell)
    if actual != expected:
        raise ValueError(
            f"lock source paths differ from exact cell inputs: expected {expected!r}, got {actual!r}"
        )
    return lock_bytes.replace(_REPO_ROOT_BYTES, _REPO_ROOT_TOKEN)


def _strip_top_level_field(raw: bytes, field: str) -> bytes:
    """Remove one additive top-level JSON field without reserializing other bytes."""
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()
    depth = 0
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"' and depth == 1:
            key, key_end = decoder.raw_decode(text, index)
            colon = key_end
            while colon < len(text) and text[colon].isspace():
                colon += 1
            if key == field and colon < len(text) and text[colon] == ":":
                value_start = colon + 1
                while text[value_start].isspace():
                    value_start += 1
                _, value_end = decoder.raw_decode(text, value_start)
                start = index
                before = start - 1
                while before >= 0 and text[before].isspace():
                    before -= 1
                if before >= 0 and text[before] == ",":
                    start = before
                else:
                    after = value_end
                    while after < len(text) and text[after].isspace():
                        after += 1
                    if after < len(text) and text[after] == ",":
                        value_end = after + 1
                return (text[:start] + text[value_end:]).encode("utf-8")
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return raw


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
    return {"mode": mode, "justification": justification}


def load_policy(policy_path: str = POLICY_PATH) -> dict:
    """Load the byte-only comparison policy; unsupported modes are rejected."""
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
    if rel_path == "base_template.md":
        return "base-template"
    if rel_path == "structural_goldens.json":
        return "structural"
    if rel_path == "CLAUDE.md":
        return "CLAUDE.md"
    if rel_path.startswith(os.path.join(".claude", "agents") + os.sep):
        return "agents"
    if rel_path == os.path.join("spec", "overlay-manifest.lock"):
        return "lock"
    if rel_path.startswith(_OVERLAY_CONTENT_PREFIX):
        return "overlay-content"
    if rel_path == "refusal.txt":
        return "refusal"
    if rel_path == "exit_code.txt":
        return "exit-code"
    return "warnings"


def _tree_files(root: str) -> set:
    return {
        os.path.relpath(os.path.join(dirpath, name), root)
        for dirpath, _, names in os.walk(root)
        for name in names
    }


def _inventory_failures(cell: "matrix.Cell", produced: set) -> list:
    expected = set(cell.expected_files)
    failures = [
        f"[{cell.name}] missing produced artifact: {rel}"
        for rel in sorted(expected - produced)
    ]
    failures.extend(
        f"[{cell.name}] unexpected produced artifact: {rel}"
        for rel in sorted(produced - expected)
    )
    classes = {_classify(rel) for rel in expected}
    if classes != set(cell.expected_artifacts):
        failures.append(
            f"[{cell.name}] required class mismatch: declared={sorted(cell.expected_artifacts)!r}, "
            f"files={sorted(classes)!r}"
        )
    return failures


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
    label: str, expected: bytes, actual: bytes, *, require_report: bool,
    cell: "matrix.Cell",
) -> list:
    """Remove only the additive report; preserve and compare every other byte."""
    failures: list = []
    try:
        produced = json.loads(actual.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"{label}: produced lock is not parseable JSON: {exc!r}"]

    report = produced.get("degradation_report")
    stripped = _strip_top_level_field(actual, "degradation_report")
    try:
        stripped = _normalize_produced_lock_paths(stripped, cell)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"{label}: {exc}"]
    msg = _compare_bytes(label, expected, stripped)
    if msg:
        failures.append(msg + " (after stripping additive degradation_report)")

    if require_report:
        if not isinstance(report, dict):
            failures.append(f"{label}: missing additive degradation_report")
            return failures
        caps = report.get("capabilities")
        if not isinstance(caps, dict) or not caps:
            failures.append(
                f"{label}: degradation_report.capabilities is empty"
            )
            return failures
        for cap, entry in caps.items():
            status = (entry or {}).get("status")
            if status not in _DEGRADATION_STATUS_ENUM:
                failures.append(
                    f"{label}: degradation_report[{cap!r}].status {status!r} not in "
                    f"{_DEGRADATION_STATUS_ENUM}"
                )
            if not (entry or {}).get("mechanism"):
                failures.append(
                    f"{label}: degradation_report[{cap!r}] has no mechanism"
                )
            if status != "native":
                failures.append(
                    f"{label}: claude-code degradation_report[{cap!r}].status is "
                    f"{status!r}, expected 'native'"
                )
    return failures


def _rerun_composed(cell: "matrix.Cell", cell_dir: str, *, seed_lock=True):
    """Re-run the oracle, optionally seeding the prior lock for deterministic bytes."""
    project_dir = tempfile.mkdtemp(prefix=f"rerun-{cell.name}-")
    home = None
    if seed_lock:
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

        produced_files = _tree_files(project_dir) | {"warnings.txt"}
        failures.extend(_inventory_failures(cell, produced_files))
        for rel in cell.expected_files:
            snap_path = os.path.join(cell_dir, rel)
            if rel == "warnings.txt":
                actual = run.stderr.encode("utf-8")
            else:
                produced = os.path.join(project_dir, rel)
                if not os.path.isfile(produced):
                    continue
                actual = _read_bytes(produced)
            cls = _classify(rel)
            expected = _read_bytes(snap_path)
            if cls == "lock":
                failures.extend(
                    _compare_lock(
                        f"[{cell.name}] {rel}", expected, actual,
                        require_report=False, cell=cell,
                    )
                )
            else:
                msg = _compare_bytes(f"[{cell.name}] {rel}", expected, actual)
                if msg:
                    failures.append(msg)
        oracle.cleanup_run(run)
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
    return failures


# Compiler driver (in-process ir.compose -> ClaudeCodeBackend().emit)

def _compiler_compose_emit(cell: "matrix.Cell", cell_dir: str, *, seed_lock=True):
    """Run the in-process compiler for a composed cell into a temp project."""
    import importlib

    project_dir = tempfile.mkdtemp(prefix=f"compiler-{cell.name}-")
    if seed_lock:
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
        produced_files = _tree_files(project_dir) | {"warnings.txt"}
        failures.extend(_inventory_failures(cell, produced_files))
        for rel in cell.expected_files:
            snap_path = os.path.join(cell_dir, rel)
            if rel == "warnings.txt":
                actual = _render_compiler_warnings(cell, cell_dir).encode("utf-8")
            else:
                produced = os.path.join(project_dir, rel)
                if not os.path.isfile(produced):
                    continue
                actual = _read_bytes(produced)
            cls = _classify(rel)
            expected = _read_bytes(snap_path)
            if cls == "lock":
                failures.extend(
                    _compare_lock(
                        f"[{cell.name}] {rel}", expected, actual,
                        require_report=True, cell=cell,
                    )
                )
            else:
                msg = _compare_bytes(f"[{cell.name}] {rel}", expected, actual)
                if msg:
                    failures.append(msg)
    except Exception as exc:  # noqa:  — surface as a failure, not a crash
        failures.append(f"[{cell.name}] compiler driver error: {exc!r}")
    finally:
        if project_dir is not None:
            shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)
    return failures


def _render_compiler_warnings(cell: "matrix.Cell", cell_dir: str, *, seed_lock=True) -> str:
    """Render the neutral stderr warning stream the CLI would emit for *cell*."""
    import importlib
    import io

    from system2_compiler import cli

    project_dir = tempfile.mkdtemp(prefix=f"warn-{cell.name}-")
    if seed_lock:
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
    """Compiler-driver refusal check: compose returns graph=None + errors."""
    import io

    from system2_compiler import cli

    failures = []
    project_dir = tempfile.mkdtemp(prefix=f"compiler-{cell.name}-")
    saved_stdout = sys.stdout
    saved_stderr = sys.stderr
    try:
        argv = [
            "--target", "claude-code",
            "--overlays", ",".join(cell.overlays),
            "--base", oracle.PLUGIN_ROOT,
            "--project", project_dir,
            "--format", "json",
        ]
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        code = cli.main(argv)
        sys.stdout = saved_stdout
        sys.stderr = saved_stderr
        stdout = stdout_buf.getvalue()
        stderr = stderr_buf.getvalue()

        failures.extend(_inventory_failures(
            cell, _tree_files(project_dir) | {"exit_code.txt", "refusal.txt", "warnings.txt"}
        ))
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
        expected_warn = _read_bytes(os.path.join(cell_dir, "warnings.txt"))
        msg = _compare_bytes(
            f"[{cell.name}] warnings.txt", expected_warn, stderr.encode("utf-8")
        )
        if msg:
            failures.append(msg)
    finally:
        sys.stdout = saved_stdout
        sys.stderr = saved_stderr
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

        failures.extend(_inventory_failures(
            cell, _tree_files(project_dir) | {"exit_code.txt", "refusal.txt", "warnings.txt"}
        ))
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


def _fresh_timestamp_tree(cell, cell_dir, driver):
    if driver == "oracle":
        run, project_dir, home = _rerun_composed(cell, cell_dir, seed_lock=False)
        if run.exit_code != 0:
            raise RuntimeError(f"fresh oracle exited {run.exit_code}: {run.stderr!r}")
        warnings = run.stderr.encode("utf-8")
    else:
        run = None
        _written, project_dir, home = _compiler_compose_emit(
            cell, cell_dir, seed_lock=False
        )
        warnings = _render_compiler_warnings(
            cell, cell_dir, seed_lock=False
        ).encode("utf-8")
    try:
        files = _tree_files(project_dir) | {"warnings.txt"}
        failures = _inventory_failures(cell, files)
        tree = {
            rel: (warnings if rel == "warnings.txt" else _read_bytes(os.path.join(project_dir, rel)))
            for rel in cell.expected_files
            if rel == "warnings.txt" or os.path.isfile(os.path.join(project_dir, rel))
        }
        lock_rel = os.path.join("spec", "overlay-manifest.lock")
        lock = json.loads(tree[lock_rel].decode("utf-8"))
        timestamp = lock.get("composed_at", "")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", timestamp):
            failures.append(f"[fresh:{driver}] invalid lock composed_at {timestamp!r}")
        matches = _TIMESTAMP_RE.findall(tree["CLAUDE.md"])
        if matches != [timestamp.encode("utf-8")]:
            failures.append(
                f"[fresh:{driver}] CLAUDE.md and lock timestamps are inconsistent"
            )
        lock_bytes = tree[lock_rel]
        if driver == "compiler":
            lock_bytes = _strip_top_level_field(lock_bytes, "degradation_report")
        tree[lock_rel] = _normalize_produced_lock_paths(lock_bytes, cell).replace(
            timestamp.encode("utf-8"), b"<TS>"
        )
        tree["CLAUDE.md"] = tree["CLAUDE.md"].replace(
            timestamp.encode("utf-8"), b"<TS>"
        )
        return tree, failures
    finally:
        if run is not None:
            oracle.cleanup_run(run)
        shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)


def _diff_fresh_timestamp_parity(cell, cell_dir):
    try:
        expected, failures = _fresh_timestamp_tree(cell, cell_dir, "oracle")
        actual, compiler_failures = _fresh_timestamp_tree(cell, cell_dir, "compiler")
        failures.extend(compiler_failures)
        for rel in sorted(set(expected) | set(actual)):
            if rel not in expected:
                failures.append(f"[fresh] unexpected compiler artifact: {rel}")
            elif rel not in actual:
                failures.append(f"[fresh] missing compiler artifact: {rel}")
            else:
                mismatch = _compare_bytes(f"[fresh] {rel}", expected[rel], actual[rel])
                if mismatch:
                    failures.append(mismatch)
        return failures
    except Exception as exc:
        return [f"[fresh] timestamp parity driver error: {exc!r}"]


def run_goldens(
    goldens_dir: str = DEFAULT_GOLDENS_DIR,
    policy_path: str = POLICY_PATH,
    driver: str = "oracle",
) -> list:
    """Run every cell and return the list of failure messages (empty == green)."""
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
    if driver == "compiler":
        fresh_cell = matrix.get_cell("core+overlay")
        failures.extend(
            _diff_fresh_timestamp_parity(
                fresh_cell, fresh_cell.snapshot_dir(goldens_dir)
            )
        )
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the golden byte-diff comparator.")
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
        help="oracle: frozen subprocess cross-check; compiler: in-process compose->emit.",
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
