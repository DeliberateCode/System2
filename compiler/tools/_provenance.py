"""Write consistent ``PROVENANCE.json`` files for regenerated distributions."""

import datetime
import hashlib
import json
import os

import build_bundle  # sibling tool; regen_all puts compiler/tools on sys.path

__all__ = ["source_sha256", "write_provenance", "PROVENANCE_FILENAME"]

PROVENANCE_FILENAME = "PROVENANCE.json"

# Exclude caches and generated bundle output from distribution input hashes.
_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git",
     "_system2_compiler"}
)


def _iter_input_files(roots):
    """Yield ``(relpath, abspath)`` over every input file, sorted by relpath."""
    out = []
    for label, path in roots:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            out.append((label, path))
            continue
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDE_DIRS)
            for fn in sorted(filenames):
                if fn.endswith(".pyc"):
                    continue
                abspath = os.path.join(dirpath, fn)
                rel = os.path.relpath(abspath, path).replace(os.sep, "/")
                out.append((f"{label}/{rel}", abspath))
    out.sort(key=lambda pair: pair[0])
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


def write_provenance(
    dest_dir: str, *, inputs, generator: str, channel_version: str, compiler_root: str
) -> dict:
    """Write ``<dest_dir>/PROVENANCE.json`` and return the provenance dict."""
    provenance = {
        "source_sha256": source_sha256(inputs),
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
