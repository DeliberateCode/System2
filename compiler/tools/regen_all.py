"""Regenerate and freshness-check every committed generated artifact.

    python3 compiler/tools/regen_all.py [--check] [--only bundle|codex|pi]

* **default** — regenerate every registered artifact in the fixed order
  ``bundle -> codex -> pi`` into its committed location (each writes its
  lock + provenance).
* **--check** — regenerate each artifact into a temp dir and byte-diff it against the
  committed tree; exit 1 on the first divergence with
  ``"<artifact> is stale: regenerate via python3 compiler/tools/regen_all.py"``.
  An artifact without a committed tree is skipped with a note. Exit 0 when every
  checked artifact matches.
* **--only <artifact>** — operate on just that artifact.

Determinism contract: every builder's output is byte-stable given identical
source. Freshness is checked per artifact:

* **bundle** — delegated to ``check_bundle_fresh.py``'s authoritative criterion
  (``compiler_source_sha256`` match, the bundle's DESIGNED drift anchor). We do NOT
  byte-diff ``BUNDLE.json``, because its ``generated_from`` (a git-rev stamp) and
  ``bundled_at`` legitimately vary; reusing the oracle also means bundle ``--check``
  and ``check_bundle_fresh.py`` can never disagree.
* **distributions (codex/pi)** — regenerated into a temp dir and byte-diffed
  vs the committed tree. The ONLY fields allowed to differ are the breadcrumb
  provenance fields in ``IGNORED_PROVENANCE_FIELDS`` (``generated_at`` and the
  one-commit-lagging ``generated_from``), which live ONLY in ``PROVENANCE.json`` and
  are compared field-wise. Keep that ignore set MINIMAL and EXPLICIT: a broadened
  ignore set is itself a defect; the regeneration guard asserts the set exactly.

This module wraps ``build_bundle.py`` and ``check_bundle_fresh.py`` without
reimplementing or weakening either. It uses only the standard library.

Builder registry
----------------
``REGISTRY`` is the ordered list of artifacts. ``bundle``, ``codex`` and ``pi`` are
active now. A future artifact can be registered as a PLACEHOLDER slot
(``builder=None``, which errors clearly on an explicit ``--only`` and is skipped
with a note otherwise) until its builder lands. Activation defines
``_build_<name>(dest_abs, ctx)`` and assigns that function to ``builder``;
Pi was activated this way through ``build_pi_package.py``. A newly activated
builder is automatically covered by ``regen_all --check`` and by
``test_regen_guards.py`` (it iterates ``REGISTRY``).
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_TOOLS_DIR)
_REPO_ROOT = os.path.dirname(_COMPILER_ROOT)

if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
if _COMPILER_ROOT not in sys.path:
    sys.path.insert(0, _COMPILER_ROOT)

import _provenance  # noqa: E402
import build_bundle  # noqa: E402
import build_pi_package  # noqa: E402
import check_bundle_fresh  # noqa: E402
from system2_compiler import ir  # noqa: E402
from system2_compiler.backends.codex import CodexBackend  # noqa: E402
from system2_compiler.backends.pi import PiBackend  # noqa: E402

__all__ = [
    "REGISTRY",
    "IGNORED_PROVENANCE_FIELDS",
    "IGNORED_INSTALL_SH_PIN_LINES",
    "GENERATOR",
    "stale_message",
    "main",
]

# The stale diagnostic names this single documented refresh command.
_REGEN_COMMAND = "python3 compiler/tools/regen_all.py"

# Identifies this generator in every distribution's PROVENANCE.json.
GENERATOR = "compiler/tools/regen_all.py"

# The MINIMAL, EXPLICIT provenance-field ignore set. Every field here is a
# non-correctness BREADCRUMB that legitimately varies between two regens, NOT a
# freshness property:
#   * bundled_at / generated_at — wall-clock regen timestamps.
#   * generated_from — the "System2@<short-rev>" git-rev stamp, which INHERENTLY lags
#     by one commit: the commit that adds/refreshes an artifact moves HEAD, so a later
#     regen stamps a newer rev than the committed file. Comparing it would be a
#     permanent false positive. It is a breadcrumb only; genuine staleness is still
#     caught by the content bytes + source_sha256 — neither of which is ignored.
# These fields live ONLY in provenance files (PROVENANCE.json) and are excluded from
# the --check field-wise comparison. Widening this set is itself a defect (asserted by
# test_regen_guards.py). The bundle does NOT go through this path at all: its freshness
# is delegated to check_bundle_fresh.py's compiler_source_sha256 oracle (see _check).
IGNORED_PROVENANCE_FIELDS = ("bundled_at", "generated_at", "generated_from")

# Distribution provenance files compared field-wise (JSON) rather than byte-for-byte,
# so the breadcrumb fields above can be ignored. (BUNDLE.json is NOT listed: the bundle
# artifact is checked via check_bundle_fresh.py, never tree-diffed here.)
_PROVENANCE_FILENAMES = (_provenance.PROVENANCE_FILENAME,)

# Directory names skipped when walking a tree for the --check byte-diff (build/test
# detritus, never committed artifact content).
_WALK_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}
)

# The overlay set the committed codex distribution is composed from: EMPTY (the BASE
# System2 plugin). The shippable Codex plugin is the general-purpose base — 13 role
# skills, orchestrator, doctor, hooks, lock with ``overlay_sources: []`` — and it is
# FULLY PORTABLE: an empty overlay set means the emission carries no machine-specific
# absolute paths, so ``regen_all --check`` reproduces it byte-for-byte on any machine
# or CI. (Composing a fixture overlay would bake this machine's absolute overlay path
# into the lock's ``overlay_sources``, breaking portability. Overlays are applied by
# end users via the CLI rather than being vendored into the base plugin.)
_CODEX_OVERLAY_RELPATHS: tuple = ()


@dataclass(frozen=True)
class _Context:
    compiler_root: str
    repo_root: str
    plugin_root: str
    codex_overlays: List[str]


def _context() -> _Context:
    plugin_root = os.environ.get("SYSTEM2_PLUGIN_ROOT") or os.path.join(
        _REPO_ROOT, "plugin"
    )
    return _Context(
        compiler_root=_COMPILER_ROOT,
        repo_root=_REPO_ROOT,
        plugin_root=os.path.abspath(plugin_root),
        codex_overlays=[os.path.join(_REPO_ROOT, r) for r in _CODEX_OVERLAY_RELPATHS],
    )


# ---------------------------------------------------------------------------
# Builders. Each writes its full artifact tree (+ lock + provenance) under
# *dest_abs* (the directory the committed artifact lives in). Deterministic:
# identical source -> byte-identical tree except IGNORED_PROVENANCE_FIELDS.
# ---------------------------------------------------------------------------

def _build_bundle(dest_abs: str, ctx: _Context) -> None:
    """Regenerate the vendored Claude bundle by wrapping build_bundle (unweakened).

    ``build_bundle`` writes ``<dest_abs>/_system2_compiler/`` + its ``BUNDLE.json``;
    BUNDLE.json IS the bundle's provenance (no separate PROVENANCE.json — design).
    """
    build_bundle.build_bundle(ctx.compiler_root, dest_abs)


def _build_codex(dest_abs: str, ctx: _Context) -> None:
    """Regenerate the Codex plugin tree + lock + PROVENANCE.json under *dest_abs*."""
    if os.path.isdir(dest_abs):
        shutil.rmtree(dest_abs)
    os.makedirs(dest_abs, exist_ok=True)

    result = ir.compose(ctx.plugin_root, list(ctx.codex_overlays), dest_abs)
    if result.graph is None:
        raise RuntimeError(f"codex compose refused: {result.errors!r}")
    CodexBackend(overlay_sources=list(ctx.codex_overlays)).emit(result.graph, dest_abs)

    manifest = _read_json(os.path.join(dest_abs, ".codex-plugin", "plugin.json"))
    inputs = [("plugin", ctx.plugin_root)]
    inputs += [(f"overlay{i}", o) for i, o in enumerate(ctx.codex_overlays)]
    _provenance.write_provenance(
        dest_abs,
        inputs=inputs,
        generator=GENERATOR,
        channel_version=str(manifest.get("version", "")),
        compiler_root=ctx.compiler_root,
    )
    # NOTE: the package-data mirror (see _mirror_user_hooks_into_package below) is
    # deliberately NOT called here. This builder runs under BOTH _regen (real
    # dest_abs = distributions/codex/) and _check (dest_abs = a throwaway temp
    # dir, for a read-only byte-diff) -- mirroring here would make `--check`
    # silently overwrite the real packaged copy on every read-only run. It is
    # called only from _regen, after a REAL dest_abs write.


_PACKAGED_USER_HOOKS_REL = os.path.join(
    "system2_compiler", "_packaged_data", "codex_user_hooks"
)


def _mirror_user_hooks_into_package(codex_dest_abs: str, compiler_root: str) -> None:
    src = os.path.join(codex_dest_abs, "user-hooks")
    dst = os.path.join(compiler_root, _PACKAGED_USER_HOOKS_REL)
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _package_data_matches_current_codex_emission(ctx: _Context) -> bool:
    """Whether bundleable hook package-data matches the current Codex emission.

    ``--only bundle`` must never vendor a stale hook reference tree merely because
    its source copy happens to match the previous Codex artifact.  The default
    registry order builds Codex first; this narrow preflight protects the explicit
    single-artifact escape hatch without making it silently rewrite Codex output.
    """
    mirror = os.path.join(ctx.compiler_root, _PACKAGED_USER_HOOKS_REL)
    with tempfile.TemporaryDirectory(prefix="system2-codex-package-data-") as tmp:
        _build_codex(tmp, ctx)
        return os.path.isdir(mirror) and _trees_match(
            os.path.join(tmp, "user-hooks"), mirror
        )


def _build_pi(dest_abs: str, ctx: _Context) -> None:
    """Regenerate the @deliberatecode/pi-system2 npm package + PROVENANCE.json.

    Emit the Pi backend's canonical BASE tree (EMPTY overlays -> fully portable, no
    machine-specific absolute paths, same decision as the codex distribution) into a
    temp staging dir, then transform it into the npm package layout via
    ``build_pi_package``. The transform is pure tooling, so the provenance fingerprints
    only the plugin source (its composition input), matching the codex builder.
    """
    with tempfile.TemporaryDirectory(prefix="pi-emit-") as staging:
        result = ir.compose(ctx.plugin_root, [], staging)
        if result.graph is None:
            raise RuntimeError(f"pi compose refused: {result.errors!r}")
        PiBackend(overlay_sources=[]).emit(result.graph, staging)
        build_pi_package.build(staging, dest_abs, build_pi_package.PACKAGE_VERSION)

    _provenance.write_provenance(
        dest_abs,
        inputs=[("plugin", ctx.plugin_root)],
        generator=GENERATOR,
        channel_version=build_pi_package.PACKAGE_VERSION,
        compiler_root=ctx.compiler_root,
    )


@dataclass(frozen=True)
class _Artifact:
    name: str
    dest_rel: str        # dir the builder writes into, relative to repo_root
    content_rel: str     # subpath under dest_rel that IS the artifact tree ("" = all)
    builder: Optional[Callable[[str, _Context], None]]
    todo_task: str = ""  # naming the task that will activate a placeholder slot
    # When True, --check delegates freshness to check_bundle_fresh.py's
    # compiler_source_sha256 oracle instead of a temp-regen tree byte-diff (the bundle
    # carries a git-rev/timestamp in BUNDLE.json that a byte-diff would false-positive).
    bundle_oracle: bool = False


# Fixed order: codex -> pi -> bundle. Bundle deliberately runs LAST: it vendors
# the system2_compiler/ tree verbatim, and codex's builder mutates a part of that
# same tree through the _packaged_data/codex_user_hooks/ mirror. If the bundle runs
# first, a single default `regen_all.py` invocation vendors the pre-mirror-update tree,
# silently
# leaving the bundle one full regen run behind any hook-source change -- confirmed
# by direct reproduction. Bundle must run after every builder capable of touching
# system2_compiler/ itself, not just after the artifacts it merely reads from.
REGISTRY: List[_Artifact] = [
    _Artifact("codex", os.path.join("distributions", "codex"), "",
              _build_codex),
    _Artifact("pi", os.path.join("distributions", "pi"), "",
              _build_pi),
    _Artifact("bundle", os.path.join("plugin", "scripts"), "_system2_compiler",
              _build_bundle, bundle_oracle=True),
]


def stale_message(artifact: str) -> str:
    """The  divergence message: names the artifact AND the exact regen command."""
    return f"{artifact} is stale: regenerate via {_REGEN_COMMAND}"


def _version_drift_note(artifact: str, committed_root: str, regen_root: str) -> Optional[str]:
    """advisory-only diagnostic,
    never a new failure mode -- ``--check`` already fails on staleness by itself.
    Appends a note to that SAME failure when the emitted content genuinely changed
    but the channel's version constant (``PROVENANCE.json``'s ``channel_version``,
    sourced directly from ``_CODEX_PLUGIN_VERSION``/``PACKAGE_VERSION``) did not --
    exactly the gap the original review flagged as unguarded. Comparing the two
    freshly-computed PROVENANCE.json copies (committed vs. regenerated) needs no git
    history: the regenerated copy's ``channel_version`` already reflects whatever the
    CURRENT source constant says.
    """
    for name in ("codex", "pi"):
        if artifact != name:
            continue
        try:
            committed_version = _read_json(
                os.path.join(committed_root, "PROVENANCE.json")
            ).get("channel_version")
            regen_version = _read_json(
                os.path.join(regen_root, "PROVENANCE.json")
            ).get("channel_version")
        except (OSError, json.JSONDecodeError):
            return None
        if committed_version is not None and committed_version == regen_version:
            return (
                f"  note: {artifact}'s emitted content changed but its version "
                f"constant did not move (still {regen_version!r}) -- see the bump "
                f"policy comment on _CODEX_PLUGIN_VERSION/PACKAGE_VERSION"
            )
    return None


def _placeholder_message(art: _Artifact) -> str:
    return (
        f"{art.name} builder is not yet implemented — see {art.todo_task}; "
        f"regen_all registers it as a placeholder slot"
    )


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# --check tree comparison
# ---------------------------------------------------------------------------

def _relfiles(root: str):
    """Return the set of POSIX relpaths under *root*, excluding build/test detritus."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _WALK_EXCLUDE_DIRS]
        for fn in filenames:
            if fn.endswith(".pyc"):
                continue
            abspath = os.path.join(dirpath, fn)
            out.add(os.path.relpath(abspath, root).replace(os.sep, "/"))
    return out


