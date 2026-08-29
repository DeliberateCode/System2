I read the core System2 repo and the overlay template, and I would **not** put multi-harness support into every package. I would keep **Claude Code as the reference UX and highest-fidelity target**, then add a **compiler/renderer layer** that translates System2’s semantic concepts into harness-specific artifacts.

The short answer is:

> **System2, Overlays, and Profiles should be harness-neutral at the concept/schema layer. Claude Code, Pi, Goose, etc. should be target renderers. Claude Code should remain the privileged/native target, not be degraded into a lowest-common-denominator abstraction.**

## What your current design implies

System2 is already more than a Claude prompt bundle. Conceptually, it is a structured orchestration protocol: scope, context, requirements, design, tasks, implementation, verification, and ship, with 13 specialized roles and explicit quality gates. The current repo implements that through Claude Code’s subagent and plugin model, but the underlying abstractions are broader than Claude Code. The README describes the workflow, the 13 agents, the gate model, and the `/spec` artifacts separately from the Claude mechanics. ([GitHub][1])

That said, the current implementation is concretely Claude-shaped. It writes `CLAUDE.md`, installs `.claude/agents/*.md`, relies on Claude Code YAML frontmatter for agents, and uses Claude-specific hooks, allowlists, permission modes, and plugin skills. ([GitHub][1])

The overlay template reinforces the same pattern: overlays are conceptually additive contributions to System2, but today they compose into Claude-specific surfaces such as `CLAUDE.md`, `.claude/agents`, and Claude-style hooks/tools/permissions. The template also makes a useful architectural commitment: overlays are **additive-only** contributions, not arbitrary replacement patches. ([GitHub][2])

That is the key clue. You already have the start of an intermediate representation; it is just currently entangled with Claude output paths.

## Recommendation

Create a package that is best thought of as:

```text
System2 semantic graph  ->  target-specific renderers
```

Not:

```text
Claude Code package  ->  hacked ports to Pi/Goose
```

And not:

```text
Every overlay package carries Claude + Pi + Goose implementations
```

A good package split would be:

```text
DeliberateCode/System2
  Reference Claude Code distribution.
  Keeps the current UX: plugin install, /system2:init, /system2:compose, /system2:doctor.

DeliberateCode/System2-OverlayTemplate
  Stays mostly as-is.
  Authors write one System2-native overlay.

DeliberateCode/System2-Compiler
  Reads core + overlays + profiles.
  Produces a canonical System2 graph.
  Renders that graph to target harnesses.

DeliberateCode/System2-Target-Claude
  Could initially just be the existing behavior.
  Eventually becomes the Claude renderer.

DeliberateCode/System2-Target-Pi
  Emits Pi-compatible context files, skills, prompt templates, and optional extension/package scaffolding.

DeliberateCode/System2-Target-Goose
  Emits Goose recipes, subagent recipes, extension configuration, and runner scripts.
```

You do not need all of those repos on day one. The important architectural move is to separate **composition** from **emission**.

## The core design: compile to a System2 graph first

Right now, `/system2:compose` validates overlays, detects conflicts, writes composed Claude artifacts, writes `.system2/overlays/...`, and writes `spec/overlay-manifest.lock`. It already does dry-run previews, warning emission, lock-file-based replay, and safety checks. ([GitHub][3])

I would split that into two conceptual phases:

```text
1. Compose
   core + overlays + profile
   -> System2Graph

2. Render
   System2Graph + target=harness
   -> harness-specific files
```

The `System2Graph` should contain things like:

```text
roles:
  - repo-governor
  - spec-coordinator
  - requirements-engineer
  - design-architect
  - executor
  ...

gates:
  - scope
  - context
  - requirements
  - design
  - tasks
  - ship

artifacts:
  - spec/context.md
  - spec/requirements.md
  - spec/design.md
  - spec/tasks.md
  - spec/security.md

overlays:
  - ordered additive contributions

profile:
  - named collection of overlays

capabilities:
  - needs_subagents
  - needs_tool_allowlists
  - needs_post_tool_hooks
  - needs_path_protection
  - needs_mcp_servers
  - needs_slash_commands
```

Then each renderer decides how faithfully it can express those capabilities.

## Claude Code should remain the native reference target

Do not break the Claude Code UX.

Claude is currently the most natural home for System2 because your repo directly uses Claude Code’s project/plugin conventions: `CLAUDE.md`, `.claude/agents`, frontmatter-based subagents, tool lists, hooks, permission modes, and plugin skills. ([GitHub][1])

So the Claude target should remain:

```bash
/system2:init
/system2:compose
/system2:compose --profile ...
/system2:doctor
```

Under the hood, you can eventually make those commands call the compiler. But from the user’s perspective, nothing should change.

I would make Claude the **golden-output target**. Before refactoring, snapshot the current generated `CLAUDE.md`, `.claude/agents`, lock files, and warnings. Then require the new compiler’s Claude renderer to produce byte-identical or semantically identical output.

That gives you freedom to support other harnesses without making Claude users pay the abstraction cost.

