---
name: system2-codex
description: "Run a prompt through OpenAI's Codex CLI (`codex exec`) non-interactively for a second opinion or code review from a fresh Codex instance."
---

# system2-codex -- Codex CLI Runner

You are executing the system2-codex skill. Follow these steps exactly.

This spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex instance with none of this session's context. It is a second opinion from a clean slate, not a fork of the current session.

## Prerequisite

Requires the OpenAI Codex CLI (`codex`) on PATH. If the CLI is missing, stop and report the missing prerequisite verbatim; do not improvise a substitute.

## Known Pi limitation

The System2 Pi extension's protect-sensitive gate scans the ENTIRE bash command, with no override, and Pi has no permission prompt to bypass it — so a prompt containing a token like `credentials` or `secrets` is hard-blocked before the CLI runs. Workaround: reword the prompt to avoid those tokens. This is the gate doing its declared job, not a bug — stated here so a block is never a surprise.

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

Run a **single shell command**; allow up to 10 minutes before assuming a hang:

```
codex exec --ephemeral -c history.persistence=none '<prompt>' [flags...]
```

**Statelessness (required).** This skill is a one-shot second opinion; each call must be hermetic and must not see or leave behind any record of other invocations. Codex otherwise persists state to two on-disk stores under `$CODEX_HOME` (default `~/.codex`): session rollout transcripts in `sessions/`, and a running prompt log in `history.jsonl` (default persistence `save-all`). Plain `codex exec` does not auto-resume those, but the running agent can read them mid-task, and every run appends to them. To prevent both:

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
