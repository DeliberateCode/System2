"""Shared ``PROVENANCE.json`` writer for the distribution artifacts ``regen_all.py``
regenerates (design §Provenance scheme).

Each committed distribution (``distributions/{codex,pi}/``) carries a
``PROVENANCE.json`` recording the fingerprint of the generator's INPUTS plus the
consolidated repo rev, the generator, the versions, and a timestamp:

    {
      "source_sha256":     <sha256 over sorted (relpath, bytes) of generator inputs>,
      "generated_from":    "System2@<short-rev>",
      "generator":         "compiler/tools/regen_all.py",
      "channel_version":   <per-artifact version>,
      "compiler_version":  <compiler/pyproject.toml project.version>,
      "generated_at":      <ISO-8601 UTC — the ONLY field that varies between regens>
    }

``generated_at`` is timestamp-like: it is the single field that differs between two
regens of identical source, so ``regen_all --check`` compares provenance files
field-wise with it excluded (see ``regen_all.IGNORED_PROVENANCE_FIELDS``). Every
other byte of every other emitted file is stable.

The bundle artifact does NOT get a ``PROVENANCE.json``: its own ``BUNDLE.json``
(written by ``build_bundle.py``) is its provenance and keeps its schema (design keeps
``compiler_source_sha256`` as the drift anchor). This module serves the distribution
artifacts only.

Stdlib-only. The rev/version helpers are reused verbatim from ``build_bundle`` so a
distribution's ``generated_from``/``compiler_version`` cannot drift from the bundle's.
"""

import datetime
import hashlib
import json
import os

import build_bundle  # sibling tool; regen_all puts compiler/tools on sys.path

__all__ = ["source_sha256", "write_provenance", "PROVENANCE_FILENAME"]

PROVENANCE_FILENAME = "PROVENANCE.json"

# Directory names never hashed into a distribution's input fingerprint. The cache
# dirs are build/test detritus; ``_system2_compiler`` is the vendored (derived)
# bundle subtree under ``plugin/scripts/`` — it is generated output, never a
# composition INPUT to any distribution, so hashing it would falsely couple a
# distribution's ``source_sha256`` to a bundle regen.
_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git",
     "_system2_compiler"}
)


def _iter_input_files(roots):
    """Yield ``(relpath, abspath)`` over every input file, sorted by relpath.

    *roots* is an iterable of ``(label, path)``; *label* namespaces each root so two
    distinct input roots cannot collide in the digest. A *path* may be a file (hashed
    under its bare label) or a directory (walked). Mirrors the digest shape of
    ``build_bundle.compute_source_hash``.
    """
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
    """Return the sha256 over the sorted ``(relpath, bytes)`` of the generator inputs.

    Identical inputs -> identical hash, independent of filesystem ordering. The
    relpath (with its namespacing label) is folded into the digest so a moved/renamed
    input changes the hash even when its bytes are unchanged.
    """
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
    """Write ``<dest_dir>/PROVENANCE.json`` and return the provenance dict.

    *inputs* is the ``(label, path)`` iterable fed to :func:`source_sha256`.
    """
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
