# System2 - Multi-Agent Engineering Workflows

A framework for **deliberate, spec-driven, verification-first** software engineering with AI assistance.

## What is System2?

System2 provides a structured multi-agent workflow for building production-grade software. Instead of ad-hoc prompting, it coordinates specialized agents through quality gates:

```
Scope → Context → Requirements → Design → Tasks → Implementation → Verification → Ship
```

The name comes from Daniel Kahneman's dual-process theory: **System 1** is fast and intuitive; **System 2** is slow and deliberate. This framework embodies System 2 thinking—analytical, verification-focused, and risk-aware.

Claude Code uses **subagents** defined as Markdown files with YAML frontmatter. The main conversation acts as the **orchestrator**, delegating specialist work to purpose-built subagents.

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

### Step 1: Add the System2 Marketplace

```
/plugin marketplace add DeliberateCode/System2
```

### Step 2: Install the Plugin

```
/plugin install system2@system2-marketplace
```

This installs all 13 agents, hooks, allowlists, and the `/system2:init`, `/system2:compose`, and `/system2:doctor` commands.

```
System2 Plugin
├── agents/              # 13 subagent definitions
├── skills/              # Skills (/system2:init, /system2:compose, /system2:doctor)
├── hooks/               # Validation and quality hook scripts
├── allowlists/          # Per-agent file restriction patterns
├── schemas/             # Overlay manifest schema and anchor map
├── scripts/             # Overlay composer and shared validation helpers
└── .claude-plugin/      # Plugin identity and marketplace metadata
```

### Step 3: Restart Claude Code

After installing, restart Claude Code so the new agents, hooks, and skills are loaded.

### Step 4: Initialize CLAUDE.md

In your project directory, run:

```
/system2:init
```

This writes the System2 orchestrator instructions to `CLAUDE.md` in your project root.

To overwrite an existing CLAUDE.md:

```
/system2:init --force
```

### Step 5: Restart Claude Code

Restart Claude Code again so the new `CLAUDE.md` orchestrator instructions take effect.

### Optional: Compose Overlays

System2 includes an opt-in overlay mechanism for extending the base workflow without forking the plugin. Overlays are local directories with a `system2.overlay.json` manifest and referenced content files.

Preview an overlay composition without writing files:

```
/system2:compose --dry-run /path/to/my-overlay
```

Apply an overlay after preview and approval:

```
/system2:compose /path/to/my-overlay
```

`/system2:compose` validates manifests, detects structural conflicts, reports warnings, then writes project-local composed artifacts:

- `CLAUDE.md` with base System2 instructions plus overlay-contributed sections
- `.system2/overlays/<overlay-name>/` with local copies of overlay content
- `.claude/agents/<auxiliary-agent>.md` for overlay-contributed auxiliary agents
- `spec/overlay-manifest.lock` with versions, hashes, and applied contributions

The lock file records the overlay source paths used during composition. On subsequent updates, `--from-lock` reads those paths so you don't need to retype them:

```
/system2:compose --from-lock
```

To remove an overlay without affecting others, use `--uninstall` with the overlay's name (not its path):

```
/system2:compose --uninstall my-overlay
```

This recomposes the project with the remaining overlays and cleans up the removed overlay's cached content, auxiliary agents, and lock file entries. If the removed overlay was the last one, the project reverts to base System2. A dry-run preview is always shown before any files are modified.

Overlay composition is explicit. `/system2:init` remains base-only and produces the same orchestrator instructions regardless of installed or available overlays.

## Updating

System2 updates are handled by the Claude Code plugin system. No manual update commands are needed.

To check plugin status:

```
/plugin list
```

Plugin updates do not automatically rewrite composed overlay artifacts in your project. After a plugin update, check whether your composed overlays need refreshing:

```
/system2:doctor
```

If the doctor reports drift (stale base or stale overlay), recompose using the locked overlay paths:

```
/system2:compose --from-lock
```

This reads the overlay source paths recorded in `spec/overlay-manifest.lock` and runs the normal compose flow (dry-run preview, approval, write).

## Usage

### Basic Workflow

With `CLAUDE.md` in place, Claude Code acts as the orchestrator. At session start, it assesses the spec artifact state:

```
You: Build a user authentication system

Claude: ## Spec State Assessment

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

You can invoke subagents directly:

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
