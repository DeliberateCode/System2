# Runbook: Codex marketplace-resolution + hook-trust smoke test

**Owner:** Release owner (executed with the `codex` CLI).
**When:** once, before release. Confirms marketplace resolution, advisory-state
honesty messaging, and both directions of the canary marker-file protocol on a real
Codex install.
**Records to:** `spec/codex-smoke-record.md` (workspace-root cycle home). Paste every command
and its output there as you go.

This runbook is step-complete: run it top to bottom without further context. It exercises
BOTH verdict directions of the `system2-doctor` canary — pre-trust (marker exists → ADVISORY)
and post-trust (marker absent + nonce-bearing block payload → active).

---

## 0. Preconditions

1. The `codex` CLI is installed and on PATH. Confirm loudly:
   ```
   codex --version
   ```
   Record the exact version. Do NOT guess an install command — if `codex` is missing, stop
   and install it out of band, then record how.
2. The Phase 2 commits (including `distributions/codex/` and root
   `.agents/plugins/marketplace.json`) are reachable by the `codex` CLI at
   `DeliberateCode/System2`. Confirm with the release owner which ref the CLI will resolve
   (branch vs default branch). Pushing to the already-public Core repo is routine development,
   not an outward "action" — the release owner confirms the ref before you start.
3. The System2 compiler CLI is available for the hook-materialization step's `system2 codex init` (the console script
   `system2`, or `python3 -m system2_compiler.cli` from a checkout). Confirm:
   ```
   system2 codex init --help    # or: python3 -m system2_compiler.cli codex init --help
   ```
4. Work in a throwaway scratch project directory so nothing you install touches real work:
   ```
   mkdir -p ~/codex-smoke && cd ~/codex-smoke
   ```

---

## 1. Marketplace resolution

```
codex plugin marketplace add DeliberateCode/System2
```

**Observe and record:** does the CLI resolve the root `.agents/plugins/marketplace.json`
(which points at `./distributions/codex`), or does it demand a plugin manifest at the repo
root?

- **If it resolves via `.agents/plugins/marketplace.json`** → record "root pointer
  honored" and continue.
- **If it requires a root plugin** → STOP and do not improvise a fix. Record the exact
  error, then route a corrective amendment to add a root `.codex-plugin/plugin.json`
  whose pointers stay inside `distributions/codex/` with no `..` or absolute paths.
  Resume this runbook after that lands.

Record the marketplace listing:
```
codex plugin marketplace list
```

---

## 2. Install the plugin (skills + advisory messaging)

Install with the fully-qualified `<plugin>@<marketplace>` id (the marketplace name is
`system2`, from the root `marketplace.json`):

```
codex plugin add system2@system2
```

(CLI ≥ 0.139.0 uses `codex plugin add` to install from a marketplace — there is no
`plugin install` subcommand. `add` accepts `--marketplace <name>` as an alternative to the
`@<marketplace>` suffix.)

Record the install output. Confirm it reports the `system2` plugin (name `system2`,
version `0.1.0`) and its skills, and that `codex plugin list` shows it `installed, enabled`.
Confirm no error.

**The plugin delivers the skills and the advisory messaging only.** On current codex-cli,
plugin-bundled hooks are NOT dispatched (`plugin_hooks` is removed/false), so enforcement is
delivered separately through the user-level `~/.codex/hooks.json` config layer — that is
the hook-materialization step (`system2 codex init`). Until that command runs and the hooks are trusted, System2 is advisory-only.

---

## 3. Materialize the enforcement hooks — `system2 codex init` (once, globally)

From the compiler checkout (the console script `system2`, or
`python3 -m system2_compiler.cli`), run the one-time global install:

```
system2 codex init
```

This copies the committed guard scripts from `distributions/codex/user-hooks/hooks/*.js`
into `~/.codex/system2/hooks/` and renders `distributions/codex/user-hooks/hooks.json.tmpl`
into `~/.codex/hooks.json`, substituting `{{SYSTEM2_HOOKS_DIR}}` for the RESOLVED ABSOLUTE
`~/.codex/system2/hooks` — a user-scope hook fires with cwd = the current project, never
`~/.codex`, so the `command` MUST be an absolute path. Confirm, out of band:

```
cat ~/.codex/hooks.json     # each command is: node /<ABS>/.codex/system2/hooks/system2-*.js
ls -l ~/.codex/system2/hooks/
```

Confirm no `{{SYSTEM2_HOOKS_DIR}}` placeholder remains and the `command` paths are absolute.

**Pre-existing `~/.codex/hooks.json` (machine-wide stakes).** If you already have a
non-System2 `~/.codex/hooks.json`, `init` REFUSES and prints a LOUD warning rather than
clobbering it. Re-run with `--force` to write a timestamped `.bak` beside it and install:
```
system2 codex init --force
```
Record which path fired (fresh install vs backup-then-install).

The install is advisory-only until you complete the hook review and trust step. It is idempotent — re-running
`system2 codex init` re-renders identically and preserves any original backup.

---

## 4. Unreviewed-hooks advisory check (before trusting anything)

The whole point: right after `init`, the user-scope hooks are installed but untrusted and
MUST report advisory-only — **nothing may report as enforced.**

