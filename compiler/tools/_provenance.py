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


def _path_mode(path: str, description: str, trusted_root: str) -> int:
    """Return ``path``'s no-follow mode after validating components below root."""
    path = os.path.abspath(path)
    trusted_root = os.path.abspath(trusted_root)
    try:
        if os.path.commonpath((trusted_root, path)) != trusted_root:
            raise ValueError(f"{description} escapes trusted root: {path}")
    except ValueError:
        raise ValueError(f"{description} escapes trusted root: {path}") from None

    relative = os.path.relpath(path, trusted_root)
    components = [] if relative == os.curdir else relative.split(os.sep)
    current = trusted_root
    try:
        if components:
            root_mode = os.lstat(current).st_mode
            if not stat.S_ISDIR(root_mode):
                raise ValueError(
                    f"{description} trusted root has an invalid file type: {current}"
                )
        for index, component in enumerate(components):
            current = os.path.join(current, component)
            mode = os.lstat(current).st_mode
            if index < len(components) - 1 and not stat.S_ISDIR(mode):
                raise ValueError(
                    f"{description} has an invalid ancestor file type: {current}"
                )
        return os.lstat(path).st_mode if not components else mode
    except FileNotFoundError:
        raise FileNotFoundError(f"{description} missing: {current}") from None


def _require_type(
    path: str, predicate, description: str, *, trusted_root: str
) -> None:
    """Reject links, special files, and invalid ancestors without dereferencing."""
    if not predicate(_path_mode(path, description, trusted_root)):
        raise ValueError(f"{description} has an invalid file type: {path}")


def _require_file(path: str, description: str, *, trusted_root: str) -> None:
    _require_type(path, stat.S_ISREG, description, trusted_root=trusted_root)


def _require_directory(path: str, description: str, *, trusted_root: str) -> None:
    _require_type(path, stat.S_ISDIR, description, trusted_root=trusted_root)


def _iter_input_files(roots):
    """Yield ``(relpath, abspath)`` over every input file, sorted by relpath."""
    out = []
    for entry in roots:
        if len(entry) == 3:
            label, path, trusted_root = entry
        else:
            label, path = entry
            path = os.path.abspath(path)
            trusted_root = path if os.path.isdir(path) else os.path.dirname(path)
        path = os.path.abspath(path)
        trusted_root = os.path.abspath(trusted_root)
        try:
            mode = _path_mode(path, "provenance input", trusted_root)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"provenance input missing: {label} ({path})"
            ) from None
        if stat.S_ISREG(mode):
            out.append((label, path, trusted_root))
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError(
                f"provenance input is not a regular file or directory: "
                f"{label} ({path})"
            )
        for dirpath, dirnames, filenames in os.walk(path):
            _require_directory(
                dirpath, "provenance input directory", trusted_root=trusted_root
            )
            retained = []
            for dirname in sorted(dirnames):
                child = os.path.join(dirpath, dirname)
                _require_directory(
                    child, "provenance input directory", trusted_root=trusted_root
                )
                if dirname not in _EXCLUDE_DIRS:
                    retained.append(dirname)
            dirnames[:] = retained
            for fn in sorted(filenames):
                abspath = os.path.join(dirpath, fn)
                _require_file(
                    abspath, "provenance input file", trusted_root=trusted_root
                )
                if fn.endswith(".pyc"):
                    continue
                rel = os.path.relpath(abspath, path).replace(os.sep, "/")
                out.append((f"{label}/{rel}", abspath, trusted_root))
    out.sort(key=lambda entry: entry[0])
    labels = [label for label, _path, _root in out]
    if len(labels) != len(set(labels)):
        raise ValueError("provenance input labels must be unique")
    return out


def source_sha256(roots) -> str:
    """Return the sha256 over the sorted ``(relpath, bytes)`` of the generator inputs."""
    digest = hashlib.sha256()
    for rel, abspath, trusted_root in _iter_input_files(roots):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        _require_file(abspath, "provenance input file", trusted_root=trusted_root)
        with open(abspath, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


def artifact_inventory(dest_dir: str, *, trusted_root=None):
    """Return the exact sorted artifact-file inventory, excluding provenance itself."""
    root = os.path.abspath(dest_dir)
    trusted_root = os.path.abspath(trusted_root or root)
    _require_directory(
        root, "artifact root directory", trusted_root=trusted_root
    )
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        _require_directory(
            dirpath, "artifact directory", trusted_root=trusted_root
        )
        retained = []
        for dirname in sorted(dirnames):
            child = os.path.join(dirpath, dirname)
            _require_directory(
                child, "artifact directory", trusted_root=trusted_root
            )
            if dirname not in _ARTIFACT_EXCLUDE_DIRS:
                retained.append(dirname)
        dirnames[:] = retained
        for fn in sorted(filenames):
            abspath = os.path.join(dirpath, fn)
            _require_file(abspath, "artifact file", trusted_root=trusted_root)
            if fn.endswith(".pyc"):
                continue
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            if rel == PROVENANCE_FILENAME:
                continue
            out.append(rel)
    return sorted(out)


def artifact_sha256(dest_dir: str, inventory=None, *, trusted_root=None) -> str:
    """Hash the sorted ``(artifact relpath, bytes)`` without self-reference."""
    dest_dir = os.path.abspath(dest_dir)
    trusted_root = os.path.abspath(trusted_root or dest_dir)
    inventory = (
        artifact_inventory(dest_dir, trusted_root=trusted_root)
        if inventory is None else inventory
    )
    digest = hashlib.sha256()
    for rel in inventory:
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        path = os.path.join(dest_dir, rel.replace("/", os.sep))
        _require_file(path, "artifact file", trusted_root=trusted_root)
        with open(path, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


def artifacts_match(dest_dir: str, provenance=None, *, trusted_root=None) -> bool:
    """Fail closed unless provenance pins the exact current artifact set and bytes."""
    dest_dir = os.path.abspath(dest_dir)
    trusted_root = os.path.abspath(trusted_root or dest_dir)
    try:
        _require_directory(
            dest_dir, "artifact root directory", trusted_root=trusted_root
        )
    except (OSError, ValueError):
        return False
    if provenance is None:
        try:
            provenance_path = os.path.join(dest_dir, PROVENANCE_FILENAME)
            _require_file(
                provenance_path, "provenance file", trusted_root=trusted_root
            )
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
        current_inventory = artifact_inventory(
            dest_dir, trusted_root=trusted_root
        )
        if recorded_inventory != current_inventory:
            return False
        return artifact_sha256(
            dest_dir, current_inventory, trusted_root=trusted_root
        ) == recorded_digest
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
