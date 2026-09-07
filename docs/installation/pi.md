# Pi publication status

The Pi channel is **not published**. No npm installation command is available, and the
package name must not be treated as currently installable. For the other harnesses, see the
[Installation and Updating](../installation.md) index.

## Validated candidate

The current generated package candidate has non-skipping native acceptance against **Pi
0.85.1**. That evidence covers:

- package discovery of the `delegate` and `system2-init` commands;
- `/delegate design-architect` through Pi's public RPC command path;
- persistence and reconstruction of the selected role and its generated prompt across reload;
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

The candidate contains the compiler-generated **base workflow**: 13 detailed role prompts,
an in-session `/delegate <role>` switch, the orchestrator context, three System2 skills, and a
Pi extension for bounded safety gates. Delegation changes the role contract in the same
session; it does not create an isolated subagent.

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
- `enforce-lease` is adapted/partial. It gates structured write/edit targets and supported
  literal redirection/`tee` targets, but it is not a general shell parser.
- `budget`, formatting, and type-checking are advisory and do not gate execution.
- Empty-scope roles fail closed for writes, and malformed persisted role state restores a
  restrictive read-only prompt.

The generated [Pi package README](../../distributions/pi/README.md) and
`system2.pi.lock.json` contain the complete current limitations and per-capability report.

## Updating and removal

Not applicable until publication. Installation, updating, and removal instructions will be
added only after the package is published and release provenance is recorded.
