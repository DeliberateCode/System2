"""Regenerate and freshness-check every committed generated artifact."""

import argparse
import contextlib
import io
import json
import os
import shutil
import stat
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
from system2_compiler.ir import build as ir_build  # noqa: E402
from system2_compiler.ir import manifest as ir_manifest  # noqa: E402
from system2_compiler.backends.codex import CodexBackend  # noqa: E402
from system2_compiler.backends.pi import PiBackend  # noqa: E402
from system2_compiler.channel_version import CHANNEL_VERSION  # noqa: E402

__all__ = [
    "REGISTRY",
    "IGNORED_PROVENANCE_FIELDS",
    "GENERATOR",
    "stale_message",
    "main",
]

# The stale diagnostic names this single documented refresh command.
_REGEN_COMMAND = "python3 compiler/tools/regen_all.py"

# Identifies this generator in every distribution's PROVENANCE.json.
GENERATOR = "compiler/tools/regen_all.py"

# Ignore only volatile provenance breadcrumbs; content hashes still detect staleness.
IGNORED_PROVENANCE_FIELDS = ("generated_at", "generated_from")

# Distribution provenance is compared field-wise to omit volatile breadcrumbs.
_PROVENANCE_FILENAMES = (_provenance.PROVENANCE_FILENAME,)

# Directory names skipped when walking a tree for the --check byte-diff (build/test
# detritus, never committed artifact content).
_WALK_EXCLUDE_DIRS = frozenset(
    {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}
)

# Empty overlays keep the committed Codex distribution portable across machines.
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


# Builders write deterministic artifact trees under dest_abs.

def _build_bundle(dest_abs: str, ctx: _Context) -> None:
    """Regenerate the vendored Claude bundle by wrapping build_bundle (unweakened)."""
    build_bundle.build_bundle(ctx.compiler_root, dest_abs)


def _plugin_graph_inputs(ctx: _Context):
    """Return only the canonical plugin files consumed by graph construction."""
    schema_path = os.path.join(ctx.plugin_root, "schemas", "overlay.schema.json")
    anchor_map_path = os.path.join(ctx.plugin_root, "schemas", "anchor-map.json")
    anchor_map = _read_json(anchor_map_path, trusted_root=ctx.plugin_root)
    inputs = [
        ("base/plugin_metadata", os.path.join(
            ctx.plugin_root, ".claude-plugin", "plugin.json"
        ), ctx.plugin_root),
        ("base/init_template", os.path.join(
            ctx.plugin_root, "skills", "init", "SKILL.md"
        ), ctx.plugin_root),
        ("base/schema/overlay", schema_path, ctx.plugin_root),
        ("base/schema/anchor_map", anchor_map_path, ctx.plugin_root),
    ]
    inputs.extend(
        (f"base/agent/{name}", os.path.join(
            ctx.plugin_root, "agents", f"{name}.md"
        ), ctx.plugin_root)
        for name in sorted(anchor_map.get("agents", {}))
    )
    inputs.extend(
        (f"base/allowlist/{role}", os.path.join(
            ctx.plugin_root, "allowlists", filename
        ), ctx.plugin_root)
        for role, filename in sorted(ir_build._ROLE_ALLOWLISTS.items())
    )
    return inputs


def _overlay_graph_inputs(index: int, overlay_path: str, ctx: _Context):
    """Return a manifest and only the overlay files it actually references."""
    manifest_path = os.path.join(overlay_path, "system2.overlay.json")
    try:
        manifest = _read_json(manifest_path, trusted_root=overlay_path)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"provenance overlay manifest missing: {manifest_path}"
        ) from None

    references = []
    ir_manifest._collect_content_files_from_manifest(manifest, references)
    agents = manifest.get("contributions", {}).get("agents", {})
    for agent in agents.values():
        if not isinstance(agent, dict):
            continue
        for hook in agent.get("hooks", []):
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                references.append(hook["command"])
    for reference in references:
        components = reference.replace("\\", "/").split("/")
        if os.path.isabs(reference) or ".." in components:
            continue  # Preserve validate_manifest's established diagnostics.
        try:
            _require_file(
                os.path.join(overlay_path, reference),
                "provenance overlay source",
                trusted_root=overlay_path,
            )
        except FileNotFoundError:
            pass  # Preserve validate_manifest's established missing-file diagnostic.

    validation = ir_manifest.validate_manifest(
        manifest,
        _read_json(
            os.path.join(ctx.plugin_root, "schemas", "overlay.schema.json"),
            trusted_root=ctx.plugin_root,
        ),
        overlay_path,
        _read_json(
            os.path.join(ctx.plugin_root, "schemas", "anchor-map.json"),
            trusted_root=ctx.plugin_root,
        ),
    )
    if not validation.valid:
        raise ValueError(
            f"invalid provenance overlay {overlay_path!r}: "
            + "; ".join(validation.errors)
        )

    prefix = f"overlay/{index:04d}"
    inputs = [(f"{prefix}/manifest", manifest_path, overlay_path)]
    normalized = {
        os.path.normpath(reference).replace(os.sep, "/"): reference
        for reference in references
    }
    inputs.extend(
        (
            f"{prefix}/reference/{label}",
            os.path.join(overlay_path, reference),
            overlay_path,
        )
        for label, reference in sorted(normalized.items())
    )
    return inputs


