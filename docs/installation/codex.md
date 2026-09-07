# Codex pre-release status

The Codex channel is **not yet an accepted installation channel**. A compiler-generated
base-workflow projection exists, but native Codex acceptance is still pending. Do not treat
the generated distribution as released or rely on it for enforcement.

## Current scope

The projection lowers the orchestrator and 13 roles to skills with in-session role
switching. It does not provide native subagent isolation or the Claude Code overlay/profile
commands. Its capability report distinguishes adapted and advisory behavior; those labels
are not claims of mechanism parity with Claude Code or Pi.

## Source-build review

Release reviewers working from an authorized source checkout can confirm that the committed
candidate matches its canonical sources:

```sh
python3 compiler/tools/regen_all.py --check --only codex
```

Native harness behavior must then be evaluated against the installed Codex CLI using the
[pre-release acceptance runbook](../runbooks/codex-smoke.md). Native CLI requirements will
be set only from recorded acceptance evidence.

## Updating and removal

There is no supported update or removal procedure until native acceptance is recorded and
the channel is released.
