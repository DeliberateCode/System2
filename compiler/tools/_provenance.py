"""Write consistent ``PROVENANCE.json`` files for regenerated distributions."""

import datetime
import hashlib
import json
import os
import stat

import build_bundle  # sibling tool; regen_all puts compiler/tools on sys.path

__all__ = [
    "artifact_inventory",
    "artifact_sha256",
    "artifacts_match",
    "source_sha256",
    "write_provenance",
    "PROVENANCE_FILENAME",
]

PROVENANCE_FILENAME = "PROVENANCE.json"

# Exclude caches and generated bundle output from distribution input hashes.
_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git",
     "_system2_compiler"}
)

# Artifact inventories omit only their self-referential provenance file and volatile
# build/test detritus. Generated product directories remain part of the artifact tree.
_ARTIFACT_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}
)


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


def _iter_input_files(roots):
    """Yield ``(relpath, abspath)`` over every input file, sorted by relpath."""
    out = []
    for label, path in roots:
        path = os.path.abspath(path)
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            raise FileNotFoundError(
                f"provenance input missing: {label} ({path})"
            ) from None
        if stat.S_ISREG(mode):
            out.append((label, path))
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError(
                f"provenance input is not a regular file or directory: "
                f"{label} ({path})"
            )
        for dirpath, dirnames, filenames in os.walk(path):
            _require_directory(dirpath, "provenance input directory")
            retained = []
            for dirname in sorted(dirnames):
                child = os.path.join(dirpath, dirname)
                _require_directory(child, "provenance input directory")
                if dirname not in _EXCLUDE_DIRS:
                    retained.append(dirname)
            dirnames[:] = retained
            for fn in sorted(filenames):
                abspath = os.path.join(dirpath, fn)
                _require_file(abspath, "provenance input file")
                if fn.endswith(".pyc"):
                    continue
                rel = os.path.relpath(abspath, path).replace(os.sep, "/")
                out.append((f"{label}/{rel}", abspath))
    out.sort(key=lambda pair: pair[0])
    labels = [label for label, _path in out]
    if len(labels) != len(set(labels)):
        raise ValueError("provenance input labels must be unique")
    return out


def source_sha256(roots) -> str:
    """Return the sha256 over the sorted ``(relpath, bytes)`` of the generator inputs."""
    digest = hashlib.sha256()
    for rel, abspath in _iter_input_files(roots):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with open(abspath, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


def artifact_inventory(dest_dir: str):
    """Return the exact sorted artifact-file inventory, excluding provenance itself."""
    root = os.path.abspath(dest_dir)
    _require_directory(root, "artifact root directory")
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        _require_directory(dirpath, "artifact directory")
        retained = []
        for dirname in sorted(dirnames):
            child = os.path.join(dirpath, dirname)
            _require_directory(child, "artifact directory")
            if dirname not in _ARTIFACT_EXCLUDE_DIRS:
                retained.append(dirname)
        dirnames[:] = retained
        for fn in sorted(filenames):
            abspath = os.path.join(dirpath, fn)
            _require_file(abspath, "artifact file")
            if fn.endswith(".pyc"):
                continue
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            if rel == PROVENANCE_FILENAME:
                continue
            out.append(rel)
    return sorted(out)


def artifact_sha256(dest_dir: str, inventory=None) -> str:
    """Hash the sorted ``(artifact relpath, bytes)`` without self-reference."""
    dest_dir = os.path.abspath(dest_dir)
    inventory = artifact_inventory(dest_dir) if inventory is None else inventory
    digest = hashlib.sha256()
    for rel in inventory:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        path = os.path.join(dest_dir, rel.replace("/", os.sep))
        _require_file(path, "artifact file")
        with open(path, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


def artifacts_match(dest_dir: str, provenance=None) -> bool:
    """Fail closed unless provenance pins the exact current artifact set and bytes."""
    dest_dir = os.path.abspath(dest_dir)
    try:
        _require_directory(dest_dir, "artifact root directory")
    except (OSError, ValueError):
        return False
    if provenance is None:
        try:
            provenance_path = os.path.join(dest_dir, PROVENANCE_FILENAME)
            _require_file(provenance_path, "provenance file")
            with open(provenance_path, "r", encoding="utf-8") as fh:
                provenance = json.load(fh)
        except (OSError, ValueError):
            return False
    if not isinstance(provenance, dict):
        return False
    recorded_inventory = provenance.get("artifact_inventory")
    recorded_digest = provenance.get("artifact_sha256")
    if (
        not isinstance(recorded_inventory, list)
        or any(not isinstance(rel, str) for rel in recorded_inventory)
        or recorded_inventory != sorted(set(recorded_inventory))
        or not isinstance(recorded_digest, str)
        or len(recorded_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in recorded_digest)
    ):
        return False
    try:
        current_inventory = artifact_inventory(dest_dir)
        if recorded_inventory != current_inventory:
            return False
        return artifact_sha256(dest_dir, current_inventory) == recorded_digest
    except (OSError, ValueError):
        return False


def write_provenance(
    dest_dir: str, *, inputs, generator: str, channel_version: str, compiler_root: str
) -> dict:
    """Write ``<dest_dir>/PROVENANCE.json`` and return the provenance dict."""
    inventory = artifact_inventory(dest_dir)
    provenance = {
        "source_sha256": source_sha256(inputs),
        "artifact_sha256": artifact_sha256(dest_dir, inventory),
        "artifact_inventory": inventory,
        "generated_from": f"System2@{build_bundle._git_rev(compiler_root)}",
        "generator": generator,
        "channel_version": channel_version,
        "compiler_version": build_bundle._compiler_version(compiler_root),
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(os.path.join(dest_dir, PROVENANCE_FILENAME), "w", encoding="utf-8") as fh:
        json.dump(provenance, fh, indent=2)
        fh.write("\n")
    return provenance
