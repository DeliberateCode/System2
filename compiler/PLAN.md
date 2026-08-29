# System2 Multi-Harness Support — Implementation Plan

*Synthesis of three independent design reviews (`scratch/a.md`, `b.md`, `c.md`). All three reached the same verdict; this plan keeps their consensus, resolves the few places they diverge, and grounds each step in the real `composer.py` seams.*

## The decision

Build a **compiler with capability-typed backends**. Do **not** carry harness support inside core, every overlay, and the template.

All three reviews are emphatic and unanimous on this. The reason is an N×M argument: three harnesses × (13 agents + every overlay) forces every overlay author to understand Pi's extension model and Goose recipes just to write domain guidance — which destroys the overlay ecosystem whose entire value is *write the domain knowledge once*. A compiler is N backends + M neutral packages = N+M. It also confines all harness logic to one place, so Claude Code becomes the reference target you protect rather than a path you put at risk by editing every agent and hook.

The single most important reframe (from review A): **`composer.py` is already a compiler with exactly one hardcoded backend.** Everything through `_topological_sort` + the profile logic is a harness-neutral front-end (it consumes content files, overlay manifests, and the anchor map; validates; detects conflicts; topologically orders contributions). Only `_generate_claude_md`, `_insert_overlay_sections`, and `_write_outputs` are the Claude projection. So the task is not "add a package" — it's **cut the seam that already exists and turn Claude emission into one backend among several.**

## Principles the three reviews agree on

1. **Compose, then render — two phases.** `compose(core + overlays + profile) → System2 IR`, then `render(IR, target) → harness artifacts`. The IR ("System2 graph") holds roles, gate graph, spec artifact set, delegation contract, trigger rules, ordered overlay contributions, and the profile.
2. **Claude Code stays the privileged, full-fidelity reference target.** Never reduce it to a lowest-common-denominator abstraction. Its UX (`/system2:init`, `/system2:compose`, `/system2:doctor`, plugin install) must not change.
3. **Backends are capability-typed and never silently lossy.** Each backend declares, per capability, whether it is `native` / `adapted` / `advisory` / `unsupported`, and the compiler emits that into the lock file as a degradation report. This is the honest version of "support without breaking Claude UX."
4. **Bash is not the abstraction layer.** It cannot represent role semantics, gate state, delegation, or enforcement. Bash is only ever a thin generated *installer/launcher* per target — which is exactly what `scripts/install.sh` already is for Claude.
5. **Overlays stay single-source and harness-neutral**, with a rare optional escape hatch (`targets.{claude,pi,goose}`) for advanced overlays that want harness-specific affordances. Default path: `overlay contribution → IR → all backends`.
6. **Profiles stay harness-neutral** — "these overlays, in this order, on this core version." Same profile compiles to any target.

## The one insight that should drive the design: enforcement fidelity

Review A surfaces the real footgun, and it's the thing to decide before writing any backend.

On Claude Code your safety primitives **actually block**: the write-lease, `dangerous-command-blocker`, `sensitive-file-protector`, and the `.regex` path allowlists are enforced via PreToolUse/PostToolUse hooks exiting non-zero. The other harnesses have fundamentally different enforcement models:

- **Pi** ships a minimal core (Read/Write/Edit/Bash) with no built-in permission system, but is extensible via TypeScript. You can *generate a TS extension that implements the gate logic yourself* — meaning Pi can reach **higher** enforcement fidelity than Goose, because you own the gates.
- **Goose** has native subagents (recipe-based, isolated sessions) and MCP extensions, but its safety layer is **built-in** (prompt-injection detection, permission controls, sandbox mode) — you cannot install arbitrary PreToolUse/PostToolUse scripts. Your specific hook semantics map only partially.

The danger: a naive port turns *enforced* leases into *advisory prompt text* on another harness. The workflow still appears to "work" while the safety guarantee silently evaporates. This is why capabilities must be **first-class typed objects in the IR**, not metadata — agents should stop encoding Claude mechanisms (`tools:`, `hooks:`, `permissionMode`) and instead **declare intent**: required capabilities like `enforce-lease`, `block-dangerous`, `protect-sensitive`, `format`, `typecheck`, `budget`, plus write-scope, model hint, and gate role.

**Decision to make before Phase 3:** for each non-Claude target, will write-leases and command-blocking be *enforced* (Pi: yes, via generated TS gate; Goose: partial) or shipped *advisory-only*? That answer determines how much backend code you generate vs. how loud the degradation report must be.

## The real work item: the anchor map

