# system2-init

Set up the System2 workflow on Pi.

1. The `.pi/extensions/system2.ts` extension is auto-discovered by Pi from `.pi/extensions/`; it installs the native safety gates (enforce-lease, block-dangerous, protect-sensitive) and the budget report.
2. Read `.pi/SYSTEM.md` for the orchestrator context and the MIXED enforcement story.
3. Use `/delegate <role>` to dispatch to one of the 13 roles.
