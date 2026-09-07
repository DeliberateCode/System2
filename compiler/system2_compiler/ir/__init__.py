"""Public front-end entry point for the System2 compiler."""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import build as _build
from . import conflicts as _conflicts
from . import manifest as _manifest
from . import profiles as _profiles
from .graph import ProfileRef, System2Graph, Warnings

__all__ = ["compose", "CompileResult", "System2Graph"]


@dataclass(frozen=True)
class CompileResult:
    graph: Optional[System2Graph]
    errors: List[str]
    warnings: Warnings
    files_to_write: List[str]
    report: dict


def _empty_warnings() -> Warnings:
    return Warnings(
        validation=[],
        structural_conflicts=[],
        additive_overlaps=[],
        semantic_tensions=[],
        injection=[],
    )


def _refusal(errors: List[str], report: dict) -> CompileResult:
    return CompileResult(
        graph=None,
        errors=errors,
        warnings=_empty_warnings(),
        files_to_write=[],
        report=report,
    )


def _resolved_path_is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _resolve_overlays(
    profile: str, overlay_paths: List[str]
) -> Tuple[Optional[List[str]], Optional[ProfileRef], Optional[List[str]]]:
    """Resolve a profile name to ordered overlay paths and return any errors."""
    try:
        result = _profiles.resolve_profile(profile)
    except _profiles.ProfileError as exc:
        return None, None, [exc.message]

    if result.unknown:
        return None, None, [_profiles.unknown_profile_message(profile)]

    if result.stale:
        errors = [
            f"Profile {profile!r} cannot be activated: overlay path is stale "
            f"({entry.reason}): {entry.path}"
            for entry in result.stale
        ]
        errors.append(
            f"Fix the missing overlay sources, or update profile {profile!r} "
            f"with /system2:compose edit (re-add the corrected paths), then "
            f"retry activation. No files were written."
        )
        return None, None, errors

    if not result.ordered_paths:
        return None, None, [
            f"Profile {profile!r} has no overlays to compose. "
            "No files were written."
        ]

    ref = ProfileRef(name=profile, ordered_source_paths=list(result.ordered_paths))
    return list(result.ordered_paths), ref, None


