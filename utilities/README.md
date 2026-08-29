# System2-UtilitySkills

Utility skills for Claude Code — orthogonal to the core [System2](https://github.com/DeliberateCode/System2) multi-agent pipeline.

## What is this?

A Claude Code plugin that provides general-purpose utility skills. These are skills outside the core purpose of System2's spec-driven workflow.

## Installation

### Step 1: Add the Marketplace

```
/plugin marketplace add DeliberateCode/System2-UtilitySkills
```

### Step 2: Install the Plugin

```
/plugin install sys2@system2-utility-skills-marketplace
```

### Step 3: Restart Claude Code

Restart so the new skills are loaded.

## Skills

### `/sys2:stateless-loop`

Runs an instruction in a stateless subprocess loop. Each iteration invokes `claude -p` with no LLM context from prior runs — the only continuity between iterations is the file system on disk.

Each iteration runs as a separate Bash call, so every iteration gets its own 10-minute timeout window (a hard limit imposed by Claude Code on bash processes) rather than the entire loop sharing one.

The sub-agent is instructed to output `STATUS: CLEAN` when the task is fully resolved. The loop exits when that signal is detected or when the iteration cap is reached.

**Usage:**

```
/sys2:stateless-loop fix all type errors in src/
```

```
/sys2:stateless-loop "run the test suite and fix any failures" --max_iterations 20
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--max_iterations N` | 10 | Hard cap on loop iterations |

### `/sys2:gemini`

Runs a prompt through Google's Antigravity CLI (`agy`, which replaced the older standalone `gemini` CLI) non-interactively via `agy -p`. Useful for getting a second opinion, code review, or any task where you want Gemini's perspective on the current project. (`agy` can also target Claude and GPT-OSS models via `--model`.)

**Usage:**

```
/sys2:gemini check my unstaged changes and perform a code review
```

```
/sys2:gemini "explain the architecture of this project" --model "Gemini 3.1 Pro (High)"
```

Any flags supported by `agy` (e.g. `--model`, `--sandbox`, `--dangerously-skip-permissions`, `--add-dir`) are passed through. Old `gemini` flags map across: `--yolo` → `--dangerously-skip-permissions`, `--include-directories` → `--add-dir`, `--resume` → `--continue`/`--conversation`.

### `/sys2:codex`

Runs a prompt through OpenAI's Codex CLI non-interactively via `codex exec`. Useful for getting a second opinion, code review, or any task where you want Codex's perspective on the current project.

**Usage:**

```
/sys2:codex review the recent changes for security issues
```

```
/sys2:codex "find and fix type errors in src/" --model o3
```

Any flags supported by `codex exec` (e.g. `--model`, `--sandbox`, `--config`) are passed through.

## License

MIT — see [LICENSE](LICENSE).