def _provenance_equivalent(committed_path: str, regen_path: str) -> bool:
    """Compare two provenance JSON files field-wise, ignoring the timestamp fields."""
    try:
        a = _read_json(committed_path)
        b = _read_json(regen_path)
    except (OSError, json.JSONDecodeError):
        return _bytes_equal(committed_path, regen_path)
    strip = lambda d: {k: v for k, v in d.items() if k not in IGNORED_PROVENANCE_FIELDS}
    return strip(a) == strip(b)


def _bytes_equal(a: str, b: str) -> bool:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def _trees_match(committed_root: str, regen_root: str) -> bool:
    """Return True iff the two trees are byte-identical modulo provenance timestamps."""
    if _relfiles(committed_root) != _relfiles(regen_root):
        return False
    for rel in _relfiles(committed_root):
        committed_path = os.path.join(committed_root, rel)
        regen_path = os.path.join(regen_root, rel)
        if os.path.basename(rel) in _PROVENANCE_FILENAMES:
            if not _provenance_equivalent(committed_path, regen_path):
                return False
        elif not _bytes_equal(committed_path, regen_path):
            return False
    return True


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------

def _regen(selected: List[_Artifact], ctx: _Context, explicit: bool) -> int:
    selected_names = {art.name for art in selected}
    if "bundle" in selected_names and "codex" not in selected_names:
        if not _package_data_matches_current_codex_emission(ctx):
            sys.stderr.write(
                "bundle depends on Codex's packaged user-hooks, which are stale: "
                "run `python3 compiler/tools/regen_all.py --only codex` first, "
                "then rerun with --only bundle (or run the default full regen).\n"
            )
            return 1
    for art in selected:
        if art.builder is None:
            if explicit:
                sys.stderr.write(_placeholder_message(art) + "\n")
                return 1
            sys.stdout.write(f"skip {art.name}: {_placeholder_message(art)}\n")
            continue
        dest_abs = os.path.join(ctx.repo_root, art.dest_rel)
        art.builder(dest_abs, ctx)
        if art.name == "codex":
            # mirror the freshly-emitted user-hooks/ subtree into a
            # location INSIDE the system2_compiler package (real package-data,
            # pyproject.toml) so `system2 codex init` works from a `pip
            # install`ed wheel regardless of install method -- verified
            # end-to-end (built a real wheel, installed it fresh, ran the
            # command from outside any checkout). Single source of truth: a
            # byte-for-byte copy of what CodexBackend just emitted to the REAL
            # dest_abs, never a second, independently-authored tree. Real-regen
            # only (never during --check's temp-dir dry run).
            _mirror_user_hooks_into_package(dest_abs, ctx.compiler_root)
        sys.stdout.write(f"regenerated {art.name} -> {art.dest_rel}\n")
    return 0


