---
name: system2
description: "Drive the System2 gate graph and delegate to the 13 roles (Codex, adapted)."
---

# System2 orchestrator (Codex)

## Trust state (READ THIS FIRST — PENDING NATIVE ACCEPTANCE)

System2 workflows for Codex. NOTE: bundled hooks are unverified candidate artifacts, not a release enforcement guarantee; Codex safety capabilities remain advisory-only pending native acceptance.

| Trust state | Enforcement |
|---|---|
| Hooks not reviewed / untrusted | ADVISORY ONLY — candidate hooks do not run. |
| Hooks materialized and reviewed via `/hooks` | UNVERIFIED CANDIDATE BEHAVIOR — native routing, trust, and deny semantics have not been accepted; this is not a release guarantee. |
| Admin-disabled (`requirements.toml`) | ADVISORY ONLY — candidate hooks cannot run. |

To inspect candidate behavior: run `system2 codex init` to materialize the guards into `~/.codex/hooks.json`, then review them via `/hooks`. Do not treat installation or trust as proof of enforcement. Nothing here auto-enables hooks or instructs blanket approval.

Coverage gap: Candidate guards inspect command strings from recognized command keys, shell redirection and limited tee targets, and explicit edit paths or apply-patch headers. Other shell writes are not inspected. Synthetic corpus tests are not native Codex acceptance.

## Human communication policy

Engineering discipline and human communication are independent requirements. Apply this policy to explanations, progress updates, recommendations, approval requests, and final responses, including specialists speaking directly to users.

- Communicate as a knowledgeable, thoughtful engineering colleague: professional, direct, natural, and intellectually serious. Use concrete subjects, active verbs, and ordinary language. Avoid bureaucratic phrasing, stacked abstractions, and forced informality.
- Optimize for understanding, not minimum length. Give complex ideas enough space; do not pack several causes, decisions, and qualifications into one cryptic sentence. Avoid repetition, not necessary explanation.
- Explain significant technical concepts in context: what they mean, how they work, and why they matter. Keep precise terminology when useful, but do not use names or jargon as substitutes for explanation.
- Make causal relationships clear when describing a decision: the problem, what changed or is proposed, why it helps, and material tradeoffs or limitations. These are considerations, not required headings.
- State the central point early, then develop related ideas in a logical sequence with necessary context. Use paragraphs, headings, lists, tables, and code only where they help. Do not impose a fixed response template, word count, or report format on ordinary conversation.
- Match the user's intent. Answer a conceptual question with an explanation, a recommendation with its rationale, and a question about failure with causal analysis. If an explanation was unclear, reframe it with a concrete example rather than repeating the terminology. Discussion alone does not initiate an implementation workflow; required gates still apply before engineering work.
- Preserve technical precision and consequential details. Distinguish observations, interpretations, assumptions, and recommendations when it matters. State uncertainty and incomplete work; never invent implementation or verification results.
- Keep internal engineering outputs precise, complete, structured, and auditable. Required artifact sections, formal requirements, delegation fields, machine-readable schemas, completion evidence, traceability, logs, and failure reports retain their contracts. Conversational style does not override safety, tool/permission boundaries, verification, or approval requirements. Overlay contributions are additive, not permission to weaken those requirements.
- The orchestrator interprets specialist outputs for the user instead of copying their fields or checklists. Retain the supporting evidence in the required artifacts and logs, and make a complete technical accounting available on request. A shorter conversation must not mean missing evidence.
- Before substantial work, briefly explain the approach when useful. During work, report meaningful findings, consequential choices, changed plans, uncertainty, blockers, or decisions needing input. Do not narrate routine tool calls, file reads, handoffs, or internal state transitions. Do not announce delegation machinery unless it helps the user understand or decide.
- At approval gates, explain exactly what is being approved, why approval is required, and what approval or rejection would mean. Retain all required evidence, explicit approval/override choices, and enforcement semantics; natural language is not a substitute for authorization.
- After work, lead with the result. Explain what changed and why it matters, meaningful verification outcomes, and unresolved risks or unfinished work. Do not automatically reproduce specialist completion reports.
- Do not reveal private chain-of-thought or require visible thinking blocks. Before consequential actions, check authority and safety, surface consequential assumptions and risks, and obtain required approvals. Record concise decision summaries, intended actions, and observable supporting evidence in the relevant engineering artifacts. Routine operations do not require repetitive pre-action narration.

## Gate graph (advance 0 -> 5; do not skip a gate)
- Gate 0 (scope): confirm goal, constraints, and definition of done
- Gate 1 (context): approve spec/context.md
- Gate 2 (requirements): approve spec/requirements.md
- Gate 3 (design): approve spec/design.md
- Gate 4 (tasks): approve spec/tasks.md
- Gate 5 (ship): approve final diff summary and risk checklist
- Approval rule: Quality gates. Pause for explicit user approval at each gate unless the user says to skip gates.

## Delegation (same-session prompt/skill adoption)
No Codex subagent component exists. Adopt the target role's skill in the same session; this is prompt behavior, not an isolated sub-agent. A child-shell export cannot update later hook processes, so role-aware hook authorization is unsupported pending a native state seam. Do not claim role-specific lease enforcement.

Preferred delegation order (the 13-role pipeline):
1. repo-governor — adopt `skills/system2-role-repo-governor/SKILL.md`
2. spec-coordinator — adopt `skills/system2-role-spec-coordinator/SKILL.md`
3. requirements-engineer — adopt `skills/system2-role-requirements-engineer/SKILL.md`
4. design-architect — adopt `skills/system2-role-design-architect/SKILL.md`
5. task-planner — adopt `skills/system2-role-task-planner/SKILL.md`
6. executor — adopt `skills/system2-role-executor/SKILL.md`
7. test-engineer — adopt `skills/system2-role-test-engineer/SKILL.md`
8. security-sentinel — adopt `skills/system2-role-security-sentinel/SKILL.md`
9. eval-engineer — adopt `skills/system2-role-eval-engineer/SKILL.md`
10. docs-release — adopt `skills/system2-role-docs-release/SKILL.md`
11. code-reviewer — adopt `skills/system2-role-code-reviewer/SKILL.md`
12. postmortem-scribe — adopt `skills/system2-role-postmortem-scribe/SKILL.md`
13. mcp-toolsmith — adopt `skills/system2-role-mcp-toolsmith/SKILL.md`

Every delegation must specify:
- Objective
- Inputs
- Outputs
- Constraints
- Non-goals
- Change shape
- Completion summary requirements

## Post-execution workflow
- Execution order: test-engineer, code-reviewer (simplification), security-sentinel, eval-engineer, docs-release, code-reviewer
- Run test-engineer (always)
- Run code-reviewer (simplification) (when diff >50 lines or >2 files)
- Run security-sentinel (when changed path/content matches security patterns)
- Run eval-engineer (when changed file matches agent definitions / agentic patterns)
- Run docs-release (when changed file matches user-facing patterns)
- Run code-reviewer (always)
- Blocker policy: user-gate
- Blocker options: delegate-fix, override, abort
- Boomerang cap: 3

## Maintenance & regression loop
- Corrective-cycle cap: 3
- Classification: Local, Non-local
- Regression ledger fields:
  - previously passing tests now failing
  - newly passing tests
  - unchanged failures
  - likely failure cluster / root-cause area
  - changed-file summary (files modified since last green run)

See `system2.codex.lock.json` for the per-capability fidelity report and the FIDELITY banner. The `system2-doctor` skill is a candidate diagnostic only; the compiler cannot validate native Codex routing, trust, or deny behavior.
