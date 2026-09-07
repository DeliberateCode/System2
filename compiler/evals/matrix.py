"""Declarative golden-suite input matrix."""

import os
from dataclasses import dataclass, field

from evals import oracle

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Reused fixtures live under the plugin repo's evals tree (read-only). Resolved
# via the portable plugin-root logic (sibling layout or SYSTEM2_PLUGIN_ROOT).
_PLUGIN_FIXTURES = os.path.join(oracle.PLUGIN_REPO_ROOT, "evals", "fixtures")
# New fixtures live under the compiler's own evals tree.
_COMPILER_FIXTURES = os.path.join(_THIS_DIR, "fixtures")

TEST_OVERLAY = os.path.join(_PLUGIN_FIXTURES, "test-overlay")

# Profile store fixture and its profile name.
PROFILE_NAME = "test-profile"
PROFILE_STORE_FIXTURE = os.path.join(_COMPILER_FIXTURES, "profiles", PROFILE_NAME + ".json")

# Conflict-cell overlays.
CONFLICT_A = os.path.join(_COMPILER_FIXTURES, "conflict-a")
CONFLICT_B = os.path.join(_COMPILER_FIXTURES, "conflict-b")

# Tension-cell overlays.
TENSION_A = os.path.join(_COMPILER_FIXTURES, "tension-a")
TENSION_B = os.path.join(_COMPILER_FIXTURES, "tension-b")

# Anchor-file cell: a prompt_sections contribution to an UNKNOWN anchor that carries a content_file.
ANCHORFILE = os.path.join(_COMPILER_FIXTURES, "anchorfile")

# Every cell declares both required classes and an exact closed-world file set.
ARTIFACTS_CORE = ("base-template", "structural")
ARTIFACTS_COMPOSED = ("CLAUDE.md", "lock", "warnings")
ARTIFACTS_REFUSAL = ("refusal", "warnings", "exit-code")
_FILES_CORE = ("base_template.md", "structural_goldens.json")
_FILES_REFUSAL = ("exit_code.txt", "refusal.txt", "warnings.txt")
_FILES_COMPOSED = ("CLAUDE.md", os.path.join("spec", "overlay-manifest.lock"), "warnings.txt")
_TEST_OVERLAY_FILES = (
    os.path.join(".claude", "agents", "test-scout.md"),
    os.path.join(".system2", "overlays", "test-overlay", "agents", "test-scout.md"),
    os.path.join(".system2", "overlays", "test-overlay", "contributions", "agents", "executor-discipline.md"),
    os.path.join(".system2", "overlays", "test-overlay", "contributions", "orchestrator", "gate-3-consultation.md"),
    os.path.join(".system2", "overlays", "test-overlay", "contributions", "orchestrator", "principles.md"),
)


@dataclass(frozen=True)
class Cell:
    name: str
    overlays: tuple = ()
    profile: "str | None" = None
    profile_store: "str | None" = None
    expected_artifacts: tuple = ARTIFACTS_COMPOSED
    expected_files: tuple = _FILES_COMPOSED
    refusal: bool = False
    pending: bool = False
    notes: str = ""

    def snapshot_dir(self, goldens_dir: str) -> str:
        return os.path.join(goldens_dir, self.name)


_CELLS = (
    Cell(
        name="core",
        overlays=(),
        expected_artifacts=ARTIFACTS_CORE,
        expected_files=_FILES_CORE,
        notes="Static core exception: base template plus structural inventory only.",
    ),
    Cell(
        name="core+overlay",
        overlays=(TEST_OVERLAY,),
        expected_artifacts=ARTIFACTS_COMPOSED + ("agents", "overlay-content"),
        expected_files=_FILES_COMPOSED + _TEST_OVERLAY_FILES,
        notes=(
            "Reuses System2/evals/fixtures/test-overlay: principles, gate-3 consultation, "
            "advisory source, the executor.implementation_discipline anchor contribution, a spec "
            "required-section, and the test-scout auxiliary agent."
        ),
    ),
    Cell(
        name="core+overlay+profile",
        overlays=(),
        profile=PROFILE_NAME,
        profile_store=PROFILE_STORE_FIXTURE,
        expected_artifacts=ARTIFACTS_COMPOSED + ("agents", "overlay-content"),
        expected_files=_FILES_COMPOSED + _TEST_OVERLAY_FILES,
        pending=True,
        notes="Profile resolving >=1 overlay via the hermetic temp-HOME store.",
    ),
    Cell(
        name="core+conflict",
        overlays=(CONFLICT_A, CONFLICT_B),
        expected_artifacts=ARTIFACTS_REFUSAL,
        expected_files=_FILES_REFUSAL,
        refusal=True,
        pending=True,
        notes="known_conflicts pair -> refusal.",
    ),
    Cell(
        name="core+tension",
        overlays=(TENSION_A, TENSION_B),
        pending=True,
        notes="Shared review_when_combined_with_tags tag -> semantic-tension warning (proceeds).",
    ),
    Cell(
        name="core+anchorfile",
        overlays=(ANCHORFILE,),
        expected_artifacts=ARTIFACTS_COMPOSED + ("overlay-content",),
        expected_files=_FILES_COMPOSED + (
            os.path.join(".system2", "overlays", "anchorfile", "contributions", "known.md"),
        ),
        notes=(
            "An unknown-anchor contribution carrying a content_file is excluded. The applied "
            "collector copies and fingerprints known.md only, never extra.md."
        ),
    ),
)


def all_cells() -> tuple:
    """Return every declared matrix cell."""
    return _CELLS


def get_cell(name: str) -> Cell:
    for cell in _CELLS:
        if cell.name == name:
            return cell
    raise KeyError(name)


def resolved_overlay_sources(cell: Cell) -> tuple:
    if cell.profile is None:
        return tuple(os.path.abspath(path) for path in cell.overlays)
    with open(cell.profile_store, encoding="utf-8") as fh:
        store = __import__("json").load(fh)
    profile = store["profiles"][cell.profile]
    return tuple(TEST_OVERLAY for entry in profile.get("overlays", []) if entry.get("path"))


def snapshot_files(cell: Cell, goldens_dir: str) -> set:
    cell_dir = cell.snapshot_dir(goldens_dir)
    if not os.path.isdir(cell_dir):
        return set()
    return {
        os.path.relpath(os.path.join(root, name), cell_dir)
        for root, _, names in os.walk(cell_dir)
        for name in names
    }


def assert_complete(goldens_dir: str) -> None:
    """Require every cell's exact declared snapshot inventory."""
    failures = []
    for cell in _CELLS:
        actual = snapshot_files(cell, goldens_dir)
        expected = set(cell.expected_files)
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            failures.append(f"{cell.name}: missing={missing}, extra={extra}")
    if failures:
        raise AssertionError("matrix inventory mismatch: " + "; ".join(failures))
