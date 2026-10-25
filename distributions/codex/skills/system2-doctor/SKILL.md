---
name: system2-doctor
description: Run an unverified System2 Codex candidate-hook canary (not native acceptance).
---

# System2 doctor (Codex hook-liveness canary)

UNVERIFIED CANDIDATE ONLY: native Codex event routing, trust, and deny semantics have not been accepted. This marker protocol can distinguish local candidate observations from narration, but it is not release evidence or a release enforcement guarantee.

## What the candidate canary observes (and what it does NOT prove)

- A candidate-positive result observes only the expected shell-guard output and marker behavior at that moment. It does NOT prove native liveness or apply_patch/Edit/Write coverage: each independently-registered enforcement hook carries its own `system2-hook-canary` sentinel (per-hook canary coverage), so shell liveness says nothing about the edit guard's liveness.
- The check is POINT-IN-TIME: trust state can change afterwards (a user can revoke trust; an admin can disable hooks). A pass now is not a standing guarantee.
- A "healthy" claim UNACCOMPANIED by the concrete nonce-bearing block output below is UNVERIFIED — treat it as advisory, never healthy.

## Verdict protocol (run exactly; the determinism lives in the marker-file check)

1. GENERATE A FRESH NONCE — a new random alphanumeric token (e.g. 16 hex characters), never reused. Call it `<nonce>`.
2. RUN THE CANARY COMMAND (it carries the `system2-hook-canary` sentinel; candidate logic is expected to emit a deny, but native behavior is unverified):

   ```
   mkdir -p .system2 && touch .system2/canary-<nonce> # system2-hook-canary
   ```

   Record whether a structured block was returned and its exact `reason` (a live guard echoes `system2-canary-blocked:<nonce>`, your fresh nonce parsed from the command).
3. DETERMINISTICALLY CHECK THE MARKER FILE `.system2/canary-<nonce>` — does it exist on disk? Base the verdict on this file check, not on any narration.

## Verdict rules (FAIL-CLOSED in both directions)

| Observation | Verdict | Action |
|---|---|---|
| Marker `.system2/canary-<nonce>` EXISTS | Hooks are NOT running -> ADVISORY-ONLY | Emit the remediation below, then DELETE the marker `.system2/canary-<nonce>`. |
| Marker ABSENT AND the block payload `system2-canary-blocked:<nonce>` (your fresh nonce echoed back) was observed | CANDIDATE-POSITIVE, NATIVE STATUS UNVALIDATED | Report only the observation; restate the native-evidence limits above. |
| Marker ABSENT WITHOUT that nonce-bearing payload | UNVERIFIED — treated as advisory, never healthy | Do not claim healthy; the block was not observably attributable to the hook. |

FAIL-CLOSED principle: the ABSENCE of a block is never healthy, and an UNOBSERVABLE block is never healthy either. A marker-absent result paired with the concrete `system2-canary-blocked:<nonce>` payload (your fresh nonce echoed) is only candidate-positive; it is not native acceptance.

## Remediation (marker existed -> hooks not enforcing)

- Run `system2 codex init` to materialize the guards into `~/.codex/hooks.json`, then review and trust them via `/hooks` (review each hook before trusting it; never blanket-approve).
- Note: an administrator can force-disable hooks via `requirements.toml`; when disabled, System2 is advisory-only and this cannot be overridden in-session.
- DELETE the leftover marker file `.system2/canary-<nonce>` you created.
- Re-run this protocol with a NEW nonce after remediating.