def _distribution_inputs(channel: str, ctx: _Context):
    """Return stable labels for every effective producer input of *channel*."""
    package_root = os.path.join(ctx.compiler_root, "system2_compiler")
    backend_root = os.path.join(package_root, "backends")
    inputs = _plugin_graph_inputs(ctx) + [
        ("lowering/ir", os.path.join(package_root, "ir"), ctx.compiler_root),
        ("lowering/system2_compiler_init", os.path.join(
            package_root, "__init__.py"
        ), ctx.compiler_root),
        ("backend/base", os.path.join(backend_root, "base.py"), ctx.compiler_root),
        ("backend/degradation", os.path.join(
            backend_root, "_degradation.py"
        ), ctx.compiler_root),
        ("backend/enforcement", os.path.join(
            backend_root, "_enforcement.py"
        ), ctx.compiler_root),
        ("backend/yaml", os.path.join(backend_root, "_yaml.py"), ctx.compiler_root),
        ("generator/regen_all", os.path.join(
            ctx.compiler_root, "tools", "regen_all.py"
        ), ctx.compiler_root),
        ("generator/provenance", os.path.join(
            ctx.compiler_root, "tools", "_provenance.py"
        ), ctx.compiler_root),
        ("generator/build_bundle_helpers", os.path.join(
            ctx.compiler_root, "tools", "build_bundle.py"
        ), ctx.compiler_root),
        ("metadata/channel_version", os.path.join(
            package_root, "channel_version.py"
        ), ctx.compiler_root),
        ("metadata/compiler_project", os.path.join(
            ctx.compiler_root, "pyproject.toml"
        ), ctx.compiler_root),
    ]
    if channel == "codex":
        inputs.extend([
            ("backend/codex", os.path.join(
                backend_root, "codex.py"
            ), ctx.compiler_root),
            ("backend/capabilities/codex", os.path.join(
                backend_root, "capabilities", "codex.json"
            ), ctx.compiler_root),
        ])
        for index, overlay in enumerate(ctx.codex_overlays):
            inputs.extend(_overlay_graph_inputs(index, overlay, ctx))
    elif channel == "pi":
        inputs.extend([
            ("backend/pi", os.path.join(
                backend_root, "pi.py"
            ), ctx.compiler_root),
            ("backend/capabilities/pi", os.path.join(
                backend_root, "capabilities", "pi.json"
            ), ctx.compiler_root),
            ("package/pi_builder", os.path.join(
                ctx.compiler_root, "tools", "build_pi_package.py"
            ), ctx.compiler_root),
            ("package/pi_templates", os.path.join(
                ctx.compiler_root, "tools", "templates"
            ), ctx.compiler_root),
            ("metadata/license", os.path.join(
                build_pi_package._REPO_ROOT, "LICENSE"
            ), build_pi_package._REPO_ROOT),
        ])
    else:
        raise ValueError(f"unknown distribution channel: {channel!r}")
    return inputs


def _require_channel_version(channel: str, emitted_version: str) -> None:
    """Reject generated channel metadata that diverges from the authority."""
    if emitted_version != CHANNEL_VERSION:
        raise RuntimeError(
            f"{channel} channel version mismatch: emitted {emitted_version!r}, "
            f"expected {CHANNEL_VERSION!r}"
        )


