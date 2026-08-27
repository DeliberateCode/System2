"""The ``system2`` CLI — full lifecycle parity with the frozen ``composer.py``.

Phase 1 shipped a single implicit ``compile`` verb. Phase 5 grows this into a
subcommand dispatcher reaching FULL parity with the plugin's ``composer.py``
``main()`` contract:

    system2 compile   --target {claude-code|pi|codex} [--profile N | --overlays P
                      | --from-lock] --base B --project P [--dry-run]
                      [--allow-newer-schema] [--allow-injection] [--format text|json]
    system2 uninstall --target {…} --base B --project P --name OVERLAY
                      [--dry-run] [--allow-injection] [--allow-newer-schema] [--format …]
    system2 doctor    --target {…} --base B --project P [--format …]
    system2 from-lock --target {…} --base B --project P
                      [--dry-run] [--allow-injection] [--allow-newer-schema] [--format …]
    system2 profile   {list | inspect NAME | save NAME | create NAME --paths P |
                       edit NAME [--add P]… [--remove OVERLAY]… | delete NAME}
                      [--project P] [--force] [--format …]   # harness-neutral; no --target

For back-compat, a leading ``--target`` (or no subcommand) dispatches to
``compile`` so the Phase-0..4 ``main(["--target", …])`` invocation is unchanged.

The ``claude-code`` path of every verb reproduces the frozen oracle's EXACT arg
names, exit codes, stdout/stderr report bodies, and JSON envelopes (the contract
the plugin skills parse, and the post-flip shim later mirrors). Pi/Codex get the
same verbs, target-aware. Profiles are harness-NEUTRAL (an activated profile
feeds compose for ANY ``--target``) and write only ``~/.system2/profiles.json``.

Imports only ``ir`` (``__init__`` + ``graph`` + ``profiles``), ``backends.*``,
and stdlib (module-boundaries).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from typing import List, Optional, Tuple

from system2_compiler import ir
from system2_compiler.ir import profiles as ir_profiles
from system2_compiler.ir.graph import System2Graph
from system2_compiler.backends.base import Backend, DoctorReport, UninstallResult
from system2_compiler.backends.claude_code import ClaudeCodeBackend
from system2_compiler.backends.pi import PiBackend
from system2_compiler.backends import codex as codex_ops
from system2_compiler.backends.codex import CodexBackend

__all__ = ["main"]

_TARGETS = ("claude-code", "pi", "codex")
_VERBS = ("compile", "uninstall", "doctor", "from-lock", "profile")

# Static backend registry (the Phase-3/4 CLI surface): target -> a default-
# constructed backend instance. ``emit`` is fully IR-driven and needs no
# constructor injection, so these defaults serve ``compile`` and the registry
# tests. The lifecycle verbs build base_path/compose_fn-injected instances via
# ``_backend_for`` (the lifecycle methods require those).
_BACKENDS = {
    "claude-code": ClaudeCodeBackend(),
    "pi": PiBackend(),
    "codex": CodexBackend(),
}


def _select_backend(target: str) -> Backend:
    """Return the registered default backend for *target* (Phase-3/4 surface)."""
    return _BACKENDS[target]

# Contribution-type suffixes deferred (declared but not applied this phase). The
# CLI reconstructs the oracle's report.contributions_applied / report.deferred
# from the IR's ordered scopes (the same rule the backend's lock uses), so the
# CLI's report envelope is byte-identical to composer.py's.
_DEFERRED_SUFFIXES = (".tools", ".hooks")


def _backend_for(
    target: str, base_path: str, overlay_sources: Optional[List[str]] = None
) -> Backend:
    """Construct the active backend, injecting ``base_path``/``compose_fn``.

    The lifecycle verbs (uninstall/doctor/from-lock) need the System2 plugin root
    (base template + version + anchor map) and ``ir.compose`` to recompose the
    remaining/lock overlay set; ``emit`` itself is fully IR-driven and needs
    neither. Pi/Codex additionally accept the neutral ``overlay_sources`` list so
    their from-lock/uninstall recompose targets the right overlay set.
    """
    if target == "claude-code":
        return ClaudeCodeBackend(base_path=base_path, compose_fn=ir.compose)
    if target == "pi":
        return PiBackend(
            base_path=base_path, compose_fn=ir.compose,
            overlay_sources=overlay_sources,
        )
    if target == "codex":
        return CodexBackend(
            base_path=base_path, compose_fn=ir.compose,
            overlay_sources=overlay_sources,
        )
    raise ValueError(f"unknown target {target!r}")


# ---------------------------------------------------------------------------
# Oracle-shaped report reconstruction (byte-parity with composer.py)
# ---------------------------------------------------------------------------

def _contributions_applied(graph: System2Graph) -> dict:
    """Reconstruct ``report.contributions_applied`` from the IR ordered scopes.

    Mirrors the oracle: skip deferred suffixes; per non-deferred scope collect
    ``id`` | ``name`` | ``tool`` per contribution; include the scope only when at
    least one id is present. Scope iteration follows the IR's insertion order,
    which the front-end built identically to the oracle.
    """
    applied: dict = {}
    for (type_path, _target), records in graph.contributions.scopes.items():
        if any(type_path.endswith(s) for s in _DEFERRED_SUFFIXES):
            continue
        ids = []
        for rec in records:
            contrib = rec.raw
            cid = (
                contrib.get("id")
                or contrib.get("name")
                or contrib.get("tool")
                or ""
            )
            if cid:
                ids.append(cid)
        if ids:
            applied[type_path] = ids
    return applied


def _deferred(graph: System2Graph) -> dict:
    """Reconstruct ``report.deferred`` (per deferred scope, contribution count)."""
    deferred: dict = {}
    for (type_path, _target), records in graph.contributions.scopes.items():
        if any(type_path.endswith(s) for s in _DEFERRED_SUFFIXES):
            deferred[type_path] = deferred.get(type_path, 0) + len(records)
    return deferred


def _composed_lines(claude_md_text: str) -> int:
    return claude_md_text.count("\n") + 1


def _build_compose_report(
    result, claude_md_text: str
) -> dict:
    """Build the oracle-shaped compose ``report`` dict.

    Key insertion order matches ``composer.compose``'s report exactly so the JSON
    envelope is byte-identical: ``overlays``, ``contributions_applied``,
    ``deferred``, ``injection_warnings``, ``validation_warnings``, ``conflicts``,
    ``composed_lines``, ``files_to_write`` (+ optional ``size_warning``,
    ``profile``). ``files_written`` is appended by the caller after a successful
    write.
    """
    graph = result.graph
    composed_lines = _composed_lines(claude_md_text)
    report = {
        "overlays": [
            {"name": ov["name"], "version": ov["version"]}
            for ov in result.report.get("overlays", [])
        ],
        "contributions_applied": _contributions_applied(graph),
        "deferred": _deferred(graph),
        "injection_warnings": list(result.warnings.injection),
        "validation_warnings": list(result.warnings.validation),
        "conflicts": result.report.get("conflicts", {}),
        "composed_lines": composed_lines,
        "files_to_write": list(result.files_to_write),
    }
    if composed_lines > 500:
        report["size_warning"] = (
            f"Composed CLAUDE.md is {composed_lines} lines "
            f"(exceeds 500-line threshold)."
        )
    if "profile" in result.report:
        report["profile"] = result.report["profile"]
    return report


def _render_composed_text(graph: System2Graph, backend: Backend) -> str:
    """Render the composed CLAUDE.md text without writing into the project.

    The oracle's report carries ``composed_lines`` and the dry-run text preview;
    the IR-driven ``emit`` only returns paths. To obtain the composed text
    byte-faithfully WITHOUT touching the real project, emit into a throwaway temp
    dir (claude-code writes a ``CLAUDE.md`` there) and read it back. Non-claude
    targets have no CLAUDE.md; they return an empty string (composed_lines is a
    claude-only report field).
    """
    if backend.name != "claude-code":
        return ""
    tmp = tempfile.mkdtemp(prefix="system2-render-")
    try:
        backend.emit(graph, tmp)
        claude_path = os.path.join(tmp, "CLAUDE.md")
        if os.path.isfile(claude_path):
            with open(claude_path, "r", encoding="utf-8") as fh:
                return fh.read()
        return ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Shared stderr warning emission (relocated verbatim from the oracle, REQ-046)
# ---------------------------------------------------------------------------

def _emit_stderr_warnings(report: dict) -> None:
    """Emit all warning categories to stderr (verbatim from ``composer._emit_stderr_warnings``).

    Order is load-bearing for byte-identity with the oracle (REQ-046):
    size_warning, validation_warnings, injection_warnings, then semantic tensions.
    """
    if "size_warning" in report:
        sys.stderr.write(f"  WARNING: {report['size_warning']}\n")
    for vw in report.get("validation_warnings", []):
        sys.stderr.write(f"  WARNING: {vw}\n")
    for iw in report.get("injection_warnings", []):
        sys.stderr.write(f"  WARNING: {iw}\n")
    conflicts = report.get("conflicts", {})
    if conflicts.get("semantic_tensions"):
        sys.stderr.write("Semantic tensions (warnings):\n")
        for st in conflicts["semantic_tensions"]:
            sys.stderr.write(f"  WARNING: {st['message']}\n")


def _emit_error(msg: str, fmt: str) -> None:
    """Emit a single error (verbatim from ``composer._emit_error``)."""
    if fmt == "json":
        sys.stdout.write(json.dumps({"status": "error", "message": msg}) + "\n")
    else:
        sys.stderr.write(f"ERROR: {msg}\n")


def _classify_exit(errors: List[str]) -> int:
    """Classify a refusal's exit code (verbatim from the oracle's classifier)."""
    if any("Structural conflict" in e for e in errors):
        return 2
    if any("Cannot" in e or "I/O" in e for e in errors):
        return 3
    return 1


def _emit_refusal(errors: List[str], report: dict, fmt: str) -> int:
    """Render a compose/uninstall/from-lock refusal exactly as the oracle does."""
    exit_code = _classify_exit(errors)
    if fmt == "json":
        sys.stdout.write(json.dumps({
            "status": "error",
            "errors": errors,
            "report": report,
        }, indent=2) + "\n")
    else:
        for err in errors:
            sys.stderr.write(f"ERROR: {err}\n")
    return exit_code


# ---------------------------------------------------------------------------
# compose / from-lock (shared write pipeline)
# ---------------------------------------------------------------------------

def _resolve_overlay_paths(
    target: str, base_path: str, project_path: str,
    overlays: str, from_lock: bool, profile: Optional[str], fmt: str,
) -> Tuple[Optional[List[str]], Optional[int]]:
    """Resolve the overlay source set for a write verb.

    Returns ``(overlay_paths, None)`` on success or ``(None, exit_code)`` after
    emitting the oracle's exact refusal. ``--from-lock`` reads the active
    backend's lock; otherwise ``--overlays`` is split. Profile activation is
    handled upstream (it goes through ``ir.compose(profile=…)``).
    """
    if from_lock:
        backend = _backend_for(target, base_path)
        try:
            paths = backend.read_lock_overlay_sources(project_path)
        except FileNotFoundError:
            _emit_error(
                "No lock file found at spec/overlay-manifest.lock; "
                "cannot use --from-lock"
                if target == "claude-code"
                else f"No {os.path.basename(backend.lock_path(project_path))} "
                f"found; cannot use --from-lock",
                fmt,
            )
            return None, 1
        except (OSError, json.JSONDecodeError) as exc:
            _emit_error(f"Cannot read lock file: {exc}", fmt)
            return None, 1
        if not paths:
            _emit_error("Lock file contains no overlay source paths", fmt)
            return None, 1
        return paths, None

    if overlays:
        return [
            os.path.abspath(p.strip())
            for p in overlays.split(",")
            if p.strip()
        ], None

    if profile:
        # Profile activation resolves its own paths inside ir.compose.
        return [], None

    _emit_error("Either --overlays or --from-lock is required", fmt)
    return None, 1


def _do_compose(args, target: str, from_lock_verb: bool = False) -> int:
    """Run the compose→emit pipeline (shared by ``compile`` and ``from-lock``).

    Reproduces ``composer.main()``'s compose/profile-activation/from-lock branch:
    overlay resolution, refusal classification, stderr warnings, the dry-run
    preview, the injection-block (exit 4), the write, and the success report — all
    byte-identical for the claude-code target (REQ-049/046).
    """
    base_path = os.path.abspath(args.base)
    project_path = os.path.abspath(args.project)
    fmt = args.format
    profile = getattr(args, "profile", "") or None
    from_lock = from_lock_verb or getattr(args, "from_lock", False)

    overlay_paths, refusal = _resolve_overlay_paths(
        target, base_path, project_path,
        getattr(args, "overlays", ""), from_lock, profile, fmt,
    )
    if refusal is not None:
        return refusal

    result = ir.compose(
        base_path,
        overlay_paths,
        project_path,
        profile=profile,
        dry_run=args.dry_run,
        allow_newer_schema=args.allow_newer_schema,
    )

    if result.graph is None:
        # Profile activation carries an authoritative exit code.
        report = result.report or {}
        if profile is not None and "exit_code" in report:
            if fmt == "json":
                sys.stdout.write(json.dumps({
                    "status": "error",
                    "errors": result.errors,
                    "report": {},
                }, indent=2) + "\n")
            else:
                for err in result.errors:
                    sys.stderr.write(f"ERROR: {err}\n")
            return report["exit_code"]
        return _emit_refusal(result.errors, report, fmt)

    backend = _backend_for(target, base_path, overlay_paths)

    # Profile activation note (text mode), then the report.
    if profile is not None and fmt == "text":
        prof = result.report.get("profile", {})
        sys.stdout.write(
            f"Activating profile '{prof.get('activated', profile)}' "
            f"({len(prof.get('source_paths', []))} overlay(s)).\n"
        )

    claude_text = _render_composed_text(result.graph, backend)
    report = _build_compose_report(result, claude_text)

    _emit_stderr_warnings(report)

    if fmt == "text":
        sys.stdout.write("Composition Report\n")
        sys.stdout.write("=" * 40 + "\n")
        for ov in report.get("overlays", []):
            sys.stdout.write(f"  Overlay: {ov['name']}@{ov['version']}\n")
        sys.stdout.write(
            f"\nComposed CLAUDE.md: {report.get('composed_lines', 0)} lines\n"
        )
        sys.stdout.write("\nContributions applied:\n")
        for scope, ids in report.get("contributions_applied", {}).items():
            sys.stdout.write(f"  {scope}: {ids}\n")
        deferred_report = report.get("deferred", {})
        if deferred_report:
            sys.stdout.write(
                "\nDeferred (declared but not applied in this phase):\n"
            )
            for scope, count in deferred_report.items():
                sys.stdout.write(f"  {scope}: {count}\n")
        conflicts = report.get("conflicts", {})
        if conflicts.get("additive_overlaps"):
            sys.stdout.write("\nAdditive overlaps (deterministic order):\n")
            for ao in conflicts["additive_overlaps"]:
                sys.stdout.write(f"  {ao['scope']}: {ao['order']}\n")

    if args.dry_run:
        if fmt == "json":
            sys.stdout.write(json.dumps({
                "status": "success",
                "report": report,
            }, indent=2) + "\n")
        else:
            sys.stdout.write("\n--- Composed CLAUDE.md (preview) ---\n")
            preview_lines = claude_text.split("\n")[:20]
            for pl in preview_lines:
                sys.stdout.write(pl + "\n")
            if len(claude_text.split("\n")) > 20:
                sys.stdout.write("... (truncated)\n")
            sys.stdout.write("\nFiles that would be written:\n")
            for fp in report["files_to_write"]:
                sys.stdout.write(f"  {fp}\n")
        return 0

    injection_warns = report.get("injection_warnings", [])
    if injection_warns and not args.allow_injection:
        if fmt == "json":
            sys.stdout.write(json.dumps({
                "status": "injection_blocked",
                "injection_warnings": injection_warns,
                "report": report,
                "message": (
                    "Prompt injection warnings detected. "
                    "Re-run with --allow-injection to proceed."
                ),
            }, indent=2) + "\n")
        else:
            sys.stderr.write(
                "\nERROR: Prompt injection warnings detected in overlay "
                "content files. Composition blocked in write mode. Review "
                "the warnings above, then re-run with --allow-injection to "
                "proceed, or fix the overlay content files.\n"
            )
        return 4

    try:
        written = backend.emit(result.graph, project_path)
    except OSError as exc:
        _emit_error(f"I/O error writing outputs: {exc}", fmt)
        return 3

    if fmt == "json":
        report["files_written"] = written
        sys.stdout.write(json.dumps({
            "status": "success",
            "report": report,
        }, indent=2) + "\n")
    else:
        sys.stdout.write("\nFiles written:\n")
        for fp in written:
            sys.stdout.write(f"  {fp}\n")
    return 0


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def _remaining_source_paths(backend: Backend, project_path: str, target_name: str):
    """Read the lock's overlay source paths for the overlays OTHER than *target_name*.

    Mirrors the oracle's multi-overlay recompose-set extraction so the CLI can
    rebuild the recomposed compose report (the oracle's uninstall JSON/text dry-run
    embed the FULL recomposed compose report). Returns ``None`` when the lock has
    no overlays other than the target (the last-overlay edge).
    """
    try:
        lp = backend.lock_path(project_path)
        with open(lp, "r", encoding="utf-8") as fh:
            lock_data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    paths = [
        ov.get("source_path", "")
        for ov in lock_data.get("overlays", [])
        if ov.get("name") != target_name and ov.get("source_path")
    ]
    return paths or None


def _uninstall_report(
    args, target: str, base_path: str, project_path: str, backend: Backend,
    result: UninstallResult,
) -> Tuple[dict, str]:
    """Reconstruct the oracle's uninstall ``report`` dict + the composed CLAUDE.md text.

    Last-overlay edge: the fixed base-only report (``overlays:[]``,
    ``contributions_applied:{}``, base-template ``composed_lines``, ``files_to_write
    = [CLAUDE.md]``). Multi-overlay: the FULL recomposed compose report (recomposing
    the remaining source set in-process) with ``report["uninstall"]`` inserted after
    ``files_to_write``. The key insertion order is byte-identical to ``composer.py``.
    """
    uninstall_meta = {
        "removed": result.removed,
        "remaining": result.remaining,
        "artifacts_removed": result.artifacts_removed,
    }

    if result.is_last_overlay:
        # Render the base template text the backend reverted CLAUDE.md to.
        claude_text = result.preview
        report = {
            "uninstall": uninstall_meta,
            "overlays": [],
            "contributions_applied": {},
            "composed_lines": _composed_lines(claude_text),
            "files_to_write": [os.path.join(project_path, "CLAUDE.md")],
        }
        return report, claude_text

    remaining_paths = _remaining_source_paths(backend, project_path, args.name)
    recomposed = ir.compose(
        base_path, remaining_paths or [], project_path,
        dry_run=args.dry_run, allow_newer_schema=args.allow_newer_schema,
    )
    claude_text = ""
    if recomposed.graph is not None:
        render_backend = _backend_for(target, base_path, remaining_paths)
        claude_text = _render_composed_text(recomposed.graph, render_backend)
    report = _build_compose_report(recomposed, claude_text)
    # The oracle inserts `uninstall` AFTER files_to_write (preserving the compose
    # report key order), then `files_written` last.
    report["uninstall"] = uninstall_meta
    return report, claude_text


def _do_uninstall(args, target: str) -> int:
    """Run the uninstall pipeline (ports ``composer.main()``'s ``--uninstall`` block).

    Renders the oracle's exact dry-run preview (the embedded recomposed Composition
    Report + uninstall metadata + CLAUDE.md preview), the injection-block (exit 4),
    the "Uninstall complete." body, and the JSON envelope. Refusals (not-installed /
    malformed / no-lock) match the oracle's text + exit codes.
    """
    base_path = os.path.abspath(args.base)
    project_path = os.path.abspath(args.project)
    fmt = args.format

    backend = _backend_for(target, base_path)
    try:
        result = backend.uninstall(
            project_path, args.name, dry_run=args.dry_run,
            allow_newer_schema=args.allow_newer_schema,
        )
    except OSError as exc:
        _emit_error(f"I/O error during uninstall: {exc}", fmt)
        return 3

    if result.errors:
        return _emit_refusal(result.errors, {}, fmt)

    removed = result.removed
    remaining = result.remaining
    artifacts = result.artifacts_removed
    report, claude_text = _uninstall_report(
        args, target, base_path, project_path, backend, result,
    )

    _emit_stderr_warnings(report)

    if args.dry_run:
        if fmt == "json":
            report["files_to_write"] = list(result.files_written)
            sys.stdout.write(json.dumps({
                "status": "success",
                "report": report,
            }, indent=2) + "\n")
        else:
            sys.stdout.write("Composition Report\n")
            sys.stdout.write("=" * 40 + "\n")
            for ov in report.get("overlays", []):
                sys.stdout.write(f"  Overlay: {ov['name']}@{ov['version']}\n")
            sys.stdout.write(
                f"\nComposed CLAUDE.md: {report.get('composed_lines', 0)} lines\n"
            )
            contribs = report.get("contributions_applied", {})
            if contribs:
                sys.stdout.write("\nContributions applied:\n")
                for scope, ids in contribs.items():
                    sys.stdout.write(f"  {scope}: {ids}\n")
            sys.stdout.write(
                f"\nUninstall: {removed.get('name', '')}@"
                f"{removed.get('version', '')}\n"
            )
            if remaining:
                remaining_str = ", ".join(
                    f"{r['name']}@{r.get('version', '')}" for r in remaining
                )
                sys.stdout.write(f"Remaining overlays: {remaining_str}\n")
            else:
                sys.stdout.write("Remaining overlays: (none)\n")
            if artifacts:
                sys.stdout.write("Files/directories to remove:\n")
                for a in artifacts:
                    sys.stdout.write(f"  {a}\n")
            sys.stdout.write("\n--- Composed CLAUDE.md (preview) ---\n")
            preview_lines = claude_text.split("\n")[:20]
            for pl in preview_lines:
                sys.stdout.write(pl + "\n")
            if len(claude_text.split("\n")) > 20:
                sys.stdout.write("... (truncated)\n")
            sys.stdout.write("\nFiles that would be written:\n")
            for fp in result.files_written:
                sys.stdout.write(f"  {fp}\n")
        return 0

    if result.injection_warnings and not args.allow_injection:
        if fmt == "json":
            sys.stdout.write(json.dumps({
                "status": "injection_blocked",
                "injection_warnings": result.injection_warnings,
                "report": report,
                "message": (
                    "Prompt injection warnings detected. "
                    "Re-run with --allow-injection to proceed."
                ),
            }, indent=2) + "\n")
        else:
            sys.stderr.write(
                "\nERROR: Prompt injection warnings detected in overlay "
                "content files. Composition blocked in write mode. Review "
                "the warnings above, then re-run with --allow-injection to "
                "proceed, or fix the overlay content files.\n"
            )
        return 4

    written = result.files_written

    if fmt == "json":
        report["files_written"] = written
        sys.stdout.write(json.dumps({
            "status": "success",
            "report": report,
        }, indent=2) + "\n")
    else:
        sys.stdout.write("Uninstall complete.\n")
        sys.stdout.write(
            f"  Removed: {removed.get('name', '')}@"
            f"{removed.get('version', '')}\n"
        )
        if remaining:
            remaining_str = ", ".join(
                f"{r['name']}@{r.get('version', '')}" for r in remaining
            )
            sys.stdout.write(f"  Remaining: {remaining_str}\n")
        else:
            sys.stdout.write("  Remaining: (none)\n")
        sys.stdout.write("  Files written:\n")
        for fp in written:
            sys.stdout.write(f"    {fp}\n")
        if artifacts:
            sys.stdout.write("  Files/directories removed:\n")
            for a in artifacts:
                sys.stdout.write(f"    {a}\n")
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def _doctor_result_dict(rpt: DoctorReport) -> dict:
    """Reconstruct the oracle's ``drift_check`` result dict from a ``DoctorReport``."""
    return {
        "status": rpt.status,
        "details": rpt.details,
        "system2_version": rpt.system2_version,
        "overlays": rpt.overlays,
        "claude_md_composed": rpt.composed,
    }


