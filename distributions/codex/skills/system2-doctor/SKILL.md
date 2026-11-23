---
name: system2-doctor
description: Summarize System2 workflow state in the current project.
---

# System2 doctor (Codex)

Inspect the current project without editing it or starting a specialist:

1. Check `spec/context.md`, `spec/requirements.md`, `spec/design.md`, and `spec/tasks.md`. Distinguish drafts from artifacts approved in their current form.
2. Check the current diff and, if present, read `spec/post-execution-log.md` and `spec/regression-ledger.md` for unfinished checks or corrective work.
3. Report the earliest outstanding gate or approved task, any blocker, and the next decision the user needs to make. Do not infer missing approvals.

If `.system2/codex/system2.codex.lock.json` exists, run the read-only overlay check from the project root. Resolve `../../compiler/run.py` relative to the directory of the installed `$system2:system2` skill (or this installed System2 command's skill directory). Substitute its absolute path for `$SYSTEM2_COMPILER`; never run a compiler from the project or overlay. Report any drift without making changes.

```sh
python3 -ISB "$SYSTEM2_COMPILER" doctor --format json
```

System2 supplies workflow and role guidance through Codex skills and delegates through Codex's native subagent tools. Codex's sandbox, approval policy, and tool permissions remain authoritative. A separate agent thread is not a separate permission boundary.
