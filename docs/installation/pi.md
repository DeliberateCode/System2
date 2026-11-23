# Installation and Updating (Pi)

System2 is available as a Pi package. It installs the workflow, overlay and profile commands, and 13 specialist role skills. Use Pi 1.6.0 or newer. Overlay and profile commands also need Python 3.11 or newer on your `PATH`.

## Install

In your terminal:

```sh
pi install git:github.com/DeliberateCode/System2
```

Restart Pi or run `/reload`. The package sets up its specialist automatically: it creates `agents/system2-specialist.md` under your Pi agent directory (`~/.pi/agent` by default) without replacing an existing file. It reuses a compatible `subagent` tool or loads the bundled [official Pi example](https://github.com/earendil-works/pi/tree/v1.6.0/packages/coding-agent/examples/extensions/subagent). `/system2-setup` is only for checking or retrying this step if Pi reports a problem.

Verify the installation with:

```sh
pi list
```

## Run

Open your project and start the workflow with:

```text
/system2 <your task>
```

For an existing System2 project, ask it to read the current `spec/` files and diff, then continue the next unfinished task.

Use `/system2-doctor` for a read-only summary of the current workflow state. `/system2-compose` and `/system2-profile` work with the bundled compiler. See [examples for overlays and profiles](../overlays/codex-pi.md).

## Runtime boundary

The parent session keeps user approvals and dispatches each specialist through the `subagent` tool. The official extension starts a separate Pi process with the task and role skill. Separate context is not a permission sandbox: the child uses Pi's normal subprocess configuration, not necessarily the parent's per-session tool selection. System2 does not add hooks or a permission layer. If delegation is unavailable, the workflow pauses rather than silently doing specialist work in the parent.

Pause any other agent before allowing Pi to edit the same checkout.

## Migrating projects initialized with the Pi preview

If you previously ran `/system2-init` or emitted a project with the old extension-based compiler, update alone is not enough. Pi can still load the old `.pi/SYSTEM.md` as a system prompt even though the updated package no longer provides tools such as `system2_delegate`. The new package uses the official `subagent` tool instead, and its `/system2` prompt does not replace a project system prompt.

In each affected project, close Pi and review the following paths before updating:

- `.pi/SYSTEM.md`: archive it if it is the old System2 prompt (look for references to `system2_delegate` and `system2_request_gate_approval`). If it contains your own instructions, keep them separately and restore only the project-specific guidance, without the retired tool requirements. Leave unrelated system prompts alone.
- `system2.pi.lock.json`: archive the old preview lock with the generated prompt. The new installed package does not need a project lock. If you previously compiled a project locally, the old lock's ownership inventory can help identify other generated artifacts, including obsolete prompts and skills that can shadow the new package. Inspect each file before moving it; not every listed file was necessarily installed.
- `.pi/extensions/system2.ts`: archive this file if it is the legacy System2 project extension. Do not remove other extensions or caller-owned settings.

Move only the reviewed legacy files to a backup **outside Pi's discovery paths**, preserving local edits. Do not delete `.pi/`, `AGENTS.md`, or `spec/`. If the old preview was installed separately from the new package source, remove its entry with `pi remove <old-source>` using the exact source shown by `pi list`. After updating, restart Pi or run `/reload` to set up the new specialist automatically. Use `/system2-doctor` to check the existing workflow state before resuming.

## Update

```sh
pi update git:github.com/DeliberateCode/System2
```

Restart Pi or run `/reload` to pick up the updated package. You can also update all installed Pi packages with:

```sh
pi update --extensions
```

## Remove

```sh
pi remove git:github.com/DeliberateCode/System2
```

The package's extension is removed with the package. If you no longer need the setup-created `agents/system2-specialist.md` in your Pi agent directory, review it and remove or archive it separately. Existing extensions and project specs are not changed.
