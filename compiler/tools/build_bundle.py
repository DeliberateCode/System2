"""Deterministic bundler: emit the vendored, stdlib-only ``_system2_compiler/`` subtree.

Phase 5 (AC-5.5, OQ-5.4). The plugin ships a verbatim copy of the compiler's
product modules so its runtime stays ZERO-DEPENDENCY (G7/C3). This tool generates
that copy from the ``compiler/`` source:

    <dest>/_system2_compiler/
        system2_compiler/      (the product package — verbatim)
            ir/                (verbatim)
            backends/          (verbatim)
            plugin_adapter.py
            cli.py             (the adapter's PRIVATE dispatch dependency; see below)
        _freshness.py          (plugin-side tamper checker; a COMPANION, NOT hashed)
        BUNDLE.json

``plugin_adapter.py`` is the bundle's ENTRY (``--target`` hard-pinned to
``claude-code``). It delegates to ``cli`` for the single, contract-proven verb
dispatch (so the adapter and ``cli.py`` cannot drift). ``cli.py`` therefore ships
as the adapter's private implementation dependency — not as an exposed entry; the
multi-target ``cli.main`` is never reachable from the plugin because the adapter
pins the target. Both are stdlib-only, so the bundle is stdlib-only by
construction (a pure copy of the stdlib-only compiler — REQ-016/043).

The bundle is a PURE COPY (no import rewriting): the package structure is
preserved so imports resolve as-is and the module-boundary / stdlib-only
properties are EXACTLY the compiler's. Byte-identical Claude output is guaranteed
by construction (the goldens then prove it).

``_freshness.py`` is a COMPANION, not a hashed member: it is the plugin-side tamper
checker that ships WITH the bundle (so the plugin can verify integrity with no
compiler source present) but is the bundle's CONSUMER, not its product. Like
``BUNDLE.json`` it is vendored on every build yet EXCLUDED from
``compiler_source_sha256`` (it would otherwise have to hash itself). Because it is
re-emitted on every build, a regen — which removes the bundle dir first — can never
silently drop it (the regression that motivated ``_BUNDLE_COMPANIONS``).

``BUNDLE.json`` records ``compiler_source_sha256`` (the drift anchor — a sha256
over the sorted ``(relpath, bytes)`` of the copied source), ``compiler_version``,
``generated_from`` (``System2@<short-rev>`` — the consolidated repo's HEAD at regen
time, informational only and EXCLUDED from the hash), and ``bundled_at`` (ISO).
``bundled_at`` is EXCLUDED from the hash so a re-bundle of identical source is
hash-stable (the drift guard depends on this). The drift anchor stays
``compiler_source_sha256``, whose relpath basis (``system2_compiler/…``) is
unchanged by the in-repo move.

Note the inherent one-commit lag in ``generated_from``: the recorded rev is HEAD at
regen time, i.e. the PARENT of the commit that then records the regenerated bundle —
same semantics as before the consolidation.

Stdlib-only, emit-only, deterministic. ``--dest`` lets the generator write into a
TEMP dir (this tool never writes into ``System2/plugin/`` itself — that is the
flip task's job).
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

__all__ = ["build_bundle", "compute_source_hash"]

# The vendored member: the single ``system2_compiler`` product package, copied
# verbatim. It contains the ``ir`` and ``backends`` subpackages, the
# ``plugin_adapter`` entry, and ``cli`` (the adapter's private dispatch dependency —
# the one place the composer.py contract is encoded). Stdlib-only by construction.
_BUNDLE_MEMBERS = ("system2_compiler",)

# Directory names excluded from the verbatim copy (build/test detritus only — never
# product source). ``evals`` is never under ``ir/``/``backends/`` so this guards the
# package-dir walk.
_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

# Bundle COMPANIONS: files vendored into the bundle but DELIBERATELY OUTSIDE the
# hashed member set. ``_freshness.py`` is the plugin-side tamper checker — it ships
# WITH the bundle (so the plugin can verify integrity with no compiler source) but
# is the bundle's CONSUMER, not its product, so it is excluded from
# ``compiler_source_sha256`` (exactly like ``BUNDLE.json``; it would otherwise have
# to hash itself). Each entry is ``(source_relpath_from_compiler_root,
# dest_relpath_in_bundle)``. Companions are re-emitted on every build so a regen
# (which rmtrees the bundle dir first) can NEVER silently drop them.
_BUNDLE_COMPANIONS = (("tools/_freshness.py", "_freshness.py"),)

_BUNDLE_DIRNAME = "_system2_compiler"


def _iter_source_files(compiler_root: str):
    """Yield ``(relpath, abspath)`` for every bundled source file, sorted.

    ``relpath`` is POSIX-normalized and relative to the bundle root (so it is
    identical on the source side and inside ``_system2_compiler/``) — this is the
    hash key, so it must be deterministic and copy-stable.
    """
    out = []
    for member in _BUNDLE_MEMBERS:
        src = os.path.join(compiler_root, member)
        if os.path.isfile(src):
            out.append((member, src))
            continue
        if not os.path.isdir(src):
            raise FileNotFoundError(
                f"bundle member missing from compiler source: {member}"
            )
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDE_DIRS)
            for fn in sorted(filenames):
                if fn.endswith(".pyc"):
                    continue
                abspath = os.path.join(dirpath, fn)
                rel = os.path.relpath(abspath, compiler_root)
                out.append((rel.replace(os.sep, "/"), abspath))
    out.sort(key=lambda pair: pair[0])
    return out


def compute_source_hash(compiler_root: str) -> str:
    """Return the sha256 over the sorted ``(relpath, bytes)`` of the bundled source.

    This is the drift anchor: identical source -> identical hash, independent of
    filesystem ordering or ``bundled_at``. The relpath is folded into the digest
    so a moved/renamed file changes the hash even if its bytes are unchanged.
    """
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
    """Vendor each bundle companion into ``bundle_root`` (non-hashed, re-emitted).

    Companions (see ``_BUNDLE_COMPANIONS``) are NOT part of the hashed member set but
    MUST be present in every emitted bundle. Copying them on every build makes regen
    idempotent and prevents the ``rmtree``-then-rebuild from silently dropping them.
    """
    for src_rel, dest_rel in _BUNDLE_COMPANIONS:
        src = os.path.join(compiler_root, src_rel.replace("/", os.sep))
        if not os.path.isfile(src):
            raise FileNotFoundError(
                f"bundle companion missing from compiler source: {src_rel}"
            )
        dest = os.path.join(bundle_root, dest_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)


def _compiler_version(compiler_root: str) -> str:
    """Read the compiler version from ``pyproject.toml`` (``project.version``)."""
    path = os.path.join(compiler_root, "pyproject.toml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "0.0.0"


def _git_rev(compiler_root: str) -> str:
    """Return the short git rev of the consolidated repo HEAD, or ``unknown``.

    ``compiler_root`` lives inside the consolidated ``System2`` repo (subtree, no
    own ``.git``), so this resolves the monorepo's HEAD — the one-commit-lag rev
    stamped into ``generated_from`` as ``System2@<short-rev>``.
    """
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=compiler_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return rev or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_bundle(compiler_root: str, dest: str) -> dict:
    """Generate ``<dest>/_system2_compiler/`` + ``BUNDLE.json``; return the manifest.

    Deterministic and emit-only. The vendored subtree is rebuilt from scratch (any
    prior contents at the bundle root are removed first) so a re-bundle of
    identical source yields a byte-identical tree and an identical
    ``compiler_source_sha256`` (``bundled_at`` is the only varying field, and it is
    excluded from the hash). Hashed members AND non-hashed companions (see
    ``_BUNDLE_COMPANIONS``) are both re-emitted, so the rmtree can never leave a
    companion behind.
    """
    compiler_root = os.path.abspath(compiler_root)
    bundle_root = os.path.join(os.path.abspath(dest), _BUNDLE_DIRNAME)

    if os.path.exists(bundle_root):
        shutil.rmtree(bundle_root)
    os.makedirs(bundle_root)

    _copy_subtree(compiler_root, bundle_root)
    _copy_companions(compiler_root, bundle_root)

    source_hash = compute_source_hash(compiler_root)
    manifest = {
        "compiler_source_sha256": source_hash,
        "compiler_version": _compiler_version(compiler_root),
        "generated_from": f"System2@{_git_rev(compiler_root)}",
        "bundled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
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