def _print_doctor_report(result: dict) -> None:
    """Print the human-readable drift report (verbatim from ``composer._print_doctor_report``)."""
    status = result["status"]
    version_info = result["system2_version"]
    overlays = result["overlays"]

    status_labels = {
        "current": "Current",
        "stale_base": "Stale base",
        "stale_overlay": "Stale overlay",
        "broken": "Broken",
        "no_lock": "No lock file",
    }
    sys.stdout.write(f"Status: {status_labels.get(status, status)}\n")
    sys.stdout.write(
        f"System2 version: installed={version_info['installed']}, "
        f"locked={version_info['locked']}\n"
    )
    sys.stdout.write(
        f"CLAUDE.md composed: {'yes' if result['claude_md_composed'] else 'no'}\n"
    )

    if overlays:
        sys.stdout.write(f"\nOverlays ({len(overlays)}):\n")
        for ov in overlays:
            src_ok = "ok" if ov["source_exists"] else "MISSING"
            local_ok = "ok" if ov["local_exists"] else "MISSING"
            manifest_ok = (
                "ok" if ov["manifest_match"] is True
                else "CHANGED" if ov["manifest_match"] is False
                else "n/a"
            )
            content_ok = (
                "ok" if ov["content_match"] is True
                else "CHANGED" if ov["content_match"] is False
                else "n/a"
            )
            local_content_ok = (
                "ok" if ov.get("local_match") is True
                else "CHANGED" if ov.get("local_match") is False
                else "n/a"
            )
            sys.stdout.write(
                f"  {ov['name']}: source={src_ok}, local={local_ok}, "
                f"manifest={manifest_ok}, content={content_ok}, "
                f"local_content={local_content_ok}\n"
            )

    if result["details"]:
        sys.stdout.write("\nFindings:\n")
        for d in result["details"]:
            sys.stdout.write(f"  - {d['message']}\n")

    if status == "current":
        sys.stdout.write(
            "\nAll overlays match the lock file. No action needed.\n"
        )
    elif status in ("stale_base", "stale_overlay"):
        sys.stdout.write(
            "\nRun /system2:compose --from-lock to refresh composition.\n"
        )
    elif status == "broken":
        sys.stdout.write(
            "\nOverlay source paths or local copies are missing. "
            "Fix the paths, then run /system2:compose --from-lock.\n"
        )


