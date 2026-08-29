"""Plugin-side bundle TAMPER check (ships WITH the vendored bundle).

Phase 5 drift guard, the plugin half (AC-5.7, G8/NFR-006). The plugin runs the
vendored ``_system2_compiler/`` bundle and CANNOT see the compiler source, so it
can only perform the INTERNAL-INTEGRITY (tamper) check:

  recompute the sha256 over the vendored subtree's source bytes (the SAME
  ``(relpath, bytes)`` algorithm ``tools/build_bundle.py`` uses) and compare it to
  ``BUNDLE.json``'s recorded ``compiler_source_sha256``.

A mismatch means a vendored file was hand-edited without re-running the bundler
(``bundle_tampered`` -> regenerate via the compiler's ``tools/build_bundle.py``).

This is DISTINCT from the cross-repo STALENESS check
(``System2-Compiler/tools/check_bundle_fresh.py``): staleness asks "does the
vendored bundle still match the *current compiler source*?", which requires the
compiler source and is therefore the CI guard. The plugin ships only the bundle,
so it surfaces the *recorded provenance* + the *internal-integrity* (tamper)
result through ``system2:doctor``. Both keys off the same
``compiler_source_sha256``.

Stdlib-only, report-only (it never blocks compose). Runs with no compiler source
present. ``BUNDLE.json`` and ``__pycache__``/``*.pyc`` are excluded from the hash
(``BUNDLE.json`` is not a bundled member; the volatile ``bundled_at`` lives only
inside it and so never enters the digest).
"""

import hashlib
import json
import os
import sys

__all__ = [
    "BUNDLE_DIR",
    "compute_subtree_hash",
    "check_bundle_integrity",
    "format_findings",
]

# The vendored member, matching ``tools/build_bundle.py``'s ``_BUNDLE_MEMBERS``:
# the single ``system2_compiler`` product package (its ``ir``/``backends``
# subpackages, the ``plugin_adapter`` entry, and ``cli``). Every relpath under it is
# IDENTICAL on the compiler source side and inside ``_system2_compiler/`` (the
# bundle is a pure copy), so this digest reproduces the recorded hash exactly. NOTE:
# ``_freshness.py`` itself is a COMPANION (not a member), so it lives at the bundle
# root alongside this nested package and is correctly excluded from the digest.
_BUNDLE_MEMBERS = ("system2_compiler",)

# Build/test detritus excluded from the verbatim copy (and so from the hash),
# matching ``tools/build_bundle.py``'s ``_EXCLUDE_DIRS``.
_EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

# This module lives at the bundle root, so its own dir IS the vendored bundle.
BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))


def _iter_source_files(bundle_dir):
    """Yield ``(relpath, abspath)`` for every vendored source file, sorted.

    ``relpath`` is POSIX-normalized and relative to *bundle_dir* — the same key
    ``tools/build_bundle.py`` folds into the digest. ``BUNDLE.json`` is not a
    member and ``*.pyc`` / ``__pycache__`` are skipped, so the digest is stable.
    """
    out = []
    for member in _BUNDLE_MEMBERS:
        src = os.path.join(bundle_dir, member)
        if os.path.isfile(src):
            out.append((member, src))
            continue
        if not os.path.isdir(src):
            raise FileNotFoundError(
                f"vendored bundle member missing: {member}"
            )
        for dirpath, dirnames, filenames in os.walk(src):
            dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDE_DIRS)
            for fn in sorted(filenames):
                if fn.endswith(".pyc"):
                    continue
                abspath = os.path.join(dirpath, fn)
                rel = os.path.relpath(abspath, bundle_dir)
                out.append((rel.replace(os.sep, "/"), abspath))
    out.sort(key=lambda pair: pair[0])
    return out


def compute_subtree_hash(bundle_dir):
    """Return the sha256 over the sorted ``(relpath, bytes)`` of the vendored subtree.

    Byte-for-byte the algorithm ``tools/build_bundle.compute_source_hash`` runs on
    the compiler source, so an untampered bundle's recomputed hash equals its
    recorded ``compiler_source_sha256``.
    """
    digest = hashlib.sha256()
    for rel, abspath in _iter_source_files(bundle_dir):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with open(abspath, "rb") as fh:
            digest.update(fh.read())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_manifest(bundle_dir):
    with open(os.path.join(bundle_dir, "BUNDLE.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_bundle_integrity(bundle_dir=None):
    """Return the structured ``bundle_freshness`` / ``bundle_tampered`` result.

    The returned dict is report-only (it never blocks compose):

      * ``tampered``  -- True when the recomputed subtree hash != the recorded
                         ``compiler_source_sha256`` (a hand-edited bundle).
      * ``recorded_source_sha256`` / ``recomputed_source_sha256`` -- the anchors.
      * ``compiler_version`` / ``generated_from`` -- the recorded provenance.
      * ``error`` -- a message when ``BUNDLE.json`` is unreadable (treated as
                     tampered: the manifest is part of the bundle's integrity).
    """
    bundle_dir = bundle_dir or BUNDLE_DIR
    result = {
        "tampered": False,
        "recorded_source_sha256": None,
        "recomputed_source_sha256": None,
        "compiler_version": None,
        "generated_from": None,
        "error": None,
    }
    try:
        manifest = _read_manifest(bundle_dir)
    except (OSError, ValueError) as exc:
        result["tampered"] = True
        result["error"] = f"unreadable BUNDLE.json: {exc}"
        return result

    result["recorded_source_sha256"] = manifest.get("compiler_source_sha256")
    result["compiler_version"] = manifest.get("compiler_version")
    result["generated_from"] = manifest.get("generated_from")

    recomputed = compute_subtree_hash(bundle_dir)
    result["recomputed_source_sha256"] = recomputed
    result["tampered"] = recomputed != result["recorded_source_sha256"]
    return result


def format_findings(result):
    """Render the doctor surface (a ``bundle_freshness`` line, then the verdict).

    Always reports ``bundle_freshness`` (recorded provenance + source hash). On a
    mismatch it adds a LOUD ``bundle_tampered`` finding directing the user to
    regenerate via the compiler's bundler; otherwise an ``ok`` line.
    """
    recorded = result["recorded_source_sha256"]
    short = (recorded[:12] + "…") if recorded else "<none>"
    lines = [
        "bundle_freshness: compiler_source_sha256={short} "
        "compiler_version={ver} generated_from={src}".format(
            short=short,
            ver=result["compiler_version"],
            src=result["generated_from"],
        )
    ]
    if result["tampered"]:
        detail = result["error"] or (
            "vendored subtree hash {got} != recorded {want}".format(
                got=(result["recomputed_source_sha256"] or "<none>")[:12] + "…",
                want=short,
            )
        )
        lines.append(
            "bundle_tampered: the vendored _system2_compiler/ bundle was "
            "hand-edited (" + detail + "); regenerate it via the compiler's "
            "tools/build_bundle.py (do not edit the vendored bundle by hand)."
        )
    else:
        lines.append(
            "bundle_tampered: ok (vendored subtree matches the recorded "
            "compiler_source_sha256)."
        )
    return "\n".join(lines)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    bundle_dir = argv[0] if argv else BUNDLE_DIR
    result = check_bundle_integrity(bundle_dir)
    sys.stdout.write(format_findings(result) + "\n")
    return 1 if result["tampered"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
