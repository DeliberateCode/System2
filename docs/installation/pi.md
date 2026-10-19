# Pi publication status

The Pi channel is **not published**. No npm installation command is available, and the
package name must not be treated as currently installable. For the other harnesses, see the
[Installation and Updating](../installation.md) index.

## Validated candidate

The current generated package candidate has non-skipping native acceptance against **Pi
0.85.1**. That evidence covers:

- package discovery of the `delegate`, `system2-status`, `system2-history`, `system2-new`, `system2-resume`, and `system2-init` commands;
- Orchestrator-first startup through Pi's public RPC and SDK paths;
- model-callable sanitized `system2_status`, parent verification Bash, and one-operation
  interactive approval for parent structured writes;
- visible human gate-decision audit messages, explicitly human-authorized gate skipping,
  repeatable full/maintenance workflow cycles, archived decision/evidence history, and
  branch-local reconstruction across reload;
- dispatch contracts for all 13 roles through ephemeral subprocess invocations;
- a real Pi JSON-mode child, driven by a deterministic test provider, executing the
  terminating completion-claim tool;
- recoverable rejected completion claims, incomplete exits, safe failure categories,
  extension-derived partial-change inventories, and the three-failure infrastructure circuit;
- package-discovered `/system2-init`, including preservation of caller-owned `AGENTS.md`;
- bounded native blocking of declared dangerous-command and sensitive-path patterns, with a
  benign-command negative control; and
- adapted write-scope gating for a structured off-scope write.

Additional synthetic extension-handler controls exercise supported literal shell
redirection/`tee` targets and the broader matcher corpus. They are not represented as native
CLI acceptance. This pins the native evidence to the tested Pi version; it is not a
compatibility claim for untested versions and does not make an unpublished package available.

The package uses Pi's documented `"*"` peer range for host-provided core imports. That loader
convention is not a claim that System2 has been validated against every Pi version.

### Study-derived end-to-end acceptance

The original `tasker` study is now represented by a cohesive, non-skipping acceptance in
`compiler/evals/test_pi_study_acceptance.py`. It drives a complete Gate 0–5 workflow and a
follow-up maintenance workflow through real Pi 0.85.1 JSON agent loops using an offline,
deterministic provider. The study creates all four spec artifacts, delegates implementation,
writes `.gitignore` through the executor lease, runs the 29-test suite in both executor and
test-engineer children, independently reruns all 29 tests through Pi's parent Bash tool, runs the post-execution
review roles, records visible human-decision audit messages, and creates an arbitrary root
Markdown document through `docs-release` after starting a new workflow. It also verifies that an
ordinary context input is launch-snapshotted without becoming human-approved gate evidence.
No manual file creation or pasted test output is used.

Run that acceptance from `compiler/` with:

```sh
PI_PKG_ENTRY="$(npm root -g)/@earendil-works/pi-coding-agent/dist/index.js" \
PYTHONPATH=. python3 -m unittest evals.test_pi_study_acceptance -v
```

This path is intentionally hermetic and sends no external model traffic. It proves the Pi agent
loop, generated extension, subprocess authority, tool execution, completion protocol, and
workflow behavior; a deterministic provider is not evidence that every LLM will make equally
good planning or implementation decisions. Before publication, repeat the same scenario in the
interactive TUI with a supported built-in provider and retain the session plus direct
`python3 -B -m unittest discover -s tests -v` output as live-model release evidence. The live run
passes only when every delegation returns a structured result, the parent independently reruns
all 29 tests, no user-created repair file is needed, Gate 5 is visibly human-approved, and a
post-ship documentation request starts and completes a new maintenance workflow.

## Current scope

