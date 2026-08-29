# Overlays (optional extensions) — Pi

**Overlays are not yet supported on Pi.**

The prebuilt Pi distribution ships the **base System2 workflow only** — the
general-purpose pipeline composed with an empty overlay set. Per-project overlays and
profiles are a compiler-path feature and are not available through the prebuilt Pi
channel, which contains no compiler and reads no overlay input at install or runtime.
(The Pi channel is also still publishing — see
[Installation and Updating (Pi)](../installation/pi.md).)

Advanced (from source): you can compile overlays for Pi by cloning this repository and
running the System2 compiler CLI against your overlays:

```
git clone https://github.com/DeliberateCode/System2
# then, from the checkout:
python3 -m system2_compiler.cli compile --target pi /path/to/my-overlay
```

Native overlay support (the `/system2:compose` workflow) is available on Claude Code today —
see [Overlays — Claude Code](claude-code.md).
