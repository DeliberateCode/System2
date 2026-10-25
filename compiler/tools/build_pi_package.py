"""Build the npm package from the Pi backend's canonical output."""

import json
import os
import shutil

from system2_compiler.channel_version import CHANNEL_VERSION

__all__ = ["build", "PACKAGE_NAME", "PACKAGE_VERSION"]

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_TOOLS_DIR, "templates")
_REPO_ROOT = os.path.dirname(os.path.dirname(_TOOLS_DIR))

PACKAGE_NAME = "@deliberatecode/pi-system2"
PACKAGE_VERSION = CHANNEL_VERSION

# The ``.pi/<X>/`` subtrees that ARE pi package component types: hoisted to ``<X>/``.
_COMPONENT_PREFIXES = ("extensions", "skills", "prompts")

_VERSION_PLACEHOLDER = "__VERSION__"
_MANAGED_FILES_PLACEHOLDER = "__MANAGED_FILES__"
_PACKAGE_NAME_PLACEHOLDER = "__PACKAGE_NAME__"
_FORBIDDEN_PACKAGE_KEYS = ("scripts", "dependencies", "devDependencies")


def build(staging_emission, dest, package_version=PACKAGE_VERSION):
    """Transform the canonical Pi emission at *staging_emission* into *dest*."""
    if package_version != PACKAGE_VERSION:
        raise ValueError(
            f"Pi package version mismatch: requested {package_version!r}, "
            f"expected {PACKAGE_VERSION!r}"
        )
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    managed = []
    for rel, abspath in _iter_files(staging_emission):
        dest_rel, managed_rel = _classify(rel)
        if managed_rel is not None:
            managed.append(managed_rel)
        out = os.path.join(dest, dest_rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copyfile(abspath, out)

    _write_package_json(dest, package_version)
    _write_init_extension(dest, sorted(managed))
    _write_readme(dest)
    _write_license(dest)


def _iter_files(root):
    """Yield ``(posix_relpath, abspath)`` for every file under *root*, deterministically."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            abspath = os.path.join(dirpath, fn)
            yield os.path.relpath(abspath, root).replace(os.sep, "/"), abspath


def _classify(rel):
    """Map an emitted relpath to ``(dest_relpath, managed_relpath_or_None)``."""
    if rel.startswith(".pi/"):
        inner = rel[len(".pi/"):]
        top = inner.split("/", 1)[0]
        if top in _COMPONENT_PREFIXES:
            return inner, None
    return "payload/project/" + rel, rel


def _write_package_json(dest, version):
    content = _read_template("pi_package.json").replace(_VERSION_PLACEHOLDER, version)
    # Reject install scripts and dependencies before publishing.
    obj = json.loads(content)
    for key in _FORBIDDEN_PACKAGE_KEYS:
        if key in obj:
            raise ValueError(
                f"pi_package.json must not declare {key!r} (the package "
                f"carries no scripts and no dependencies)"
            )
    with open(os.path.join(dest, "package.json"), "w", encoding="utf-8") as fh:
        fh.write(content)


def _write_init_extension(dest, managed):
    content = _read_template("system2_init_ts.template").replace(
        _MANAGED_FILES_PLACEHOLDER, json.dumps(managed)
    )
    out = os.path.join(dest, "extensions", "system2-init.ts")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(content)


def _write_readme(dest):
    content = _read_template("pi_readme.md.template").replace(
        _PACKAGE_NAME_PLACEHOLDER, PACKAGE_NAME
    )
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(content)


def _write_license(dest):
    # Copy the repo-root LICENSE verbatim (single source of truth; the package's copy
    # cannot drift, and the regen freshness guard byte-diffs it).
    with open(os.path.join(_REPO_ROOT, "LICENSE"), "r", encoding="utf-8") as fh:
        content = fh.read()
    with open(os.path.join(dest, "LICENSE"), "w", encoding="utf-8") as fh:
        fh.write(content)


def _read_template(name):
    with open(os.path.join(_TEMPLATES_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()
