# Installation and Updating (Pi)

System2 is available as a Pi package. It installs the `/system2` orchestrator, `/system2-doctor`, and 13 specialist role skills.

## Install

```sh
pi install git:github.com/DeliberateCode/System2
```

Restart Pi or run `/reload`. No separate System2 configuration is required; continue using Pi's normal model, tool, trust, and extension settings.

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

Use `/system2-doctor` for a read-only summary of the current workflow state.

## Runtime boundary

Pi applies System2 specialist role skills in the current session rather than isolated subagents. System2 does not install an extension, hooks, or a permission layer; Pi's active tools, project trust, and user configuration remain authoritative.

Pause any other agent before allowing Pi to edit the same checkout.

## Update

```sh
pi update git:github.com/DeliberateCode/System2
```

You can also update all installed Pi packages with:

```sh
pi update --extensions
```

## Remove

```sh
pi remove git:github.com/DeliberateCode/System2
```

Existing project specs remain project files and are not deleted automatically.
