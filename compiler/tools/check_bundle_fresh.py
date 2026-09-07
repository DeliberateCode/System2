"""Drift guard: fail when the committed vendored bundle is stale or hand-edited."""

import argparse
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_bundle  # noqa: E402

__all__ = ["check_bundle_fresh", "STALE_MESSAGE"]

STALE_MESSAGE = "vendored bundle is stale: regenerate via tools/build_bundle.py"

_BUNDLE_DIRNAME = "_system2_compiler"
_VOLATILE_MANIFEST_FIELDS = frozenset({"bundled_at", "generated_from"})


def _fail(detail: str) -> int:
    sys.stderr.write(f"{STALE_MESSAGE}\n  ({detail})\n")
    return 1


def _require_type(path: str, predicate, description: str) -> None:
    """Reject links and special files without dereferencing them."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        raise FileNotFoundError(f"{description} missing: {path}") from None
    if not predicate(mode):
        raise ValueError(f"{description} has an invalid file type: {path}")


def _require_file(path: str, description: str) -> None:
    _require_type(path, stat.S_ISREG, description)


def _require_directory(path: str, description: str) -> None:
    _require_type(path, stat.S_ISDIR, description)


def _read_manifest(bundle_dir: str) -> dict:
    path = os.path.join(bundle_dir, "BUNDLE.json")
    _require_file(path, "bundle manifest")
    with open(path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    if not isinstance(manifest, dict):
        raise ValueError("BUNDLE.json must contain an object")
    return manifest


def _root_entries(bundle_dir: str):
    _require_directory(bundle_dir, "bundle root")
    return {
        name for name in os.listdir(bundle_dir)
        if name not in build_bundle._EXCLUDE_DIRS and not name.endswith(".pyc")
    }


def _expected_root_entries():
    entries = {"BUNDLE.json"}
    entries.update(member.replace("\\", "/").split("/", 1)[0]
                   for member in build_bundle._BUNDLE_MEMBERS)
    entries.update(dest.replace("\\", "/").split("/", 1)[0]
                   for _source, dest in build_bundle._BUNDLE_COMPANIONS)
    return entries


def _iter_nonmember_files(bundle_dir: str):
    member_prefixes = tuple(
        member.replace("\\", "/").rstrip("/")
        for member in build_bundle._BUNDLE_MEMBERS
    )
    out = set()
    for dirpath, dirnames, filenames in os.walk(bundle_dir):
        _require_directory(dirpath, "bundle directory")
        retained = []
        for dirname in sorted(dirnames):
            child = os.path.join(dirpath, dirname)
            _require_directory(child, "bundle directory")
            if dirname not in build_bundle._EXCLUDE_DIRS:
                retained.append(dirname)
        dirnames[:] = retained
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            _require_file(path, "bundle file")
            if filename.endswith(".pyc"):
                continue
            rel = os.path.relpath(path, bundle_dir).replace(os.sep, "/")
            if rel == "BUNDLE.json":
                continue
            if any(rel == member or rel.startswith(member + "/")
                   for member in member_prefixes):
                continue
            out.add(rel)
    return out


def _companions_match(compiler_root: str, bundle_dir: str) -> bool:
    expected = {
        dest.replace("\\", "/")
        for _source, dest in build_bundle._BUNDLE_COMPANIONS
    }
    try:
        if _iter_nonmember_files(bundle_dir) != expected:
            return False
        for source_rel, dest_rel in build_bundle._BUNDLE_COMPANIONS:
            source = os.path.join(compiler_root, source_rel.replace("/", os.sep))
            target = os.path.join(bundle_dir, dest_rel.replace("/", os.sep))
            _require_file(source, "canonical bundle companion")
            _require_file(target, "vendored bundle companion")
            with open(source, "rb") as source_fh, open(target, "rb") as target_fh:
                if source_fh.read() != target_fh.read():
                    return False
    except (OSError, ValueError):
        return False
    return True


def _nonvolatile(manifest: dict) -> dict:
    return {
        key: value for key, value in manifest.items()
        if key not in _VOLATILE_MANIFEST_FIELDS
    }


def check_bundle_fresh(compiler_root: str, target_bundle_dir: str) -> int:
    """Return 0 only for an exact, current package/companion/manifest bundle."""
    compiler_root = os.path.abspath(compiler_root)
    target_bundle_dir = os.path.abspath(target_bundle_dir)
    if os.path.basename(target_bundle_dir) != _BUNDLE_DIRNAME:
        target_bundle_dir = os.path.join(target_bundle_dir, _BUNDLE_DIRNAME)

    try:
        _require_directory(target_bundle_dir, "vendored bundle root")
        root_entries = _root_entries(target_bundle_dir)
    except (OSError, ValueError) as exc:
        return _fail(f"invalid vendored bundle root: {exc}")

    if root_entries != _expected_root_entries():
        return _fail("bundle root inventory has missing or extra entries")

    if not _companions_match(compiler_root, target_bundle_dir):
        return _fail("bundle companion inventory or bytes differ from canonical source")

    try:
        manifest = _read_manifest(target_bundle_dir)
        expected = build_bundle.expected_manifest(compiler_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _fail(f"unreadable BUNDLE.json or compiler source: {exc}")

    if set(manifest) != set(expected):
        return _fail("BUNDLE.json field inventory differs from the canonical manifest")
    if _nonvolatile(manifest) != _nonvolatile(expected):
        return _fail("nonvolatile BUNDLE.json fields differ from current compiler source")

    recorded_hash = manifest["compiler_source_sha256"]
    try:
        target_hash = build_bundle.compute_source_hash(target_bundle_dir)
    except (OSError, ValueError) as exc:
        return _fail(f"vendored package inventory is incomplete: {exc}")
    if recorded_hash != target_hash:
        return _fail(
            "vendored package was hand-edited: its exact bytes no longer match "
            "compiler_source_sha256"
        )

    sys.stdout.write(
        "vendored bundle is fresh "
        f"(compiler_source_sha256 {recorded_hash[:12]}…)\n"
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