def _bundle_is_fresh(ctx: _Context, committed_root: str) -> bool:
    """Delegate bundle freshness to check_bundle_fresh.py's authoritative criterion.

    The bundle already has a proven freshness oracle: ``compiler_source_sha256`` match
    (its DESIGNED drift anchor). Reusing it — rather than re-deriving freshness as a
    BUNDLE.json byte-diff — removes the ``generated_from``/``bundled_at`` one-commit
    lag entirely and guarantees bundle ``--check`` never disagrees with
    ``check_bundle_fresh.py``. Its diagnostics are captured so ``--check``
    speaks with a single  voice.
    """
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        rc = check_bundle_fresh.check_bundle_fresh(ctx.compiler_root, committed_root)
    return rc == 0


def _check(selected: List[_Artifact], ctx: _Context, explicit: bool) -> int:
    for art in selected:
        if art.builder is None:
            if explicit:
                sys.stderr.write(_placeholder_message(art) + "\n")
                return 1
            sys.stdout.write(
                f"skip {art.name}: not yet implemented ({art.todo_task})\n"
            )
            continue
        committed_root = os.path.join(ctx.repo_root, art.dest_rel, art.content_rel)
        if not os.path.isdir(committed_root):
            if explicit:
                sys.stderr.write(
                    f"{art.name}: no committed tree at {art.dest_rel} yet\n"
                )
                return 1
            sys.stdout.write(
                f"skip {art.name}: not yet committed at {art.dest_rel}\n"
            )
            continue
        if art.bundle_oracle:
            if not _bundle_is_fresh(ctx, committed_root):
                sys.stderr.write(stale_message(art.name) + "\n")
                return 1
            sys.stdout.write(f"{art.name}: fresh\n")
            continue
        with tempfile.TemporaryDirectory(prefix=f"regen-{art.name}-") as tmp:
            dest_abs = os.path.join(tmp, art.name)
            art.builder(dest_abs, ctx)
            regen_root = os.path.join(dest_abs, art.content_rel)
            if not _trees_match(committed_root, regen_root):
                sys.stderr.write(stale_message(art.name) + "\n")
                drift = _version_drift_note(art.name, committed_root, regen_root)
                if drift:
                    sys.stderr.write(drift + "\n")
                return 1
        if art.name == "codex":
            # the
            # committed distributions/codex/user-hooks/ tree is verified fresh
            # above; separately verify the package-data mirror
            # (system2_compiler/_packaged_data/codex_user_hooks/) still matches it
            # byte-for-byte -- these are two independently-committed copies of the
            # same content, and only _regen (never --check) keeps them in sync, so
            # a hand-edit to either one alone must be caught here.
            mirror_root = os.path.join(
                ctx.compiler_root, _PACKAGED_USER_HOOKS_REL
            )
            user_hooks_committed = os.path.join(committed_root, "user-hooks")
            if not os.path.isdir(mirror_root) or not _trees_match(
                user_hooks_committed, mirror_root
            ):
                sys.stderr.write(
                    "codex: package-data mirror "
                    f"({os.path.relpath(mirror_root, ctx.repo_root)}) is out of "
                    "sync with distributions/codex/user-hooks/: "
                    f"regenerate via {_REGEN_COMMAND}\n"
                )
                return 1
        sys.stdout.write(f"{art.name}: fresh\n")
    return 0


def _select(only: Optional[str]) -> List[_Artifact]:
    if only is None:
        return list(REGISTRY)
    return [art for art in REGISTRY if art.name == only]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Single regeneration entrypoint + freshness guard for every "
                    "committed generated artifact.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Regenerate each artifact to a temp dir and byte-diff vs the committed "
             "tree; exit 1 on the first divergence (names the artifact + regen command).",
    )
    parser.add_argument(
        "--only", choices=[art.name for art in REGISTRY], default=None,
        help="Operate on just this artifact.",
    )
    args = parser.parse_args(argv)

    ctx = _context()
    selected = _select(args.only)
    explicit = args.only is not None
    if args.check:
        return _check(selected, ctx, explicit)
    return _regen(selected, ctx, explicit)


if __name__ == "__main__":
    raise SystemExit(main())