def _build_codex(dest_abs: str, ctx: _Context) -> None:
    """Regenerate the Codex plugin tree + lock + PROVENANCE.json under *dest_abs*."""
    if os.path.isdir(dest_abs):
        shutil.rmtree(dest_abs)
    os.makedirs(dest_abs, exist_ok=True)

    result = ir.compose(ctx.plugin_root, list(ctx.codex_overlays), dest_abs)
    if result.graph is None:
        raise RuntimeError(f"codex compose refused: {result.errors!r}")
    CodexBackend(overlay_sources=list(ctx.codex_overlays)).emit(result.graph, dest_abs)

    manifest = _read_json(
        os.path.join(dest_abs, ".codex-plugin", "plugin.json"),
        trusted_root=dest_abs,
    )
    emitted_version = str(manifest.get("version", ""))
    _require_channel_version("codex", emitted_version)
    _provenance.write_provenance(
        dest_abs,
        inputs=_distribution_inputs("codex", ctx),
        generator=GENERATOR,
        channel_version=emitted_version,
        compiler_root=ctx.compiler_root,
    )
    # Mirror package data only during real regeneration; --check must stay read-only.


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
    """Whether bundleable hook package-data matches the current Codex emission."""
    mirror = os.path.join(ctx.compiler_root, _PACKAGED_USER_HOOKS_REL)
    with tempfile.TemporaryDirectory(prefix="system2-codex-package-data-") as tmp:
        _build_codex(tmp, ctx)
        return _is_regular_directory(
            mirror, trusted_root=ctx.compiler_root
        ) and _trees_match(
            os.path.join(tmp, "user-hooks"),
            mirror,
            committed_trusted_root=tmp,
            regen_trusted_root=ctx.compiler_root,
        )


def _build_pi(dest_abs: str, ctx: _Context) -> None:
    """Regenerate the @deliberatecode/pi-system2 npm package + PROVENANCE.json."""
    with tempfile.TemporaryDirectory(prefix="pi-emit-") as staging:
        result = ir.compose(ctx.plugin_root, [], staging)
        if result.graph is None:
            raise RuntimeError(f"pi compose refused: {result.errors!r}")
        PiBackend(overlay_sources=[]).emit(result.graph, staging)
        build_pi_package.build(staging, dest_abs, build_pi_package.PACKAGE_VERSION)

    package = _read_json(
        os.path.join(dest_abs, "package.json"), trusted_root=dest_abs
    )
    emitted_version = str(package.get("version", ""))
    _require_channel_version("pi", emitted_version)
    _provenance.write_provenance(
        dest_abs,
        inputs=_distribution_inputs("pi", ctx),
        generator=GENERATOR,
        channel_version=emitted_version,
        compiler_root=ctx.compiler_root,
    )


@dataclass(frozen=True)
class _Artifact:
    name: str
    dest_rel: str        # dir the builder writes into, relative to repo_root
    content_rel: str     # subpath under dest_rel that IS the artifact tree ("" = all)
    builder: Optional[Callable[[str, _Context], None]]
    todo_task: str = ""  # describes an inactive placeholder
    # Bundle manifests contain volatile metadata, so compare their source hash.
    bundle_oracle: bool = False


# Build the bundle last because Codex regeneration refreshes packaged hook data.
REGISTRY: List[_Artifact] = [
    _Artifact("codex", os.path.join("distributions", "codex"), "",
              _build_codex),
    _Artifact("pi", os.path.join("distributions", "pi"), "",
              _build_pi),
    _Artifact("bundle", os.path.join("plugin", "scripts"), "_system2_compiler",
              _build_bundle, bundle_oracle=True),
]


def stale_message(artifact: str) -> str:
    """Name the stale artifact and its regeneration command."""
    return f"{artifact} is stale: regenerate via {_REGEN_COMMAND}"


def _placeholder_message(art: _Artifact) -> str:
    return (
        f"{art.name} builder is not yet implemented — see {art.todo_task}; "
        f"regen_all registers it as a placeholder slot"
    )


def _read_json(path: str, *, trusted_root=None) -> dict:
    trusted_root = os.path.abspath(trusted_root or os.path.dirname(path))
    _require_file(path, "JSON file", trusted_root=trusted_root)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# check tree comparison

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


def _is_regular_directory(path: str, *, trusted_root=None) -> bool:
    try:
        _require_directory(
            path, "artifact root", trusted_root=trusted_root or path
        )
    except (OSError, ValueError):
        return False
    return True


