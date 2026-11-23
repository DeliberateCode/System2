# Installation and Updating (Codex)

System2 is available as a Codex plugin. It installs the `$system2` orchestrator, `$system2-doctor`, and 13 specialist role skills.

## Install

```sh
codex plugin marketplace add DeliberateCode/System2
codex plugin add system2@system2-marketplace
```

Restart Codex. No separate System2 configuration is required; continue using Codex's normal sandbox, approval, and tool settings.

Verify the installation with:

```sh
codex plugin list --json
```

## Run

Open your project and invoke `$system2` with your task. For an existing System2 project, ask it to read the current `spec/` files and diff, then continue the next unfinished task.

Use `$system2-doctor` for a read-only summary of the current workflow state.

## Runtime boundary

Codex applies System2 specialist role skills in the current session rather than isolated subagents. System2 does not install hooks or modify Codex configuration; Codex's sandbox, approval policy, and tool permissions remain authoritative.

Pause any other agent before allowing Codex to edit the same checkout.

## Update

```sh
codex plugin marketplace upgrade system2-marketplace
codex plugin remove system2@system2-marketplace
codex plugin add system2@system2-marketplace
```

## Remove

```sh
codex plugin remove system2@system2-marketplace
```

To remove the marketplace as well:

```sh
codex plugin marketplace remove system2-marketplace
```

Existing project specs remain project files and are not deleted automatically.
