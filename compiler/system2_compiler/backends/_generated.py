"""Shared lifecycle for generated guidance-only harness artifacts."""

import json
import os
import re
import shutil
import tempfile
from typing import Callable, List, Optional, Tuple

from system2_compiler.ir.graph import System2Graph

from .base import (
    DoctorReport,
    UninstallResult,
    build_artifact_ownership,
    lock_sources_outside_project,
    preflight_artifact_write,
    validate_artifact_ownership,
    validate_project_target,
    verify_owned_artifacts,
)

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validated_overlay_sources(lock_data: object) -> List[str]:
    if not isinstance(lock_data, dict):
        raise ValueError("Lock file is malformed: expected a JSON object")
    sources = lock_data.get("overlay_sources")
    if not isinstance(sources, list):
        raise ValueError("Lock file is malformed: 'overlay_sources' is not a list")
    if any(not isinstance(source, str) or not source for source in sources):
        raise ValueError(
            "Lock file is malformed: 'overlay_sources' must contain only "
            "non-empty string paths"
        )
    return list(sources)


def _overlay_records(lock_data: dict) -> List[dict]:
    sources = validated_overlay_sources(lock_data)
    records = lock_data.get("overlays")
    if records is None:  # Locks from the first generated distributions used basenames.
        return [{"name": os.path.basename(os.path.normpath(source)), "source_path": source}
                for source in sources]
    if (not isinstance(records, list) or len(records) != len(sources)
            or any(not isinstance(record, dict) or record.get("source_path") != source
                   or not isinstance(record.get("name"), str) or not _KEBAB_RE.fullmatch(record["name"])
                   for record, source in zip(records, sources))):
        raise ValueError("Lock file is malformed: invalid overlay inventory")
    if len({record['name'] for record in records}) != len(records):
        raise ValueError("Lock file is malformed: duplicate overlay names")
    return records


def _graph_overlays(ir: System2Graph) -> List[dict]:
    return [{"name": item.manifest["name"], "version": item.manifest["version"],
             "source_path": item.source_path} for item in ir.overlay_inputs]


def _makedirs_tracked(dir_path: str, dirs_created: List[str]) -> None:
    if not dir_path or os.path.isdir(dir_path):
        return
    missing: List[str] = []
    current = dir_path
    while not os.path.isdir(current):
        missing.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    os.makedirs(dir_path, exist_ok=True)
    dirs_created.extend(missing)


