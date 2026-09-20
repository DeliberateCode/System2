# Pi publication status

The Pi channel is **not published**. No npm installation command is available, and the
package name must not be treated as currently installable. For the other harnesses, see the
[Installation and Updating](../installation.md) index.

## Validated candidate

The current generated package candidate has non-skipping native acceptance against **Pi
0.85.1**. That evidence covers:

- package discovery of the `delegate`, `system2-status`, and `system2-init` commands;
- Orchestrator-first startup through Pi's public RPC and SDK paths;
- persistence and branch-local reconstruction of gate/delegation state across reload;
- dispatch contracts for all 13 roles through ephemeral subprocess invocations;
- a real Pi JSON-mode child, driven by a deterministic test provider, executing the
  terminating structured completion tool;
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

## Current scope

The candidate contains the compiler-generated **base workflow**: a persistent parent
Orchestrator, 13 detailed specialist prompts, model-callable subprocess delegation and gate
request tools, human status/recovery commands, three System2 skills, and bounded safety
gates. `/delegate <role>` is only a human convenience request to the parent; it never changes
the parent into that role. Each ordinary specialist dispatch starts an ephemeral Pi process
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
  the delegated task scope for structured write/edit targets and supported literal
  redirection/`tee` targets, but it is not a general shell parser. The parent active
  tool set excludes bash/write/edit and its safety handler blocks them if re-enabled.
- `budget` is a bounded specialist self-report checked against the contract, not a computed
  repository diff; formatting and type-checking remain advisory.
- Empty task/role scopes fail closed for supported writes. Task scopes and artifact-input
  digests must be part of the latest human gate approval before delegation.
- Invalid or interrupted branch state blocks mutation/delegation until human-confirmed
  `/system2-recover`. Updating/regenerating the extension intentionally changes its provenance
  digest, so an existing workflow requires this explicit recovery after an extension upgrade.

The generated [Pi package README](../../distributions/pi/README.md) and
`system2.pi.lock.json` contain the complete current limitations and per-capability report.

## Updating and removal

Not applicable until publication. Installation, updating, and removal instructions will be
added only after the package is published and release provenance is recorded.
