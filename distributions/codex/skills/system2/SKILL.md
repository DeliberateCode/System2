---
name: system2
description: "Drive the System2 gate graph and delegate to the 13 roles (Codex, adapted)."
---

# System2 orchestrator (Codex)

## Trust state (READ THIS FIRST — enforcement is CONDITIONAL on Codex)

System2 workflows for Codex. NOTE: safety enforcement is INACTIVE until you review and trust the bundled hooks via /hooks; until then System2 runs advisory-only.

| Trust state | Enforcement |
|---|---|
| Hooks not reviewed / untrusted | ADVISORY ONLY — nothing is blocked; the hooks do not run. |
| Hooks materialized to `~/.codex/hooks.json` and trusted via `/hooks` | CONDITIONAL ENFORCEMENT — dangerous shell commands, sensitive-path access, and off-lease edits are blocked before they run, with the coverage gap below. |
| Admin-disabled (`requirements.toml`) | ADVISORY ONLY — immutable; hooks cannot run and this cannot be overridden. |

To activate enforcement: run `system2 codex init` to materialize the guards into `~/.codex/hooks.json`, then review and trust them via `/hooks`. An administrator may disable hooks via `requirements.toml`; when disabled, System2 is advisory-only and this cannot be overridden. Nothing here auto-enables hooks or instructs blanket approval — review each hook before trusting it.

Coverage gap: Even with hooks trusted, Codex hooks intercept shell commands and apply_patch-matched edits; they do NOT intercept WebSearch or other non-shell, non-MCP tools. Enforcement on Codex is therefore ADAPTED, never total.

## Gate graph (advance 0 -> 5; do not skip a gate)
- Gate 0 (scope): confirm goal, constraints, and definition of done
- Gate 1 (context): approve spec/context.md
- Gate 2 (requirements): approve spec/requirements.md
- Gate 3 (design): approve spec/design.md
- Gate 4 (tasks): approve spec/tasks.md
- Gate 5 (ship): approve final diff summary and risk checklist

## Delegation (in-session role-switching — the Pi /delegate precedent)
No Codex subagent component exists, so delegation is an in-session role switch: adopt the target role's skill and, so the hooks enforce that role's write lease, set the `SYSTEM2_ACTIVE_ROLE` environment variable to the role name for subsequent tool calls. This is ADAPTED (subagent_isolation is never native): the role switch shares the session, it is not an isolated sub-agent.

Preferred delegation order (the 13-role pipeline):
1. repo-governor — adopt `skills/system2-role-repo-governor/SKILL.md`
2. spec-coordinator — adopt `skills/system2-role-spec-coordinator/SKILL.md`
3. requirements-engineer — adopt `skills/system2-role-requirements-engineer/SKILL.md`
4. design-architect — adopt `skills/system2-role-design-architect/SKILL.md`
5. task-planner — adopt `skills/system2-role-task-planner/SKILL.md`
6. executor — adopt `skills/system2-role-executor/SKILL.md`
7. test-engineer — adopt `skills/system2-role-test-engineer/SKILL.md`
8. security-sentinel — adopt `skills/system2-role-security-sentinel/SKILL.md`
9. eval-engineer — adopt `skills/system2-role-eval-engineer/SKILL.md`
10. docs-release — adopt `skills/system2-role-docs-release/SKILL.md`
11. code-reviewer — adopt `skills/system2-role-code-reviewer/SKILL.md`
12. postmortem-scribe — adopt `skills/system2-role-postmortem-scribe/SKILL.md`
13. mcp-toolsmith — adopt `skills/system2-role-mcp-toolsmith/SKILL.md`

Every delegation must specify:
- Objective
- Inputs
- Outputs
- Constraints
- Non-goals
- Change shape
- Completion summary requirements

## Post-execution workflow
- Execution order: test-engineer, code-reviewer (simplification), security-sentinel, eval-engineer, docs-release, code-reviewer
- Run test-engineer (always)
- Run code-reviewer (simplification) (when diff >50 lines or >2 files)
- Run security-sentinel (when changed path/content matches security patterns)
- Run eval-engineer (when changed file matches agent definitions / agentic patterns)
- Run docs-release (when changed file matches user-facing patterns)
- Run code-reviewer (always)
- Boomerang cap: 3; on blockers: user-gate

## Maintenance & regression loop
- Corrective-cycle cap: 3
- Classification: Local, Non-local

See `system2.codex.lock.json` for the per-capability fidelity report and the FIDELITY banner. Run the `system2-doctor` skill to verify hook liveness (the compiler cannot read Codex trust state).