def _default_file_mode(existing_path: Optional[str] = None) -> int:
    if existing_path is not None and os.path.exists(existing_path):
        return os.stat(existing_path).st_mode & 0o777
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def write_outputs(
    project_path: str,
    planned: List[Tuple[str, str]],
    stale_paths: Optional[List[str]] = None,
) -> List[str]:
    """Write generated files atomically and restore prior bytes on failure."""
    project_root = os.path.abspath(project_path)

    def relative(path: str) -> str:
        return os.path.relpath(path, project_root).replace(os.sep, "/")

    stale_rels = [relative(path) for path in stale_paths or []]
    for rel in stale_rels:
        validate_project_target(project_path, rel)
    for rel, _content in planned:
        validate_project_target(project_path, rel.replace(os.sep, "/"))

    backups: List[Tuple[str, str]] = []
    newly_created: List[str] = []
    dirs_created: List[str] = []
    written: List[str] = []
    try:
        for rel in stale_rels:
            path = validate_project_target(project_path, rel)
            if not os.path.isfile(path):
                raise ValueError(f"owned artifact is no longer a regular file: {rel}")
            fd, backup = tempfile.mkstemp(
                prefix=f".{os.path.basename(path)}.",
                suffix=".bak",
                dir=os.path.dirname(path),
            )
            os.close(fd)
            shutil.copy2(path, backup)
            backups.append((path, backup))
            os.unlink(path)

        for rel, content in planned:
            canonical_rel = rel.replace(os.sep, "/")
            dst = validate_project_target(project_path, canonical_rel)
            parent = os.path.dirname(dst)
            _makedirs_tracked(parent, dirs_created)
            dst = validate_project_target(project_path, canonical_rel)
            if os.path.lexists(dst):
                if not os.path.isfile(dst):
                    raise ValueError(
                        f"owned artifact is no longer a regular file: {canonical_rel}"
                    )
                fd, backup = tempfile.mkstemp(
                    prefix=f".{os.path.basename(dst)}.", suffix=".bak", dir=parent
                )
                os.close(fd)
                shutil.copy2(dst, backup)
                backups.append((dst, backup))
            else:
                newly_created.append(dst)

            fd, staged = tempfile.mkstemp(dir=parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(content)
                os.chmod(staged, _default_file_mode(dst))
                os.replace(staged, dst)
            except Exception:
                if os.path.exists(staged):
                    os.unlink(staged)
                raise
            written.append(dst)
    except Exception:
        for original, backup in reversed(backups):
            try:
                if os.path.exists(backup):
                    shutil.copy2(backup, original)
                    os.unlink(backup)
            except Exception:
                pass
        for created in newly_created:
            try:
                if os.path.exists(created):
                    os.unlink(created)
            except Exception:
                pass
        for directory in dirs_created:
            try:
                os.rmdir(directory)
            except Exception:
                pass
        raise

    for _original, backup in backups:
        try:
            if os.path.exists(backup):
                os.unlink(backup)
        except OSError:
            pass
    return written


class GeneratedArtifactBackend:
    """Common owned-artifact lifecycle for Codex and Pi guidance projections."""

    name = ""
    lock_name = ""
    version = ""
    version_key = ""
    runtime_boundary = ""

    def __init__(
        self,
        base_path: Optional[str] = None,
        compose_fn: Optional[Callable[..., object]] = None,
        overlay_sources: Optional[List[str]] = None,
        *,
        workspace: bool = False,
    ) -> None:
        self._base_path = base_path
        self._compose_fn = compose_fn
        self._overlay_sources = list(overlay_sources) if overlay_sources else None
        self._prefix = f".system2/{self.name}/" if workspace else ""
        self.lock_name = self._prefix + self.lock_name

    def _planned_files(self, ir: System2Graph) -> List[Tuple[str, str]]:
        raise NotImplementedError

    def _lock_details(self, ir: System2Graph) -> dict:
        raise NotImplementedError

    def _artifacts(self, ir: System2Graph) -> List[Tuple[str, str]]:
        return [(self._prefix + path.replace(os.sep, "/"), text)
                for path, text in self._planned_files(ir)]

    def _validate_legacy_overlay_sources(self, ir: System2Graph) -> None:
        if (
            self._overlay_sources is not None
            and tuple(self._overlay_sources) != ir.overlay_sources
        ):
            raise ValueError(
                "constructor overlay_sources do not match authoritative graph "
                "provenance"
            )

    def emit(self, ir: System2Graph, project_path: str) -> List[str]:
        return self._emit_graph(
            ir,
            project_path,
            recompose=os.path.lexists(self.lock_path(project_path)),
        )

    def plan(self, ir: System2Graph, project_path: str) -> List[str]:
        return self._emit_graph(
            ir,
            project_path,
            dry_run=True,
            recompose=os.path.lexists(self.lock_path(project_path)),
        )

    def _emit_graph(
        self,
        ir: System2Graph,
        project_path: str,
        *,
        dry_run: bool = False,
        recompose: bool = False,
    ) -> List[str]:
        self._validate_legacy_overlay_sources(ir)
        planned = self._artifacts(ir)
        lock = self._lock_details(ir)
        lock["ownership"] = build_artifact_ownership(planned, self.lock_name)
        lock["overlay_sources"] = list(ir.overlay_sources)
        lock["overlays"] = _graph_overlays(ir)
        planned.append((self.lock_name, json.dumps(lock, indent=2) + "\n"))
        planned_paths, stale_paths = preflight_artifact_write(
            project_path, planned, self.lock_name, recompose=recompose
        )
        if dry_run or bool(getattr(ir, "dry_run", False)):
            return planned_paths + ["(remove) " + path for path in stale_paths]
        return write_outputs(project_path, planned, stale_paths)

    def lock_path(self, project_path: str) -> str:
        return os.path.join(project_path, self.lock_name)

    def read_lock_overlays(self, project_path: str) -> List[dict]:
        lock_path = validate_project_target(project_path, self.lock_name)
        if not os.path.isfile(lock_path):
            raise FileNotFoundError(lock_path)
        with open(lock_path, "r", encoding="utf-8") as stream:
            return _overlay_records(json.load(stream))

    def read_lock_overlay_sources(self, project_path: str) -> List[str]:
        return [record["source_path"] for record in self.read_lock_overlays(project_path)]

    def recompose_from_lock(
        self, ir: System2Graph, project_path: str, *, dry_run: bool = False
    ) -> List[str]:
        return self._emit_graph(ir, project_path, dry_run=dry_run, recompose=True)

    def uninstall(
        self,
        project_path: str,
        overlay_name_to_remove: str,
        *,
        dry_run: bool = False,
        allow_newer_schema: bool = False,
        allow_injection: bool = False,
    ) -> UninstallResult:
        def error_result(errors: List[str]) -> UninstallResult:
            return UninstallResult(
                removed={},
                remaining=[],
                artifacts_removed=[],
                files_written=[],
                is_last_overlay=False,
                injection_warnings=[],
                preview="",
                errors=errors,
            )

        if not _KEBAB_RE.match(overlay_name_to_remove):
            return error_result([
                f"Invalid overlay name {overlay_name_to_remove!r}: must be kebab-case "
                "(lowercase alphanumeric, hyphens only)"
            ])

        try:
            lock_path = validate_project_target(project_path, self.lock_name)
        except ValueError as exc:
            return error_result([str(exc)])
        if not os.path.isfile(lock_path):
            return error_result(["No lock file found; no overlays are composed"])
        try:
            with open(lock_path, "r", encoding="utf-8") as stream:
                lock_data = json.load(stream)
        except json.JSONDecodeError:
            return error_result(["Lock file is malformed (invalid JSON)"])
        except OSError as exc:
            return error_result([f"Cannot read lock file: {exc}"])

        try:
            records = _overlay_records(lock_data)
            owned_artifacts = verify_owned_artifacts(
                project_path, lock_data, self.lock_name, require_all=False
            )
        except ValueError as exc:
            return error_result([str(exc)])

        installed = [record["name"] for record in records]
        if overlay_name_to_remove not in installed:
            return error_result([
                f"Overlay {overlay_name_to_remove!r} is not installed. "
                f"Installed: {installed}"
            ])

        remaining = [record for record in records if record["name"] != overlay_name_to_remove]
        remaining_sources = [record["source_path"] for record in remaining]
        if not remaining_sources:
            return self._uninstall_last_overlay(
                project_path,
                overlay_name_to_remove,
                dry_run,
                owned_artifacts,
            )

        compose_fn = self._require_compose_fn("uninstall")
        result = compose_fn(
            self._require_base_path("uninstall"),
            remaining_sources,
            project_path,
            dry_run=dry_run,
            allow_newer_schema=allow_newer_schema,
        )
        if getattr(result, "errors", None):
            errors = list(result.errors)
            errors.append(
                "Remediation: verify that all remaining overlay source paths are "
                "accessible, then retry."
            )
            return UninstallResult(
                removed={"name": overlay_name_to_remove},
                remaining=remaining,
                artifacts_removed=[],
                files_written=[],
                is_last_overlay=False,
                injection_warnings=[],
                preview="",
                errors=errors,
            )

        report = result.report or {}
        warnings = list(report.get("injection_warnings", []))
        try:
            files_written = self._emit_graph(
                result.graph,
                project_path,
                dry_run=dry_run or (bool(warnings) and not allow_injection),
                recompose=True,
            )
        except (FileExistsError, OSError, ValueError, json.JSONDecodeError) as exc:
            return error_result([f"Cannot safely recompose owned artifacts: {exc}"])

        return UninstallResult(
            removed={"name": overlay_name_to_remove},
            remaining=remaining,
            artifacts_removed=[],
            files_written=files_written,
            is_last_overlay=False,
            injection_warnings=warnings,
            preview="",
            errors=[],
        )

    def _uninstall_last_overlay(
        self,
        project_path: str,
        overlay_name_to_remove: str,
        dry_run: bool,
        owned_artifacts: List[str],
    ) -> UninstallResult:
        artifacts = list(owned_artifacts) + [
            validate_project_target(project_path, self.lock_name)
        ]
        relative_paths = [
            os.path.relpath(path, os.path.abspath(project_path)).replace(os.sep, "/")
            for path in artifacts
        ]
        artifacts = [
            validate_project_target(project_path, rel) for rel in relative_paths
        ]
        if dry_run:
            return UninstallResult(
                removed={"name": overlay_name_to_remove},
                remaining=[],
                artifacts_removed=artifacts,
                files_written=["(remove) " + path for path in artifacts],
                is_last_overlay=True,
                injection_warnings=[],
                preview="",
                errors=[],
            )

        backups: List[Tuple[str, str]] = []
        try:
            for rel in relative_paths:
                path = validate_project_target(project_path, rel)
                if not os.path.isfile(path):
                    raise ValueError(
                        f"owned artifact is no longer a regular file: {rel}"
                    )
                fd, backup = tempfile.mkstemp(
                    prefix=f".{os.path.basename(path)}.",
                    suffix=".bak",
                    dir=os.path.dirname(path),
                )
                os.close(fd)
                shutil.copy2(path, backup)
                backups.append((path, backup))
            for rel in relative_paths:
                os.unlink(validate_project_target(project_path, rel))
        except Exception:
            for original, backup in reversed(backups):
                try:
                    if os.path.exists(backup):
                        shutil.copy2(backup, original)
                        os.unlink(backup)
                except Exception:
                    pass
            raise

        for _original, backup in backups:
            try:
                if os.path.exists(backup):
                    os.unlink(backup)
            except OSError:
                pass
        return UninstallResult(
            removed={"name": overlay_name_to_remove},
            remaining=[],
            artifacts_removed=artifacts,
            files_written=[],
            is_last_overlay=True,
            injection_warnings=[],
            preview="",
            errors=[],
        )

    def doctor(self, project_path: str) -> DoctorReport:
        details: List[dict] = []
        unchecked_lock = self.lock_path(project_path)
        if not os.path.lexists(unchecked_lock):
            return DoctorReport(
                status="no_lock",
                details=[{
                    "kind": "no_lock",
                    "message": f"No {self.lock_name} found; nothing is composed.",
                }],
                system2_version={"installed": "", "locked": ""},
                overlays=[],
                composed=False,
                exit_code=1,
                validator_available=False,
            )
        try:
            lock_path = validate_project_target(project_path, self.lock_name)
        except ValueError as exc:
            return self._broken_report(f"Unsafe lock path: {exc}")
        if not os.path.isfile(lock_path):
            return self._broken_report("Lock path is not a regular file.")
        try:
            with open(lock_path, "r", encoding="utf-8") as stream:
                lock_data = json.load(stream)
        except (json.JSONDecodeError, OSError) as exc:
            return self._broken_report(f"Lock file is unreadable: {exc}")
        if not isinstance(lock_data, dict):
            return self._broken_report("Lock file is malformed: expected a JSON object.")
        try:
            records = _overlay_records(lock_data)
        except ValueError as exc:
            return self._broken_report(str(exc), locked=lock_data.get(self.version_key, ""))

        sources = [record["source_path"] for record in records]
        overlays = [
            {"name": record["name"], "source_present": os.path.isdir(record["source_path"])}
            for record in records
        ]
        status = "ok"
        for source in sources:
            if not os.path.isdir(source):
                status = "stale_overlay"
                details.append({
                    "kind": "stale_overlay",
                    "message": f"recorded overlay source is missing: {source}",
                })
        try:
            verify_owned_artifacts(
                project_path, lock_data, self.lock_name, require_all=True
            )
        except ValueError as exc:
            status = "broken"
            details.append({
                "kind": "broken",
                "message": f"Generated {self.name} artifact integrity failure: {exc}",
            })

        if status == "ok" and self._prefix:
            if lock_data.get(self.version_key) != self.version:
                status = "stale_base"
                details.append({"kind": status, "message": "The composed workflow uses a different channel version."})
            elif not self._workspace_is_current(project_path, sources, lock_data):
                status = "stale_overlay"
                details.append({"kind": status, "message": "Current inputs no longer reproduce this composition. Preview and refresh it from the lock."})

        details.append({"kind": "runtime_boundary", "message": self.runtime_boundary})
        details.extend(lock_sources_outside_project(sources, project_path))
        return DoctorReport(
            status=status,
            details=details,
            system2_version={
                "installed": self.version,
                "locked": lock_data.get(self.version_key, ""),
            },
            overlays=overlays,
            composed=True,
            exit_code=0 if status == "ok" else 1,
            validator_available=True,
        )

    def _workspace_is_current(self, project_path: str, sources: List[str], lock: dict) -> bool:
        try:
            result = self._require_compose_fn("doctor")(
                self._require_base_path("doctor"), sources, project_path, dry_run=True,
            )
            if result.graph is None or _graph_overlays(result.graph) != lock.get("overlays"):
                return False
            expected = build_artifact_ownership(self._artifacts(result.graph), self.lock_name)
            return dict(validate_artifact_ownership({"ownership": expected}, self.lock_name)) == dict(
                validate_artifact_ownership(lock, self.lock_name)
            )
        except (OSError, ValueError):
            return False

    def _broken_report(self, message: str, locked: str = "") -> DoctorReport:
        return DoctorReport(
            status="broken",
            details=[{"kind": "broken", "message": message}],
            system2_version={"installed": self.version, "locked": locked},
            overlays=[],
            composed=True,
            exit_code=1,
            validator_available=False,
        )

    def _require_base_path(self, verb: str) -> str:
        if not self._base_path:
            raise ValueError(
                f"{type(self).__name__}.{verb} requires base_path; construct "
                f"{type(self).__name__}(base_path=...)"
            )
        return self._base_path

    def _require_compose_fn(self, verb: str) -> Callable[..., object]:
        if self._compose_fn is None:
            raise ValueError(
                f"{type(self).__name__}.{verb} requires compose_fn to recompose "
                "the remaining overlay set"
            )
        return self._compose_fn
