"""Grep oracle: fail on any LIVE reference to a now-dead source repo.

Enforces AC1/the requirement for the consolidation: the plugin, utilities, compiler and
distributions now live in ONE repo, so nothing outside a deliberate
historical-record allowlist may still point at the old standalone repos
(``System2``-``Compiler`` / ``System2``-``UtilitySkills``). A live pointer means a
stale install path, provenance URL, or cross-repo import that would silently rot.

Scan model: a filesystem walk from ``--root`` (default: the consolidated repo
root). Infrastructure dirs (``.git``, caches, ``.venv``) are pruned; every other
file is read as text and matched against the two dead-repo names. A hit whose path
is NOT allowlisted is a violation: the file:line and matched name are printed and
the tool exits 1. A clean tree exits 0.

The two needles are assembled from fragments below so THIS oracle (and its eval)
never contain the literal strings and therefore never flag themselves — no
self-allowlist is required.

Stdlib-only (1 of the 7-module simplicity budget). All scanned file contents are
treated as untrusted data.
"""

import argparse
import fnmatch
import os
import sys

__all__ = ["OLD_REPOS", "ALLOWLIST", "SKIP_DIRS", "scan"]

# The dead-repo names, built from fragments so this source (and the eval that
# seeds a violation) never contain the literal strings the oracle searches for.
OLD_REPOS = ("System2-" "Compiler", "System2-" "UtilitySkills")

# Infrastructure dirs pruned from the walk: never repo source, and ``.venv`` would
# otherwise pull in third-party site-packages (slow + false positives).
SKIP_DIRS = frozenset({
    ".git",
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
})

# PRINCIPLED allowlist: genuine historical records and user docs whose install
# pointers a later cycle refreshes — NOT live coupling. Each entry is a POSIX path
# relative to the scan root. An entry ending in ``/`` matches that dir and anything
# under it; a bare filename (no ``/``) matches by basename at any depth; otherwise
# it is an exact relpath. Every entry carries its justification inline.
ALLOWLIST = (
    # Historical changelogs (any depth): they record the old-repo world by design.
    "CHANGELOG.md",
    # Core cycle immutable spec archive — NEVER edited.
    "spec/",
    # Compiler cycle immutable spec archive — NEVER edited.
    "compiler/spec/",
    # Historical planning doc: records the pre-consolidation single-repo decision.
    "compiler/PLAN.md",
    # Frozen captured-output golden corpus (immutable like the spec archives). The
    # only dead-repo occurrence is the absolute overlay ``source_path`` prefix baked
    # into ``overlay-manifest.lock`` at pre-consolidation capture time, which
    # ``run_goldens._normalize_lock_paths`` redacts before the byte compare — inert
    # test data, not live coupling. Editing would churn goldens (forbidden this cycle).
    "compiler/evals/goldens/",
    # User doc: the implementation work/039 refreshes its install pointers; allowlisted this cycle
    # so the oracle can pass while the historical URLs still stand.
    "compiler/README.md",
    # User doc (examples): the implementation work/039 refreshes its install pointers.
    "compiler/examples/README.md",
    # Migration doc: deliberately names the old standalone utility-skills repo so
    # former standalone-marketplace users can find their origin. CD-1, ADOPTED
    # option (c) — see spec-consolidation-completion/decisions/
    # old-repo-name-guard-vs-migration-text.md. README.md's pointer stays
    # name-free and fully guarded; only this one migration-history-bearing doc is
    # allowlisted.
    "docs/installation/claude-code.md",
    # Compiler product/package name in the package docstring title — the compiler's
    # OWN name, not a pointer to the dead source repo. This file is a HASHED bundle
    # member, so rewording it would change compiler_source_sha256; allowlisted so the
    # product name stays put without breaking the bundle drift anchor.
    "compiler/system2_compiler/__init__.py",
    # Verbatim vendored copy of the package docstring above (same product-name title,
    # same hashed-member constraint).
    "plugin/scripts/_system2_compiler/system2_compiler/__init__.py",
)


def _is_allowlisted(rel: str) -> bool:
    for entry in ALLOWLIST:
        if entry.endswith("/"):
            if rel == entry[:-1] or rel.startswith(entry):
                return True
        elif "/" in entry:
            if rel == entry:
                return True
        elif fnmatch.fnmatch(os.path.basename(rel), entry):
            return True
    return False


def scan(root: str):
    """Return a sorted list of ``(relpath, lineno, needle)`` non-allowlisted hits.

    ``relpath`` is POSIX-normalized relative to ``root``. Infrastructure dirs are
    pruned; unreadable/binary bytes are decoded with ``errors="ignore"`` so a stray
    binary never aborts the scan (it simply won't contain the ASCII needles).
    """
    root = os.path.abspath(root)
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        # PR #10 review finding 12: `pip install -e compiler/` writes
        # <pkg>.egg-info/PKG-INFO next to the source, which embeds
        # compiler/README.md's prose verbatim -- including the old repo name --
        # as the package long-description. The directory name varies per
        # package, so it can't live in the exact-match SKIP_DIRS set.
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        ]
        for fn in filenames:
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            if _is_allowlisted(rel):
                continue
            try:
                with open(abspath, "r", encoding="utf-8", errors="ignore") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                for needle in OLD_REPOS:
                    if needle in line:
                        hits.append((rel, lineno, needle))
    hits.sort()
    return hits


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail on any live reference to a dead source repo.",
    )
    parser.add_argument(
        "--root", default=None,
        help="Scan root (default: the consolidated repo root, two levels above "
             "this tool's dir).",
    )
    args = parser.parse_args(argv)

    # compiler/tools/check_no_old_repo_refs.py -> compiler/tools -> compiler -> root
    root = args.root or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    hits = scan(root)
    for rel, lineno, needle in hits:
        sys.stdout.write(f"{rel}:{lineno}: live dead-repo reference: {needle}\n")
    if hits:
        sys.stderr.write(
            f"FAIL: {len(hits)} non-allowlisted dead-repo reference(s) found.\n"
        )
        return 1
    sys.stdout.write("OK: no non-allowlisted dead-repo references.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
