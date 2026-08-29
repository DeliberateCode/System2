# AGENTS.md — System2 (Pi project context)

This project runs the System2 multi-agent workflow on Pi. The generated `.pi/extensions/system2.ts` extension provides the native safety gates (see `.pi/SYSTEM.md` for the MIXED enforcement story).

## The 13-role pipeline
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

## Gate pipeline
Gate 0 (scope) -> Gate 1 (context) -> Gate 2 (requirements) -> Gate 3 (design) -> Gate 4 (tasks) -> Gate 5 (ship)

## Where to look
- `.pi/SYSTEM.md` — full orchestrator context + enforcement honesty.
- `.pi/skills/system2-{init,compose,doctor}/SKILL.md` — the skills.
- `/delegate <role>` — dispatch a sub-task to one of the 13 roles.
- `system2.pi.lock.json` — the per-capability degradation report.
