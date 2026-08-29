**Yes, read both repos in detail first.** I have.

### Quick Diagnosis of the Current Coupling
System2 (core) + OverlayTemplate are elegantly designed for **Claude Code** as the harness:

- Plugin system (`.claude-plugin/`, marketplace, `plugin.json`).
- Slash commands implemented as Claude Code *skills* (`/system2:init`, `/system2:compose`, `/system2:doctor`, etc.).
- Central orchestrator lives in `CLAUDE.md` (generated + overlay-injected).
- Sub-agents as Markdown + YAML frontmatter (with `tools`, `model`, `permissionMode`, `hooks`).
- Composition engine (in `plugin/scripts/` + `schemas/`) that uses an **anchor map** + `system2.overlay.json` manifest to surgically insert content into `CLAUDE.md`, write `.claude/agents/*.md`, copy hooks, produce `spec/overlay-manifest.lock`, etc.
- Hooks are Claude-specific (`PreToolUse`/`PostToolUse` etc. with `${CLAUDE_PLUGIN_ROOT}` interpolation and Python scripts that run in Claude’s context).
- Profiles are user-wide JSON (`~/.system2/profiles.json`) resolved at compose time.
- Everything is *additive-only* via overlays — which is excellent.

This gives an outstanding UX *inside Claude Code*, but it is deeply tied to Claude Code’s extension model (prompt injection points, agent format, hook lifecycle, file layout, env vars, and command surface).

Pi (minimalist, event-driven extensible CLI/TUI harness with **extensions** (TS), **skills**, **prompt templates** with variable substitution, and events like `on("tool_call")`, permission gates, protected paths) and Goose (MCP-focused, general-purpose agent with subagent orchestration, ACP server capabilities, desktop/CLI/API) have completely different extension surfaces.

### Recommended Path: Compiler Package (Not Multi-Harness Bloat in Every Package)
**Do not** carry support for multiple harnesses inside the core plugin, every overlay, or the template. That would:

- Bloat overlays (they should stay pure conceptual extensions: principles, prompt sections, spec sections, hooks, auxiliary agents, etc.).
- Slow Claude Code development velocity (your stated priority).
- Create maintenance nightmares (conditionals everywhere, harness-specific files in every overlay repo).
- Make the overlay manifest messy.

**Instead, build a dedicated compiler/transpiler package.** This is the cleanest way to support Pi, Goose, a generic/bash target (including a “Claude Bash script”), and future harnesses *without touching* the Claude Code UX or forcing changes on overlay authors.

#### High-Level Architecture
1. **Abstract System2 Model** (harness-neutral foundation)
   - Keep/enhance the existing `schemas/` (overlay manifest schema + anchor map) and make it the single source of truth.
   - Extract the *base workflow* (the 13 default agents + their responsibilities, the 6 quality gates/stages, default spec/ artifact structure, thinking protocol, delegation patterns) into declarative form:
     - A core definition (JSON/YAML + reusable Markdown templates with clear anchor/section markers).
     - This can live in a small shared package or inside the compiler repo initially.
   - Contribution types in `system2.overlay.json` stay abstract and declarative:
     - Orchestrator principles / gate consultations.
     - Per-agent prompt sections (with named anchors).
     - Required sections for spec/ artifacts.
     - Auxiliary agents.
     - Hook/event logic (with portable description + optional harness-specific implementations).
     - Tool/permission/MCP declarations (mapped by compiler).
   - Profiles remain exactly what they are: named collections of overlays.

2. **Claude Code Implementation Stays the Primary/Reference (Unchanged UX)**
   - The existing `System2` plugin, compose logic, slash commands, `CLAUDE.md` generation, hooks, etc. continue to work *exactly* as today for Claude Code users.
   - You keep developing and polishing against Claude Code at full speed.
   - Overlays written with the current template continue to work perfectly (and instantly) for Claude Code.
   - Optionally (later, non-breaking): make the internal compose step delegate to the compiler’s `claude-code` target for consistency. No user-visible change.