def _do_doctor(args, target: str) -> int:
    """Run the read-only drift check (ports ``composer.main()``'s ``--doctor`` block).

    For claude-code the JSON / text output and the exit-code rule (0 iff
    ``current``) match the oracle byte-for-byte. Pi/Codex additionally surface the
    LOUD ``validator_unavailable`` finding (exit 0 with the loud finding, never a
    silent "current"); their exit code comes from the backend's ``DoctorReport``.
    """
    base_path = os.path.abspath(args.base)
    project_path = os.path.abspath(args.project)
    fmt = args.format

    backend = _backend_for(target, base_path)
    rpt = backend.doctor(project_path)

    if target == "claude-code":
        result = _doctor_result_dict(rpt)
        if fmt == "json":
            sys.stdout.write(json.dumps(result, indent=2) + "\n")
        else:
            _print_doctor_report(result)
        return rpt.exit_code

    # Pi/Codex: surface the neutral report shape (incl. validator availability).
    if fmt == "json":
        sys.stdout.write(json.dumps({
            "status": rpt.status,
            "details": rpt.details,
            "system2_version": rpt.system2_version,
            "overlays": rpt.overlays,
            "validator_available": rpt.validator_available,
        }, indent=2) + "\n")
    else:
        sys.stdout.write(f"Status: {rpt.status}\n")
        if not rpt.validator_available:
            sys.stdout.write(
                "  WARNING: validator_unavailable "
                f"(the {target} validator is not on PATH; structural checks "
                "ran, validity could not be confirmed)\n"
            )
        if rpt.details:
            sys.stdout.write("\nFindings:\n")
            for d in rpt.details:
                sys.stdout.write(f"  - {d.get('message', d)}\n")
    return rpt.exit_code


