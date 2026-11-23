# @deliberatecode/system2

System2 provides a deliberate, spec-driven engineering workflow for Pi.

## Install

Install directly from GitHub:

```sh
pi install git:github.com/DeliberateCode/System2
```

Use Pi 1.6.0 or newer. Restart Pi or run `/reload`; System2 sets up its specialist automatically without overwriting an existing definition. Start work with `/system2 <your task>`. Use `/system2-setup` only to check or retry specialist setup.

Use `/system2-doctor` for a read-only summary of the current workflow state. `/system2-compose` and `/system2-profile` use the bundled compiler and require Python 3.11 or newer. See [overlays and profiles](https://github.com/DeliberateCode/System2/blob/main/docs/overlays/codex-pi.md).

## Runtime boundary

System2 uses a configured `subagent` tool or loads the bundled official Pi example if none is registered. Each specialist runs in a separate Pi process and receives its role skill; the parent session retains approvals. This separates context, not permissions. System2 adds no custom enforcement layer.

## Update or remove

If you previously ran `/system2-init`, follow the [preview migration steps](https://github.com/DeliberateCode/System2/blob/main/docs/installation/pi.md#migrating-projects-initialized-with-the-pi-preview) to retire the old project prompt and any legacy local System2 extension before updating. Restart Pi or run `/reload` after updating.

```sh
pi update git:github.com/DeliberateCode/System2
pi remove git:github.com/DeliberateCode/System2
```

## License

MIT — see `LICENSE`.
