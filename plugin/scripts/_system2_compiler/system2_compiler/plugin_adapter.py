"""The vendored plugin entry: the ``composer.py`` flag-CLI contract, encoded once.

Phase 5 flips the plugin's ``scripts/composer.py`` to a thin shim that delegates
to the vendored bundle's :func:`main_composer_contract`. This module is that
delegate — the ONE place the ``composer.py`` flag surface (``--doctor`` /
``--uninstall`` / ``--from-lock`` / ``--profile`` / ``--save-profile`` /
``--profile-op`` / ``--profile-*`` / ``--base`` / ``--overlays`` / ``--project`` /
``--dry-run`` / ``--format`` / ``--allow-injection`` / ``--allow-newer-schema`` /
``--force``) is mapped onto the compiler's claude-code lifecycle + profile API,
reproducing exit codes and stdout/stderr BYTE-FOR-BYTE.

``--target`` is NOT a flag here; it is hard-pinned to ``claude-code`` (the plugin
is Claude-only — OQ-5.4).

**Anti-drift.** The contract-faithful dispatch already lives in :mod:`cli` (its
``_do_compose`` / ``_do_uninstall`` / ``_do_doctor`` / ``_do_profile`` are pinned
byte-for-byte against the frozen ``composer.py`` oracle by the CLI-contract
goldens, TASK-507). This adapter does NOT re-implement any of it: it parses the
flat composer flag surface, applies composer's exact mutual-exclusion /
sub-flag-rejection refusals, then translates the request into the ``system2``
verb argv (``compile`` / ``uninstall`` / ``doctor`` / ``from-lock`` / ``profile``,
``--target claude-code``) and delegates to :func:`cli.main`. The adapter and
``cli.py`` therefore cannot drift — there is a single dispatch implementation.

Because the adapter delegates to ``cli`` for that single implementation, the
bundler (``tools/build_bundle.py``) vendors ``cli.py`` alongside ``ir/`` +
``backends/`` as the adapter's PRIVATE dependency. ``cli.py`` is not the bundle's
entry (the adapter is, with ``--target`` pinned); it ships purely so the one
dispatch body is shared, not copied. It is stdlib-only, so the bundle stays
zero-dependency.

Stdlib-only (REQ-016/043): this module imports only stdlib + the vendored
compiler product modules (``cli`` → ``ir`` + ``backends``).
"""

import argparse
import sys
from typing import List, Optional

from system2_compiler import cli

__all__ = ["main_composer_contract", "main"]

# Pinned: the plugin is Claude-only (OQ-5.4). Every translated verb carries this.
_TARGET = "claude-code"


def _build_parser() -> argparse.ArgumentParser:
    """Reproduce ``composer.main()``'s flat flag parser verbatim (same flags/metavars).

    The flags, defaults, ``metavar``s, and ``choices`` mirror ``composer.py``'s
    ``argparse`` exactly so usage/error text matches the frozen oracle.
    """
    parser = argparse.ArgumentParser(
        description="System2 overlay composition engine",
    )
    parser.add_argument("--base", required=True, help="Path to the System2 plugin root (e.g., plugin/)")
    parser.add_argument("--overlays", default="", help="Comma-separated overlay directory paths")
    parser.add_argument("--project", required=True, help="Path to the target project root")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing files")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    parser.add_argument("--allow-injection", action="store_true", help="Proceed despite prompt injection warnings")
    parser.add_argument("--allow-newer-schema", action="store_true", help="Allow overlays with unsupported schema_version (degraded mode)")
    parser.add_argument("--doctor", action="store_true", help="Read-only drift check against the lock file")
    parser.add_argument("--from-lock", action="store_true", help="Read overlay source paths from the existing lock file")
    parser.add_argument("--uninstall", metavar="NAME", default="", help="Remove a named overlay from the current composition")
    parser.add_argument("--profile", metavar="NAME", default="", help="Activate (compose) the named profile's overlay set")
    parser.add_argument("--save-profile", metavar="NAME", default="", help="Save the project's current composition (from the lock) as a profile")
    parser.add_argument("--profile-op", choices=["create", "edit", "delete"], default="", help="Profile mutation operation")
    parser.add_argument("--profile-name", metavar="NAME", default="", help="Profile name for the mutation operation")
    parser.add_argument("--profile-paths", metavar="PATHS", default="", help="Comma-separated overlay source paths (create)")
    parser.add_argument("--profile-add", action="append", default=None, help="Overlay source path to add (edit); repeatable")
    parser.add_argument("--profile-remove", action="append", default=None, help="Overlay NAME to remove (edit); repeatable")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing profile on create/save-profile")
    return parser


def _emit_error(msg: str, fmt: str) -> None:
    """Emit a single error exactly as the oracle (reuses cli's verbatim emitter)."""
    cli._emit_error(msg, fmt)


def _common(base: str, project: str, fmt: str) -> List[str]:
    return ["--target", _TARGET, "--base", base, "--project", project, "--format", fmt]


def _compose_tail(args) -> List[str]:
    tail: List[str] = []
    if args.dry_run:
        tail.append("--dry-run")
    if args.allow_newer_schema:
        tail.append("--allow-newer-schema")
    if args.allow_injection:
        tail.append("--allow-injection")
    return tail


def _doctor_argv(args) -> List[str]:
    return ["doctor"] + _common(args.base, args.project, args.format)


def _uninstall_argv(args) -> List[str]:
    return (
        ["uninstall"] + _common(args.base, args.project, args.format)
        + ["--name", args.uninstall] + _compose_tail(args)
    )


