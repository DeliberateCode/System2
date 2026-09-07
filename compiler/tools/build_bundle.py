"""Deterministic bundler: emit the vendored, stdlib-only ``_system2_compiler/`` subtree."""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

__all__ = ["build_bundle", "compute_source_hash", "expected_manifest"]

# Vendor the complete system2_compiler package verbatim.
_BUNDLE_MEMBERS = ("system2_compiler",)

# Exclude build and test detritus from the package walk.
_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

# Companions stay outside the self-referential package hash. The external freshness
# guard validates their exact inventory and compares each one to its canonical source.
_BUNDLE_COMPANIONS = (("tools/_freshness.py", "_freshness.py"),)

_BUNDLE_DIRNAME = "_system2_compiler"


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


def _iter_source_files(compiler_root: str):
    """Yield ``(relpath, abspath)`` for every bundled source file, sorted."""
    compiler_root = os.path.abspath(compiler_root)
    _require_directory(compiler_root, "compiler source root")
    out = []
    for member in _BUNDLE_MEMBERS:
        src = os.path.join(compiler_root, member)
        try:
            mode = os.lstat(src).st_mode
        except FileNotFoundError:
            raise FileNotFoundError(
                f"bundle member missing from compiler source: {member}"
            ) from None
        if stat.S_ISREG(mode):
            out.append((member, src))
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError(
                f"bundle member is not a regular file or directory: {member}"
            )
        for dirpath, dirnames, filenames in os.walk(src):
            _require_directory(dirpath, "bundle source directory")
            retained = []
            for dirname in sorted(dirnames):
                child = os.path.join(dirpath, dirname)
                _require_directory(child, "bundle source directory")
                if dirname not in _EXCLUDE_DIRS:
                    retained.append(dirname)
            dirnames[:] = retained
            for fn in sorted(filenames):
                abspath = os.path.join(dirpath, fn)
                _require_file(abspath, "bundle source file")
                if fn.endswith(".pyc"):
                    continue
                rel = os.path.relpath(abspath, compiler_root)
                out.append((rel.replace(os.sep, "/"), abspath))
    out.sort(key=lambda pair: pair[0])
    return out


def compute_source_hash(compiler_root: str) -> str:
    """Return the sha256 over the sorted ``(relpath, bytes)`` of the bundled source."""
    digest = hashlib.sha256()
    for rel, abspath in _iter_source_files(compiler_root):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with open(abspath, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_subtree(compiler_root: str, bundle_root: str) -> None:
    """Copy every bundled member verbatim into ``bundle_root`` (a clean dir)."""
    for rel, abspath in _iter_source_files(compiler_root):
        dest = os.path.join(bundle_root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(abspath, dest)


def _copy_companions(compiler_root: str, bundle_root: str) -> None:
    """Vendor each bundle companion into ``bundle_root`` (non-hashed, re-emitted)."""
    for src_rel, dest_rel in _BUNDLE_COMPANIONS:
        src = os.path.join(compiler_root, src_rel.replace("/", os.sep))
        _require_file(src, f"bundle companion {src_rel}")
        dest = os.path.join(bundle_root, dest_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)


def _compiler_version(compiler_root: str) -> str:
    """Read the compiler version from ``pyproject.toml`` (``project.version``)."""
    path = os.path.join(compiler_root, "pyproject.toml")
    try:
        _require_file(path, "compiler project metadata")
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "0.0.0"


def _git_rev(compiler_root: str) -> str:
    """Return the short git rev of the consolidated repo HEAD, or ``unknown``."""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=compiler_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return rev or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def expected_manifest(compiler_root: str) -> dict:
    """Return the complete manifest expected for the current compiler source."""
    return {
        "compiler_source_sha256": compute_source_hash(compiler_root),
        "compiler_version": _compiler_version(compiler_root),
        "generated_from": f"System2@{_git_rev(compiler_root)}",
        "bundled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def build_bundle(compiler_root: str, dest: str) -> dict:
    """Generate ``<dest>/_system2_compiler/`` + ``BUNDLE.json``; return the manifest."""
    compiler_root = os.path.abspath(compiler_root)
    bundle_root = os.path.join(os.path.abspath(dest), _BUNDLE_DIRNAME)

    if os.path.exists(bundle_root):
        shutil.rmtree(bundle_root)
    os.makedirs(bundle_root)

    _copy_subtree(compiler_root, bundle_root)
    _copy_companions(compiler_root, bundle_root)

    manifest = expected_manifest(compiler_root)
    with open(os.path.join(bundle_root, "BUNDLE.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the vendored _system2_compiler/ bundle + BUNDLE.json.",
    )
    parser.add_argument(
        "--dest", required=True,
        help="Destination dir; the bundle is written to <dest>/_system2_compiler/.",
    )
    parser.add_argument(
        "--compiler-root", default=None,
        help="Compiler source root (default: the parent of this tool's dir).",
    )
    args = parser.parse_args(argv)

    compiler_root = args.compiler_root or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    manifest = build_bundle(compiler_root, args.dest)
    sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
