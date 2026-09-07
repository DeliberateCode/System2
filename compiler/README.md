# System2 Compiler

The System2 compiler composes the workflow core and optional overlays into a structured IR,
then lowers that IR through a harness-specific backend. It is source tooling in this
repository, not a claim that every generated channel is released or mechanically equivalent.

## Channel status

| Target | Current status | Projection |
|--------|----------------|------------|
| `claude-code` | Current reference channel | Native isolated subagents and the end-user overlay/profile UX. The compiler preserves the plugin's composed output. |
| `codex` | Pre-release; native acceptance pending | Base distribution uses role skills and in-session role switching. No supported install channel yet. |
| `pi` | Pre-release; npm publication pending | Base distribution uses an in-session `/delegate` role switch and a Pi safety extension. No npm install command yet. |

The source compiler accepts overlay input for all targets. That developer capability does not
provide `/system2:compose` or `/system2:profile` UX on Codex or Pi; their generated end-user
distributions are base-workflow projections.

## Architecture

- `system2_compiler/ir/` validates core and overlay inputs and builds the harness-neutral
  graph.
- `system2_compiler/backends/` owns target rendering and capability reports.
- `system2_compiler/cli.py` dispatches compile and lifecycle operations.
- `tools/regen_all.py` regenerates or freshness-checks committed generated artifacts.
- `evals/` contains golden, lifecycle, safety, and native-loader checks.

Each backend reports capabilities as `native`, `adapted`, `advisory`, or `unsupported`.
Those statuses describe target-specific evidence; they do not imply mechanism parity.
Claude Code is the reference output. Pi's extension is checked with Pi discovery and
synthetic pre-execution blocking tests. Codex remains pending native acceptance.

## Source usage

Run the compiler from a repository checkout:

```sh
cd compiler
python3 -m system2_compiler.cli compile \
  --target claude-code \
  --base .. \
  --project /path/to/project \
  --overlays /path/to/overlay-a,/path/to/overlay-b
```

Available targets are `claude-code`, `codex`, and `pi`. Codex and Pi invocations are
source-build/developer paths while their release gates remain pending:

```sh
python3 -m system2_compiler.cli compile \
  --target codex --base .. --project /path/to/project

python3 -m system2_compiler.cli compile \
  --target pi --base .. --project /path/to/project
```

The CLI also provides `uninstall`, `doctor`, `from-lock`, and `profile` verbs. Use
`python3 -m system2_compiler.cli --help` and the subcommand help for the current arguments.
Profiles are shared compiler inputs; only Claude Code exposes the supported end-user profile
commands.

## Generated artifacts

Do not hand-edit generated distributions or the vendored plugin bundle. From the repository
root, check freshness with:

```sh
python3 compiler/tools/regen_all.py --check
```

Regeneration has a single canonical entry point:

```sh
python3 compiler/tools/regen_all.py
```

## Verification

From the repository root:

```sh
pytest -q
```

Narrow compiler checks can be run from `compiler/`:

```sh
python3 -m unittest discover -s evals -p 'test_*.py'
python3 -m evals.run_goldens --driver compiler
```

The test suite covers deterministic output, Claude reference fidelity, lifecycle behavior,
capability reporting, bundle freshness, Pi loading, and Pi guard behavior. These checks do
not substitute for the pending Codex native acceptance or Pi publication gates.

## Invariants

- Treat manifests, overlay content, agent definitions, and schema text as untrusted input.
- Backends consume the structured IR rather than reading overlay sources directly.
- Generated writes stay under the requested project path and use rollback on failure.
- Missing capability mappings fail loudly rather than being silently dropped.
- The plugin's runtime bundle remains dependency-free and pins its target to Claude Code.
