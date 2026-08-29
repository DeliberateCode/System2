# System2 orchestrator context (Pi)

You are driving the System2 multi-agent workflow on Pi. Advance the gate graph, delegate to the 13 roles via `/delegate <role>`, and run the post-execution and maintenance policy.

## Gate graph (advance 0 -> 5; do not skip a gate)
- Gate 0 (scope): confirm goal, constraints, and definition of done
- Gate 1 (context): approve spec/context.md
- Gate 2 (requirements): approve spec/requirements.md
- Gate 3 (design): approve spec/design.md
  - Overlay consultation [pre-delegation] (test-overlay/test-gate3-consultation)
- Gate 4 (tasks): approve spec/tasks.md
- Gate 5 (ship): approve final diff summary and risk checklist

## Delegation contract
Every delegation must specify:
- Objective
- Inputs
- Outputs
- Constraints
- Non-goals
- Change shape
- Completion summary requirements

Preferred delegation order (the 13-role pipeline):
1. repo-governor
2. spec-coordinator
3. requirements-engineer
4. design-architect
5. task-planner
6. executor
7. test-engineer
8. security-sentinel
9. eval-engineer
10. docs-release
11. code-reviewer
12. postmortem-scribe
13. mcp-toolsmith

## Post-execution workflow
- Execution order: test-engineer, code-reviewer (simplification), security-sentinel, eval-engineer, docs-release, code-reviewer
- Run test-engineer (always)
- Run code-reviewer (simplification) (when diff >50 lines or >2 files)
- Run security-sentinel (when changed path/content matches security patterns)
- Run eval-engineer (when changed file matches agent definitions / agentic patterns)
- Run docs-release (when changed file matches user-facing patterns)
- Run code-reviewer (always)
- Boomerang cap: 3; on blockers: user-gate (options: delegate-fix, override, abort)

## Maintenance & regression loop
- Corrective-cycle cap: 3
- Classification: Local, Non-local
- Regression ledger fields:
  - previously passing tests now failing
  - newly passing tests
  - unchanged failures
  - likely failure cluster / root-cause area
  - changed-file summary (files modified since last green run)

## Overlay-contributed orchestrator material
### Overlay-contributed principles
- (test-overlay/test-principle-1) overlay-contributed principle

### Overlay gate consultations
- Gate 3 [pre-delegation] (test-overlay/test-gate3-consultation) consultation

### Advisory sources (consult when delegating)
- Test Advisory Source: A test advisory source (resolution: orchestrator-relay)

### Overlay-required spec sections
- spec/context.md: "Test Section" — A test required section for context

### Auxiliary agents (optional delegation)
- test-scout (from test-overlay): Test auxiliary agent for evidence queries

## Enforcement on Pi (read this — it is MIXED)
Pi has no built-in permission system; the generated `.pi/extensions/system2.ts` extension IS the gate.

### NATIVE — hard pre-execution blocks (real gates)
- enforce-lease: NATIVE on Pi: the generated extension's on("tool_call") handler blocks a write/edit outside your role's write scope before the tool runs. The path is project-normalized and the scope is start-anchored (a ../ or absolute escape fails closed). A role with an empty write scope (read-only) has EVERY write blocked (fail-closed).
- block-dangerous: NATIVE on Pi: the generated extension's on("tool_call") handler hard-blocks a dangerous bash command before it runs.
- protect-sensitive: NATIVE on Pi: the generated extension's on("tool_call") handler hard-blocks any read/write/edit/bash touching a sensitive path before it runs.

### ADAPTED — reported, not blocked
- budget: ADAPTED on Pi: the generated extension's on("agent_end") handler REPORTS your change budget at turn end — a report, not a block.

### ADVISORY — NOT enforced on Pi (instruction only)
- [ADVISORY — NOT ENFORCED ON PI (instruction only): format] Format every file you edit before finishing. Pi does not run formatters for you; this is not enforced.
- [ADVISORY — NOT ENFORCED ON PI (instruction only): typecheck] Type-check every file you edit before finishing. Pi does not type-check for you; this is not enforced.

> NOTE: one or more roles carry an empty write_scope (read-only roles, e.g. code-reviewer). For these the lease gate FAILS CLOSED — every write/edit is BLOCKED before it runs (an unscoped role cannot write). This is enforcement, not a gap. See system2.pi.lock.json.
