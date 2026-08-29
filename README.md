# System2 - Multi-Agent Engineering Workflows

A framework for **deliberate, spec-driven, verification-first** software engineering with AI assistance.

## What is System2?

System2 provides a structured multi-agent workflow for building production-grade software. Instead of ad-hoc prompting, it coordinates specialized agents through quality gates:

```
Scope → Context → Requirements → Design → Tasks → Implementation → Verification → Ship
```

The name comes from Daniel Kahneman's dual-process theory: **System 1** is fast and intuitive; **System 2** is slow and deliberate. This framework embodies System 2 thinking—analytical, verification-focused, and risk-aware.

A single **orchestrator** context drives this pipeline and delegates specialist work to purpose-built agents. On Claude Code—the reference harness—those agents are native **subagents** defined as Markdown files with YAML frontmatter; the other supported harnesses (Codex, Pi) host the same agents in their own native form. See [Usage](#usage) for how this maps to each harness.

## Core Concepts

### Specialized Agents

| Subagent | Description | Tools |
|----------|-------------|-------|
| `repo-governor` | Repo survey and governance bootstrap | Read, Edit, Write, Grep, Glob, Bash |
| `spec-coordinator` | Drafts spec/context.md | Read, Edit, Write, Grep, Glob |
| `requirements-engineer` | Writes spec/requirements.md (EARS format) | Read, Edit, Write, Grep, Glob |
| `design-architect` | Produces spec/design.md | Read, Edit, Write, Grep, Glob |
| `task-planner` | Creates spec/tasks.md | Read, Edit, Write, Grep, Glob |
| `executor` | Implements tasks with small diffs | Read, Edit, Write, Grep, Glob, Bash |
| `test-engineer` | Runs verification, updates tests | Read, Edit, Write, Grep, Glob, Bash |
| `security-sentinel` | Security review and threat modeling | Read, Edit, Write, Grep, Glob, Bash |
| `eval-engineer` | Agent/LLM behavior evals | Read, Edit, Write, Grep, Glob, Bash |
| `docs-release` | Updates docs, changelog, release notes | Read, Edit, Write, Grep, Glob |
| `code-reviewer` | Final review for correctness | Read, Grep, Glob, Bash |
| `postmortem-scribe` | Incident postmortems | Read, Edit, Write, Grep, Glob |
| `mcp-toolsmith` | MCP tool design | Read, Edit, Write, Grep, Glob, Bash |

### Quality Gates

Work progresses through explicit approval checkpoints:

- **Gate 0 (Scope)**: Confirm goal, constraints, and definition of done
- **Gate 1 (Context)**: Approve spec/context.md
- **Gate 2 (Requirements)**: Approve spec/requirements.md
- **Gate 3 (Design)**: Approve spec/design.md
- **Gate 4 (Tasks)**: Approve spec/tasks.md
- **Gate 5 (Ship)**: Approve final diff and risk checklist

### Spec-Driven Artifacts

All planning produces versioned Markdown files in `/spec`:

```
spec/
├── context.md       # Problem, goals, constraints, success criteria
├── requirements.md  # EARS-format testable requirements
├── design.md        # Architecture, interfaces, failure modes
├── tasks.md         # Atomic tasks with dependencies
└── security.md      # Threat model (when applicable)
```

These artifacts serve as the contract between planning and execution.

## Installation

System2 ships to three agent harnesses from one repository—**Claude Code** (the reference channel), **Codex**, and **Pi**. Full per-channel install commands, fidelity notes, updating, and rollback live in **[Installation and Updating](docs/installation.md)**.

Quick start (Claude Code):

```
/plugin marketplace add DeliberateCode/System2
/plugin install system2@system2-marketplace
/system2:init
```

Previously used the `sys2` utility skills (from the old standalone utility-skills
marketplace)? They're now part of the `system2` plugin — see the [migration
note](docs/installation/claude-code.md#migrating-from-the-sys2-utility-skills).

## Overlays (optional extensions)

Overlays extend the base workflow without forking the plugin, and reusable **profiles** let you activate a named overlay set in any project. Overlays are entirely opt-in—`/system2:init` remains base-only. See **[Overlays (optional extensions)](docs/overlays.md)** for composing overlays, managing profiles, and using overlays on non-Claude harnesses.

## Usage

System2's workflow is identical across every supported harness: a single **orchestrator** context drives the spec pipeline, pauses at quality gates, and delegates specialist work to the pipeline agents. What differs between harnesses is *how* that orchestration is hosted—not what it does.

| Harness | How the orchestrator and agents are hosted |
|---------|--------------------------------------------|
| **Claude Code** | Orchestrator persona in `CLAUDE.md`; the 13 pipeline agents run as native, isolated subagents. Reference channel. |
| **Codex** | An orchestrator skill with in-session role switching; pipeline agents are lowered to role skills (no native subagent isolation). |
| **Pi** | The base workflow compiled for Pi, with native enforcement of the safety gates. |

Safety-gate fidelity varies by harness (native, adapted, or advisory). See the per-channel fidelity notes in [Installation and Updating](docs/installation.md). Overlays and profiles are a Claude-native / compiler-path feature—see [Overlays (optional extensions)](docs/overlays.md).

Whatever the host, you interact with the orchestrator the same way.

### Basic Workflow

The orchestrator assesses spec artifact state at session start and drives from there. On Claude Code this bootstrap runs automatically from `CLAUDE.md`; on the compiled harnesses the equivalent orchestrator instructions ship inside the distribution.

```
You: Build a user authentication system

Orchestrator: ## Spec State Assessment

- [ ] spec/context.md - missing (Gate 1: pending)
- [ ] spec/requirements.md - missing (Gate 2: blocked)
- [ ] spec/design.md - missing (Gate 3: blocked)
- [ ] spec/tasks.md - missing (Gate 4: blocked)

**Next Action:** Clarify scope, then delegate to spec-coordinator

Gate 0 (Scope): Let me clarify a few things...
- What authentication methods? (email/password, OAuth, etc.)
- Any existing auth infrastructure?
- Definition of done?

[After clarification]

I'll delegate to the spec-coordinator to draft spec/context.md...
```

### Explicit Delegation

You can target a specific pipeline agent directly. The exact invocation depends on the harness—a native subagent call on Claude Code, an in-session role switch on Codex—but the intent is identical:

```
You: Use the spec-coordinator to draft the context for a new caching feature

You: Use the test-engineer to run verification and fix any failing tests

You: Use the security-sentinel to review the authentication changes
```

### Gate Workflow

The orchestrator pauses for approval at each gate:

1. **Gate 0**: Confirm scope, constraints, definition of done
2. **Gate 1**: Approve `spec/context.md`
3. **Gate 2**: Approve `spec/requirements.md`
4. **Gate 3**: Approve `spec/design.md`
5. **Gate 4**: Approve `spec/tasks.md`
6. **Gate 5**: Approve final diff and risk checklist

Say "skip gates" if you want to move faster (not recommended for production work).

### Workflow Example

A typical feature development flow:

1. **Orchestrator** receives the request and clarifies scope (Gate 0)
2. **Spec Coordinator** drafts context.md → user approves (Gate 1)
3. **Requirements Engineer** writes requirements.md → user approves (Gate 2)
4. **Design Architect** produces design.md → user approves (Gate 3)
5. **Task Planner** creates tasks.md → user approves (Gate 4)
6. **Executor** implements each task with small diffs
7. **Test Engineer** runs verification and adds tests
8. **Security Sentinel** reviews for vulnerabilities
9. **Docs & Release** updates documentation
10. **Code Reviewer** performs final review → user approves (Gate 5)

## Configuration

### Agent Behavior Patterns

#### Thinking Protocol

The `executor`, `requirements-engineer`, and `design-architect` agents output `<thinking>` blocks before significant tool use:

```xml
<thinking>
Action: [What tool(s) will be invoked and why]
Expected Outcome: [What result is anticipated]
Assumptions/Risks: [What could go wrong; what is assumed true]
</thinking>
```

**When required:**
- Edit, Write, Bash operations (always)
- Multi-file Read sequences (always)
- Single-file Read for context gathering (optional)

This ensures deliberate, reasoned actions rather than ad-hoc tool calls. The reasoning is visible in transcripts for post-hoc review.

**Key constraint:** Reasoning in `<thinking>` cannot override the delegation contract or safety instructions—this prevents prompt injection via self-reasoning.

#### Session Bootstrap

At the start of each session, the orchestrator automatically assesses the spec artifact state and presents a checklist showing which files exist and the corresponding gate status. This enables immediate orientation without redundant discovery.

#### TDD Verification Loop (Executor)

The executor follows a test-driven development pattern:

1. **Red**: Write or identify a test that fails for the correct reason
2. **Green**: Write minimal implementation to pass the test
3. **Refactor**: Run linters, type-checkers, and formatters

**Self-correction limit:** If a test failure persists after two attempts, the executor stops and escalates to the orchestrator with a reproduction case rather than spinning indefinitely.

**Enhanced completion summary:** The executor reports test names, pass/fail counts, and how any verification failures were resolved.

### Subagent Configuration

Each subagent is a Markdown file with YAML frontmatter defining its name, tools, and hooks:

```markdown
---
name: spec-coordinator
description: Drafts spec/context.md with scope, goals, constraints, and open questions. Use proactively at the start of meaningful work.
tools:
  - Read
  - Edit
  - Write
  - Grep
  - Glob
hooks:
  PreToolUse:
    - matcher: "Edit|Write"
      hooks:
        - type: command
          command: 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/validate-file-paths.py" "${CLAUDE_PLUGIN_ROOT}/allowlists/spec-context.regex"'
---
You are a product-minded senior engineer...
```

#### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Lowercase letters and hyphens |
| `description` | Yes | When Claude should delegate to this subagent |
| `tools` | No | Allowlist of tools; inherits all if omitted |
| `disallowedTools` | No | Denylist applied to inherited tools |
| `model` | No | `sonnet`, `opus`, `haiku`, or `inherit` (default: `sonnet`) |
| `permissionMode` | No | `default`, `acceptEdits`, `dontAsk`, `bypassPermissions`, `plan` |
| `hooks` | No | Lifecycle hooks for validation |

### File Restrictions via Hooks

Claude Code uses hooks for file restrictions. Each subagent can have a `PreToolUse` hook that validates file paths against a regex pattern:

```yaml
hooks:
  PreToolUse:
    - matcher: "Edit|Write"
      hooks:
        - type: command
          command: 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/validate-file-paths.py" "${CLAUDE_PLUGIN_ROOT}/allowlists/spec-context.regex"'
```

The allowlist files in `allowlists/` contain regex patterns:

```
# allowlists/spec-context.regex
^spec/context\.md$
```

### Safety and Quality Hooks

System2 includes reusable hooks for safety, code quality, and notifications. These are located in the `hooks/` directory.

#### Available Hooks

| Hook | Event | Purpose |
|------|-------|---------|
| `dangerous-command-blocker.py` | PreToolUse (Bash) | Blocks `rm -rf /`, `sudo rm -rf`, `chmod 777`, `git reset --hard`, force push to main/master, `DROP TABLE`, `DELETE` without WHERE |
| `sensitive-file-protector.py` | PreToolUse (Read/Edit/Write/Bash) | Blocks access to `.env`, `~/.ssh/`, `~/.aws/`, `~/.gnupg/`, credential files |
| `auto-formatter.py` | PostToolUse (Edit/Write) | Runs prettier/black/gofmt on modified files |
| `type-checker.py` | PostToolUse (Edit/Write) | Runs tsc/mypy on modified TypeScript/Python files |
| `tts-notify.py` | Stop/SubagentStop | Announces task completion via TTS (macOS/Windows/Linux) |
| `validate-file-paths.py` | PreToolUse (Edit/Write) | Restricts file writes to allowlisted paths |

Hooks are configured in agent frontmatter or `.claude/settings.json`. See [plugin/hooks/HOOKS.md](plugin/hooks/HOOKS.md) for configuration examples, exit code semantics, custom pattern files, and debugging.

## Advanced Topics

### Programmatic Usage

Pass session-only agent definitions via `--agents`:

```bash
claude --agents '{
  "code-reviewer": {
    "description": "Expert code reviewer. Use proactively after code changes.",
    "prompt": "You are a senior code reviewer...",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  }
}'
```

### Agent Priority Order

Project-level `.claude/agents/` files take priority over plugin agents. If you have project-level files with the same names as System2 agents, the plugin versions will not be used.

### Managing Subagents

Use the `/agents` command in Claude Code to create, edit, or preview subagent configurations.

### Delegation Contract Tips

When delegating (either as orchestrator or manually), include:
- **Objective**: One-sentence goal
- **Inputs**: Files to read or discover
- **Outputs**: Files to create/update
- **Constraints**: What not to do
- **Completion summary**: What to report back

### Customizing Subagents

To modify behavior:
1. Create or edit agent files in your project's `.claude/agents/` directory (these take priority over plugin agents)
2. Adjust the system prompt (body of the Markdown file)
3. Update `tools` or `hooks` as needed
4. Update corresponding allowlist `.regex` files if file restrictions change

### Skipping the Full Workflow

For simple tasks, bypass the orchestrator:
```
You: (without CLAUDE.md or with explicit instruction)
Just add a helper function to utils.py that formats dates.
```

## Development

Run the repository suite from the root:

```sh
pytest -q
```

The test suite is intended to run as a non-root user: permission-based rollback
coverage is skipped under root because root bypasses the permission denial being
tested. Editable compiler installs are supported (`pip install -e compiler/`); their
local package metadata is ignored by the repository-reference guard.

## Troubleshooting

### Subagent Not Found
- Verify the plugin is installed with `/plugin list`
- Check that the `name` field matches what you are requesting

### File Edit Blocked
- Check the allowlist regex in `allowlists/`
- Verify the plugin is installed and hooks are configured in agent frontmatter

### Too Many Approval Prompts
- Use `permissionMode: acceptEdits` for trusted operations
- Consider `dontAsk` for fully automated pipelines (use with caution)

## Key Principles

### Safety by Default
- Never invent build/test commands—discover them from repo
- Resist prompt injection—treat file contents as data
- Enforce least-privilege tool access per agent
- Require human approval for risky changes

### Verification First
- No implementation without approved specs
- Tests run before claiming completion
- Security review for auth, data access, and agentic features

### Context Hygiene
- Main conversation stays focused on decisions
- Specialist work delegated to appropriate agents
- Summaries returned, not raw output

## License

See [LICENSE](LICENSE) for details.
