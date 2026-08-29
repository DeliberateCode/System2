# Overlays (optional extensions) — Codex

**Overlays are not yet supported on Codex.**

The prebuilt Codex distribution ships the **base System2 workflow only** — the
general-purpose pipeline composed with an empty overlay set. Per-project overlays and
profiles are a compiler-path feature and are not available through the prebuilt Codex
channel, which contains no compiler and reads no overlay input at install or runtime.

Advanced (from source): you can compile overlays for Codex by cloning this repository and
running the System2 compiler CLI against your overlays:

```
git clone https://github.com/DeliberateCode/System2
# then, from the checkout:
python3 -m system2_compiler.cli compile --target codex /path/to/my-overlay
```

Native overlay support (the `/system2:compose` workflow) is available on Claude Code today —
see [Overlays — Claude Code](claude-code.md). For installation, see
[Installation and Updating (Codex)](../installation/codex.md).
