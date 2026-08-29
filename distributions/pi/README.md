# @deliberatecode/pi-system2

GENERATED npm package — do not hand-edit. Regenerate via `python3 compiler/tools/regen_all.py` in the DeliberateCode/System2 repo.

System2 is a deliberate-reasoning workflow for the [Pi coding agent](https://github.com/earendil-works/pi-coding-agent): a native safety gate (block dangerous shell commands, protect sensitive paths, enforce per-role write leases), a 13-role `/delegate` orchestrator, and the System2 skills and prompts. The package carries no install scripts and no dependencies.

## Install

```
pi install npm:@deliberatecode/pi-system2
```

Pi auto-discovers the bundled extension (`extensions/system2.ts`), skills, and prompts from the package. The native safety gate is active as soon as the extension loads — there is no separate enable step.

## Materialize project files

The package ships project-level context files (`AGENTS.md`, `.pi/SYSTEM.md`, and the fidelity lock `system2.pi.lock.json`) that are not Pi package component types, so they are not discovered automatically. Materialize them into the current project with the bundled command:

```
/system2-init
```

Re-running is idempotent: a file already byte-identical to the package payload is skipped, so a second run is a zero-diff no-op. A file you have modified locally is left untouched and reported; pass `--force` to overwrite it (the exact file being replaced is printed before it is written). Paths outside the project root are refused, and unmanaged files are never touched.

## What this package ships (and what it does NOT)

This package ships the **base 13-role workflow** — the general-purpose spec-driven pipeline composed with an empty overlay set — **plus three utility skills**, `system2-codex`, `system2-gemini`, and `system2-stateless-loop`, each requiring its own external CLI on PATH.

Overlays and profiles for per-project role customization are a **compiler-path** feature. They are NOT available through this npm package: applying overlays requires cloning [DeliberateCode/System2](https://github.com/DeliberateCode/System2) and running the System2 compiler CLI (`system2 compile --target pi …`) against your overlays. This package is the precompiled base emission; it contains no compiler and reads no overlay input at install time or at runtime.

## Utility skills

Three adapted second-opinion skills ship alongside the 13-role workflow, each requiring its own external CLI on PATH. If the CLI is missing, the skill stops and reports the missing prerequisite rather than improvising a substitute.

- `system2-codex` — run a prompt through OpenAI's Codex CLI (`codex exec`) non-interactively for a second opinion or code review from a fresh Codex instance. This spawns a NEW non-interactive `codex exec` subprocess — a fresh Codex instance with none of this session's context. It is a second opinion from a clean slate, not a fork of the current session. Requires the OpenAI Codex CLI (`codex`) on PATH.
- `system2-gemini` — run a prompt through Google's Antigravity CLI (`agy`) non-interactively for a second opinion or code review from a fresh instance. Requires Google's Antigravity CLI (`agy`) on PATH.
- `system2-stateless-loop` — run an instruction in a stateless subprocess loop using `claude -p` until the task reports STATUS: CLEAN or max iterations are reached. Requires the Claude Code CLI (`claude`) on PATH — required even though this host is not Claude Code.

**Known Pi limitation.** The System2 Pi extension's protect-sensitive gate scans the ENTIRE bash command, with no override, and Pi has no permission prompt to bypass it — so a prompt containing a token like `credentials` or `secrets` is hard-blocked before the CLI runs. Workaround: reword the prompt to avoid those tokens. This is the gate doing its declared job, not a bug — stated here so a block is never a surprise.

See `docs/installation/claude-code.md`'s utility-skill disambiguation for how the Claude-channel `codex` skill relates to this package's `system2-codex` skill — not restated here.

## Enforcement fidelity

The Pi extension is a NATIVE gate for shell commands, file writes, and sensitive-path reads: it registers Pi's `tool_call` handler and blocks in-process. `/delegate` is an in-session role switch (honestly reported as `adapted`, not a native isolated sub-session). See `system2.pi.lock.json` for the per-capability fidelity report and the FIDELITY banner.

## License

MIT — see `LICENSE`.
