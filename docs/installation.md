# Installation and Updating

System2 supports Claude Code, Codex, and Pi for its spec-driven workflow.

| Harness | After installing | Start work |
|---------|------------------|------------|
| **[Claude Code](installation/claude-code.md)** | Restart, run `/system2:init` once in each project, then restart to load the project instructions. | Describe your task in Claude Code. |
| **[Codex](installation/codex.md)** | Restart Codex. No separate System2 setup. | `$system2:system2` with your task. |
| **[Pi](installation/pi.md)** | Restart Pi or run `/reload`. The package sets up its specialist automatically. | `/system2 <task>` |

Codex and Pi include the overlay and profile tools in the normal install. These commands need Python 3.11 or newer (`python3`); no separate compiler install or environment setting is required. The per-harness guides also cover cleanup for older preview installs.

All three share gates, role contracts, spec artifacts, and verification guidance. Claude Code uses native subagents and plugin hooks. Codex uses native subagent threads; Pi uses the official subagent extension. On Codex and Pi, the host remains responsible for permissions and enforcement.

For workflow concepts, see the [README](../README.md). For optional overlays and profiles, see [Overlays and profiles](overlays.md).
