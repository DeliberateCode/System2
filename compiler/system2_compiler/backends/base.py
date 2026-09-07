"""The lowering + lifecycle contract."""

import hashlib
import json
import os
from dataclasses import dataclass
from typing import List, Protocol, Tuple, runtime_checkable

from system2_compiler.ir.graph import System2Graph

__all__ = [
    "Backend",
    "UninstallResult",
    "DoctorReport",
    "OWNERSHIP_SCHEMA_VERSION",
    "build_artifact_ownership",
    "lock_sources_outside_project",
    "preflight_artifact_write",
    "validate_artifact_ownership",
    "validate_project_target",
    "verify_owned_artifacts",
]


OWNERSHIP_SCHEMA_VERSION = 1


def _canonicalize_relative_artifact_path(path: object) -> str:
    """Convert a trusted backend path to the portable lock-path form."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError(f"invalid owned artifact path: {path!r}")
    canonical = path.replace("\\", "/")
    parts = canonical.split("/")
    if (
        canonical.startswith("/")
        or (len(canonical) >= 2 and canonical[1] == ":")
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"invalid owned artifact path: {path!r}")
    return "/".join(parts)


def _validate_relative_artifact_path(path: object) -> str:
    """Accept only canonical slash-separated paths from an untrusted lock."""
    canonical = _canonicalize_relative_artifact_path(path)
    if path != canonical:
        raise ValueError(f"invalid owned artifact path: {path!r}")
    return canonical


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _resolved_path_is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def validate_project_target(project_path: str, relative_path: object) -> str:
    """Return a safe absolute target below the resolved project root.

    The relative path must be canonical, every existing path component must be a
    real directory (never a symlink), and the nearest existing parent must remain
    inside the project.  Missing suffixes are allowed so callers can validate
    planned writes before creating their parent directories.
    """
    rel = _validate_relative_artifact_path(relative_path)
    project_root = os.path.abspath(project_path)
    resolved_project_root = os.path.realpath(project_root)
    if not os.path.isdir(project_root):
        raise ValueError(f"project path is not a directory: {project_path!r}")

    current = project_root
    parts = rel.split("/")
    for index, part in enumerate(parts):
        candidate = os.path.join(current, part)
        if os.path.islink(candidate):
            raise ValueError(f"project artifact path contains a symlink: {rel}")
        if not os.path.lexists(candidate):
            break
        if index < len(parts) - 1 and not os.path.isdir(candidate):
            raise ValueError(
                f"project artifact parent is not a directory: "
                f"{'/'.join(parts[:index + 1])}"
            )
        current = candidate

    existing_parent = current if os.path.isdir(current) else os.path.dirname(current)
    parent_real = os.path.realpath(existing_parent)
    if not _resolved_path_is_within(parent_real, resolved_project_root):
        raise ValueError(f"project artifact path escapes project root: {rel}")
    if not os.path.isdir(existing_parent):
        raise ValueError(f"project artifact parent is not a directory: {rel}")

    target = os.path.join(project_root, *parts)
    target_real = os.path.realpath(target)
    if not _resolved_path_is_within(target_real, resolved_project_root):
        raise ValueError(f"project artifact path escapes project root: {rel}")
    return target


def build_artifact_ownership(
    planned: List[Tuple[str, str]], lock_relative_path: str
) -> dict:
    """Build the narrow ownership inventory embedded in a target lock."""
    lock_rel = _canonicalize_relative_artifact_path(lock_relative_path)
    artifacts: List[dict] = []
    seen = set()
    for relative_path, content in planned:
        rel = _canonicalize_relative_artifact_path(relative_path)
        if rel == lock_rel or rel in seen:
            raise ValueError(f"duplicate owned artifact path: {rel!r}")
        seen.add(rel)
        artifacts.append({
            "path": rel,
            "sha256": _sha256_bytes(content.encode("utf-8")),
        })
    artifacts.append({"path": lock_rel})
    return {
        "schema_version": OWNERSHIP_SCHEMA_VERSION,
        "artifacts": artifacts,
    }


def validate_artifact_ownership(
    lock_data: object, lock_relative_path: str
) -> List[Tuple[str, str]]:
    """Validate a lock's complete inventory and return non-lock path/digest pairs."""
    lock_rel = _validate_relative_artifact_path(lock_relative_path)
    if not isinstance(lock_data, dict):
        raise ValueError("lock file is malformed: expected an object")
    ownership = lock_data.get("ownership")
    if not isinstance(ownership, dict):
        raise ValueError("lock file lacks a valid ownership record")
    if set(ownership) != {"schema_version", "artifacts"}:
        raise ValueError("lock file ownership record is malformed")
    if ownership.get("schema_version") != OWNERSHIP_SCHEMA_VERSION:
        raise ValueError("lock file has an unsupported ownership schema version")
    artifacts = ownership.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("lock file lacks a valid ownership artifact inventory")

    owned: List[Tuple[str, str]] = []
    seen = set()
    lock_entries = 0
    for entry in artifacts:
        if not isinstance(entry, dict):
            raise ValueError("lock ownership artifact entry is malformed")
        rel = _validate_relative_artifact_path(entry.get("path"))
        if rel in seen:
            raise ValueError(f"duplicate owned artifact path: {rel!r}")
        seen.add(rel)
        if rel == lock_rel:
            if set(entry) != {"path"}:
                raise ValueError("lock ownership entry must not contain a digest")
            lock_entries += 1
            continue
        if set(entry) != {"path", "sha256"}:
            raise ValueError(f"owned artifact entry is malformed: {rel!r}")
        digest = entry.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)
        ):
            raise ValueError(f"owned artifact digest is malformed: {rel!r}")
        owned.append((rel, digest))
    if lock_entries != 1:
        raise ValueError("ownership inventory must contain the target lock exactly once")
    return owned