1. Surface the install/trust messaging. Confirm the advisory sentence is visible on the
   install surface (manifest `description`) — verbatim:

   > System2 workflows for Codex. NOTE: safety enforcement is INACTIVE until you review and
   > trust the bundled hooks via /hooks; until then System2 runs advisory-only.

2. Inspect the hook trust state:
   ```
   codex /hooks
   ```
   Record it. Confirm the System2 user-scope hooks are listed **installed but pending review**
   (on codex 0.142.5 this shows as `PreToolUse Installed 1 Active 0 Review 1` — installed,
   NOT auto-trusted), and that NO capability reports enforced. Screenshot/paste the state.

3. Run the doctor canary in the untrusted state to prove the fail-closed ADVISORY verdict:
   - In the Codex session, invoke the `system2-doctor` skill.
   - It will (a) generate a fresh nonce `<nonce>`, (b) run the canary command
     `mkdir -p .system2 && touch .system2/canary-<nonce> # system2-hook-canary`,
     (c) deterministically check the marker file.
   - **Expected (pre-trust):** the untrusted hook does NOT run, so the command is NOT blocked
     and the marker file `.system2/canary-<nonce>` **EXISTS on disk**. Verify it yourself,
     out of band:
     ```
     ls -l .system2/canary-*
     ```
   - The doctor must report **ADVISORY-ONLY** (hooks not running), emit the `/hooks`
     remediation, and delete the marker. Confirm the marker was deleted:
     ```
     ls -l .system2/canary-* 2>&1   # expect: no such file
     ```
   Record: marker existed → ADVISORY verdict. This exercises the marker-exists direction.

---

## 5. Review + trust the hooks via `/hooks` (one-time, global)

```
codex /hooks
```

Review each System2 user hook individually — read it before trusting; never blanket-approve.
Trust the System2 hooks, and ensure `features.hooks` is enabled. This one-time `/hooks` trust
is paid once per machine and then applies across ALL projects. Record which hooks you trusted
and the resulting trust state (expect `Active` to advance for the System2 `PreToolUse` hooks).

(If an admin `requirements.toml` force-disables hooks in this environment, enforcement
stays advisory-only and cannot be overridden in-session — note it and skip step 6's
"active" expectation, recording the admin-disabled state instead.)

---

## 6. Post-trust canary doctor (marker absent + modern block reason → active)

Re-run the `system2-doctor` skill now that the user-scope hooks are trusted. To confirm
that the absolute command path works from any project cwd, run this from a second project
directory different from where you ran `init`:

- It generates a NEW fresh nonce `<nonce2>` and runs the canary command carrying the
  `system2-hook-canary` sentinel.
- **Expected (post-trust):** the live shell guard hard-blocks the command BEFORE it runs, so
  the marker file `.system2/canary-<nonce2>` is **ABSENT**, AND Codex surfaces the modern block
  reason (`permissionDecisionReason`) echoing your fresh nonce — for example:

  > The command was blocked by a PreToolUse hook: system2-canary-blocked:<nonce2>

- Verify the marker is absent, out of band:
  ```
  ls -l .system2/canary-<nonce2> 2>&1   # expect: no such file
  ```
- The doctor must report enforcement **ACTIVE for shell hooks (point-in-time)** and restate
  the coverage caveats (shell + apply_patch/Edit/Write only; not WebSearch/other; each hook
  carries its own sentinel; state can change afterwards).

Record: marker absent AND `system2-canary-blocked:<nonce2>` observed in the block reason →
ACTIVE verdict. This exercises the marker-absent plus nonce-payload direction.

**Fail-closed cross-check:** if the marker was absent but you did NOT see the nonce-bearing
`system2-canary-blocked:<nonce2>` payload, the correct verdict is **UNVERIFIED — advisory,
never healthy**. Confirm the doctor does not claim "healthy" without the concrete payload.

---

## 7. Teardown + record

1. Remove the enforcement hooks from `~/.codex` (restores any backup, removes only System2
   artifacts):
   ```
   system2 codex uninstall
   ```
   Confirm `~/.codex/hooks.json` is gone (fresh install) or restored to your original backup
   (the `--force` path), and `~/.codex/system2/hooks/` is removed.
2. Uninstall the plugin from the test environment (CLI ≥ 0.139.0: the verb is `remove`, and
   `marketplace remove` takes the marketplace *name* `system2`, not the `owner/repo` source):
   ```
   codex plugin remove system2@system2
   codex plugin marketplace remove system2
   ```
3. Remove the scratch project directory.
4. In `spec/codex-smoke-record.md`, record: the `codex` version; marketplace outcome
   (root pointer honored vs fallback required); the `system2 codex init` outcome (fresh vs
   `--force` backup); the pre-trust ADVISORY verdict (marker existed); the post-trust ACTIVE
   verdict (marker absent plus modern `permissionDecisionReason` nonce payload); whether the
   absolute hook command fired from another project; confirmation that nothing reported
   enforced before trusting; and an explicit PASS/FAIL line.

**PASS criteria:** marketplace add resolved (or the fallback was routed as an amendment);
`system2 codex init` rendered `~/.codex/hooks.json` with absolute commands (no placeholder);
fresh install reported advisory-only with nothing enforced; both canary directions behaved
fail-closed as above (post-trust block surfaced the modern `permissionDecisionReason` nonce
payload); Codex manifest validated in the real harness.