## Pi target: render context, skills, and optional extension package

Pi is a plausible target, but it is not Claude-shaped. Pi loads project context from `AGENTS.md` or `CLAUDE.md`, supports `.pi/SYSTEM.md`, supports skills, prompt templates, and TypeScript extensions, and can package those resources. ([GitHub][4])

But Pi also states that it does **not** include a built-in permission system; by default it runs with the permissions of the launching process. Stronger boundaries need sandboxing/containerization. ([GitHub][5])

That means the Pi renderer should not pretend Claude hooks and permission policies map cleanly. It should emit:

```text
.pi/SYSTEM.md
  System2 orchestrator instructions.

AGENTS.md or CLAUDE.md
  Project-level System2 operating guidance.

.pi/skills/system2-init/SKILL.md
.pi/skills/system2-compose/SKILL.md
.pi/skills/system2-doctor/SKILL.md
  Skill equivalents where possible.

.pi/prompts/...
  Prompt templates for gate transitions or role invocation.

optional TypeScript extension
  Only if you need commands, event handlers, extra tools, or policy checks.
```

For Pi, I would label capability support explicitly:

```text
subagents: partial/advisory unless implemented through extension conventions
slash commands: adapted through Pi commands/templates
skills: native
context files: native
hooks: unsupported or extension-emulated
permission allowlists: advisory unless sandboxed
MCP/tools: target-specific
```

That warning model matters. A user should know when System2 has preserved behavior and when it has merely emitted instructions.

## Goose target: render recipes and subagent recipes

Goose is more naturally recipe-shaped. Goose recipes are portable YAML configurations with fields like instructions, prompt, extensions, activities, settings, retry behavior, and parameters; they can be run from the CLI with `goose run --recipe ...` and validated with `goose recipe validate`. ([Goose Docs][6])

Goose also supports subagents, including recipe-based subagents, and can connect external agents through MCP/ACP patterns. ([Goose Docs][7])

So the Goose renderer should emit something like:

```text
.system2/dist/goose/
  system2.recipe.yaml
  agents/
    repo-governor.recipe.yaml
    requirements-engineer.recipe.yaml
    design-architect.recipe.yaml
    executor.recipe.yaml
    test-engineer.recipe.yaml
    ...
  run-system2.sh
  README.md
```

The orchestrator recipe would contain the System2 top-level workflow. Each role can become either:

```text
1. a recipe-based Goose subagent, when the role maps cleanly; or
2. a section of orchestrator instructions, when subagent behavior is not needed.
```

A generated `run-system2.sh` is reasonable here:

```bash
goose recipe validate .system2/dist/goose/system2.recipe.yaml
goose run --recipe .system2/dist/goose/system2.recipe.yaml
```

That is a good use of bash: a **thin target launcher**, not the abstraction boundary.

## Do not make bash the main abstraction

The bash-script idea is attractive because it feels universal, but it is too low-level to be the primary System2 interface.

Bash can launch things. It cannot faithfully represent:

```text
role semantics
delegation rules
gate state
artifact requirements
tool permissions
hook behavior
subagent availability
MCP configuration
profile composition
capability degradation
```

So I would use bash only as generated glue:

```text
Claude:
  native plugin UX remains primary.
  optional generated installer/debug scripts are secondary.

Pi:
  optional install/run script for generated package.

Goose:
  generated run-system2.sh is useful because Goose recipes are CLI-runnable.
```

The compiler should own semantics. Bash should only invoke rendered artifacts.

## The capability matrix is essential

The most important technical device is a target capability matrix.

For each target, track support like this:

| System2 capability                      |          Claude Code |                       Pi |                      Goose |
| --------------------------------------- | -------------------: | -----------------------: | -------------------------: |
| Project-level orchestrator instructions |               native |                   native |                     native |
| Role/subagent definitions               |               native |          partial/adapted |             native/adapted |
| Slash commands                          |               native |                  adapted |                    adapted |
| Skills                                  | native Claude skills |         native Pi skills |   recipe/extension pattern |
| Hook-based path safety                  |               native | extension/sandbox needed |            target-specific |
| Tool allowlists                         |               native |  advisory/sandbox needed | extension/config dependent |
| MCP servers                             |    native/configured |          target-specific |  native/extension-oriented |
| Profiles                                |       System2-native |           System2-native |             System2-native |
| Overlay composition                     |       System2-native |           System2-native |             System2-native |

Then the compiler emits warnings into the lock file:

```json
{
  "target": "pi",
  "warnings": [
    {
      "capability": "permission_allowlists",
      "support": "advisory",
      "message": "Pi target cannot enforce Claude-style permissionMode/tool allowlists without sandboxing or an extension."
    }
  ]
}
```

This matches the spirit of your current compose behavior, where some contributions are validated and surfaced but not necessarily activated silently. ([GitHub][3])

## Overlay packages should stay mostly single-source

Overlay authors should not have to write:

```text
Claude overlay
Pi overlay
Goose overlay
```

They should write one System2 overlay.

The overlay manifest can remain mostly as-is, with one optional escape hatch:

