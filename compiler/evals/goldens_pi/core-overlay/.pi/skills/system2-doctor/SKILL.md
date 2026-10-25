---
name: system2-doctor
description: Verify the System2 extension loads and the gates are live.
---

# system2-doctor

Verify the System2 extension loads and the gates are live.

1. Run `/system2-status`; confirm mode=orchestrator, healthy source topology, and the model-callable `system2_delegate` and gate tools.
2. Confirm the parent and child `tool_call` handlers are registered for the bounded gates.
3. The operator analogue of the proven-blocking test: a dangerous bash command and a sensitive-path read must be BLOCKED before they run; an off-scope write must be BLOCKED when a child's target falls outside either its role scope or task scope.
4. Read `system2.pi.lock.json` for the per-capability degradation report, unsupported shell-write disclosure, and FIDELITY banner.
