# System2 — Deliberate Engineering Workflows

System2 is a **spec-driven, verification-first** workflow for software engineering with AI assistance. It is available for **Claude Code**, **Codex**, and **Pi**; all three support the same gates, roles, and project artifacts.

## What is System2?

System2 replaces ad-hoc prompting with a deliberate engineering sequence:

```text
Scope → Context → Requirements → Design → Tasks → Implementation → Verification → Ship
```

The name comes from Daniel Kahneman's dual-process theory: System 1 is fast and intuitive, while System 2 is slow and deliberate. This workflow emphasizes analysis, explicit decisions, verification, and risk awareness.

An orchestrator drives the workflow and adopts or delegates specialist roles as needed. Each harness integrates those roles differently, but the engineering contract is the same.

## Core workflow

### Specialist roles

| Role | Responsibility |
|------|----------------|
| `repo-governor` | Survey repository structure and establish governance constraints |
| `spec-coordinator` | Draft `spec/context.md` |
| `requirements-engineer` | Write testable requirements in `spec/requirements.md` |
| `design-architect` | Produce `spec/design.md` |
| `task-planner` | Create an ordered plan in `spec/tasks.md` |
| `executor` | Implement approved tasks with small, reviewable changes |
| `test-engineer` | Run verification and improve tests |
| `security-sentinel` | Review security-sensitive changes and threat models |
| `eval-engineer` | Evaluate agentic or model-dependent behavior |
| `docs-release` | Update documentation, changelogs, and release notes |
| `code-reviewer` | Review correctness, scope, and maintainability |
| `postmortem-scribe` | Produce incident postmortems |
| `mcp-toolsmith` | Design and review MCP tools |

The active harness controls which tools are available to these roles.

### Quality gates

Work progresses through explicit approval checkpoints:

- **Gate 0 — Scope:** confirm the goal, constraints, and definition of done
- **Gate 1 — Context:** approve `spec/context.md`
- **Gate 2 — Requirements:** approve `spec/requirements.md`
- **Gate 3 — Design:** approve `spec/design.md`
- **Gate 4 — Tasks:** approve `spec/tasks.md`
- **Gate 5 — Ship:** approve the final diff and risk checklist

You can ask to skip gates for lower-risk work. System2 otherwise pauses at each checkpoint.

### Project artifacts

Planning produces versioned Markdown files under `spec/`:

```text
spec/
├── context.md       # Problem, goals, constraints, and success criteria
├── requirements.md  # Testable requirements
├── design.md        # Architecture, interfaces, and failure modes
├── tasks.md         # Ordered implementation tasks
└── security.md      # Threat model when applicable
```

These files form the contract between planning, implementation, and verification.

## Choose a harness

All three harnesses are supported ways to run System2. Choose the one that fits your existing tools and preferred interaction model.

| Harness | Start command | Role execution | Host authority |
|---------|---------------|----------------|----------------|
| **Claude Code** | `/system2:init` | Native subagents | Claude Code permissions and plugin hooks |
| **Codex** | `$system2` | Role skills in the current session | Codex sandbox, approvals, and tools |
| **Pi** | `/system2 <task>` | Role skills in the current session | Pi tools, project trust, and configuration |

The workflow is equivalent; process isolation and enforcement mechanisms are harness-specific.

### Claude Code

```text
/plugin marketplace add DeliberateCode/System2
/plugin install system2@system2-marketplace
/system2:init
```

See [Claude Code installation](docs/installation/claude-code.md).

### Codex

```sh
codex plugin marketplace add DeliberateCode/System2
codex plugin add system2@system2-marketplace
```

Restart Codex, then invoke `$system2` with your task. See [Codex installation](docs/installation/codex.md).

### Pi

```sh
pi install git:github.com/DeliberateCode/System2
```

Restart Pi or run `/reload`, then invoke `/system2 <your task>`. See [Pi installation](docs/installation/pi.md).

No separate System2 configuration is required for Codex or Pi. Continue using the harness's normal model, permission, tool, and trust settings.

## Using System2

Start with a concrete engineering request:

```text
Build email-and-password authentication with account lockout and audit logging.
```

The orchestrator checks existing `spec/` artifacts, clarifies scope, and proposes the next gate. For an existing System2 project, ask it to read the current specs and diff, then continue the next unfinished task.

You can also request a specialist role directly:

```text
Use the spec-coordinator to draft the context for a caching feature.
Use the test-engineer to verify the current implementation.
Use the security-sentinel to review the authentication changes.
```

A typical feature flow is:

1. Clarify scope.
2. Draft and approve context.
3. Write and approve requirements.
4. Produce and approve the design.
5. Create and approve the task plan.
6. Implement tasks with focused changes.
7. Run tests and relevant quality checks.
8. Perform security, documentation, and final code review as needed.
9. Approve the final diff and risks.

Use the harness-specific doctor command for a read-only status summary:

| Harness | Doctor |
|---------|--------|
| Claude Code | `/system2:doctor` |
| Codex | `$system2-doctor` |
| Pi | `/system2-doctor` |

## Harness boundaries

System2 supplies workflow and role guidance. The active harness remains authoritative for models, tools, filesystem access, command execution, sandboxing, trust, and approvals.

- Claude Code can execute roles as isolated subagents and supports plugin hooks.
- Codex and Pi apply role guidance inside the current conversation.
- Role instructions do not override host permissions or configuration.
- Do not allow multiple agents to edit the same checkout concurrently.

These differences affect execution mechanics, not whether a harness can complete the System2 workflow.

## Optional overlays and profiles

The base workflow is available on all three harnesses. Optional overlay and profile management commands are currently available through the Claude Code integration. Source-compiler users can also project composed workflows for Codex and Pi.

See [Overlays and profiles](docs/overlays.md).

## Engineering behavior

### Clear communication

System2 explains mechanisms, decisions, risks, and tradeoffs without narrating routine tool calls or exposing private reasoning. Structured requirements, evidence, approvals, and completion records remain precise.

See [Clear communication, rigorous execution](docs/communication.md).

### Verification loop

The executor follows a compact test-driven loop:

1. **Read and locate:** confirm the approved task and actual entry points.
2. **Red:** write or identify a test that fails for the intended reason.
3. **Green:** make the smallest change that passes.
4. **Refactor:** clean up and run relevant formatters or type checks.
5. **Verify:** run the appropriate test suite and report observed results.

If a failure persists after two correction attempts, the executor stops and reports a minimal reproduction instead of continuing indefinitely.

## Development

Run the repository suite from the root with Python 3.11:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
```

Regenerate committed channel artifacts with:

```sh
python3 compiler/tools/regen_all.py
```

See the [compiler README](compiler/README.md) for compiler usage and focused verification commands.

## Troubleshooting

| Harness | Verify installation | Reload |
|---------|---------------------|--------|
| Claude Code | `/plugin list` | Restart Claude Code |
| Codex | `codex plugin list --json` | Restart Codex |
| Pi | `pi list` | Run `/reload` or restart Pi |

If a System2 command is missing, first verify installation and reload the harness. For channel-specific update and removal commands, see [Installation and Updating](docs/installation.md).

## Principles

- **Deliberate before editing:** establish scope and important assumptions first.
- **Specs are contracts:** implementation follows approved requirements, design, and tasks.
- **Verification before claims:** report only checks that actually ran.
- **Small changes:** prefer focused diffs and existing abstractions.
- **Host authority:** respect the active harness's permissions and safety controls.
- **Honest completion:** state unresolved risks, failures, and unfinished work.

## License

See [LICENSE](LICENSE).