# ---------------------------------------------------------------------------
# profile (harness-neutral; no --target)
# ---------------------------------------------------------------------------

def _reject_inapplicable_subflags(args, mode: str, offenders, fmt: str) -> Optional[int]:
    """Return exit 1 if any profile sub-flag in *offenders* is present for *mode*.

    Ported verbatim from ``composer._reject_inapplicable_subflags``. *offenders* and
    *mode* are stated in the frozen oracle's composer-flag vocabulary
    (``--profile-name``/``--profile-paths``/``--profile-add``/``--profile-remove``/
    ``--force``; modes ``--save-profile``/``--profile-op create|edit|delete``) so the
    relayed error text stays byte-identical to the plugin's dispatch (the skills
    parse it). Presence is read off the compiler CLI's own args: ``--paths`` maps to
    ``--profile-paths``, ``--add``→``--profile-add``, ``--remove``→``--profile-remove``,
    ``--force`` is store_true.
    """
    present = {
        "--profile-name": bool(getattr(args, "profile_name", "")),
        "--profile-paths": bool(getattr(args, "paths", "")),
        "--profile-add": bool(getattr(args, "add", None)),
        "--profile-remove": bool(getattr(args, "remove", None)),
        "--force": bool(getattr(args, "force", False)),
    }
    bad = [flag for flag in offenders if present.get(flag)]
    if bad:
        _emit_error(
            f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} not "
            f"applicable to {mode}.",
            fmt,
        )
        return 1
    return None


