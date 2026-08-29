# System2 Compiler — Security Review and Threat Model

> Security findings are named by behavior rather than numbered. This keeps code,
> tests, and documentation understandable after any planning document is replaced.

## Scope

The review covers:

- untrusted overlay manifests and contribution files;
- profile and lock parsing;
- path containment and symlink handling;
- neutral IR construction;
- Claude, Pi, and Codex rendering;
- generated hook and extension behavior;
- atomic project writes and lifecycle cleanup;
- generated bundle, package, and provenance tooling;
- CI validator and publishing workflows.

The compiler is local and single-shot. It has no service authentication model, remote API,
or runtime telemetry.

## Data Classification

| Data | Trust | Handling |
|---|---|---|
| Overlay manifests and content | Untrusted third-party input | Parse, validate, contain, scan, and render as data; never execute |
| Profile store and target locks | User-controlled local input | Validate shape and paths before use |
| Plugin schemas, anchor map, templates, versions | Installer-owned configuration | Parse as data; source of allowed vocabulary and base content |
| Generated source, Markdown, JSON, and YAML | Derived output | Escape target syntax; write only under approved roots |
| Capability and provenance metadata | Non-secret derived metadata | Keep factual; may include source paths and hashes |

The compiler does not read credentials intentionally. Local absolute paths may appear in
locks or diagnostics and should be treated as environment metadata when sharing artifacts.

## Assets

- Integrity of the plugin, compiler, overlay source trees, and files outside the target
  project.
- Integrity of pre-existing project files during partial failures.
- Correctness of blocking controls and fidelity claims.
- Integrity of the user's real profile, Pi, and Codex configuration.
- Integrity and reproducibility of generated distributions.
- Process safety: untrusted content must never become arbitrary code execution.

## Threat Actors

- A malicious overlay author crafting traversal paths, symlinks, injected prompt text,
  executable hooks, malformed nesting, or ordering cycles.
- A local attacker or accidental edit poisoning profiles, locks, generated output, or user
  hook configuration.
- A supply-chain attacker targeting unpinned actions, validators, package dependencies, or
  generated artifacts.
- A maintenance regression that silently weakens a guard or overstates enforcement.

## Trust Boundaries

```text
untrusted overlays + user profiles + existing locks
                       |
                       v
       validation, containment, conflict checks, injection scan
                       |
                       v
             neutral immutable System2Graph
                       |
                       v
        target renderer + atomic project writer
                       |
                       v
 project artifacts / optional user-approved Codex hook installation
```

Target validators and generated hook runtimes form additional boundaries. Their absence or
unobservable trust state must never be interpreted as a healthy result.

## Abuse Cases and Defenses

### Path traversal or absolute-path escape

A manifest points a content, agent, or hook path outside its overlay. Validation rejects
absolute paths and parent traversal, resolves real paths, and requires the result to remain
under the overlay root.

### Symlink escape

A path lexically inside an overlay resolves through a symlink to another tree. Realpath
containment rejects it before reading or copying.

### Writing into source or installation trees

Composition refuses a project path inside the base/plugin tree. Backends also reject writes
into overlay source trees and normalize every planned destination under `project_path`.

### Prompt injection in contribution text

Contribution text is never evaluated by the compiler. It is rendered as data and scanned for
suspicious instruction patterns. Findings are surfaced as warnings. Because scanning is
pattern-based and advisory, users must still review third-party overlay content.

### Malicious overlay hook

Hook validation rejects banned process-execution modules, dynamic imports, `exec`, `eval`,
network modules, unsafe shell calls, and collisions with installer-owned safety hook names.
The compiler never executes overlay hook code during composition.

### Ordering or nesting denial of service

Duplicate contribution identities and ordering cycles are detected deterministically.
Recursive collection remains bounded by practical manifest size; deeply nested adversarial
JSON can still consume parser or recursion resources and should be capped if manifests become
remote or unbounded.

### Partial-write corruption

Writers back up existing files and generated directories, write through temporary files, and
restore prior state on any exception. Newly created files and directories are removed during
rollback.

### Generated-source injection

Pi TypeScript and Codex JavaScript use JSON-compatible string escaping for all IR-derived
values. Generated regular expressions come from backend-owned constants or validated scope
patterns rather than raw executable source fragments.

### Command-gate bypass

Generated guards normalize alternate event shapes, command strings and argv arrays, chained
commands, patch payloads, and path-bearing keys. Input size caps and watchdogs bound matching.
Malformed input, timeout, oversize, or internal errors fail closed.

### Block-everything regression

Blocking suites include benign commands, reads, and in-scope writes. A gate that blocks every
input fails those negative controls.

### Inactive or untrusted Codex hooks

Codex hook enforcement is inactive until guards are materialized and explicitly trusted.
Manifest, README, orchestrator, and lock surfaces state this consistently. At rest, Codex
capabilities do not claim native or enforced status. Doctor cannot observe trust and therefore
directs users to a marker-file canary with a fresh nonce.

### User hook configuration clobbering

Codex initialization refuses a pre-existing non-System2 `hooks.json` unless `--force` is
provided. Forced installation creates a timestamped backup and records enough state for
uninstall to restore only what System2 replaced.

### Real-home mutation during tests or emit

Tests use hermetic home directories. Pi emission writes only under the target project. Codex
user-scope initialization exposes an explicit home-directory test seam. Tests snapshot the
real user stores where necessary and assert they remain unchanged.

