# Installation and Updating

System2 supports Claude Code, Codex, and Pi as equally viable ways to run the same spec-driven workflow.

| Harness | Install | Start | Role execution |
|---------|---------|-------|----------------|
| **Claude Code** | Add the marketplace and install `system2@system2-marketplace` | `/system2:init` | Native subagents |
| **Codex** | Add the marketplace and install `system2@system2-marketplace` | `$system2` | In-session role skills |
| **Pi** | `pi install git:github.com/DeliberateCode/System2` | `/system2 <task>` | In-session role skills |

Choose the harness that matches your existing environment:

- [Claude Code](installation/claude-code.md)
- [Codex](installation/codex.md)
- [Pi](installation/pi.md)

All three provide the same gates, role contracts, spec artifacts, and verification workflow. Their host-specific execution and permission mechanisms differ.

For workflow concepts, see the [README](../README.md). For optional overlays and profiles, see [Overlays and profiles](overlays.md).
