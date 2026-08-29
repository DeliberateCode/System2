---
name: system2-stateless-loop
description: "Run an instruction in a stateless subprocess loop using `claude -p` until the task reports a CLEAN status or max iterations are reached."
---

# system2-stateless-loop -- Stateless Subprocess Loop

You are executing the system2-stateless-loop skill. Follow these steps exactly.

## Prerequisite

Requires the Claude Code CLI (`claude`) on PATH — required even though this host is not Claude Code. If the CLI is missing, stop and report the missing prerequisite verbatim; do not improvise a substitute.

## Arguments

The user provides:
- **instruction** (required): the task to run each iteration. May be bare text or quoted.
- **--max_iterations N** (optional): iteration cap, default 10.

## Execution

Do NOT run the Python script. Orchestrate the loop yourself so each iteration is a separate shell command, each allowed up to 10 minutes before assuming a hang.

### Prompt construction

Build the full prompt by appending this stop directive to the user's instruction:

```
<instruction>

---
STOP CONDITION: When the task above is fully resolved and no further action is needed, output exactly this line on its own:
STATUS: CLEAN
If the task is NOT fully resolved, do NOT output STATUS: CLEAN. Describe what remains.
---
```

### Loop

For each iteration from 1 to max_iterations:

1. Print a header: `Iteration {i}/{max_iterations}`
2. Run a **single shell command**; allow up to 10 minutes before assuming a hang:
   ```
   claude -p '<full_prompt>'
   ```
   Shell-quoting rules:
   - If the prompt contains no single quotes, wrap it in single quotes.
   - If it contains single quotes, wrap it in double quotes and escape any `"`, `$`, or `` ` `` inside.
   - Never pass the prompt unquoted.
3. Check the output for `STATUS: CLEAN`.
   - If found: report `Resolved after {i} iteration(s).` and stop.
   - If not found: proceed to the next iteration.
4. If `claude -p` exits non-zero, log the exit code but continue to the next iteration.

If all iterations complete without `STATUS: CLEAN`, report:
`Max iterations ({max_iterations}) reached without STATUS: CLEAN.`

## Output

Present subprocess output directly to the user. After completion, report the iteration count and final status.
