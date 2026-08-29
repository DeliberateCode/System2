---
name: system2-gemini
description: "Run a prompt through Google's Antigravity CLI (`agy`) non-interactively for a second opinion or code review from a fresh instance."
---

# system2-gemini -- Antigravity (agy) CLI Runner

You are executing the system2-gemini skill. It drives Google's Antigravity CLI, whose binary is `agy` (this replaced the older standalone `gemini` CLI). Follow these steps exactly.

## Prerequisite

Requires Google's Antigravity CLI (`agy`) on PATH. If the CLI is missing, stop and report the missing prerequisite verbatim; do not improvise a substitute.

## Arguments

The user provides:
- **prompt** (required): the instruction to send to the model. May be bare text or quoted.
- **additional flags** (optional): any flags supported by `agy` (e.g. `--model <model>`, `--sandbox`, `--dangerously-skip-permissions`). These are passed through verbatim.

## Execution

### Argument parsing

1. Split the user's input into the **prompt** portion and any **flags** (tokens starting with `-` or `--`, plus their values).
2. Known `agy` flags that take a value: `--model`, `--add-dir` (repeatable), `--conversation`, `--log-file`, `--print-timeout`. When encountered, consume the next token as the flag's value.
3. Known `agy` boolean flags: `--continue`/`-c`, `--sandbox`, `--dangerously-skip-permissions`. These stand alone.
4. Everything that is not a recognized flag or a flag's value is the **prompt**.

Note on migration: the old `gemini` flags map as follows — `--yolo`/`-y` -> `--dangerously-skip-permissions`; `--include-directories` -> `--add-dir`; `--resume`/`-r` -> `--continue`/`-c` (most recent) or `--conversation <id>` (specific session); `-m` short alias is gone (use `--model`). Flags like `--debug`/`-d`, `--output-format`, `--extensions`, `--yolo` have no `agy` equivalent — drop them if a user passes them, and tell the user you did.

### Running agy

Run a **single shell command**; allow up to 10 minutes before assuming a hang:

```
agy -p '<prompt>' [flags...]
```

`agy`'s print mode has its own `--print-timeout` (default 5m), which is shorter than the 10-minute window above. To avoid `agy` cutting off a long run early, append `--print-timeout 9m` unless the user already supplied a `--print-timeout` flag.

Shell-quoting rules for the prompt:
- If the prompt contains no single quotes, wrap it in single quotes.
- If it contains single quotes, wrap it in `$'...'` syntax with internal single quotes escaped as `\'`.
- Never pass the prompt unquoted.

### Error handling

If `agy` exits non-zero, report the exit code and any stderr output to the user. If the failure is `command not found: agy`, tell the user the Antigravity CLI is not installed or not on PATH (it is typically installed at `/opt/homebrew/bin/agy`).

## Output

Present agy's output directly to the user. After completion, report success or failure status.
