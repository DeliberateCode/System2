---
name: codex
description: Run a prompt through OpenAI's Codex CLI non-interactively via `codex exec`. Use for getting a second opinion, code review, or any task where you want Codex's perspective on the current project.
argument-hint: "<prompt> [codex exec flags...]"
---

# /sys2:codex -- Codex CLI Runner

You are executing the /sys2:codex skill. Follow these steps exactly.

## Arguments

The user provides:
- **prompt** (required): the instruction to send to Codex. May be bare text or quoted.
- **additional flags** (optional): any flags supported by `codex exec` (e.g. `--model <model>`, `--sandbox <mode>`, `--config <key=value>`). These are passed through verbatim.

## Execution

### Argument parsing

1. Split the user's input into the **prompt** portion and any **flags** (tokens starting with `-` or `--`, plus their values).
2. Known Codex exec flags that take a value: `--model`/`-m`, `--config`/`-c`, `--image`/`-i`, `--sandbox`/`-s`, `--profile`/`-p`, `--local-provider`, `--remote-auth-token-env`. When encountered, consume the next token as the flag's value.
3. Known Codex exec boolean flags: `--oss`, `--strict-config`, `--dangerously-bypass-approvals-and-sandbox`. Also `--enable` and `--disable` take a value each.
4. Everything that is not a recognized flag or a flag's value is the **prompt**.

### Running Codex

Run a **single Bash call** with timeout 600000 (10 minutes):

```
codex exec --ephemeral -c history.persistence=none '<prompt>' [flags...]
```

**Statelessness (required).** This skill is a one-shot second opinion; each call must be
hermetic and must not see or leave behind any record of other invocations. Codex otherwise
persists state to two on-disk stores under `$CODEX_HOME` (default `~/.codex`): session
rollout transcripts in `sessions/`, and a running prompt log in `history.jsonl` (default
persistence `save-all`). Plain `codex exec` does not auto-resume those, but the running
agent can read them mid-task, and every run appends to them. To prevent both:

- Always pass `--ephemeral` (do not write session rollout files), unless the user explicitly passed it.
- Always pass `-c history.persistence=none` (do not append to `history.jsonl`), unless the user already supplied a `history.persistence` override via their own `-c`/`--config`.
- Never add `resume` / `--last`, and never set `experimental_resume` — those deliberately reload prior context.

Shell-quoting rules for the prompt:
- If the prompt contains no single quotes, wrap it in single quotes.
- If it contains single quotes, wrap it in `$'...'` syntax with internal single quotes escaped as `\'`.
- Never pass the prompt unquoted.

### Error handling

If `codex exec` exits non-zero, report the exit code and any stderr output to the user.

## Output

Present Codex's output directly to the user. After completion, report success or failure status.
