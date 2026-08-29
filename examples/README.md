# System2 Compiler — Examples

These directories are **real, generated artifact trees** produced by the
`system2` compiler when it lowers System2 + an overlay onto the two non-Claude
targets:

- [`system2-for-goose/`](./system2-for-goose/) — the Goose backend
  (`--target goose`): a recipe tree, `goose/permission.yaml`, a thin
  `run-system2.sh` launcher, and the `system2.goose.lock.json`.
- [`system2-for-pi/`](./system2-for-pi/) — the Pi backend (`--target pi`): the
  `.pi/` tree (extension, `SYSTEM.md`, prompts, skills), `AGENTS.md`, and the
  `system2.pi.lock.json`.

They are committed so you can read what the compiler emits **without installing
anything**. Each subdirectory's `README.md` shows the exact `system2 compile`
command that produced it and how to run the result.

These are *examples*, not a published package: nothing here is uploaded to
Goose's recipe registry, npm, or any marketplace.

## How these were generated

Every file under the two subdirectories was written by the `system2` console
script (stdlib-only, zero runtime dependencies). The base is the System2 plugin
and the overlay is the repository's `test-overlay` fixture.

```sh
# 1. Build + install the compiler into a throwaway virtualenv.
python3 -m venv /tmp/s2-venv
/tmp/s2-venv/bin/pip install build
/tmp/s2-venv/bin/python -m build System2-Compiler
/tmp/s2-venv/bin/pip install System2-Compiler/dist/system2_compiler-0.1.0-py3-none-any.whl

# 2. Regenerate the Goose example.
/tmp/s2-venv/bin/system2 compile \
  --target goose \
  --base   <path-to>/System2/plugin \
  --overlays <path-to>/System2/evals/fixtures/test-overlay \
  --project System2-Compiler/examples/system2-for-goose

# 3. Regenerate the Pi example.
/tmp/s2-venv/bin/system2 compile \
  --target pi \
  --base   <path-to>/System2/plugin \
  --overlays <path-to>/System2/evals/fixtures/test-overlay \
  --project System2-Compiler/examples/system2-for-pi
```

`--base` is the System2 plugin root; `--overlays` is the demo overlay. The lock
files (`system2.{goose,pi}.lock.json`) record the **absolute** overlay source
path as provenance, so a regeneration on your machine will rewrite that path to
your checkout location — that single-line diff is expected.

The generation is hermetic: the compiler writes only inside `--project`. It
never touches your real `~/.config/goose` or `~/.pi`. Running the artifacts is
opt-in and described per-example below.
