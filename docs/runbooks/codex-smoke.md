# Pre-release Codex native acceptance

**Status:** pending. This is a release-acceptance checklist, not an installation guide.
Nothing in this page makes the Codex channel available to end users.

## Preconditions

- Use an authorized release-candidate checkout in a disposable project.
- Install the Codex CLI out of band and record the exact version used for the evaluation.
- Confirm the generated candidate is fresh:

  ```sh
  python3 compiler/tools/regen_all.py --check --only codex
  ```

- Review the generated fidelity report before running any hooks.

## Acceptance checks

1. Using the installed Codex CLI's own documentation, load the local generated candidate.
   Record every command and its output; do not publish the candidate or present the command
   as an end-user installation path.
2. Confirm the base orchestrator and all 13 role skills are discoverable. Confirm role
   switching remains in-session and is not described as native subagent isolation.
3. Confirm no overlay/profile command is advertised by the generated channel.
4. Before trusting hooks, confirm no safety capability reports active enforcement.
5. Review each generated hook individually. If the CLI provides a trust flow, trust only the
   reviewed System2 hooks and record the resulting state.
6. Exercise both a benign command and a non-destructive guard canary. Record whether the
   expected action ran, whether the canary was blocked before execution, and the exact block
   reason. An absent or ambiguous block reason is not acceptance evidence.
7. Remove the local candidate and any System2 hook material created during the evaluation.
   Confirm pre-existing user configuration is restored.

## Verdict

Record an explicit PASS or FAIL with the CLI version, candidate commit, commands used,
discovery results, pre-trust state, canary evidence, and teardown result. The Codex channel
remains pending until a release owner reviews and accepts that native evidence.
