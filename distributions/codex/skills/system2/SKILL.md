---
name: system2
description: "Drive the System2 gate graph and delegate to the 13 roles (Codex, adapted)."
---

# System2 orchestrator (Codex)

## Trust state (READ THIS FIRST — PENDING NATIVE ACCEPTANCE)

System2 workflows for Codex. NOTE: bundled hooks are unverified candidate artifacts, not a release enforcement guarantee; Codex safety capabilities remain advisory-only pending native acceptance.

| Trust state | Enforcement |
|---|---|
| Hooks not reviewed / untrusted | ADVISORY ONLY — candidate hooks do not run. |
| Hooks materialized and reviewed via `/hooks` | UNVERIFIED CANDIDATE BEHAVIOR — native routing, trust, and deny semantics have not been accepted; this is not a release guarantee. |
| Admin-disabled (`requirements.toml`) | ADVISORY ONLY — candidate hooks cannot run. |

To inspect candidate behavior: run `system2 codex init` to materialize the guards into `~/.codex/hooks.json`, then review them via `/hooks`. Do not treat installation or trust as proof of enforcement. Nothing here auto-enables hooks or instructs blanket approval.

Coverage gap: Candidate guards inspect command strings from recognized command keys, shell redirection and limited tee targets, and explicit edit paths or apply-patch headers. Other shell writes are not inspected. Synthetic corpus tests are not native Codex acceptance.

## Gate graph (advance 0 -> 5; do not skip a gate)
- Gate 0 (scope): confirm goal, constraints, and definition of done
- Gate 1 (context): approve spec/context.md
- Gate 2 (requirements): approve spec/requirements.md
- Gate 3 (design): approve spec/design.md
- Gate 4 (tasks): approve spec/tasks.md
- Gate 5 (ship): approve final diff summary and risk checklist
- Approval rule: Quality gates. Pause for explicit user approval at each gate unless the user says to skip gates.

## Delegation (same-session prompt/skill adoption)
No Codex subagent component exists. Adopt the target role's skill in the same session; this is prompt behavior, not an isolated sub-agent. A child-shell export cannot update later hook processes, so role-aware hook authorization is unsupported pending a native state seam. Do not claim role-specific lease enforcement.

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
- Blocker policy: user-gate
- Blocker options: delegate-fix, override, abort
- Boomerang cap: 3

## Maintenance & regression loop
- Corrective-cycle cap: 3
- Classification: Local, Non-local
- Regression ledger fields:
  - previously passing tests now failing
  - newly passing tests
  - unchanged failures
  - likely failure cluster / root-cause area
  - changed-file summary (files modified since last green run)

See `system2.codex.lock.json` for the per-capability fidelity report and the FIDELITY banner. The `system2-doctor` skill is a candidate diagnostic only; the compiler cannot validate native Codex routing, trust, or deny behavior.
