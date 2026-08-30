"""The lowering + lifecycle contract."""

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
    """Advisory findings for lock-recorded overlay sources resolving OUTSIDE *project_path*."""
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
    """Neutral, target-agnostic outcome of removing one overlay."""

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
    """Neutral drift/status report for a composed project."""

    status: str
    details: List[dict]
    system2_version: dict
    overlays: List[dict]
    composed: bool
    exit_code: int
    validator_available: bool


@runtime_checkable
class Backend(Protocol):
    """Lower a ``System2Graph`` onto a concrete target and own its lifecycle."""

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
