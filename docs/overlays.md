# Overlays and profiles

The base System2 workflow runs on Claude Code, Codex, and Pi, with harness-specific execution and enforcement.

Overlay and profile management is optional on all three harnesses. Each offers the same preview, approval, and apply flow:

- [Overlay and profile commands for Claude Code](overlays/claude-code.md)
- [Overlay and profile commands for Codex and Pi](overlays/codex-pi.md)

Codex and Pi include the compiler in their normal installations and keep composed guidance in a separate directory per harness. Saved profiles are shared across all three.

For those targets, overlay principles, gate consultations, advisory sources, required specification sections, and specialist prompt sections are emitted as Markdown guidance. The compiler includes the full text of specialist contributions so the generated skills do not depend on access to the source overlay. Auxiliary agents become additional role skills handed to subagents, using the body of the agent definition without its native frontmatter configuration.

Agent tool and hook declarations, MCP setup, and permission contributions are not applied on Codex or Pi. The compiler reports them as deferred and warns; it does not change host configuration. Review the report before using an overlay that depends on those features.

Overlays are optional and do not affect the viability of the base workflow on any harness. For installation and channel commands, see [Installation and Updating](installation.md).
