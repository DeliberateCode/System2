# Clear communication, rigorous execution

System2 uses the same human communication policy across Claude Code, Codex, and Pi. The policy is maintained with the shared workflow source and carried into each harness's orchestrator and specialist roles.

## Two kinds of output

**Engineering outputs** stay structured where required: requirements, traceability, design artifacts, role contracts, verification results, decision records, and approval evidence.

**Human-facing communication** explains what that evidence means. It should lead with the important result, make causal relationships clear, state uncertainty, and avoid narrating routine tool calls or internal mechanics.

This is not a fixed response template. Technical detail is welcome when it helps the user understand or decide.

## Delivery by harness

| Harness | Policy delivery |
|---------|-----------------|
| **Claude Code** | Project orchestrator instructions and native specialist subagent prompts |
| **Codex** | The `$system2:system2` orchestrator and role skills handed to native subagents |
| **Pi** | The `/system2` orchestrator and role skills handed to Pi subprocesses |

Role execution differs by harness, but communication expectations do not. Every channel preserves the same requirements for clarity, evidence, uncertainty, approvals, and honest completion reporting.

## Review

`compiler/evals/test_human_communication.py` checks that the policy reaches every generated channel. The [communication review suite](../evals/communication/README.md) provides scenarios for manual review of clarity, causal coherence, technical fidelity, completeness, and efficiency.

Regenerate committed channel artifacts with:

```sh
python3 compiler/tools/regen_all.py
```
