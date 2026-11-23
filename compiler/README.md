# System2 Compiler

The compiler turns the canonical System2 workflow and optional overlays into harness-specific artifacts.

## Targets

| Target | Output |
|--------|--------|
| `claude-code` | Claude Code plugin artifacts, project instructions, and native subagent configuration |
| `codex` | Codex plugin with `$system2`, `$system2-doctor`, and 13 role skills |
| `pi` | Pi package with `/system2`, `/system2-doctor`, and 13 role skills |

All targets preserve the same workflow, gates, and role contracts. Claude Code uses native subagents and plugin hooks; Codex and Pi apply role guidance in the current session. Each host remains responsible for its own permissions, tools, sandboxing, trust, and approvals.

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

End users normally install the relevant plugin or package. The compiler is primarily for maintainers and source-level overlay composition.

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

## Invariants

- Backends consume the structured IR rather than reading overlay sources directly.
- Generated writes stay under the requested project path.
- Missing capability mappings fail loudly.
- Capability locks describe host-specific enforcement honestly rather than implying identical runtime mechanisms.
- The Claude Code bundle remains dependency-free.
- The Codex distribution remains a skills-only plugin.
- The Pi distribution remains a prompts-and-skills package without a runtime extension.