def _run_profile_mutation(
    project_path: str, op: str, name: str,
    paths=None, adds=None, removes=None, force=False,
) -> dict:
    """Dispatch a profile mutation to ``ir.profiles`` (ports ``composer._run_profile_mutation``).

    The ``active_in_project`` signal is the PRE-mutation active state, computed
    BEFORE the mutation via ``active_profile_for_lock`` so editing the active
    profile away from the lock still fires the recompose prompt. The only write is
    ``~/.system2/profiles.json``; ``delete`` yields ``active_in_project=False``.
    """
    base_cwd = os.getcwd()
    try:
        was_active = (
            False if op == "delete"
            else ir_profiles.active_profile_for_lock(project_path, name)
        )
        if op == "save":
            summary = ir_profiles.save_profile_from_lock(
                name, project_path, force=force,
            )
        elif op == "create":
            summary = ir_profiles.create_profile(
                name, paths or [], base_cwd, force=force,
            )
        elif op == "edit":
            if not (adds or removes):
                raise ir_profiles.ProfileError(
                    "Nothing to do: --profile-op edit requires at least one "
                    "--profile-add or --profile-remove.",
                    exit_code=1,
                )
            summary = ir_profiles.edit_profile(
                name, adds or [], removes or [], base_cwd,
            )
        elif op == "delete":
            summary = ir_profiles.delete_profile(name)
        else:
            raise ir_profiles.ProfileError(
                f"Unknown profile operation {op!r}.", exit_code=1,
            )
    except ir_profiles.ProfileError as exc:
        return {"errors": [exc.message], "exit_code": exc.exit_code}

    return {"errors": [], "summary": summary, "active_in_project": was_active}


