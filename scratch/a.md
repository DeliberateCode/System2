I'll read both repositories thoroughly before answering. Let me clone them.I've now read both repos thoroughly — core System2 (the plugin manifest, the orchestrator `CLAUDE.md`, all 13 agents, the hooks, the allowlists, the `overlay.schema.json` + `anchor-map.json`, and the ~4,500-line `composer.py` with its profile logic) and the overlay template (manifest, contribution files, auxiliary agent). Before I answer, let me ground myself on what Pi and Goose actually expose as extension surfaces, since the right architecture depends on their models.Now let me check what "Pi" refers to as a harness, since I want to ground the advice rather than guess.I've read both repos in full — the plugin manifest and `install.sh`, the orchestrator `CLAUDE.md`, all 13 agent definitions, the hooks (`_hook_utils`, the blockers, boundary/budget), the allowlists, `overlay.schema.json` + `anchor-map.json`, the ~4,500-line `composer.py` including the profile and conflict logic, and the overlay template's manifest, contribution files, and auxiliary agent. Here's my read.

## Verdict: a compiler — but you already have one

Build the compiler. Do **not** push harness-awareness into every package. And the reframe that matters: `composer.py` *is already a compiler* — it just has exactly one hardcoded backend. It consumes a harness-neutral front-end (base content + overlay manifests + anchor map → validate → conflict-detect → topologically order contributions) and then emits a Claude Code projection in `_generate_claude_md` / `_insert_overlay_sections` / `_write_outputs`. The whole question is really "where do I cut the seam that's already implicitly there," not "do I add a new package."

So the answer isn't compiler-vs-distributed. It's: extract the IR that's currently latent, and turn the Claude-specific emission into one backend among several.

## Why "support in all packages" is the wrong branch

It's an N×M explosion. Three harnesses × (13 agents + every overlay) means every overlay author has to understand Pi's extension model and Goose recipes to write domain guidance. That kills the overlay ecosystem, whose entire value proposition is *write the domain knowledge once* — your manifest is already harness-neutral (it references content files and anchors, never Claude tool names), and that property is the asset to protect. A compiler is N backends + M neutral packages = N+M.

It also directly threatens your stated constraint (don't break Claude Code UX). Option A means editing every agent and hook to add harness branches — maximal regression surface on the one path that works today. Option B confines all harness logic to one place, and lets you golden-test that the Claude backend reproduces the current plugin output byte-for-byte. Claude Code becomes your reference target, not a thing at risk.

## What's already neutral vs. what's the Claude projection

Worth being precise, because it tells you what moves into the IR:

**Already harness-neutral (this is your IR, mostly):** the 13 agent prompt *bodies*, the orchestrator persona prose, the gate graph, the delegation contract, the spec artifact set, the post-execution trigger rules, the regression loop, the overlay contribution schema, and all of `composer.py` above the emission layer.

**The Claude projection (this becomes the `claude-code` backend):** agent YAML frontmatter (`tools:`, `hooks:`, `permissionMode`, `${CLAUDE_PLUGIN_ROOT}`), the hook scripts and their exit-2 blocking protocol, the `.regex` allowlists, the `CLAUDE.md` filename and `.claude/agents/` write targets, `plugin.json` + marketplace, and the `after_section` string-matching in `anchor-map.json` (it anchors into Claude prompt text specifically).

The one genuinely awkward piece is the anchor map: today it inserts overlay content by matching literal headings in the Claude agent prompts. For multi-target, anchors have to resolve against the *IR agent definition*, and each backend decides how to render an anchored contribution into its own agent representation. Bounded, but it's the real work.

## The capability reality (this defines what "support" even means)

The hard part isn't text generation — it's that the three harnesses have very different enforcement and structural primitives, so "supported" is not binary:

- **Claude Code:** native subagents, native MCP, and *hard* enforcement via hooks (your write-lease, dangerous-command-blocker, sensitive-file-protector, path allowlists all actually *block*).
- **Goose:** native subagents that run as isolated sessions with restricted tools and, like Claude, cannot spawn further subagents; recipes are YAML capturing instructions, extensions, parameters, and subrecipes; and extensions are MCP servers. But its safety layer is built-in (prompt-injection detection, tool permission controls, sandbox mode, an adversary reviewer) — not arbitrary user PreToolUse/PostToolUse scripts. So your *specific* hook semantics don't map 1:1.
- **Pi:** deliberately minimal — a four-tool core (Read, Write, Edit, Bash) that ships without sub-agents, MCP, permission popups, or plan mode, expecting you to build them as TypeScript extensions or install packages, with context via AGENTS.md and a per-project SYSTEM.md and distribution through Pi packages over npm or git.

The consequence: on Claude your file-write leases are *enforced*; if a naive port turns them into advisory prompt text on another harness, your safety guarantee silently weakens while the workflow still appears to "work." That's the real UX/safety footgun hiding in this project, and it's why the compiler needs to be **capability-typed**, not just a text emitter.

## The shape I'd build

1. **Name the IR.** A "System2 program" = orchestrator persona + 13 agent defs (each: prompt body, abstract tool intents, write-scope, required *capabilities* like block-dangerous / protect-sensitive / enforce-lease / format / typecheck / budget, model hint, gate role) + gate graph + delegation contract + spec set + trigger rules. Overlays contribute *into* this IR. Agents stop encoding Claude mechanisms and start declaring intent.

2. **Capability-typed backends.** Each target declares a capability manifest (native-enforce / emulate-via-generated-code / advisory-only) per IR capability. The `claude-code` backend lowers to today's plugin. `goose` lowers agents → recipes/subrecipes, capabilities → MCP + permission controls where they exist. `pi` lowers agents → SYSTEM.md/prompt templates + a generated TS extension bundle that implements the subagent dispatcher and permission gates — more code to generate, but Pi's model means you can hit *higher* enforcement fidelity than Goose because you own the gate logic.

3. **Compile-time degradation report.** When a capability can't be natively enforced on a target, the compiler says so loudly rather than pretending. This is the honest version of "support without breaking Claude UX": Claude stays full-fidelity; others get a transparent capability report. Pair it with tiers — features land in the IR + Claude backend first, other backends implement as capability allows.

## On the "Claude Bash script"

The instinct (one artifact that drives the harness) is sound, but bash is the wrong lingua franca. The natural artifact per target is *declarative* — Claude's plugin tree, Goose's recipe YAML, Pi's SYSTEM.md + TS extension — and bash is a bad substrate for the enforcement semantics (you'd be reimplementing your hooks in shell) and couples you to a shell. Keep the compiler emitting native declarative artifacts. The legitimate, narrow place for a generated script is a thin per-target *installer/driver* that wires emitted artifacts into the harness's expected locations — which is what `scripts/install.sh` already is for Claude. Generate that per target; don't make it the translation layer.

## Concrete first cut in your repo

Split `composer.py` along the seam that's already there: everything through `_topological_sort` + profiles is the front-end (keep as-is); `_generate_claude_md` + `_insert_overlay_sections` + `_write_outputs` become `backends/claude_code.py` behind a `Backend` interface (`emit(ir, project_path) -> written_files`). Lift the anchor resolution out of literal-heading matching into IR-level anchors. Land that with a golden test asserting identical Claude output, then the Goose and Pi backends are additive and can't regress the Claude path.

One thing worth deciding early, since it shapes the IR: do you want the non-Claude backends to *enforce* write-leases and command-blocking (Pi: yes via generated TS gate; Goose: partially), or are you willing to ship them advisory-only on those targets? That answer determines whether "capabilities" are a first-class typed thing in the IR or just metadata — and it's the call I'd make before writing any backend.