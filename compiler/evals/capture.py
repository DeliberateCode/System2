"""Per-cell golden artifact capture.

For each matrix cell, run the frozen oracle (subprocess, hermetic HOME) and
snapshot the artifact classes it actually writes into ``evals/goldens/<cell>/``:

- composed cells (``core+overlay``, ``core+overlay+profile``, ``core+tension``):
  ``CLAUDE.md``; every produced ``.claude/agents/<aux>.md``; ``spec/overlay-manifest.lock``;
  and the stderr warning stream (``warnings.txt``).
- refusal cells (``core+conflict``): ``refusal.txt`` (the error stream) + ``exit_code.txt``
  + ``warnings.txt`` instead of files.
- the ``core`` cell (static inventory invariant, design T3): NOT a zero-overlay oracle run
  (the oracle refuses empty overlay sets). Instead snapshot the base CLAUDE.md template the
  composer starts from (``base_template.md``) and a ``structural_goldens.json`` reference
  (relative path + sha256) to the plugin's read-only structural goldens locking the
  13-agent/6-gate inventory + hook/allowlist bindings.

Determinism: the lock carries ``composed_at`` (a timestamp) and ``content_fingerprint``.
The oracle reuses ``composed_at`` from a prior lock in the project dir when the fingerprint
matches. To make back-to-back captures byte-stable, capture seeds the temp project's
``spec/overlay-manifest.lock`` from the previously-captured golden lock (when one exists)
before invoking the oracle, so a matching fingerprint reuses the frozen ``composed_at``.
No lock fields are stripped.

Baselines are materialized by *running* this module (``python3 -m evals.capture``); the
files under ``goldens/`` are written by this subprocess-driven capture, not hand-authored.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

from evals import matrix, oracle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# The anchor the `core` cell's structural-golden references are stored RELATIVE to:
# the parent dir that contains the (read-only) ``System2/`` plugin repo. Derived from
# the portable plugin-root resolution so it tracks a sibling layout or a
# ``SYSTEM2_PLUGIN_ROOT`` override, while keeping the frozen ``System2/...``-prefixed
# relative paths byte-identical.
_WORKSPACE_ROOT = os.path.dirname(oracle.PLUGIN_REPO_ROOT)

DEFAULT_GOLDENS_DIR = os.path.join(_THIS_DIR, "goldens")

# Read-only base template sources the composer resolves (init skill block first,
# then the repo CLAUDE.md fallback) — see composer._read_base_template.
_PLUGIN_ROOT = oracle.PLUGIN_ROOT
_INIT_SKILL_PATH = os.path.join(_PLUGIN_ROOT, "skills", "init", "SKILL.md")
_REPO_CLAUDE_PATH = os.path.join(os.path.dirname(_PLUGIN_ROOT), "CLAUDE.md")

# Read-only structural goldens the `core` cell references (the inventory invariant).
_PLUGIN_GOLDENS_DIR = os.path.join(oracle.PLUGIN_REPO_ROOT, "evals", "goldens")
_STRUCTURAL_GOLDEN_FILES = (
    "agent_inventory.json",
    "delegation_map.json",
    "hook_inventory.json",
    "allowlist_inventory.json",
    "agent_allowlist_bindings.json",
    "template_sections.json",
    "maintenance_thresholds.json",
    "manifest_schemas.json",
    "prohibited_patterns.json",
    "required_readme_patterns.json",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return _sha256_bytes(fh.read())


def _read_base_template() -> str:
    """Resolve the base CLAUDE.md template exactly as composer._read_base_template does.

    Tries the init skill's ``---BEGIN/END TEMPLATE---`` block first; falls back to the
    repo CLAUDE.md. Read-only: never imports or mutates the plugin.
    """
    if os.path.isfile(_INIT_SKILL_PATH):
        with open(_INIT_SKILL_PATH, "r", encoding="utf-8") as fh:
            skill_content = fh.read()
        begin = skill_content.find("---BEGIN TEMPLATE---")
        end = skill_content.find("---END TEMPLATE---")
        if begin != -1 and end != -1:
            begin += len("---BEGIN TEMPLATE---\n")
            return skill_content[begin:end].rstrip("\n") + "\n"
    if os.path.isfile(_REPO_CLAUDE_PATH):
        with open(_REPO_CLAUDE_PATH, "r", encoding="utf-8") as fh:
            return fh.read()
    raise FileNotFoundError(
        f"base CLAUDE.md template not found: checked {_INIT_SKILL_PATH} and {_REPO_CLAUDE_PATH}"
    )


def _reset_dir(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _copy_artifacts(project_dir: str, cell_dir: str) -> None:
    """Copy the composed artifact classes from a project tree into the cell golden dir."""
    claude_src = os.path.join(project_dir, "CLAUDE.md")
    if os.path.isfile(claude_src):
        shutil.copyfile(claude_src, os.path.join(cell_dir, "CLAUDE.md"))

    lock_src = os.path.join(project_dir, "spec", "overlay-manifest.lock")
    if os.path.isfile(lock_src):
        dst = os.path.join(cell_dir, "spec", "overlay-manifest.lock")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(lock_src, dst)

    agents_src = os.path.join(project_dir, ".claude", "agents")
    if os.path.isdir(agents_src):
        for name in sorted(os.listdir(agents_src)):
            if name.endswith(".md"):
                dst = os.path.join(cell_dir, ".claude", "agents", name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(agents_src, name), dst)

    # Overlay content copies (.system2/overlays/<name>/...): the anchor-filtered
    # set the oracle actually copies. Snapshotting these pins the content-file
    # selection (blocker B1): unknown-anchor content files must NOT appear here.
    overlays_src = os.path.join(project_dir, ".system2", "overlays")
    if os.path.isdir(overlays_src):
        for root, _, names in os.walk(overlays_src):
            for name in sorted(names):
                src = os.path.join(root, name)
                rel = os.path.relpath(src, project_dir)
                dst = os.path.join(cell_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)


def _seed_prior_lock(project_dir: str, cell_dir: str) -> None:
    """Seed the temp project with the previously-captured golden lock (if any).

    The oracle reuses ``composed_at`` when the project already holds a lock whose
    ``content_fingerprint`` matches the recomputed one — making capture idempotent.
    """
    prior = os.path.join(cell_dir, "spec", "overlay-manifest.lock")
    if os.path.isfile(prior):
        dst = os.path.join(project_dir, "spec", "overlay-manifest.lock")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(prior, dst)


def _materialize_profile_home(cell: "matrix.Cell") -> str:
    """Create a hermetic HOME containing ``.system2/profiles.json`` from the store fixture.

    The store's ``overlays[].path`` is rewritten to the resolved absolute ``TEST_OVERLAY``
    so the baseline is root-correct (TASK-007 portability note).
    """
    home = tempfile.mkdtemp(prefix="capture-home-")
    os.makedirs(os.path.join(home, ".system2"), exist_ok=True)
    with open(cell.profile_store, "r", encoding="utf-8") as fh:
        store = json.load(fh)
    for prof in store.get("profiles", {}).values():
        for overlay in prof.get("overlays", []):
            if isinstance(overlay, dict) and "path" in overlay:
                overlay["path"] = matrix.TEST_OVERLAY
    with open(os.path.join(home, ".system2", "profiles.json"), "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2)
    return home


def _capture_composed(cell: "matrix.Cell", cell_dir: str) -> None:
    project_dir = tempfile.mkdtemp(prefix=f"capture-{cell.name}-")
    home = None
    try:
        _seed_prior_lock(project_dir, cell_dir)
        if cell.profile is not None:
            home = _materialize_profile_home(cell)
        run = oracle.invoke_oracle(
            base=None,
            overlays=list(cell.overlays),
            project=project_dir,
            profile=cell.profile,
            home=home,
        )
        if run.exit_code != 0:
            raise RuntimeError(
                f"cell {cell.name!r} expected a successful compose but the oracle exited "
                f"{run.exit_code}; stderr={run.stderr!r} stdout={run.stdout[:200]!r}"
            )
        _reset_dir(cell_dir)
        _copy_artifacts(project_dir, cell_dir)
        _write_text(os.path.join(cell_dir, "warnings.txt"), run.stderr)
        oracle.cleanup_run(run)
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)


def _capture_refusal(cell: "matrix.Cell", cell_dir: str) -> None:
    project_dir = tempfile.mkdtemp(prefix=f"capture-{cell.name}-")
    try:
        run = oracle.invoke_oracle(
            base=None,
            overlays=list(cell.overlays),
            project=project_dir,
            profile=cell.profile,
        )
        if run.exit_code == 0:
            raise RuntimeError(
                f"cell {cell.name!r} expected a refusal (non-zero exit) but the oracle "
                f"exited 0; stdout={run.stdout[:200]!r}"
            )
        _reset_dir(cell_dir)
        # The refusal message is the engine's error output. composer emits the error
        # report on stdout (json/text) and exits non-zero; capture both streams plus code.
        refusal_text = run.stdout if run.stdout else run.stderr
        _write_text(os.path.join(cell_dir, "refusal.txt"), refusal_text)
        _write_text(os.path.join(cell_dir, "warnings.txt"), run.stderr)
        _write_text(os.path.join(cell_dir, "exit_code.txt"), str(run.exit_code) + "\n")
        oracle.cleanup_run(run)
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)


def _capture_core(cell_dir: str) -> None:
    """Capture the static inventory invariant for the ``core`` cell (design T3).

    Snapshots the base CLAUDE.md template + a reference (relative path + sha256) to the
    plugin's read-only structural goldens. The oracle is NOT invoked (it refuses empty
    overlay sets).
    """
    _reset_dir(cell_dir)
    base_template = _read_base_template()
    _write_text(os.path.join(cell_dir, "base_template.md"), base_template)

    references = []
    for name in _STRUCTURAL_GOLDEN_FILES:
        path = os.path.join(_PLUGIN_GOLDENS_DIR, name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"structural golden not found: {path}")
        rel = os.path.relpath(path, _WORKSPACE_ROOT)
        references.append({"path": rel, "sha256": _sha256_file(path)})

    reference_record = {
        "description": (
            "Static inventory invariant (13-agent/6-gate + hook/allowlist bindings). "
            "Captured by reference to the plugin's read-only structural goldens; the oracle "
            "is not invoked for the core cell because it refuses empty overlay sets (design T3)."
        ),
        "base_template_sha256": _sha256_bytes(base_template.encode("utf-8")),
        "structural_goldens": references,
    }
    _write_text(
        os.path.join(cell_dir, "structural_goldens.json"),
        json.dumps(reference_record, indent=2) + "\n",
    )


def _capture_cell(cell: "matrix.Cell", cell_dir: str) -> None:
    if cell.name == "core":
        _capture_core(cell_dir)
    elif cell.refusal:
        _capture_refusal(cell, cell_dir)
    else:
        _capture_composed(cell, cell_dir)


def capture_all(
    goldens_dir: str = DEFAULT_GOLDENS_DIR,
    rebaseline: bool = False,
    only: "str | None" = None,
) -> None:
    """Capture declared matrix cells into ``goldens_dir``.

    ``rebaseline`` is accepted for symmetry with the runner; capture always (re)writes the
    snapshot tree from the oracle, which is the baseline-materialization step (TASK-006).
    ``only`` restricts capture to a single named cell (the rest of the frozen baseline is
    left byte-for-byte untouched); ``assert_complete`` is then skipped so a partial capture
    does not fail on the unbuilt cells.
    """
    oracle.verify_pin()
    os.makedirs(goldens_dir, exist_ok=True)
    if only is not None:
        cell = matrix.get_cell(only)
        _capture_cell(cell, cell.snapshot_dir(goldens_dir))
        return
    for cell in matrix.all_cells():
        _capture_cell(cell, cell.snapshot_dir(goldens_dir))
    matrix.assert_complete(goldens_dir)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rebaseline = "--rebaseline" in argv
    only = None
    if "--cell" in argv:
        only = argv[argv.index("--cell") + 1]
    capture_all(rebaseline=rebaseline, only=only)
    target = only if only is not None else f"{len(matrix.all_cells())} cell(s)"
    print(f"captured {target} into {DEFAULT_GOLDENS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
