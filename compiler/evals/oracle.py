"""Locate, hash-pin, and invoke the frozen System2 oracle (``composer.py.preflip``)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# evals/ -> compiler package root
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_THIS_DIR)


def _resolve_plugin_root() -> str:
    """Resolve the System2 plugin root portably (CI-/machine-independent)."""
    override = os.environ.get("SYSTEM2_PLUGIN_ROOT")
    if override:
        return os.path.abspath(override)
    return os.path.abspath(os.path.join(_COMPILER_ROOT, "..", "plugin"))


PLUGIN_ROOT = _resolve_plugin_root()
# The System2 repo root (parent of the plugin dir) — the anchor for read-only
# plugin fixtures/goldens the suite reuses.
PLUGIN_REPO_ROOT = os.path.dirname(PLUGIN_ROOT)
_SCRIPTS_DIR = os.path.join(PLUGIN_ROOT, "scripts")

COMPOSER_PATH = os.path.join(_SCRIPTS_DIR, "composer.py.preflip")
PROFILES_PATH = os.path.join(_SCRIPTS_DIR, "profiles.py")
HOOK_SECURITY_PATH = os.path.join(_SCRIPTS_DIR, "hook_security.py")

LOCK_PATH = os.path.join(_THIS_DIR, "oracle.lock.json")

DRIFT_MESSAGE = "oracle changed / re-baseline required"


@dataclass(frozen=True)
class CapturedRun:
    """The full result of a single oracle subprocess invocation."""

    exit_code: int
    stdout: str
    stderr: str
    project_dir: str
    home_dir: str
    report: dict = field(default_factory=dict)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_lock() -> dict:
    """Compute the OracleLock record from the current on-disk oracle sources."""
    for path in (COMPOSER_PATH, PROFILES_PATH, HOOK_SECURITY_PATH):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"oracle source not found: {path}")
    return {
        "path": os.path.relpath(COMPOSER_PATH, PLUGIN_ROOT),
        "sha256": _sha256(COMPOSER_PATH),
        "profiles_sha256": _sha256(PROFILES_PATH),
        "hook_security_sha256": _sha256(HOOK_SECURITY_PATH),
    }


def write_lock(lock_path: str = LOCK_PATH) -> dict:
    """Materialize ``oracle.lock.json``. Used once to baseline; never on a normal run."""
    lock = compute_lock()
    with open(lock_path, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2)
        fh.write("\n")
    return lock


def load_lock(lock_path: str = LOCK_PATH) -> dict:
    with open(lock_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def verify_pin(lock_path: str = LOCK_PATH) -> dict:
    """Recompute hashes and compare to the pinned lock."""
    pinned = load_lock(lock_path)
    current = compute_lock()
    for key in ("sha256", "profiles_sha256", "hook_security_sha256"):
        if pinned.get(key) != current.get(key):
            raise RuntimeError(DRIFT_MESSAGE)
    return current


def _hermetic_env(home_dir: str, env: "dict | None") -> dict:
    base = dict(env) if env is not None else {}
    if not base:
        for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ"):
            value = os.environ.get(key)
            if value is not None:
                base[key] = value
    base["HOME"] = home_dir
    return base


def invoke_oracle(
    base,
    overlays,
    project,
    profile=None,
    dry_run=False,
    env=None,
    home=None,
) -> CapturedRun:
    """Run the frozen ``composer.py`` as a subprocess and capture its outputs."""
    base_path = base if base is not None else PLUGIN_ROOT
    project_path = project if project is not None else tempfile.mkdtemp(prefix="oracle-project-")
    os.makedirs(project_path, exist_ok=True)

    home_dir = home if home is not None else tempfile.mkdtemp(prefix="oracle-home-")
    os.makedirs(home_dir, exist_ok=True)
    run_env = _hermetic_env(home_dir, env)

    overlay_arg = ",".join(overlays) if overlays else ""

    cmd = [
        sys.executable,
        COMPOSER_PATH,
        "--base",
        base_path,
        "--project",
        project_path,
        "--format",
        "json",
    ]
    if overlay_arg:
        cmd += ["--overlays", overlay_arg]
    if profile:
        cmd += ["--profile", profile]
    if dry_run:
        cmd.append("--dry-run")

    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=run_env,
        cwd=_SCRIPTS_DIR,
    )

    report = {}
    if completed.stdout:
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                report = parsed.get("report", parsed)
        except json.JSONDecodeError:
            report = {}

    return CapturedRun(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        project_dir=project_path,
        home_dir=home_dir,
        report=report,
    )


def cleanup_run(run: CapturedRun) -> None:
    """Best-effort removal of a run's temp HOME (project dirs are caller-owned)."""
    shutil.rmtree(run.home_dir, ignore_errors=True)


if __name__ == "__main__":
    write_lock()
    print(json.dumps(load_lock(), indent=2))
