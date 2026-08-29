# System2 Compiler — Examples

These directories are **real, generated artifact trees** produced by the
`system2` compiler when it lowers System2 + an overlay onto a non-Claude
target:

- [`system2-for-pi/`](./system2-for-pi/) — the Pi backend (`--target pi`): the
  `.pi/` tree (extension, `SYSTEM.md`, prompts, skills), `AGENTS.md`, and the
  `system2.pi.lock.json`.

It is committed so you can read what the compiler emits **without installing
anything**. The subdirectory's `README.md` shows the exact `system2 compile`
command that produced it and how to run the result.

This is an *example*, not a published package: nothing here is uploaded to
npm or any marketplace.

## How this was generated

Every file under the subdirectory was written by the `system2` console
script (stdlib-only, zero runtime dependencies). The base is the System2 plugin
and the overlay is the repository's `test-overlay` fixture.

```sh
# 1. Build + install the compiler into a throwaway virtualenv.
python3 -m venv /tmp/s2-venv
/tmp/s2-venv/bin/pip install build
/tmp/s2-venv/bin/python -m build System2-Compiler
/tmp/s2-venv/bin/pip install System2-Compiler/dist/system2_compiler-0.1.0-py3-none-any.whl

# 2. Regenerate the Pi example.
/tmp/s2-venv/bin/system2 compile \
  --target pi \
  --base   <path-to>/System2/plugin \
  --overlays <path-to>/System2/evals/fixtures/test-overlay \
  --project System2-Compiler/examples/system2-for-pi
```

`--base` is the System2 plugin root; `--overlays` is the demo overlay. The lock
file (`system2.pi.lock.json`) records the **absolute** overlay source
path as provenance, so a regeneration on your machine will rewrite that path to
your checkout location — that single-line diff is expected.

The generation is hermetic: the compiler writes only inside `--project`. It
never touches your real `~/.pi`. Running the artifacts is
opt-in and described in the example's own README.