def _from_lock_argv(args) -> List[str]:
    return ["from-lock"] + _common(args.base, args.project, args.format) + _compose_tail(args)


def _compile_argv(args) -> List[str]:
    argv = ["compile"] + _common(args.base, args.project, args.format)
    if args.profile:
        argv += ["--profile", args.profile]
    elif args.from_lock:
        argv.append("--from-lock")
    elif args.overlays:
        argv += ["--overlays", args.overlays]
    return argv + _compose_tail(args)


def _profile_mutation_argv(args, op: str, name: str) -> List[str]:
    argv = ["profile", op, name]
    # ``--profile-name`` is the mutation target for create/edit/delete (carried as
    # the NAME positional here) but an INAPPLICABLE offender for ``save`` (whose
    # target is ``--save-profile``). Relay the raw ``--profile-name`` flag only for
    # ``save`` so cli's sub-flag matrix rejects ``--save-profile X --profile-name Y``
    # while leaving a bare ``save NAME`` valid (composer's exact rule).
    if op == "save" and args.profile_name:
        argv += ["--profile-name", args.profile_name]
    if args.profile_paths:
        argv += ["--paths", args.profile_paths]
    for add in args.profile_add or []:
        argv += ["--add", add]
    for rem in args.profile_remove or []:
        argv += ["--remove", rem]
    if args.force:
        argv.append("--force")
    if args.dry_run:
        argv.append("--dry-run")
    argv += ["--project", args.project, "--format", args.format]
    return argv


def main_composer_contract(argv: Optional[List[str]] = None) -> int:
    """Run the ``composer.py`` flag CLI; return its exit code.

    Parses the flat composer flag surface, applies composer's exact
    mutual-exclusion / sub-flag refusals, then delegates the request to the
    contract-proven :func:`cli.main` verb dispatch (``--target`` pinned to
    ``claude-code``). The return value is the process exit code; the shim wraps it
    in ``SystemExit``.
    """
    if argv is None:
        argv = sys.argv[1:]

    args = _build_parser().parse_args(argv)
    fmt = args.format

    # --- doctor (mutually exclusive with the write/profile flags) -------------
    if args.doctor:
        if (args.overlays or args.from_lock or args.uninstall or args.profile
                or args.save_profile or args.profile_op):
            _emit_error(
                "--doctor is mutually exclusive with --overlays, --from-lock, "
                "--uninstall, --profile, --save-profile, and --profile-op",
                fmt,
            )
            return 1
        return cli.main(_doctor_argv(args))

    # --- uninstall ------------------------------------------------------------
    if args.uninstall:
        if (args.overlays or args.from_lock or args.profile
                or args.save_profile or args.profile_op):
            _emit_error(
                "--uninstall is mutually exclusive with --overlays, --from-lock, "
                "--profile, --save-profile, and --profile-op",
                fmt,
            )
            return 1
        return cli.main(_uninstall_argv(args))

    # --- profile activation guard (composer rejects inapplicable sub-flags) ---
    if args.profile:
        if (args.overlays or args.from_lock or args.uninstall
                or args.save_profile or args.profile_op):
            _emit_error(
                "--profile is mutually exclusive with --overlays, --from-lock, "
                "--uninstall, --save-profile, and --profile-op",
                fmt,
            )
            return 1
        rej = _reject_activation_subflags(args, fmt)
        if rej is not None:
            return rej

    # --- profile mutation (save / create|edit|delete) -------------------------
    if args.save_profile or args.profile_op:
        if args.save_profile and args.profile_op:
            _emit_error("--save-profile and --profile-op are mutually exclusive", fmt)
            return 1
        if args.overlays or args.from_lock or args.uninstall or args.profile:
            _emit_error(
                "--save-profile/--profile-op are mutually exclusive with "
                "--overlays, --from-lock, --uninstall, and --profile",
                fmt,
            )
            return 1
        if args.save_profile:
            op, name = "save", args.save_profile
        else:
            op, name = args.profile_op, args.profile_name
        # cli's profile verb re-applies the dry-run / sub-flag-matrix refusals and
        # the mutation itself, byte-identically to composer.
        return cli.main(_profile_mutation_argv(args, op, name))

    # --- compose / profile-activation / from-lock (shared write pipeline) -----
    return cli.main(_compile_argv(args))


def _reject_activation_subflags(args, fmt: str) -> Optional[int]:
    """Reject sub-flags inapplicable to ``--profile`` activation (composer's rule).

    Ports ``composer._reject_inapplicable_subflags(args, "--profile activation",
    [...])``: any of ``--profile-name`` / ``--profile-paths`` / ``--profile-add`` /
    ``--profile-remove`` / ``--force`` present with ``--profile`` is exit 1 with the
    oracle's exact text.
    """
    present = {
        "--profile-name": bool(args.profile_name),
        "--profile-paths": bool(args.profile_paths),
        "--profile-add": bool(args.profile_add),
        "--profile-remove": bool(args.profile_remove),
        "--force": bool(args.force),
    }
    offenders = [
        "--profile-name", "--profile-paths",
        "--profile-add", "--profile-remove", "--force",
    ]
    bad = [flag for flag in offenders if present.get(flag)]
    if bad:
        _emit_error(
            f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} not "
            "applicable to --profile activation.",
            fmt,
        )
        return 1
    return None


def main(argv: Optional[List[str]] = None) -> int:
    """Alias for :func:`main_composer_contract` (the bundle entry name)."""
    return main_composer_contract(argv)


if __name__ == "__main__":
    raise SystemExit(main_composer_contract(sys.argv[1:]))
