# Installation and Updating

System2 has three distinct harness channels. Claude Code is the current reference channel;
Codex and Pi are compiler-generated base-workflow projections with separate release gates.

| Channel | Status | Guide |
|---------|--------|-------|
| **Claude Code** | Available | [Installation and Updating (Claude Code)](installation/claude-code.md) |
| **Codex** | Pre-release — native acceptance pending | [Codex pre-release status](installation/codex.md) |
| **Pi** | Pre-release — npm publication pending | [Pi publication status](installation/pi.md) |

The shared workflow does not imply mechanism parity. Claude Code uses native isolated
subagents and provides the end-user overlay/profile commands. Codex and Pi currently expose
only compiler-generated base-workflow projections.

For workflow concepts, see the [README](../README.md). For overlays and profiles, see
[Overlays (optional extensions)](overlays.md).
