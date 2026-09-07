"""Grep oracle: fail on any LIVE reference to a now-dead source repo."""

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

# Historical records and migration docs may retain old repository names.
ALLOWLIST = (
    # Historical changelogs (any depth): they record the old-repo world by design.
    "CHANGELOG.md",
    # Compiler specification archive.
    "compiler/spec/",
    # Frozen outputs contain historical absolute source paths.
    "compiler/evals/goldens/",
    # Compiler documentation with migration history.
    "compiler/README.md",
    # Migration guide names the former standalone utility repository.
    "docs/installation/claude-code.md",
    # Product name, not a source-repository reference.
    "compiler/system2_compiler/__init__.py",
    # Vendored copy of the product name.
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
    """Return a sorted list of ``(relpath, lineno, needle)`` non-allowlisted hits."""
    root = os.path.abspath(root)
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Editable installs copy README text into variable-name egg-info directories.
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