def _relfiles(root: str, *, trusted_root=None):
    """Return the set of POSIX relpaths under *root*, excluding build/test detritus."""
    root = os.path.abspath(root)
    trusted_root = os.path.abspath(trusted_root or root)
    _require_directory(root, "artifact root", trusted_root=trusted_root)
    out = set()
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
            if dirname not in _WALK_EXCLUDE_DIRS:
                retained.append(dirname)
        dirnames[:] = retained
        for fn in sorted(filenames):
            abspath = os.path.join(dirpath, fn)
            _require_file(abspath, "artifact file", trusted_root=trusted_root)
            if fn.endswith(".pyc"):
                continue
            out.add(os.path.relpath(abspath, root).replace(os.sep, "/"))
    return out


def _provenance_equivalent(
    committed_path: str,
    regen_path: str,
    *,
    committed_trusted_root: str,
    regen_trusted_root: str,
) -> bool:
    """Compare two provenance JSON files field-wise, ignoring volatile breadcrumbs."""
    try:
        a = _read_json(committed_path, trusted_root=committed_trusted_root)
        b = _read_json(regen_path, trusted_root=regen_trusted_root)
    except (OSError, json.JSONDecodeError):
        return _bytes_equal(
            committed_path,
            regen_path,
            a_trusted_root=committed_trusted_root,
            b_trusted_root=regen_trusted_root,
        )
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    strip = lambda d: {k: v for k, v in d.items() if k not in IGNORED_PROVENANCE_FIELDS}
    return strip(a) == strip(b)


def _bytes_equal(
    a: str, b: str, *, a_trusted_root: str, b_trusted_root: str
) -> bool:
    _require_file(a, "committed artifact", trusted_root=a_trusted_root)
    _require_file(b, "regenerated artifact", trusted_root=b_trusted_root)
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def _trees_match(
    committed_root: str,
    regen_root: str,
    *,
    committed_trusted_root=None,
    regen_trusted_root=None,
) -> bool:
    """Return True iff the two trees are byte-identical modulo provenance timestamps."""
    committed_trusted_root = committed_trusted_root or committed_root
    regen_trusted_root = regen_trusted_root or regen_root
    try:
        committed_files = _relfiles(
            committed_root, trusted_root=committed_trusted_root
        )
        regen_files = _relfiles(regen_root, trusted_root=regen_trusted_root)
        if committed_files != regen_files:
            return False
        for rel in committed_files:
            committed_path = os.path.join(committed_root, rel)
            regen_path = os.path.join(regen_root, rel)
            if os.path.basename(rel) in _PROVENANCE_FILENAMES:
                if not _provenance_equivalent(
                    committed_path,
                    regen_path,
                    committed_trusted_root=committed_trusted_root,
                    regen_trusted_root=regen_trusted_root,
                ):
                    return False
            elif not _bytes_equal(
                committed_path,
                regen_path,
                a_trusted_root=committed_trusted_root,
                b_trusted_root=regen_trusted_root,
            ):
                return False
    except (OSError, ValueError):
        return False
    return True


# Drivers

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
            # Mirror emitted hooks into package data only during real regeneration.
            _mirror_user_hooks_into_package(dest_abs, ctx.compiler_root)
        sys.stdout.write(f"regenerated {art.name} -> {art.dest_rel}\n")
    return 0


def _bundle_is_fresh(ctx: _Context, committed_root: str) -> bool:
    """Delegate bundle freshness to check_bundle_fresh.py's authoritative criterion."""
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        rc = check_bundle_fresh.check_bundle_fresh(
            ctx.compiler_root,
            committed_root,
            compiler_trusted_root=ctx.compiler_root,
            target_trusted_root=ctx.repo_root,
        )
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
        if not _is_regular_directory(
            committed_root, trusted_root=ctx.repo_root
        ):
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
            if (
                not _provenance.artifacts_match(
                    committed_root, trusted_root=ctx.repo_root
                )
                or not _provenance.artifacts_match(
                    regen_root, trusted_root=tmp
                )
                or not _trees_match(
                    committed_root,
                    regen_root,
                    committed_trusted_root=ctx.repo_root,
                    regen_trusted_root=tmp,
                )
            ):
                sys.stderr.write(stale_message(art.name) + "\n")
                return 1
        if art.name == "codex":
            # Verify the independently committed package-data mirror too.
            mirror_root = os.path.join(
                ctx.compiler_root, _PACKAGED_USER_HOOKS_REL
            )
            user_hooks_committed = os.path.join(committed_root, "user-hooks")
            if not _is_regular_directory(
                mirror_root, trusted_root=ctx.compiler_root
            ) or not _trees_match(
                user_hooks_committed,
                mirror_root,
                committed_trusted_root=ctx.repo_root,
                regen_trusted_root=ctx.compiler_root,
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
