"""Declarative golden-suite input matrix.

Each :class:`Cell` records the overlay source paths, the optional profile name,
the expected artifact classes, and whether the cell is a refusal cell. Cells whose
fixtures land in later tasks (TASK-007/008/009) are declared now and marked
``pending=True``; ``assert_complete`` still requires a snapshot dir for every
declared cell so an incomplete baseline fails loudly (REQ-001).
"""

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

# Profile store fixture (TASK-007) and its profile name.
PROFILE_NAME = "test-profile"
PROFILE_STORE_FIXTURE = os.path.join(_COMPILER_FIXTURES, "profiles", PROFILE_NAME + ".json")

# Conflict-cell overlays (TASK-008).
CONFLICT_A = os.path.join(_COMPILER_FIXTURES, "conflict-a")
CONFLICT_B = os.path.join(_COMPILER_FIXTURES, "conflict-b")

# Tension-cell overlays (TASK-009).
TENSION_A = os.path.join(_COMPILER_FIXTURES, "tension-a")
TENSION_B = os.path.join(_COMPILER_FIXTURES, "tension-b")

# Anchor-file cell: a prompt_sections contribution to an UNKNOWN anchor that
# carries a content_file. Exercises the anchor-aware content-file selection the
# backend was skipping (blocker B1) — known.md is copied/fingerprinted, extra.md
# (the unknown-anchor file) is excluded exactly as the frozen oracle excludes it.
ANCHORFILE = os.path.join(_COMPILER_FIXTURES, "anchorfile")

# Artifact classes captured per non-refusal cell.
ARTIFACTS_COMPOSED = ("CLAUDE.md", "agents", "lock", "warnings")
# Refusal cells capture the refusal text + exit code instead of files.
ARTIFACTS_REFUSAL = ("refusal", "warnings")


@dataclass(frozen=True)
class Cell:
    name: str
    overlays: tuple = ()
    profile: "str | None" = None
    profile_store: "str | None" = None
    expected_artifacts: tuple = ARTIFACTS_COMPOSED
    refusal: bool = False
    pending: bool = False
    notes: str = ""

    def snapshot_dir(self, goldens_dir: str) -> str:
        return os.path.join(goldens_dir, self.name)


_CELLS = (
    Cell(
        name="core",
        overlays=(),
        notes="No overlays. Exercises base CLAUDE.md and the 13-agent/6-gate inventory invariant.",
    ),
    Cell(
        name="core+overlay",
        overlays=(TEST_OVERLAY,),
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
        pending=True,
        notes=(
            "Profile resolving >=1 overlay via the hermetic temp-HOME store. Profile store "
            "fixture lands in TASK-007."
        ),
    ),
    Cell(
        name="core+conflict",
        overlays=(CONFLICT_A, CONFLICT_B),
        expected_artifacts=ARTIFACTS_REFUSAL,
        refusal=True,
        pending=True,
        notes="known_conflicts pair -> refusal. Overlay fixtures land in TASK-008.",
    ),
    Cell(
        name="core+tension",
        overlays=(TENSION_A, TENSION_B),
        pending=True,
        notes=(
            "Shared review_when_combined_with_tags tag -> semantic-tension warning (proceeds). "
            "Overlay fixtures land in TASK-009."
        ),
    ),
    Cell(
        name="core+anchorfile",
        overlays=(ANCHORFILE,),
        notes=(
            "prompt_sections to an UNKNOWN anchor carrying a content_file. The applied (anchor-"
            "filtered) collector copies/fingerprints known.md only; extra.md (unknown anchor) is "
            "silently excluded. Exposes blocker B1 (backend was collecting unfiltered)."
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


def assert_complete(goldens_dir: str) -> None:
    """Fail if any declared cell lacks a snapshot dir under ``goldens_dir`` (REQ-001)."""
    missing = [
        cell.name
        for cell in _CELLS
        if not os.path.isdir(cell.snapshot_dir(goldens_dir))
    ]
    if missing:
        raise AssertionError(
            "matrix incomplete: missing snapshot dir(s) for cell(s): "
            + ", ".join(missing)
            + f" (under {goldens_dir})"
        )
