# System2 for Goose

A real, committed example of compiling System2 + an overlay onto the
[Goose](https://block.github.io/goose/) backend. Everything in this directory
(except this README) was emitted by the `system2` compiler.

## How it was generated

```sh
system2 compile \
  --target goose \
  --base   <path-to>/System2/plugin \
  --overlays <path-to>/System2/evals/fixtures/test-overlay \
  --project .
```

- `--target goose` selects the Goose backend.
- `--base` is the System2 plugin root.
- `--overlays` is the demo `test-overlay` fixture.
- `--project .` writes the artifact tree into this directory.

## Generated artifact tree

```
system2-for-goose/
  system2.recipe.yaml          # orchestrator recipe (entry point)
  agents/                      # one sub-recipe per System2 role
    repo-governor.recipe.yaml
    spec-coordinator.recipe.yaml
    requirements-engineer.recipe.yaml
    design-architect.recipe.yaml
    task-planner.recipe.yaml
    executor.recipe.yaml
    test-engineer.recipe.yaml
    security-sentinel.recipe.yaml
    eval-engineer.recipe.yaml
    docs-release.recipe.yaml
    code-reviewer.recipe.yaml
    postmortem-scribe.recipe.yaml
    mcp-toolsmith.recipe.yaml
  goose/
    permission.yaml            # local permission gate (advisory on Goose)
  run-system2.sh               # thin launcher (validate -> run)
  system2.goose.lock.json      # composition lock / drift gate
```

## Running it

The compiler emits a thin launcher; **no workflow logic lives in the shell
script**. It validates every recipe, builds an *ephemeral* `XDG_CONFIG_HOME`,
and invokes `goose run` — it never writes to your real `~/.config/goose`.

```sh
./run-system2.sh
```

Requires the `goose` CLI on your `PATH`. Useful environment toggles the launcher
honors:

- `SYSTEM2_NO_PERMISSIONS=1` — skip the local permission gate.
- `SYSTEM2_KEEP_CONFIG=1` — keep the ephemeral config dir for inspection.

> Enforcement on Goose is *degraded*: Goose has no hard hook-blocking, so the
> System2 safety capabilities are surfaced as advisory permission entries rather
> than enforced gates. The `system2.goose.lock.json` and the launcher banner make
> that explicit. See the compiler's Goose degradation report for details.

This example stops short of publishing to Goose's recipe registry; it only shows
the compiled output.
