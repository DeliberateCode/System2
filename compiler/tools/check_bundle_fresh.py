"""Drift guard: fail when the committed vendored bundle is stale or hand-edited."""

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_bundle  # noqa: E402

__all__ = ["check_bundle_fresh", "STALE_MESSAGE"]

STALE_MESSAGE = "vendored bundle is stale: regenerate via tools/build_bundle.py"

_BUNDLE_DIRNAME = "_system2_compiler"


def _read_recorded_hash(bundle_dir: str) -> str:
    """Return ``compiler_source_sha256`` from ``<bundle_dir>/BUNDLE.json``."""
    manifest_path = os.path.join(bundle_dir, "BUNDLE.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        return json.load(fh)["compiler_source_sha256"]


def _recompute_target_hash(bundle_dir: str) -> str:
    """Recompute the source hash over the TARGET bundle's own vendored subtree."""
    return build_bundle.compute_source_hash(bundle_dir)


def check_bundle_fresh(compiler_root: str, target_bundle_dir: str) -> int:
    """Return 0 when the target bundle is fresh + untampered, else non-zero."""
    compiler_root = os.path.abspath(compiler_root)
    target_bundle_dir = os.path.abspath(target_bundle_dir)
    if os.path.basename(target_bundle_dir) != _BUNDLE_DIRNAME:
        target_bundle_dir = os.path.join(target_bundle_dir, _BUNDLE_DIRNAME)

    if not os.path.isdir(target_bundle_dir):
        sys.stderr.write(
            f"{STALE_MESSAGE}\n  (no vendored bundle at {target_bundle_dir})\n"
        )
        return 1

    current_hash = build_bundle.compute_source_hash(compiler_root)

    try:
        recorded_hash = _read_recorded_hash(target_bundle_dir)
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{STALE_MESSAGE}\n  (unreadable BUNDLE.json: {exc})\n")
        return 1

    tampered = recorded_hash != _recompute_target_hash(target_bundle_dir)
    stale = recorded_hash != current_hash

    if tampered:
        sys.stderr.write(
            f"{STALE_MESSAGE}\n  (vendored subtree was hand-edited: its bytes no "
            f"longer match the recorded compiler_source_sha256)\n"
        )
        return 1
    if stale:
        sys.stderr.write(
            f"{STALE_MESSAGE}\n  (recorded {recorded_hash[:12]}… != current "
            f"source {current_hash[:12]}…)\n"
        )
        return 1

    sys.stdout.write(
        f"vendored bundle is fresh (compiler_source_sha256 {current_hash[:12]}…)\n"
    )
    return 0


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when the committed vendored bundle is stale or tampered.",
    )
    parser.add_argument(
        "--target", required=True,
        help="Dir containing _system2_compiler/ (or the _system2_compiler/ dir).",
    )
    parser.add_argument(
        "--compiler-root", default=None,
        help="Compiler source root (default: the parent of this tool's dir).",
    )
    args = parser.parse_args(argv)
    compiler_root = args.compiler_root or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    return check_bundle_fresh(compiler_root, args.target)


if __name__ == "__main__":
    raise SystemExit(_main())