```json
{
  "targets": {
    "claude": {
      "extra_hooks": []
    },
    "pi": {
      "requires_extension": true
    },
    "goose": {
      "recipe_extensions": []
    }
  }
}
```

But this should be rare. The default path should be:

```text
overlay contribution -> System2Graph -> all target renderers
```

Target-specific files can live under:

```text
targets/claude/
targets/pi/
targets/goose/
```

But the compiler should ignore irrelevant target directories. That lets advanced overlays exploit harness-specific affordances without forcing every overlay to become a multi-harness package.

## Profiles should be harness-neutral

Profiles are collections of overlays. They should not become “Claude profiles” or “Goose profiles.”

The profile should mean:

```text
Use these overlays, in this order, with this System2 core version.
```

Then users can compile the same profile to different harnesses:

```bash
system2 compile --profile backend-review --target claude
system2 compile --profile backend-review --target goose
system2 compile --profile backend-review --target pi
```

For backward compatibility, keep Claude’s existing command:

```text
/system2:compose --profile backend-review
```

Internally, that can eventually be equivalent to:

```bash
system2 compile --profile backend-review --target claude
```

## Migration path I would use

### Phase 1: freeze Claude behavior

Before adding Pi or Goose, create golden tests around the current Claude output:

```text
input:
  core System2
  one overlay
  one profile

expected:
  CLAUDE.md
  .claude/agents/*.md
  spec/overlay-manifest.lock
  warnings
```

This prevents the compiler refactor from damaging the existing UX.

### Phase 2: extract compose from render

Refactor the current composer into:

```text
compose_to_graph()
render_claude()
```

Initially, `render_claude()` should reproduce current behavior.

### Phase 3: add target capability model

Add:

```text
targets/claude/capabilities.json
targets/pi/capabilities.json
targets/goose/capabilities.json
```

Every renderer must report:

```text
native
adapted
advisory
unsupported
```

No silent dropping.

### Phase 4: implement Goose first

I would probably target Goose before Pi because Goose recipes and subagent recipes give you a clearer mapping from System2 roles to target artifacts. Goose’s recipe model is explicitly portable and CLI-runnable. ([Goose Docs][6])

### Phase 5: implement Pi second

Pi is also viable, but you will need to decide whether System2 on Pi is mostly:

```text
context + skills + prompt templates
```

or whether you want a richer Pi extension that gives you stronger command and policy behavior. Pi’s extension system is powerful, but permission semantics will not be identical to Claude’s. ([GitHub][4])

## My direct answer to your fork in the road

You asked whether you should:

```text
A. carry support for multiple harnesses in all packages
B. make a compiler package that translates concepts and exposes bash scripts
```

I would choose **B, with one modification**:

> Build a compiler/renderer package, but do not make bash the main interface. Make bash only a generated convenience layer for targets that need it.

The resulting philosophy:

```text
Claude Code remains the reference UX.
System2 concepts become harness-neutral.
Overlays remain mostly single-source.
Profiles remain harness-neutral.
Compiler emits target artifacts.
Renderers are allowed to be lossy, but never silently lossy.
Claude should not be reduced to the lowest common denominator.
```

A compact architecture would be:

```text
System2 Core
  owns roles, gates, spec artifacts, orchestration semantics

Overlay
  owns additive semantic contributions

Profile
  owns ordered overlay sets

Compiler
  owns graph construction, validation, conflict detection, lock files

Target renderers
  ClaudeCodeRenderer
  PiRenderer
  GooseRenderer

Generated artifacts
  Claude: CLAUDE.md, .claude/agents, hooks, plugin skills
  Pi: AGENTS.md/.pi/SYSTEM.md, skills, prompts, optional extension
  Goose: recipe.yaml, subagent recipes, extension config, run script
```

That gives you a clean answer to future harnesses too. You do not need to ask, “How do I port every overlay to Harness X?” You ask:

```text
What capabilities does Harness X expose,
and how faithfully can it render the System2Graph?
```

That is the right abstraction boundary.

[1]: https://raw.githubusercontent.com/DeliberateCode/System2/main/README.md "raw.githubusercontent.com"
[2]: https://raw.githubusercontent.com/DeliberateCode/System2-OverlayTemplate/main/README.md "raw.githubusercontent.com"
[3]: https://raw.githubusercontent.com/DeliberateCode/System2/main/plugin/skills/compose/SKILL.md "raw.githubusercontent.com"
[4]: https://github.com/earendil-works/pi/tree/main/packages/coding-agent "pi/packages/coding-agent at main · earendil-works/pi · GitHub"
[5]: https://github.com/earendil-works/pi "GitHub - earendil-works/pi: AI agent toolkit: unified LLM API, agent loop, TUI, coding agent CLI · GitHub"
[6]: https://goose-docs.ai/docs/guides/recipes/session-recipes "Reusable Recipes | goose | Your open source AI agent"
[7]: https://goose-docs.ai/docs/guides/context-engineering/subagents "Subagents | goose | Your open source AI agent"
