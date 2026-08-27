"""``build_pi_package.py`` — transform the Pi backend's canonical emission into the
``@deliberatecode/pi-system2`` npm package layout (design §"Pi npm package"; TASK-024).

    build(staging_emission: str, dest: str, package_version: str = PACKAGE_VERSION) -> None

*staging_emission* is a directory containing the Pi backend's canonical emission
(``ir.compose`` + ``PiBackend.emit`` over the BASE plugin with empty overlays, so the
tree is fully portable and carries no absolute paths). ``build`` transforms it into the
npm package rooted at *dest*:

* ``.pi/extensions/`` -> ``extensions/``   (pi package component type)
* ``.pi/skills/``     -> ``skills/``       (pi package component type)
* ``.pi/prompts/``    -> ``prompts/``      (pi package component type)
* every OTHER emitted file (``AGENTS.md``, ``.pi/SYSTEM.md``, ``system2.pi.lock.json``)
  -> ``payload/project/<relpath>`` (preserving its project-relative layout). These are
  not package component types; they are materialized into a user's project by the
  generated ``extensions/system2-init.ts`` command.

The gate ``extensions/system2.ts`` is copied BYTE-FOR-BYTE from the backend's canonical
``.pi/extensions/system2.ts`` (no re-derivation — the package and the backend cannot
drift). ``package.json`` is stamped from ``templates/pi_package.json`` (name/keywords/pi
manifest/files whitelist/MIT; NO scripts, NO dependencies, NO postinstall — REQ-055/F12)
with the version substituted. ``extensions/system2-init.ts`` is generated from
``templates/system2_init_ts.template`` with the managed-file list (exactly the files
placed under ``payload/project/``) embedded. ``README.md`` is generated from
``templates/pi_readme.md.template`` (package name substituted); ``LICENSE`` is copied
verbatim from the repo-root ``LICENSE`` (single source of truth — the package license
cannot drift from the repo's). Both are entries in the ``package.json`` ``files``
whitelist, so they are generated here and kept under the regen freshness guard rather
than hand-committed.

The Pi BACKEND is UNTOUCHED (REQ-054): this is pure tooling downstream of its emission.
Stdlib-only; deterministic (identical emission -> byte-identical package).
"""

import json
import os
import shutil

__all__ = ["build", "PACKAGE_NAME", "PACKAGE_VERSION"]

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.join(_TOOLS_DIR, "templates")
_REPO_ROOT = os.path.dirname(os.path.dirname(_TOOLS_DIR))

PACKAGE_NAME = "@deliberatecode/pi-system2"
# PR #10 review finding 11: hand-pinned, with no automated guard forcing a bump when
# emitted content changes -- nothing today would stop a real behavior change from
# shipping under an unchanged version number. Bump policy (manual, until guarded):
# bump the PATCH digit for a bug fix that changes emitted bytes with no new
# user-facing capability (e.g. this PR's fixes); bump MINOR for a new skill/
# capability; bump MAJOR for a breaking change to the emitted surface (e.g. a
# skill/capability removal). Mirrors _CODEX_PLUGIN_VERSION's policy in
# compiler/system2_compiler/backends/codex.py.
PACKAGE_VERSION = "0.2.1"

# The ``.pi/<X>/`` subtrees that ARE pi package component types: hoisted to ``<X>/``.
_COMPONENT_PREFIXES = ("extensions", "skills", "prompts")

_VERSION_PLACEHOLDER = "__VERSION__"
_MANAGED_FILES_PLACEHOLDER = "__MANAGED_FILES__"
_PACKAGE_NAME_PLACEHOLDER = "__PACKAGE_NAME__"
_FORBIDDEN_PACKAGE_KEYS = ("scripts", "dependencies", "devDependencies")


def build(staging_emission, dest, package_version=PACKAGE_VERSION):
    """Transform the canonical Pi emission at *staging_emission* into *dest*."""
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
    """Map an emitted relpath to ``(dest_relpath, managed_relpath_or_None)``.

    A ``.pi/<component>/…`` file is a package component type and is hoisted to
    ``<component>/…`` (``managed`` is None). Anything else is a project payload file and
    goes to ``payload/project/<rel>`` (``managed`` is its project-relative path — what
    ``system2-init`` later materializes back into a project).
    """
    if rel.startswith(".pi/"):
        inner = rel[len(".pi/"):]
        top = inner.split("/", 1)[0]
        if top in _COMPONENT_PREFIXES:
            return inner, None
    return "payload/project/" + rel, rel


def _write_package_json(dest, version):
    content = _read_template("pi_package.json").replace(_VERSION_PLACEHOLDER, version)
    # Fail closed: the shipped manifest must never carry an install script or a
    # dependency (REQ-055/F12). A template edit that reintroduced one would otherwise
    # publish silently to npm; assert it here at build time.
    obj = json.loads(content)
    for key in _FORBIDDEN_PACKAGE_KEYS:
        if key in obj:
            raise ValueError(
                f"pi_package.json must not declare {key!r} (REQ-055/F12: the package "
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
