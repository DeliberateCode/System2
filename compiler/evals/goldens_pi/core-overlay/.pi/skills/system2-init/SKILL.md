---
name: system2-init
description: Set up the System2 workflow on Pi.
---

# system2-init

Set up the System2 workflow on Pi.

1. Confirm the System2 extension was discovered from this project or from the installed Pi package; package discovery does not require a project `.pi/extensions/system2.ts`.
2. Run `/system2-init` when using the package. It materializes only its managed project payload, never replaces caller-owned `AGENTS.md`, and reloads Pi after a successful write.
3. Read `.pi/SYSTEM.md` for the orchestrator context and MIXED enforcement story, then use `/delegate <role>`.
