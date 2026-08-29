# system2-doctor

Verify the System2 extension loads and the gates are live.

1. Confirm Pi discovered `.pi/extensions/system2.ts` (no load error).
2. Confirm the `tool_call` handler is registered (the native gates).
3. The operator analogue of the proven-blocking test: a dangerous bash command and a sensitive-path read must be BLOCKED before they run; an off-scope write must be BLOCKED when the active role has a write scope.
4. Read `system2.pi.lock.json` for the per-capability degradation report and the FIDELITY banner.