def _do_profile_mutation(args, op: str, name: str) -> int:
    """Run a profile mutation (save/create/edit/delete) and render the summary.

    Mutations reject ``--dry-run`` (they never write a project artifact), honor
    ``--force`` on save/create, and emit the pre-mutation ``active_in_project``
    recompose signal — all byte-identical to the plugin's profile dispatch.
    """
    fmt = args.format
    project_path = os.path.abspath(args.project) if args.project else os.getcwd()

    if getattr(args, "dry_run", False):
        _emit_error(
            "--dry-run is not supported with profile mutations "
            "(--save-profile / --profile-op); these never write project "
            "artifacts and apply immediately.",
            fmt,
        )
        return 1

    paths = [p.strip() for p in (args.paths or "").split(",") if p.strip()]
    result = _run_profile_mutation(
        project_path, op, name,
        paths=paths, adds=args.add or [], removes=args.remove or [],
        force=args.force,
    )

    if result["errors"]:
        exit_code = result.get("exit_code", 1)
        if fmt == "json":
            sys.stdout.write(json.dumps({
                "status": "error",
                "errors": result["errors"],
            }, indent=2) + "\n")
        else:
            for err in result["errors"]:
                sys.stderr.write(f"ERROR: {err}\n")
        return exit_code

    summary = result["summary"]
    active = result["active_in_project"]
    if fmt == "json":
        sys.stdout.write(json.dumps({
            "status": "success",
            "operation": op,
            "summary": summary,
            "active_in_project": active,
        }, indent=2) + "\n")
    else:
        sys.stdout.write(f"Profile '{summary['name']}' {op} complete.\n")
        sys.stdout.write(f"  Stored in: {summary['store_path']}\n")
        for overlay in summary["overlays"]:
            label = overlay.get("name") or "(unresolved)"
            sys.stdout.write(f"  {overlay['path']} -- {label}\n")
        if active:
            sys.stdout.write(
                f"Profile {summary['name']} is currently active in this "
                "project.\n"
            )
    return 0


