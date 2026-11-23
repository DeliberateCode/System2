# System2 Compiler

The compiler turns the canonical System2 workflow and optional overlays into harness-specific artifacts.

## Targets

| Target | Output |
|--------|--------|
| `claude-code` | Claude Code plugin artifacts, project instructions, and native subagent configuration |
| `codex` | Codex plugin with `$system2:system2`, `$system2:system2-doctor`, and 13 role skills |
| `pi` | Pi prompt templates and role skills; the packaged distribution adds automatic setup for the official subagent tool |

The targets share workflow, gates, and role contracts. Claude Code uses native subagents and plugin hooks. Codex delegates to native subagent threads; Pi delegates via the official example extension. Neither adds System2 runtime enforcement. Each host remains responsible for its own permissions, tools, sandboxing, trust, and approvals.

## Source usage

From `compiler/`:

```sh
python3 -m system2_compiler.cli compile \
  --target TARGET \
  --base ../plugin \
  --project /path/to/project \
  --overlays /path/to/overlay
```

Replace `TARGET` with `claude-code`, `codex`, or `pi`. The CLI also provides `uninstall`, `doctor`, `from-lock`, and `profile`; run `python3 -m system2_compiler.cli --help` for details.

The Codex and Pi installations include the compiler. The source CLI can also apply project overlays for either target. Use `--workspace` on `compile`, `from-lock`, `uninstall`, and `doctor` to use `.system2/<target>/` without replacing installed commands. Pass `--project` as the actual project root, not the output directory. Preview writes with `--dry-run` first. `profile save NAME --target TARGET --workspace --project /path/to/project` saves those applied overlay sources to the shared profile store.

The Codex and Pi backends emit supported contributions as guidance and report unsupported host configuration contributions as deferred. See the [overlay workflow](../docs/overlays/codex-pi.md).

## Generated artifacts

Regenerate all committed distributions and the vendored Claude bundle from the repository root:

```sh
python3 compiler/tools/regen_all.py
```

Check freshness without writing:

```sh
python3 compiler/tools/regen_all.py --check
```

Do not hand-edit generated distributions or the vendored bundle.

## Verification

From the repository root:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

Narrow compiler checks:

```sh
cd compiler
python3 -m unittest discover -s evals -p 'test_*.py'
python3 -m evals.run_goldens --driver compiler
python3 -m evals.run_goldens --driver oracle
```

CI also runs `bash compiler/evals/channel_upgrade_smoke.sh` from the repository root with `PI_PKG_ENTRY` set to Pi 1.6.0's `dist/index.js`. It checks real Pi resource discovery, setup with and without an existing subagent extension, a subprocess handoff with a deterministic child, and both preview transitions.

## Invariants

- Backends consume the IR's validated, ordered contributions and read referenced Markdown when needed.
- Generated writes stay under the requested project path.
- Missing capability mappings fail loudly.
- Capability locks describe host-specific enforcement honestly rather than implying identical runtime mechanisms.
- The Claude Code bundle remains dependency-free.
- The Codex plugin delegates through Codex's native tools. Its bundled compiler runs only on demand; it does not install hooks.
- The Pi package reuses a configured `subagent` tool or loads the unchanged official example. It does not implement its own child runner. Source-emitted Pi projects use the installed package's setup; they do not install a second extension.