3. **New Package: `System2-Compiler` (or `deliberatecode-system2-compiler`)**
   - Standalone CLI + library.
   - Takes as input:
     - Base core definition (the abstract workflow).
     - One or more overlays (their `system2.overlay.json` + `contributions/` files) — or a profile name (resolved from `~/.system2/profiles.json` or local).
   - `--target` flag: `claude-code`, `pi`, `goose`, `bash` (and others later).
   - `--output` directory.
   - Dry-run, validation, hash/lock support similar to current compose.
   - It **translates** the abstract concepts into harness-native artifacts.

   **What each target produces (examples):**

   - **`claude-code`** target → Exactly what current `/system2:compose` produces (CLAUDE.md with injections at anchors, `.claude/agents/`, copied hooks, `spec/overlay-manifest.lock`, etc.). This becomes the blessed engine.

   - **`bash`** target (your “Claude Bash script” idea) → A self-contained bash script (or small directory of scripts + templates) that implements the full System2 workflow:
     - Manages `spec/` directory and gate state.
     - Implements stages/gates interactively or with checks.
     - Assembles prompts from templates + overlay contributions.
     - Delegates to sub-agents via `claude` CLI (non-interactive `-p` / `--model` calls) or other backends.
     - Enforces thinking protocol, quality gates, allowlists via wrapper functions.
     - Supports profiles, compose-like operations, doctor checks.
     - Extremely portable (works in CI, containers, minimal environments, or alongside any harness). Perfect universal fallback.

   - **`pi`** target → 
     - Prompt templates (orchestrator + per-agent) in Pi’s Markdown + variable substitution format.
     - A Pi **Extension** (TypeScript) that wires up the System2 workflow: registers commands for gates/stages, listens to events (`on("tool_call")`, etc.) to implement quality gates / path protection / dangerous command blocking (directly mapping your current hook intent), manages `spec/` artifacts, provides delegation patterns.
     - Optionally a bundled skill or package that users `pi install` (from git or npm once published).
     - Leverages Pi’s minimalist philosophy and event/gate system beautifully.

   - **`goose`** target →
     - MCP server definition / implementation exposing System2 workflow stages, gates, and agents as callable capabilities.
     - Prompt configurations and any ACP-relevant wiring.
     - Leverages Goose’s subagent orchestration and MCP extensibility.

4. **Overlay Authors & Template**
   - Continue using/enhancing `System2-OverlayTemplate` almost unchanged.
   - Overlays remain harness-agnostic. They declare *what* to contribute (e.g., “insert this safety discipline section into the executor prompt at the ‘discipline’ anchor” or “add this required section to spec/requirements.md”).
   - For complex runtime hooks, overlays can provide a portable description + example; the compiler (or a small adapter layer) turns it into the right harness-native form (Python hook for Claude, event listener in Pi TS extension, MCP tool for Goose, shell guard in bash).
   - Prompt-level and spec-level contributions translate almost 1:1 — these are the highest-ROI parts anyway.

### Why This Preserves (and Protects) Claude Code UX
- Zero breaking changes for existing Claude Code users.
- Development velocity on Claude Code stays maximal — you don’t have to think about Pi/Goose when adding features or fixing bugs in the core plugin.
- New compiler features are additive and opt-in (`system2 compile --target pi ...` or equivalent).
- Overlays you (and the community) build today work immediately for Claude Code and become portable later with zero changes to the overlay itself.

### Benefits
- Clean separation of concerns: concepts (System2 workflow + overlays) vs. harness adapters.
- Overlays stay focused and simple.
- Easy to add more harnesses later (just implement a new target in the compiler).
- The bash target gives you an immediate, powerful “universal” / portable form (great for docs, demos, CI, or users who don’t want a full harness).
- Profiles work everywhere (compiler resolves them).
- You can still have a great Claude Code experience while opening the door to Pi and Goose users.

### Suggested Phased Rollout (Low Risk)
1. Formalize the abstract schema + extract base templates (small, low-risk step; improves even the Claude path).
2. Build compiler with `claude-code` target first (prove parity with existing compose).
3. Add `bash` target — quick high-value win and validates the translation approach.
4. Add `pi` target (study their excellent extension/event docs; map gates/hooks to their event system and prompt templates).
5. Add `goose` target (leverage MCP).
6. Update docs + optionally wire compiler into the Claude plugin’s compose (non-breaking).
7. Publish compiler + example “System2 for Pi” / “System2 for Goose” packages.

This matches your intuition about a compiler package that translates concepts and exposes a bash script, while generalizing cleanly to real harnesses like Pi and Goose.

If you want, I can help sketch the initial schema enhancements, compiler CLI interface, or even a minimal bash target prototype structure. Just say the word. This is a very solvable and elegant problem given how well-factored the overlay manifest + anchor system already is.