The candidate contains the compiler-generated **base workflow**: a persistent parent
Orchestrator, 13 detailed specialist prompts, model-callable subprocess delegation, sanitized
status, gate approval/skip tools, human status/history/new-workflow/resume/recovery commands,
three System2 skills, and bounded safety
gates. `/delegate <role>` is only a human convenience request to the parent; it never changes
the parent into that role. The parent supplies only delegation intent—role, objective, optional
ordinary repository `contextInputs`, concrete expected outputs, acceptance criteria, verification
commands, and optional budget/timeout. Non-empty child verification commands are valid only
for roles whose canonical tool projection includes Bash. Roles without Bash must use `[]`, and
the parent independently verifies their artifacts; impossible role/verification combinations
fail before a subprocess is spawned. Bash-capable children must run each contracted command
verbatim as a separate Bash call; the child completion tool rejects an early completion claim
until every command has been observed, so the child can correct the omission in-process. The
extension derives the current gate, approval ID,
exact task lease, human-approved gate evidence, and authority contract. Context inputs need no
gate-artifact approval: System2 normalizes and snapshots their bytes at launch, and the child
verifies those snapshots before starting. Gate evidence remains a separate byte-for-byte record
of artifacts explicitly approved by the human. Gate approval uses readable `allowedPaths` entries such as
`spec/**`, `src/*.py`, and `.gitignore`; regex syntax is rejected. Absolute paths are accepted
only when they resolve inside the project and are normalized to repository-relative paths.
At Gate 4, `plannedOutputs` is required. Before showing the approval dialog, System2 checks
every output against the proposed human path scope and all canonical role leases, rejects any
path that no role can write, and displays a path-to-compatible-role coverage matrix. Delegation
repeats this preflight before launching a child and recommends compatible roles when the
selected role cannot own an output. The executor covers common task-approved project config
such as `.gitignore`, while docs-release covers task-approved Markdown in project-owned paths.
Every gate approval or rejection produces a visible transcript audit message recording the
workflow, gate, human TUI action, timestamp, normalized paths, and artifact evidence. A model
cannot skip a gate directly: `system2_request_gate_skip` always requires an explicit interactive
human decision, records every skipped/rejected gate append-only, and cannot skip Gate 0 because
Gate 0 establishes write authority. Gate 5 completes only the current workflow. The next
interactive request offers a new full workflow, a small maintenance workflow, or completed
read-only review mode. `/system2-new [full|maintenance]` provides the same control directly;
`/system2-history` displays archived gate decisions and evidence. A new workflow starts at Gate 0
without inherited authority and never rolls back repository files.

A typical model-authored intent is:

```json
{
  "role": "design-architect",
  "objective": "Create the approved design artifact.",
  "contextInputs": ["spec/requirements.md"],
  "expectedOutputs": ["spec/design.md"],
  "acceptanceCriteria": ["The design covers interfaces and failure modes."],
  "verificationCommands": []
}
```

`contextInputs`, `budget`, and `timeoutSeconds` are optional. A child may also discover and read
other non-sensitive repository files normally; only listed context inputs carry launch-snapshot
provenance. Internal fields such as `gateContext`, `approvalId`, `taskWriteScope`, `gateEvidence`,
and digests are rejected if the model supplies them. Each ordinary specialist dispatch starts an ephemeral Pi process
with no child session persistence, no ambient extensions, a role-specific built-in tool set,
and no recursive delegation tool. When System2 runs under the Pi CLI, it reuses Pi's resolved
absolute CLI entry. SDK hosts must set `SYSTEM2_PI_BIN` to an absolute, owner-trusted,
non-group/world-writable Pi executable; System2 never falls back to a bare `pi` lookup on
`PATH`. Because ambient extensions are disabled in children, a model that exists only through
a custom-provider extension is unavailable there and delegation fails closed.

This provides separate process, Pi session, and model context. It does **not** sandbox the
repository filesystem or inherited credentials. Filesystem and credential isolation remain
false in the generated status/lock report.

The source compiler can project overlay compositions for Pi, but that is a developer path
from a repository checkout rather than end-user package UX. The package does not expose the
Claude Code overlay/profile commands.

## Project initialization

After publication, the package-discovered `/system2-init` command will materialize only these
managed project files:

- `.pi/SYSTEM.md`
- `system2.pi.lock.json`

It does not create, replace, or remove caller-owned `AGENTS.md`. A byte-identical rerun is a
no-op. A differing managed file is preserved unless `/system2-init --force` is used. Writes
use per-file atomic replacement, paths and existing path components are checked for lexical
or symlink escape, and Pi reloads resources after a successful write.

## Enforcement fidelity

The candidate deliberately does not claim full mechanism parity:

- `block-dangerous` and `protect-sensitive` are bounded native `tool_call` gates for their
  declared patterns.
