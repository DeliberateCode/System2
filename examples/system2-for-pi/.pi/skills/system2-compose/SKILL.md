# system2-compose

Run the System2 gate pipeline and delegation.

- Advance the gate graph 0 -> 5; do not skip a gate.
- Delegate to the 13 roles in the preferred order: repo-governor, spec-coordinator, requirements-engineer, design-architect, task-planner, executor, test-engineer, security-sentinel, eval-engineer, docs-release, code-reviewer, postmortem-scribe, mcp-toolsmith.
- Every delegation specifies the delegation-contract fields (see `.pi/SYSTEM.md`).
- `/delegate <role>` switches the active role; the lease gate then enforces that role's write scope.