Today `anchor-map.json` inserts overlay content by string-matching literal headings in the Claude agent prompts (`after_section`). That's intrinsically Claude-shaped. For multi-target, anchors must resolve against the **IR agent definition**, and each backend decides how to render an anchored contribution into its own representation (a Claude prompt section, a Goose recipe instruction block, a Pi prompt template slot). This is bounded but it's the genuine engineering, so plan for it explicitly in Phase 2.

## Package layout

Keep it in **one `System2-Compiler` repo with a `backends/` directory** for now (reviews A and C). Review B's per-target repos (`System2-Target-Pi`, etc.) are a *later* option if a backend grows large or wants independent release — not day one. The architectural move that matters is separating composition from emission, not the repo count.

```
System2-Compiler/
  ir/                     # System2 graph schema + builder (lifted from composer front-end)
  backends/
    base.py               # Backend interface: emit(ir, project_path) -> written_files
    claude_code.py        # today's _generate_claude_md + _insert_overlay_sections + _write_outputs
    goose.py
    pi.py
    capabilities/
      claude_code.json    # per-capability: native | adapted | advisory | unsupported
      goose.json
      pi.json
  cli.py                  # system2 compile --profile X --target {claude-code,goose,pi}
```

System2 core, OverlayTemplate, and profiles stay essentially as-is.

## Phased rollout

**Phase 0 — Freeze Claude behavior (do this first, all three reviews).**
Snapshot current output as golden tests: given core + one overlay + one profile, assert the produced `CLAUDE.md`, `.claude/agents/*.md`, `spec/overlay-manifest.lock`, and warnings are byte-identical (or semantically identical). You already have `evals/goldens/` — extend it. This makes the refactor non-negotiably safe.

**Phase 1 — Extract the IR / split compose from render.**
Cut `composer.py` along the existing seam. Everything through `_topological_sort` + profiles becomes the front-end that builds a `System2Graph`. Move `_generate_claude_md` + `_insert_overlay_sections` + `_write_outputs` behind a `Backend.emit(ir, project_path)` interface as `backends/claude_code.py`. Land with the Phase 0 goldens green — the Claude backend must reproduce current output exactly. No user-visible change.

**Phase 2 — Lift anchors to the IR + add the capability model.**
Move anchor resolution from literal-heading matching to IR-level anchors. Make agents declare capabilities (`enforce-lease`, `block-dangerous`, etc.) instead of Claude mechanisms; the `claude-code` backend lowers them back to today's hooks/allowlists/frontmatter. Add `backends/capabilities/*.json` and have every backend report `native | adapted | advisory | unsupported` per capability into the lock file. **No silent dropping.**

**Phase 3 — First non-Claude backend.**
Recommendation: **Goose first.** Its recipe YAML gives the cleanest role→artifact mapping (orchestrator → `system2.recipe.yaml`, each role → a recipe-based subagent or an orchestrator instruction block), and it forces you to exercise the capability-degradation path immediately (Goose can't do arbitrary hooks), which is exactly the muscle the whole design depends on. Emit `system2.recipe.yaml`, `agents/*.recipe.yaml`, and a thin `run-system2.sh` launcher (`goose recipe validate … && goose run --recipe …`) — the legitimate use of bash.

*Divergence to note:* review C argues for a **bash "driver" target first** as a fast way to validate the translation seam, and review A is order-agnostic. If you want to de-risk the IR extraction with the simplest possible second backend before committing to harness specifics, do a minimal bash driver as a throwaway validation step — but treat it as scaffolding, not a shipping abstraction.

**Phase 4 — Pi backend.**
Emit `.pi/SYSTEM.md` + `AGENTS.md` for context, skills under `.pi/skills/`, prompt templates, and — the high-value part — a generated **TypeScript extension** that implements the subagent dispatcher and the permission/lease gates via Pi's event model (`on("tool_call")`, protected paths). This is where you decide to spend code to buy *real* enforcement rather than advisory text.

**Phase 5 — Wire compiler into Claude's compose (optional, non-breaking) + docs + publish.**
Eventually `/system2:compose --profile X` can internally call `system2 compile --target claude-code`. Users see nothing change. Publish the compiler and example "System2 for Goose / Pi" packages.

## Direct answer to your fork

You asked: (A) carry multi-harness support in all packages, or (B) a compiler package exposing a bash script. The answer is **B, with one correction shared by all three reviews**: build the compiler/renderer, but **bash is not the interface** — it's only generated glue for targets that need a launcher (Goose especially, since recipes are CLI-runnable). The compiler owns all semantics; bash only invokes rendered artifacts. A "Claude Bash script" is fine as one optional *target* for portable/CI use, never as the translation layer.

The future-proofing payoff: when harness #4 appears you never ask "how do I port every overlay?" You ask "what capabilities does it expose, and how faithfully can it render the System2 graph?" That is the correct abstraction boundary.
