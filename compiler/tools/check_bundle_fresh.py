"""Drift guard: fail when the committed vendored bundle is stale or hand-edited.

convergence implementation (AC-5.7, G8/NFR-006). The machine-enforced cross-repo freshness check: a
stale vendored bundle CANNOT merge. It regenerates the bundle from the CURRENT
compiler source into a temp dir, recomputes the source hash, and compares it to a
TARGET bundle's recorded + recomputed hashes:

  * ``compiler_source_sha256`` recorded in the target's ``BUNDLE.json`` — catches
    a STALE bundle (the committed copy predates the current compiler source).
  * the hash recomputed over the target's OWN vendored subtree — catches a
    HAND-EDITED / TAMPERED bundle (someone changed a vendored byte without
    re-running the bundler, so the subtree no longer matches its recorded hash).

On any mismatch it exits non-zero with the exact message
``vendored bundle is stale: regenerate via tools/build_bundle.py``.

Deterministic: the bundler is a pure copy and ``bundled_at`` is excluded from the
hash, so a fresh bundle of unchanged source always matches.

Stdlib-only. Reuses ``build_bundle`` so the regenerate path cannot drift from the
generator.
"""

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
    """Recompute the source hash over the TARGET bundle's own vendored subtree.

    The target's ``_system2_compiler/`` is itself a copy of ``ir/`` + ``backends/``
    + the entry/adapter members, so ``build_bundle.compute_source_hash`` over it
    yields the hash its bytes SHOULD record. A mismatch vs ``BUNDLE.json`` means a
    vendored byte was hand-edited (tamper).
    """
    return build_bundle.compute_source_hash(bundle_dir)


def check_bundle_fresh(compiler_root: str, target_bundle_dir: str) -> int:
    """Return 0 when the target bundle is fresh + untampered, else non-zero.

    *target_bundle_dir* is the directory CONTAINING ``_system2_compiler/`` (i.e.
    the ``plugin/scripts/`` analogue), or the ``_system2_compiler/`` dir itself.
    """
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
