---
name: system2-doctor
description: Verify the System2 extension loads and the gates are live.
---

# system2-doctor

Verify the System2 extension loads and the gates are live.

1. Confirm Pi lists `/delegate` as an extension command; inspect its command source information to distinguish project and package discovery.
2. Confirm the `tool_call` handler is registered (the bounded gates).
3. The operator analogue of the proven-blocking test: a dangerous bash command and a sensitive-path read must be BLOCKED before they run; an off-scope write must be BLOCKED when the active role has a write scope.
4. Read `system2.pi.lock.json` for the per-capability degradation report, unsupported shell-write disclosure, and FIDELITY banner.
