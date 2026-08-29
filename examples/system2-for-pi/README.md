# System2 for Pi

A real, committed example of compiling System2 + an overlay onto the **Pi**
backend. Everything in this directory (except this README) was emitted by the
`system2` compiler.

## How it was generated

```sh
system2 compile \
  --target pi \
  --base   <path-to>/System2/plugin \
  --overlays <path-to>/System2/evals/fixtures/test-overlay \
  --project .
```

- `--target pi` selects the Pi backend.
- `--base` is the System2 plugin root.
- `--overlays` is the demo `test-overlay` fixture.
- `--project .` writes the artifact tree into this directory.

## Generated artifact tree

```
system2-for-pi/
  AGENTS.md                    # project-level agent index
  .pi/
    extensions/system2.ts      # Pi extension entry point
    SYSTEM.md                  # composed system persona
    prompts/
      orchestrator.md          # orchestrator prompt
      role-*.md                # one prompt per System2 role
    skills/
      system2-init/SKILL.md
      system2-compose/SKILL.md
      system2-doctor/SKILL.md
  system2.pi.lock.json         # composition lock / drift gate
```

## Running it

Pi reads its configuration from the project's `.pi/` directory, so running this
example is just:

```sh
cd <this-project>   # the directory containing .pi/
pi
```

Requires the `pi` CLI on your `PATH`. The compiler wrote only into this
project's `.pi/` tree and `AGENTS.md`; it never touched a global `~/.pi`.

> Some System2 safety capabilities that Pi cannot enforce are reported in the
> compiler's Pi degradation report and recorded in `system2.pi.lock.json`. The
> `proven`-blocking capabilities that Pi cannot honor are surfaced loudly rather
> than silently dropped.

This example stops short of publishing to any Pi extension marketplace or npm;
it only shows the compiled output.