### Stale or hand-edited generated artifacts

CI regenerates distributions and the vendored bundle from source. Source fingerprints catch
staleness; recomputing the target subtree hash catches hand edits. Mutation tests prove both
directions fail.

### Publishing from the wrong revision

Publishing is manually dispatched, restricted to the protected main environment, checks the
exact dispatched commit, regenerates freshness, uses pinned tooling, and publishes through
OIDC trusted publishing with provenance. No long-lived npm credential is stored.

## Vulnerability Checklist

### Input validation

- JSON shapes, enums, names, versions, paths, hook matchers, and frontmatter are validated.
- Unknown capability names warn rather than disappearing.
- Missing descriptor entries fail emission.
- Untrusted content is not deserialized through pickle or dynamically imported.

### Authorization and least privilege

- Filesystem containment is the primary authorization boundary.
- CI defaults to read-only contents permission.
- Publishing alone requests an OIDC token and runs behind environment protection.
- User-wide Codex hook changes require an explicit command and trust action.

### Secrets and privacy

- No compiler code reads or logs credentials intentionally.
- Secret scanning covers source, distributions, and vendored output.
- Shared locks and diagnostics may disclose local paths; sanitize them before public sharing
  when path privacy matters.

### Dependencies and network

- Compiler product code and generated-hook tooling use the Python or Node standard library.
- Compiler product modules perform no network calls or telemetry.
- External validators and CI actions are pinned and installed only in build/test workflows.
- The Pi package declares no scripts, runtime dependencies, dev dependencies, or postinstall.

### Supply chain

- Generated output is derived from one regeneration command.
- Packaged Codex hook data is mirrored from the emitted reference and checked for equality.
- Bundle and distribution provenance records source fingerprints and generator identity.
- Vendored profile and hook-security modules have pin/equivalence tests against their source.

## Findings

### Path and symlink containment is preserved

The front end rejects traversal, absolute paths, and realpath escapes before reads. Backends
recheck destination containment before writes. Keep both layers as defense in depth.

### Untrusted text is never dynamically executed

Manifest and contribution content flows through JSON parsing, validation, immutable graph
data, escaping, and rendering. No product path uses `eval`, `exec`, dynamic import, pickle,
or shell interpolation of untrusted text.

### Injection scanning remains advisory

The scanner raises visible warnings but does not prove content safe and does not block by
itself. This matches current composition behavior. Third-party overlays still require review.

### Absolute paths can appear in local artifacts

Source paths support drift detection and from-lock lifecycle operations but can reveal local
usernames or directory layout. This is a low-severity privacy consideration, not a code
execution issue.

### Deeply nested JSON is not explicitly capped

Recursive collectors can reach Python recursion limits on adversarial nesting. Current local,
bounded manifests make this low severity. Add depth and size limits before accepting remote or
unbounded manifests.

### Codex enforcement is conditional and partial

User trust gates activation, and hooks cover shell and edit-related surfaces rather than every
Codex tool. Honest adapted status, shared trust text, and the canary protocol are mandatory.

### Pi formatting and type checking are advisory

Pi lacks the required post-edit interception for native formatting and type checking in this
implementation. They remain explicit advisory instructions, not native claims.

### Generated-artifact freshness is security-relevant

A stale bundle can omit tightened validation or guards even when source is correct. Freshness
and tamper checks therefore remain merge and release gates rather than optional maintenance.

## Required Controls Before Release

- Core and compiler test suites pass without required-validator skips in CI.
- End-to-end Pi and Codex blocking corpora pass with benign negative controls.
- Capability descriptor and lock status agree for every emitted capability.
- Codex trust and coverage text is identical across all intended surfaces.
- Atomic rollback and path containment tests pass.
- Bundle and distributions regenerate cleanly with no stale or tampered bytes.
- Secret scan and package supply-chain checks pass.
- Publishing dry-run succeeds from the exact protected revision.

## Defense in Depth

- Keep input-size caps and watchdogs in generated hooks.
- Keep backend destination guards even when the front end has already checked paths.
- Keep mutation tests for fidelity messages, capability records, and freshness checks.
- Prefer exact allowlists and content signatures over broad path-prefix checks.
- Add explicit JSON depth and total-size limits if input scale or trust changes.
- Consider redacting local paths in export-oriented diagnostics while preserving internal
  locks needed for lifecycle operations.

## Residual Risk and Monitoring

- Prompt injection cannot be eliminated by pattern scans; monitor warning quality and overlay
  review practices.
- Host APIs can change tool event shapes or hook delivery. Pin validators, exercise realistic
  captured envelopes, and update normalization tests when hosts change.
- User trust can change after a successful canary. Report liveness as point-in-time only.
- Hand-pinned package and plugin versions require deliberate release review until version bumps
  are mechanically tied to behavior changes.
- Local filesystem and process behavior varies by platform; retain Linux and macOS CI coverage.

## Verdict

The compiler preserves the reference target's principal safety properties and introduces no
new network or third-party runtime dependency. Pi provides real pre-tool blocking through its
generated extension. Codex provides conditional, partial enforcement with explicit trust and
coverage limitations. Release remains acceptable only while path containment, atomic rollback,
honest degradation reporting, generated-hook fail-closed behavior, and artifact freshness stay
machine-enforced.