def verify_owned_artifacts(
    project_path: str,
    lock_data: object,
    lock_relative_path: str,
    *,
    require_all: bool,
) -> List[str]:
    """Return existing owned paths after validating every present artifact digest."""
    existing: List[str] = []
    for rel, expected_digest in validate_artifact_ownership(
        lock_data, lock_relative_path
    ):
        unchecked_path = os.path.join(os.path.abspath(project_path), *rel.split("/"))
        if os.path.islink(unchecked_path):
            raise ValueError(f"owned artifact is no longer a regular file: {rel}")
        path = validate_project_target(project_path, rel)
        if not os.path.isfile(path):
            if os.path.lexists(path):
                raise ValueError(f"owned artifact is no longer a regular file: {rel}")
            if require_all:
                raise ValueError(f"owned artifact is missing: {rel}")
            continue
        try:
            path = validate_project_target(project_path, rel)
            with open(path, "rb") as fh:
                actual_digest = _sha256_bytes(fh.read())
        except OSError as exc:
            raise ValueError(f"cannot read owned artifact {rel}: {exc}") from exc
        if actual_digest != expected_digest:
            raise ValueError(f"owned artifact was modified: {rel}")
        existing.append(path)
    return existing


def preflight_artifact_write(
    project_path: str,
    planned: List[Tuple[str, str]],
    lock_relative_path: str,
    *,
    recompose: bool,
) -> Tuple[List[str], List[str]]:
    """Return planned writes and validated stale owned files after safe preflight."""
    lock_rel = _canonicalize_relative_artifact_path(lock_relative_path)
    planned_rels = [
        _canonicalize_relative_artifact_path(rel) for rel, _content in planned
    ]
    if recompose:
        lock_path = validate_project_target(project_path, lock_rel)
        if not os.path.isfile(lock_path):
            raise ValueError(f"target lock is not a regular file: {lock_rel}")
        lock_path = validate_project_target(project_path, lock_rel)
        with open(lock_path, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
        owned_paths = verify_owned_artifacts(
            project_path, lock_data, lock_rel, require_all=True
        )
        owned_rels = [
            _canonicalize_relative_artifact_path(
                os.path.relpath(path, os.path.abspath(project_path))
            )
            for path in owned_paths
        ]
        planned_paths = [
            validate_project_target(project_path, rel) for rel in planned_rels
        ]
        owned = set(owned_rels)
        collisions = [
            rel
            for rel, path in zip(planned_rels, planned_paths)
            if rel != lock_rel
            and os.path.lexists(path)
            and rel not in owned
        ]
        stale_paths = [
            validate_project_target(project_path, rel)
            for rel in owned_rels
            if rel not in planned_rels
        ]
    else:
        planned_paths = [
            validate_project_target(project_path, rel) for rel in planned_rels
        ]
        collisions = [
            rel
            for rel, path in zip(planned_rels, planned_paths)
            if os.path.lexists(path)
        ]
        stale_paths = []
    if collisions:
        raise FileExistsError(
            "refusing to overwrite pre-existing project artifact(s): "
            + ", ".join(collisions)
        )
    return planned_paths, stale_paths


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