def _do_profile_list(args) -> int:
    """List profile names (ports the ``profiles.py --list`` shape)."""
    fmt = args.format
    names = ir_profiles.list_profiles()
    if fmt == "json":
        sys.stdout.write(json.dumps({"profiles": names}) + "\n")
    elif names:
        sys.stdout.write("\n".join(names) + "\n")
    else:
        sys.stdout.write("No profiles are defined yet.\n")
    return 0


def _do_profile_inspect(args, name: str) -> int:
    """Inspect one profile (ports the ``profiles.py --inspect`` shape)."""
    fmt = args.format
    try:
        result = ir_profiles.inspect_profile(name)
    except ir_profiles.ProfileError as exc:
        if fmt == "json":
            sys.stdout.write(
                json.dumps({"status": "error", "message": exc.message}) + "\n"
            )
        else:
            sys.stderr.write(exc.message + "\n")
        return exc.exit_code

    if fmt == "json":
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    else:
        sys.stdout.write(f"Profile '{result['name']}':\n")
        for record in result["overlays"]:
            if record["stale"]:
                sys.stdout.write(
                    f"  {record['path']} -- {record['annotation']}\n"
                )
            else:
                sys.stdout.write(
                    f"  {record['path']} -- {record['resolved_name']} "
                    f"v{record['resolved_version']}\n"
                )
    return 0


