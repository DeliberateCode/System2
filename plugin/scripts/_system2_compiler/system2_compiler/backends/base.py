"""The lowering + lifecycle contract.

Phase 1 declared a single ``emit`` seam. Phase 5 grows the contract into a
per-target *lifecycle* (``emit`` + ``uninstall`` + ``doctor`` +
``recompose_from_lock`` + lock helpers) so Goose and Pi reach feature-completeness
alongside Claude — each backend owns its own artifact lifecycle, exactly the seam
Phase 1 cut for ``emit`` (REQ-013/015; design "## Phase 5").

A backend receives only the harness-neutral ``System2Graph`` IR plus the target
project path (and, for the lifecycle verbs, its OWN target lock + artifacts under
``project_path``). It returns written paths or one of the neutral result
dataclasses declared here (``UninstallResult`` / ``DoctorReport``) so all backends
and the CLI share one shape. A backend has no access to overlay manifests, the
anchor map, profiles, or the schema except as already projected onto the IR (the
from-lock recompose path arrives as a recomposed IR). This module imports only the
IR graph type and stdlib typing (module-boundaries).
"""

import os
from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable

from system2_compiler.ir.graph import System2Graph

__all__ = [
    "Backend",
    "UninstallResult",
    "DoctorReport",
    "lock_sources_outside_project",
]


def lock_sources_outside_project(sources: List[str], project_path: str) -> List[dict]:
    """Advisory findings for lock-recorded overlay sources resolving OUTSIDE *project_path*.

    A lock's overlay ``source_path[]`` that resolves outside the project dir is
    legitimate — it is user-equivalent to having composed with an explicit
    ``--overlays`` path pointing elsewhere — but worth surfacing on a read-only
    drift check (security F-P5-1): a recompose/uninstall path will re-read those
    out-of-tree sources. Each returned finding is INFORMATIONAL only; callers must
    NOT let it change ``status`` or the exit code. Symlinks are resolved
    (``realpath``) on both sides so a source that merely points back into the
    project does not trip the advisory.
    """
    findings: List[dict] = []
    proj_real = os.path.realpath(project_path)
    prefix = proj_real + os.sep
    for s in sources:
        if not s:
            continue
        src_real = os.path.realpath(s)
        if src_real != proj_real and not src_real.startswith(prefix):
            findings.append({
                "kind": "source_outside_project",
                "message": (
                    f"lock-recorded overlay source resolves outside the project "
                    f"directory (informational; recompose will read it): {s}"
                ),
            })
    return findings


@dataclass(frozen=True)
class UninstallResult:
    """Neutral, target-agnostic outcome of removing one overlay.

    Ported byte-faithfully from ``composer._uninstall`` for the claude-code
    backend; Goose/Pi populate the same shape for their own trees. ``removed`` is
    the ``{name, version}`` of the removed overlay; ``remaining`` is the post-removal
    overlay set (``{name, version}`` dicts); ``artifacts_removed`` and
    ``files_written`` are absolute paths; ``is_last_overlay`` flags the revert-to-base
    edge; ``injection_warnings`` carries the recompose-path injection findings the CLI
    gates on; ``preview`` holds the dry-run CLAUDE.md (or equivalent) text; ``errors``
    is the refusal list (empty on success).
    """

    removed: dict
    remaining: List[dict]
    artifacts_removed: List[str]
    files_written: List[str]
    is_last_overlay: bool
    injection_warnings: List[str]
    preview: str
    errors: List[str]


@dataclass(frozen=True)
class DoctorReport:
    """Neutral drift/status report for a composed project.

    ``status`` is one of ``{current, stale_base, stale_overlay, broken, no_lock}``;
    claude-code ports ``composer.drift_check``. ``details`` is the per-finding list,
    ``system2_version`` the ``{installed, locked}`` pair, ``overlays`` the per-overlay
    match flags, ``composed`` the CLAUDE.md-composed probe, ``exit_code`` the
    oracle's rule (0 when ``status == current``, else 1). An absent validator does
    NOT force a non-zero exit (OQ-5.2): it is surfaced LOUDLY via
    ``validator_available = False`` plus a ``validator_unavailable`` finding, but the
    exit code still tracks ``status`` alone. ``validator_available`` is ``True`` for
    claude-code (no external validator); goose/pi set it ``False`` with that LOUD
    ``validator_unavailable`` finding when their real validator (``goose recipe
    validate`` / ``pi`` load) is absent — never a silent ``current``.
    """

    status: str
    details: List[dict]
    system2_version: dict
    overlays: List[dict]
    composed: bool
    exit_code: int
    validator_available: bool


@runtime_checkable
class Backend(Protocol):
    """Lower a ``System2Graph`` onto a concrete target and own its lifecycle.

    ``name`` identifies the backend in the CLI registry. ``emit`` performs the
    projection (Phase 1, unchanged). The Phase 5 lifecycle methods read the target's
    OWN lock + artifacts under ``project_path`` (never manifests/anchor-map/profiles/
    schema directly): ``uninstall`` removes one overlay (recompose-remaining or
    revert-to-base on the last, atomic restore); ``doctor`` reports drift/status;
    ``recompose_from_lock`` re-emits from a recomposed IR; ``lock_path`` and
    ``read_lock_overlay_sources`` expose the target's lock so the CLI stays
    target-agnostic.
    """

    name: str

    def emit(self, ir: System2Graph, project_path: str) -> List[str]:
        ...

    def uninstall(
        self,
        project_path: str,
        overlay_name: str,
        *,
        dry_run: bool = False,
        allow_newer_schema: bool = False,
    ) -> UninstallResult:
        ...

    def doctor(self, project_path: str) -> DoctorReport:
        ...

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        ...

    def lock_path(self, project_path: str) -> str:
        ...

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        ...
