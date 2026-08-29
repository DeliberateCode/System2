# Overlays (optional extensions)

Overlays are System2's opt-in mechanism for extending the base workflow without forking the
plugin, together with reusable overlay **profiles**. Overlays are entirely optional:
`/system2:init` (and the compiled base distributions) produce the same orchestrator
instructions regardless of installed or available overlays.

Overlay support is per-harness. Today only **Claude Code** supports overlays; the other
distributions ship the base workflow only:

| Harness | Overlays | Guide |
|---------|----------|-------|
| **Claude Code** | Supported (native `/system2:compose`) | [Overlays — Claude Code](overlays/claude-code.md) |
| **Codex** | Not yet supported | [Overlays — Codex](overlays/codex.md) |
| **Pi** | Not yet supported | [Overlays — Pi](overlays/pi.md) |

For installation and updating, see [Installation and Updating](installation.md).