- `enforce-lease` is adapted/partial. In children it intersects canonical role scope with
  an exact task scope derived from concrete expected outputs and the latest human-approved
  restricted path globs for structured write/edit targets and supported literal
  redirection/`tee` targets, but it is not a general shell parser. The parent can run Bash
  for independent verification under the dangerous-command and sensitive-path gates.
  Structured parent write/edit calls and supported explicit shell-write targets require an
  interactive human approval for that one normalized operation and are recorded with an exact
  request digest as `system2-parent-write-override` session entries. Non-interactive parent writes fail closed.
  As with child shell enforcement, implicit writes by tests/build tools and unsupported shell
  forms are not comprehensively classified.
- `budget` is adapted/partial. System2 takes bounded before/after task-scope snapshots, derives
  changed paths and artifact hashes, observes contracted Bash command outcomes, and computes
  conservative prefix/suffix line counts for bounded text files. The changed-line budget is
  additions plus removals, so rewrites and test updates need headroom; retain the large default
  when no meaningful bound is known. Only directories that can
  intersect the approved task scope are traversed, including dependency/build directories when
  approved; `.git` remains excluded. It is not a native VCS diff, and incomplete, binary,
  oversized, or symbolic-link changed-file evidence cannot produce a complete outcome.
  Unsupported out-of-scope shell writes are outside both the task-scope snapshot and lease
  claim. System2 quiesces the child process group/tree and blocks common explicit detachment
  forms before after-state capture, but does not prove that arbitrary daemon/session escape is
  impossible. Formatting and type-checking remain advisory.
- Empty task/role scopes fail closed for supported writes. The model cannot supply gate IDs,
  approval IDs, regex task scopes, gate-evidence lists, or digests. Optional context inputs are
  ordinary repository files: they require no gate-artifact approval, are normalized and
  launch-snapshotted, and are verified separately from byte-for-byte human-approved gate evidence.
  Human gate requests use path/glob syntax, and concrete expected outputs may be repository-relative
  or absolute paths inside the project. System2 canonicalizes them, verifies approved evidence bytes at dispatch,
  protects `.pi/**` and `system2.pi.lock.json`, and rejects an expected output that is not
  allowed by both human and role authority. Gate 4 also binds its concrete planned-output list;
  later delegations cannot silently add an output absent from the approved task plan.
- The parent model can call `system2_status` for a sanitized status view containing the current
  workflow identity/kind, archived workflow summaries, gate counts,
  active/last delegation identity, safe failure categories, changed paths, and active tools;
  untrusted specialist summary text and full human gate prose are omitted. `/system2-status`
  remains the richer human-facing view.
- A rejected completion claim remains recoverable for up to three attempts. A child that exits
  without an accepted claim returns an `incomplete` report with observed changes; runtime
  failures expose a safe category and diagnostic digest while withholding raw stderr.
- Delegation failures are classified as `infrastructure`, `authorization`, `application`,
  `contract`, or `operator`. Only `application` failures carry
  `countsTowardCorrectiveCycle=true`; infrastructure, scope/lease, contract, and operator failures
  do not consume the application boomerang budget. A missing contracted command event is reported
  as `VERIFICATION_NOT_RUN` in the contract domain rather than as an evidence/infrastructure
  failure; it never opens the circuit and directs the parent to verify independently or correct
  the contract. Three consecutive genuine infrastructure failures open a branch-local circuit
  and pause further delegation. `system2_status` reports the streak, circuit state, safe
  recommendations, and partial-change paths; after diagnosis and review, the human explicitly
  closes the circuit with `/system2-resume`. Repeating `/system2-resume` after closure is an
  explanatory no-op rather than an ambiguous error.
- Failed or incomplete delegations never trigger automatic rollback. System2 preserves and
  reports the observed changed paths. The parent must preview the diff and obtain explicit human
  approval for exact rollback operations if those changes should be discarded.
- Invalid or interrupted branch state blocks mutation/delegation until human-confirmed
  `/system2-recover`. Updating/regenerating the extension intentionally changes its provenance
  digest, so an existing workflow requires this explicit recovery after an extension upgrade.

The generated [Pi package README](../../distributions/pi/README.md) and
`system2.pi.lock.json` contain the complete current limitations and per-capability report.

## Updating and removal

Not applicable until publication. Installation, updating, and removal instructions will be
added only after the package is published and release provenance is recorded.