def compose(
    base_path: str,
    overlay_paths: List[str],
    project_path: str,
    *,
    profile: Optional[str] = None,
    dry_run: bool = False,
    allow_newer_schema: bool = False,
) -> CompileResult:
    """Orchestrate the front-end composition pipeline and return a ``CompileResult``."""
    errors: List[str] = []

    # Refuse every resolved project/source overlap before reading source files.
    real_base = os.path.realpath(base_path)
    real_project = os.path.realpath(project_path)
    if _resolved_path_is_within(real_project, real_base):
        return _refusal(
            [
                f"project path {project_path!r} is inside or equal to the "
                f"plugin directory {base_path!r}; composition must not write "
                f"into the installed plugin"
            ],
            {},
        )
    if _resolved_path_is_within(real_base, real_project):
        return _refusal(
            [
                f"project path {project_path!r} overlaps the plugin directory "
                f"{base_path!r}; composition requires separate project and "
                f"source directories"
            ],
            {},
        )

    # Profile resolution (lifted _activate_profile resolution half).
    active_profile: Optional[ProfileRef] = None
    if profile is not None:
        resolved_paths, active_profile, perr = _resolve_overlays(
            profile, overlay_paths
        )
        if perr is not None:
            return _refusal(perr, {})
        overlay_paths = resolved_paths

    for overlay_path in overlay_paths:
        real_overlay = os.path.realpath(overlay_path)
        if (
            _resolved_path_is_within(real_project, real_overlay)
            or _resolved_path_is_within(real_overlay, real_project)
        ):
            return _refusal(
                [
                    f"project path {project_path!r} and overlay source "
                    f"{overlay_path!r} overlap; composition requires separate "
                    f"project and source directories"
                ],
                {},
            )

    # 1. Load schema and anchor map.
    try:
        schema = _manifest.load_schema(base_path)
        anchor_map = _manifest.load_anchor_map(base_path)
    except (OSError, json.JSONDecodeError) as exc:
        return _refusal([f"Cannot load schema or anchor map: {exc}"], {})

    # 2. Read and validate each overlay manifest.
    validated_manifests: List[dict] = []
    overlay_path_map: Dict[str, str] = {}
    validation_warnings: List[str] = []

    for overlay_path in overlay_paths:
        try:
            man = _manifest.read_manifest(overlay_path)
        except FileNotFoundError:
            errors.append(f"system2.overlay.json not found in {overlay_path}")
            continue
        except json.JSONDecodeError as exc:
            errors.append(
                f"system2.overlay.json is not valid JSON in {overlay_path}: {exc}"
            )
            continue

        vr = _manifest.validate_manifest(man, schema, overlay_path, anchor_map)
        overlay_label = man.get("name", overlay_path)
        if vr.warnings:
            for w in vr.warnings:
                validation_warnings.append(f"[{overlay_label}] {w}")
        if not vr.valid:
            if allow_newer_schema:
                remaining_errors = []
                for e in vr.errors:
                    if "schema_version:" in e and "is not supported" in e:
                        validation_warnings.append(
                            e.replace(
                                "Use --allow-newer-schema", "Degraded mode active"
                            )
                        )
                    else:
                        remaining_errors.append(e)
                if remaining_errors:
                    errors.extend(remaining_errors)
                    continue
                vr.valid = True
            else:
                errors.extend(vr.errors)
                continue

        name = man.get("name", "<unknown>")
        if name in overlay_path_map:
            errors.append(
                f"duplicate overlay name {name!r}: provided from both "
                f"{overlay_path_map[name]} and {overlay_path}"
            )
            continue

        validated_manifests.append(man)
        overlay_path_map[name] = overlay_path

    if errors:
        return _refusal(errors, {"validation_errors": errors})

    # 3. Conflict detection.
    conflict_report = _conflicts.detect_conflicts(validated_manifests, anchor_map)

    if conflict_report.has_structural_conflicts:
        for sc in conflict_report.structural_conflicts:
            parts = [f"Structural conflict: {sc['message']}"]
            if "contribution_type" in sc:
                parts.append(f"  Type: {sc['contribution_type']}")
            if "target" in sc:
                parts.append(f"  Target: {sc['target']}")
            if "suggested_resolution" in sc:
                parts.append(f"  Resolution: {sc['suggested_resolution']}")
            errors.append("\n".join(parts))
        return _refusal(
            errors,
            {"conflicts": _conflict_report_to_dict(conflict_report)},
        )

    # 4. Injection scan over applied overlay content files.
    anchors_by_agent = {
        name: list(info.get("anchors", {}).keys())
        for name, info in anchor_map.get("agents", {}).items()
    }
    injection_warnings: List[str] = []
    for man in validated_manifests:
        name = man.get("name", "<unknown>")
        source_path = overlay_path_map.get(name, "")
        content_files: List[str] = []
        _manifest._collect_applied_content_files(
            man, content_files, anchors_by_agent
        )
        for cf in content_files:
            full = os.path.join(source_path, cf)
            if os.path.isfile(full):
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                injection_warnings.extend(
                    _manifest.scan_for_injection(content, f"{name}/{cf}")
                )

    # 5. Assemble warnings + graph.
    warnings = Warnings(
        validation=list(validation_warnings),
        structural_conflicts=list(conflict_report.structural_conflicts),
        additive_overlaps=list(conflict_report.additive_overlaps),
        semantic_tensions=list(conflict_report.semantic_tensions),
        injection=list(injection_warnings),
    )

    graph = _build.build_graph(
        base_path=base_path,
        validated_manifests=validated_manifests,
        overlay_path_map=overlay_path_map,
        anchor_map=anchor_map,
        schema=schema,
        project_path=project_path,
        profile=active_profile,
        warnings=warnings,
    )

    files_to_write = _compute_files_to_write(
        project_path, validated_manifests, overlay_path_map
    )

    report = {
        "overlays": [
            {"name": m.get("name", ""), "version": m.get("version", "")}
            for m in validated_manifests
        ],
        "validation_warnings": warnings.validation,
        "injection_warnings": warnings.injection,
        "conflicts": _conflict_report_to_dict(conflict_report),
        "files_to_write": files_to_write,
        "dry_run": dry_run,
    }
    if active_profile is not None:
        report["profile"] = {
            "activated": active_profile.name,
            "source_paths": active_profile.ordered_source_paths,
        }

    return CompileResult(
        graph=graph,
        errors=[],
        warnings=warnings,
        files_to_write=files_to_write,
        report=report,
    )


def _compute_files_to_write(
    project_path: str,
    validated_manifests: List[dict],
    overlay_path_map: Dict[str, str],
) -> List[str]:
    """Compute the intended write set (lifted from ``composer.compose``)."""
    files_to_write = [
        os.path.join(project_path, "CLAUDE.md"),
        os.path.join(project_path, "spec", "overlay-manifest.lock"),
    ]
    for man in validated_manifests:
        for aux in man.get("contributions", {}).get("auxiliary_agents", []):
            files_to_write.append(
                os.path.join(
                    project_path, ".claude", "agents",
                    f"{aux.get('name', '')}.md",
                )
            )
    for man in validated_manifests:
        name = man.get("name", "<unknown>")
        overlay_dir = os.path.join(
            project_path, ".system2", "overlays", name
        )
        files_to_write.append(f"{overlay_dir}/ (overlay content)")
    return files_to_write


def _conflict_report_to_dict(cr: "_conflicts.ConflictReport") -> dict:
    """Serialize a ConflictReport to a JSON-safe dict (lifted from composer)."""

    def _serialize_order(order) -> list:
        return [
            {"overlay": overlay, "id": entry.get("id")}
            for overlay, entry in order
        ]

    return {
        "structural_conflicts": cr.structural_conflicts,
        "additive_overlaps": [
            {
                "scope": ao["scope"],
                "overlays": ao["overlays"],
                "order": _serialize_order(ao["order"]),
            }
            for ao in cr.additive_overlaps
        ],
        "semantic_tensions": cr.semantic_tensions,
    }
