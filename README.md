# System2 — Deliberate Engineering Workflows

System2 is a **spec-driven, verification-first** workflow for software engineering with AI assistance. It is available for **Claude Code**, **Codex**, and **Pi**; all three support the same gates, roles, and project artifacts.

## What is System2?

System2 replaces ad-hoc prompting with a deliberate engineering sequence:

```text
Scope → Context → Requirements → Design → Tasks → Implementation → Verification → Ship
```

The name comes from Daniel Kahneman's dual-process theory: System 1 is fast and intuitive, while System 2 is slow and deliberate. This workflow emphasizes analysis, explicit decisions, verification, and risk awareness.

An orchestrator drives the workflow and delegates specialist work to subagents. The harnesses share the workflow and role contracts, but differ in how they launch subagents and enforce constraints.

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
| **Codex** | `$system2:system2` | Native subagent threads | Codex sandbox, approvals, and tools |
| **Pi** | `/system2 <task>` | Separate Pi processes via the official subagent extension | Pi tools, project trust, and configuration |

Codex and Pi give specialists separate context, but System2's workflow and approval rules are instructions, not programmatic checks.

### Claude Code

In Claude Code, install the plugin:

```text
/plugin marketplace add DeliberateCode/System2
/plugin install system2@system2-marketplace
```

Restart Claude Code, open your project, and run `/system2:init` once for that project. Restart again to load the instructions, then describe your task. Have `python3` available for hooks and overlay commands. See [Claude Code installation](docs/installation/claude-code.md).

### Codex

```sh
codex plugin marketplace add DeliberateCode/System2
codex plugin add system2@system2-marketplace
```

Restart Codex, then invoke `$system2:system2` with your task. No separate System2 setup is required. The plugin includes the compiler for overlays and profiles. See [Codex installation](docs/installation/codex.md).

### Pi

```sh
pi install git:github.com/DeliberateCode/System2
```

Use Pi 1.6.0 or newer. Restart Pi or run `/reload`, then invoke `/system2 <your task>`. The package sets up its specialist automatically and includes the compiler for overlays and profiles. See [Pi installation](docs/installation/pi.md).

If you used the earlier branch previews, follow the migration steps for [Codex hooks](docs/installation/codex.md#migrating-from-the-hook-based-preview) or [Pi project files](docs/installation/pi.md#migrating-projects-initialized-with-the-pi-preview) before updating.

## Using System2

Start with a concrete engineering request:

```text
Build email-and-password authentication with account lockout and audit logging.
```

The orchestrator checks existing `spec/` artifacts, clarifies scope, and proposes the next gate. The parent reviews each specialist's output and asks for approval before advancing; the executor starts only after the task plan is approved (or you explicitly waive that gate).

To continue an existing System2 project, including when switching harnesses, use the same checkout. Start that harness's orchestrator and ask it to resume from the current specs, recorded approvals, and diff. If approval for the current version of an artifact can't be established, confirm it again. The post-execution log and regression ledger carry verification and corrective-work evidence across sessions.

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
| Codex | `$system2:system2-doctor` |
| Pi | `/system2-doctor` |

## Harness boundaries

System2 supplies workflow and role guidance. The active harness remains authoritative for models, tools, filesystem access, command execution, sandboxing, trust, and approvals.

- Claude Code can execute roles as isolated subagents and supports plugin hooks.
- Codex and Pi delegate roles in separate contexts. This does not add a permission sandbox.
- Role instructions do not override host permissions or configuration.
- Do not allow multiple agents to edit the same checkout concurrently.

Use the harness's normal settings for permissions and trust. Workflow instructions do not add enforcement that the host does not provide.

## Optional overlays and profiles

All three harnesses can preview and apply extra project rules (overlays) and switch between saved sets (profiles). Codex and Pi include the compiler in the normal install; overlay and profile commands need Python 3.11 or newer.

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

If a System2 command is missing, verify installation and reload the harness. If Pi reports a specialist setup problem, use `/system2-setup` to check or retry; it is not an installation step. For channel-specific update and removal commands, see [Installation and Updating](docs/installation.md).

## Principles

- **Deliberate before editing:** establish scope and important assumptions first.
- **Specs are contracts:** implementation follows approved requirements, design, and tasks.
- **Verification before claims:** report only checks that actually ran.
- **Small changes:** prefer focused diffs and existing abstractions.
- **Host authority:** respect the active harness's permissions and safety controls.
- **Honest completion:** state unresolved risks, failures, and unfinished work.

## License

See [LICENSE](LICENSE).