def _do_profile(argv: List[str]) -> int:
    """Dispatch the harness-neutral ``profile`` verb.

    Subcommands: ``list`` / ``inspect NAME`` / ``save NAME`` / ``create NAME
    --paths P`` / ``edit NAME [--add P]… [--remove OVERLAY]…`` / ``delete NAME``.
    No ``--target`` (profiles are harness-neutral; an activated profile feeds
    compose for ANY target). Mutations write only ``~/.system2/profiles.json``.
    """
    parser = argparse.ArgumentParser(
        prog="system2 profile",
        description="Harness-neutral overlay-profile management.",
    )
    parser.add_argument(
        "op",
        choices=["list", "inspect", "save", "create", "edit", "delete"],
        help="Profile operation.",
    )
    parser.add_argument("name", nargs="?", default="", help="Profile name.")
    # The composer flag surface's separate ``--profile-name`` flag (distinct from
    # the NAME positional, which is the mutation target). It is only meaningful as
    # the offender signal for ``save``: present (non-empty) when the user passed
    # ``--profile-name`` alongside ``--save-profile``. The sub-flag matrix reads
    # this — NOT the positional — so ``save NAME`` is never self-rejected.
    parser.add_argument("--profile-name", dest="profile_name", default="", help=argparse.SUPPRESS)
    parser.add_argument("--paths", default="", help="Comma-separated overlay paths (create).")
    parser.add_argument("--add", action="append", default=None, help="Overlay path to add (edit); repeatable.")
    parser.add_argument("--remove", action="append", default=None, help="Overlay NAME to remove (edit); repeatable.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing profile (save/create).")
    parser.add_argument("--project", default="", help="Project root for the active_in_project signal.")
    parser.add_argument("--dry-run", action="store_true", help="Rejected for mutations.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    op = args.op
    fmt = args.format

    if op == "list":
        return _do_profile_list(args)

    if not args.name:
        _emit_error(f"profile {op} requires a NAME argument.", fmt)
        return 1

    if op == "inspect":
        return _do_profile_inspect(args, args.name)

    # Mutations: enforce the oracle's sub-flag matrix verbatim (mode + offender
    # names in composer-flag vocabulary, so the relayed error is byte-identical).
    if op == "save":
        rej = _reject_inapplicable_subflags(
            args, "--save-profile",
            ["--profile-name", "--profile-paths", "--profile-add", "--profile-remove"],
            fmt,
        )
    elif op == "create":
        rej = _reject_inapplicable_subflags(
            args, "--profile-op create", ["--profile-add", "--profile-remove"], fmt,
        )
    elif op == "edit":
        rej = _reject_inapplicable_subflags(
            args, "--profile-op edit", ["--profile-paths", "--force"], fmt,
        )
    elif op == "delete":
        rej = _reject_inapplicable_subflags(
            args, "--profile-op delete",
            ["--profile-paths", "--profile-add", "--profile-remove", "--force"],
            fmt,
        )
    else:
        rej = None
    if rej is not None:
        return rej

    return _do_profile_mutation(args, op, args.name)


# ---------------------------------------------------------------------------
# codex (user-scope enforcement install; harness-specific, no --target/--base)
# ---------------------------------------------------------------------------

def _do_codex(argv: List[str]) -> int:
    """Dispatch ``system2 codex {init|uninstall}`` (single global user-scope install).

    ``init`` materializes ``~/.codex/hooks.json`` + ``~/.codex/system2/hooks/*.js``
    from the committed ``distributions/codex/user-hooks/`` reference, resolving the
    hook ``command`` to an ABSOLUTE path (A2). ``--codex-home``/``$CODEX_HOME`` is the
    test seam (production default = real ``~/.codex``). ROQ-1: a pre-existing
    non-System2 ``hooks.json`` is never silently clobbered — ``init`` refuses without
    ``--force`` and, with it, writes a timestamped ``.bak`` first. ``uninstall``
    removes exactly the System2 artifacts and restores that backup.
    """
    parser = argparse.ArgumentParser(
        prog="system2 codex",
        description="Install/uninstall the user-scope Codex enforcement hooks.",
    )
    parser.add_argument("op", choices=["init", "uninstall"], help="Codex operation.")
    parser.add_argument(
        "--codex-home", dest="codex_home", default="",
        help="Target Codex home (default: $CODEX_HOME or ~/.codex).",
    )
    parser.add_argument(
        "--reference", default="",
        help="Committed user-hooks reference (default: repo distributions/codex/user-hooks).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Back up + overwrite a pre-existing non-System2 ~/.codex/hooks.json.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show would-write set; write nothing.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)
    fmt = args.format

    if args.op == "init":
        try:
            result = codex_ops.codex_init(
                codex_home=args.codex_home or None,
                reference_dir=args.reference or None,
                force=args.force,
                dry_run=args.dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            # N3: a missing/invalid --reference is a clean CLI error, not a traceback.
            sys.stderr.write(f"error: {exc}\n")
            return 2
    else:
        result = codex_ops.codex_uninstall(
            codex_home=args.codex_home or None, dry_run=args.dry_run,
        )

    for w in result.get("warnings", []):
        sys.stderr.write(f"  WARNING: {w}\n")

    if fmt == "json":
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
    elif args.op == "init" and result["status"] != "refused":
        sys.stdout.write(f"{result['message']}\n")
        for fp in result.get("hook_files", []):
            sys.stdout.write(f"  hook: {fp}\n")
        sys.stdout.write(f"  config: {result['hooks_json']}\n")
    elif args.op == "uninstall":
        sys.stdout.write(f"Codex uninstall: {result['status']}.\n")
        for fp in result.get("removed", []):
            sys.stdout.write(f"  removed: {fp}\n")
        if result.get("restored_backup"):
            sys.stdout.write(f"  restored backup: {result['restored_backup']}\n")

    if result.get("status") == "refused":
        return 3
    return 0


# ---------------------------------------------------------------------------
# Argument parsing for the write/lifecycle verbs
# ---------------------------------------------------------------------------

def _add_common(parser: argparse.ArgumentParser, *, require_base: bool = True) -> None:
    parser.add_argument(
        "--target", required=True, choices=sorted(_TARGETS),
        help="Backend to lower onto (claude-code, pi, or codex).",
    )
    parser.add_argument(
        "--base", required=require_base, help="Path to the System2 plugin root.",
    )
    parser.add_argument(
        "--project", required=True, help="Path to the target project root.",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format (default: text).",
    )


def _build_compile_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Compose System2 + overlays for a target backend.",
    )
    _add_common(parser)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--profile", metavar="NAME", default="", help="Activate the named profile's overlay set.")
    group.add_argument("--overlays", default="", help="Comma-separated overlay source paths.")
    group.add_argument("--from-lock", action="store_true", help="Recompose from the existing lock's overlay sources.")
    parser.add_argument("--dry-run", action="store_true", help="Compute the would-write set; write nothing.")
    parser.add_argument("--allow-newer-schema", action="store_true", help="Accept overlays with an unsupported schema_version.")
    parser.add_argument("--allow-injection", action="store_true", help="Proceed despite prompt-injection warnings.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Dispatch a ``system2`` subcommand.

    A leading ``--target`` (or no args) routes to ``compile`` for back-compat with
    the Phase-0..4 ``main(["--target", …])`` invocation.
    """
    if argv is None:
        argv = sys.argv[1:]

    # Harness-specific user-scope install (no --target/--base). Additive; the
    # existing verb/target surface is untouched.
    if argv and argv[0] == "codex":
        return _do_codex(argv[1:])

    # Back-compat: a leading flag (or empty argv) means the implicit `compile`.
    if not argv or argv[0].startswith("-"):
        verb = "compile"
        rest = argv
    elif argv[0] in _VERBS:
        verb = argv[0]
        rest = argv[1:]
    else:
        # Unknown leading token: let the compile parser raise the usage error.
        verb = "compile"
        rest = argv

    if verb == "profile":
        return _do_profile(rest)

    if verb == "compile":
        parser = _build_compile_parser("system2 compile")
        args = parser.parse_args(rest)
        return _do_compose(args, args.target)

    if verb == "from-lock":
        parser = argparse.ArgumentParser(
            prog="system2 from-lock",
            description="Recompose from the existing lock's overlay sources.",
        )
        _add_common(parser)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--allow-newer-schema", action="store_true")
        parser.add_argument("--allow-injection", action="store_true")
        args = parser.parse_args(rest)
        args.overlays = ""
        args.profile = ""
        args.from_lock = True
        return _do_compose(args, args.target, from_lock_verb=True)

    if verb == "uninstall":
        parser = argparse.ArgumentParser(
            prog="system2 uninstall",
            description="Remove a named overlay from the composition.",
        )
        _add_common(parser)
        parser.add_argument("--name", required=True, metavar="OVERLAY", help="Overlay to remove.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--allow-newer-schema", action="store_true")
        parser.add_argument("--allow-injection", action="store_true")
        args = parser.parse_args(rest)
        return _do_uninstall(args, args.target)

    if verb == "doctor":
        parser = argparse.ArgumentParser(
            prog="system2 doctor",
            description="Read-only drift/status check.",
        )
        _add_common(parser)
        args = parser.parse_args(rest)
        return _do_doctor(args, args.target)

    raise AssertionError(f"unreachable verb {verb!r}")


if __name__ == "__main__":
    raise SystemExit(main())
