# Clear communication, rigorous execution

System2 should explain its work like a thoughtful engineering colleague without relaxing
how it does that work. The shared **Human communication policy** is part of the canonical
orchestrator template in `plugin/skills/init/SKILL.md`. `CLAUDE.md` mirrors that template
under the repository's existing consistency check; it is not a second policy authority.

## What caused the problem

The old operating principle asked the orchestrator to “articulate your reasoning” before
significant action. Executor, design-architect, and requirements-engineer went further:
they required visible `<thinking>` blocks before edits and multi-file reads, with the
executor also covering Bash. That made routine work resemble a procedural report and
could solicit private reasoning instead of useful explanations.

The bootstrap required a fixed spec-state checklist, post-execution approval used an agent
roster template, and Gate 5 combined evidence aggregation with mandatory conversational
reporting. Specialist completion fields and formal artifact headings were appropriate for
internal use, but there was no clear instruction not to copy them into the conversation.
The inspected canonical prompts did not impose a general terse word limit; the central
problem was process narration and a missing clarity standard, not merely response length.
Pi's own prompt assembly also bypasses most of the Claude template's prose.

## Two kinds of output

**Engineering outputs** remain structured where required: EARS requirements and IDs,
traceability matrices, design and boundary artifacts, delegation contracts, completion
fields, command outcomes, failure evidence, and logs. Required sections still apply to
these artifacts. Tool permissions, verification-first work, approval gates, specialist
responsibilities, correction limits, and security checks are unchanged.

**Human-facing communication** explains the meaning of that evidence. Start with what the
user needs to know, explain mechanisms and causal relationships, and identify material
uncertainty. A concept question gets an explanation, not an automatic engineering workflow.
Progress updates are useful for a finding, changed plan, blocker, or decision—not every
file read or handoff. A final answer describes the result, why it matters, verification,
and anything unfinished rather than reproducing a completion object.

This is not a new response template. Technical terms are welcome when they help; explain
unfamiliar concepts rather than merely naming them. Use enough space for understanding,
without forced informality or unnecessary personality.

At gates, retain the precise decision, evidence, path scope, and explicit approval or override
mechanism. Explain the consequences of approving and rejecting. The Claude post-execution
plan still waits for approval, and the complete Gate 5 accounting and workflow metadata now
remain in `spec/post-execution-log.md`. Skipped checks, overrides, and risks that affect an
approval must not disappear into that log. Pi's digest-bound gate evidence, visible decision
audit messages, and interactive authorization are untouched.

## Delivery through the existing architecture

| Context | Actual policy delivery |
| --- | --- |
| Claude `/system2:init` | Writes the canonical template, including the policy, to project `CLAUDE.md`. |
| Claude compose, profiles, from-lock, uninstall/recompose | The compiler loads that same init template into `BaseTemplate`; the Claude backend preserves it while adding overlay contributions. Profiles choose overlays, not an alternate policy. |
| Claude specialists | All 13 canonical agent bodies reference the shared policy supplied through project instructions. Delegation guidance explicitly applies it to auxiliary agents too. Only short role-specific guidance lives in the agent definitions. |
| Pi parent | `render_human_communication_policy` extracts the section using the IR's retained template and section offsets. The backend serializes it once as `HUMAN_COMMUNICATION_POLICY` in the extension. Parent `before_agent_start` injects it alongside the digest-checked `.pi/SYSTEM.md` context and runtime authority, including package use before `/system2-init`. |
| Pi project context | `.pi/SYSTEM.md` and `prompts/orchestrator.md` point to the extension-injected policy instead of repeating it. The latter is a slash-command template, **not** the context loaded by `loadOrchestratorPrompt`; relying on it alone would miss the actual parent injection. Structured workflow material remains an execution contract, not a response template. |
| Pi specialists | Child `before_agent_start` injects that same constant alongside the independently loaded `prompts/role-*.md` and bounded delegation authority. The role files reference the policy without duplicating it. Merely relying on the parent's model context would miss this isolated-process path. |
| Codex projection | The shared orchestrator skill receives the policy; same-session role adoption retains it. Canonical role references remain in generated skills without another full copy. This does not change Codex's pre-release/native-acceptance limitations. |

Pi's parent and child handlers reuse one generated policy constant, not separately authored
role policy, repeated Markdown blocks, or a rewriting stage. Extension digests bind the
updated generated bytes through the existing provenance mechanism. There is no new
configuration, IR schema, communication agent, or model call. Overlays remain additive under the existing
schema: conversational guidance cannot replace machine-readable contracts or grant authority.
Harness-native or user instructions can still influence writing; the policy cannot guarantee
a particular model's style or suppress harness-rendered tool/status UI.

The three former thinking protocols now require concise decision records and consequential
assumptions, risks, intended actions, and supporting observations in the relevant artifacts
or completion evidence. Approval and verification still happen before consequential actions.
No unrestricted reasoning trace is requested. This does not change the model's native
thinking settings or how a harness optionally renders native reasoning.

## Tests, examples, and updates

`compiler/evals/test_human_communication.py` checks canonical and generated policy inclusion,
all specialist references, overlay/profile composition, and separation from internal formats.
Pi runtime tests check actual parent/child system-prompt injection. Existing role, gate,
completion, safety, deterministic generation, lifecycle, and golden tests remain in force.

The [communication review suite](../evals/communication/README.md) provides eight scenarios,
six review dimensions, fact/safety checks, and authored before/after examples. These are
manual-review material, not evidence of a completed same-model experiment. Automated prompt
checks alone do not establish better live-model communication.

Use `python3 compiler/tools/regen_all.py` to refresh distributions and the vendored bundle;
do not hand-edit generated prompts. Run `--check` afterward. Existing installations need
their normal instruction refresh as well as the plugin/package update: recompose Claude
projects, or review `/system2:init --force` before overwriting a customized `CLAUDE.md`.
Refresh Pi's managed project payload through its normal update/init path without replacing
caller-owned instructions. A package update does not silently rewrite every project's
previously materialized prompts.
