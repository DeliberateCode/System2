---
description: Summarize System2 workflow state in the current project.
---

# System2 doctor (Pi)

Inspect the current project without editing it or starting a specialist:

1. Check `spec/context.md`, `spec/requirements.md`, `spec/design.md`, and `spec/tasks.md`. Distinguish drafts from artifacts approved in their current form.
2. Check the current diff and, if present, read `spec/post-execution-log.md` and `spec/regression-ledger.md` for unfinished checks or corrective work.
3. Report the earliest outstanding gate or approved task, any blocker, and the next decision the user needs to make. Do not infer missing approvals.

If `.system2/pi/system2.pi.lock.json` exists, run the read-only overlay check from the project root. Call the installed System2 extension's `system2_compiler_path` tool to get the absolute path. Substitute its absolute path for `$SYSTEM2_COMPILER`; never run a compiler from the project or overlay. Report any drift without making changes.

```sh
python3 -ISB "$SYSTEM2_COMPILER" doctor --format json
```

System2 supplies workflow and role guidance through Pi prompt templates and skills, and delegates through Pi's official example `subagent` tool. The child runs in a separate Pi process with fresh context, not a new permission sandbox. Pi's tools, project trust, and user configuration remain authoritative.
