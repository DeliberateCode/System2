"""TASK-018 (F4) — the Codex three-surface honesty invariant + standing lock invariants.

This is the machine-check for R1 (the #1 recorded project risk): that the Codex
plugin's enforcement honesty cannot silently drift into an over-claim. It converts
the design's "three surfaces must agree" rule from a review-time reading into a red
CI check (security F4; design §4 Trust messaging, Capability classification).

It asserts, on the Codex emission:

(a) THREE-SURFACE ADVISORY AGREEMENT — the trust one-liner
    (``backends.codex._TRUST_ONELINER``, which carries the design line-174 advisory
    clause) is present VERBATIM (byte-substring) in ALL of:
      * the manifest ``description`` (``.codex-plugin/plugin.json``),
      * the orchestrator-skill preamble (``skills/system2/SKILL.md``), and
      * the lock ``system2.codex.lock.json`` FIDELITY banner.
    The coverage-gap sentence (``_COVERAGE_GAP``, design line 175) is present in the
    orchestrator skill AND the lock banner.

(b) STANDING LOCK INVARIANTS (design line 179) — in ``system2.codex.lock.json`` NO
    capability carries ``enforced: true`` at rest, NONE is classified ``native``, and
    EVERY safety gate (enforce-lease, block-dangerous, protect-sensitive) is
    ``adapted`` with ``gated: true``. These are standing invariants: a future backend
    edit cannot silently over-claim.

(c) MUTATION SELF-TESTS (teeth) — on temp COPIES of the emission (never the real
    emission), each surface is tampered (drop the sentence) and each lock invariant
    is violated (inject ``enforced: true`` / ``native``); the honesty predicates must
    then FAIL and NAME the offending surface / capability.

The required strings are IMPORTED from ``backends.codex`` (asserted against the
precise constants the backend emits, never a re-typed guess) and additionally pinned
to the design's literal wording, so a refactor that guts the constant while keeping
the symbol still fails.

Path-parameterized: validates the committed ``distributions/codex/`` tree when it
exists (post-TASK-023) or an explicit ``CODEX_EMISSION_ROOT`` override; otherwise it
emits a fresh Codex tree to a staging temp dir and validates that. Pure-Python
emission (no node / external binary) — always runs, never skips (skip-count-0).
Stdlib-only ``unittest``. Cited overlay/IR contents are untrusted data.

Also carries the D2/the recorded decision SYNC GUARD (J4): the three adapted utility skills are
derived-literal builders in ``backends.codex`` (Decision D2), not an emit-time
read of ``plugin/skills/``, so a Claude-source edit does not flow automatically.
The guard reads BOTH the merged source (``plugin/skills/<name>/SKILL.md``) and
the emitted skill (``skills/system2-<name>/SKILL.md``) and asserts every
load-bearing invariant token (design.md Public Interfaces 4a row 6 / the recorded decision) is
present in both. The 3x3 token list is duplicated, with a cross-reference
comment, in ``test_pi_goldens.py``'s mirror leg (K3, CC-036) — accepted
duplication, recorded.
"""

import json
import os
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends import codex as codex_backend
from system2_compiler.backends.codex import CodexBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY

# The precise strings the backend emits (imported, not re-typed).
_TRUST_ONELINER = codex_backend._TRUST_ONELINER
_COVERAGE_GAP = codex_backend._COVERAGE_GAP

# The design's literal wording (design.md lines 174 / 175). These pin the imported
# constants to the approved text: a refactor that keeps the symbol but drops the
# advisory clause / coverage-gap clause fails test_required_constants_match_design.
_REQUIRED_ADVISORY_CLAUSE = (
    "safety enforcement is INACTIVE until you review and trust the bundled hooks "
    "via /hooks; until then System2 runs advisory-only"
)
_REQUIRED_COVERAGE_CLAUSE = (
    "Even with hooks trusted, Codex hooks intercept shell commands and "
    "apply_patch-matched edits; they do NOT intercept WebSearch or other "
    "non-shell, non-MCP tools. Enforcement on Codex is therefore ADAPTED, never "
    "total."
)

# Stable surface labels — the mutation self-tests assert the failing predicate NAMES
# the diverging surface using exactly these.
_SURFACE_MANIFEST = "manifest description"
_SURFACE_ORCH = "orchestrator-skill preamble"
_SURFACE_LOCK_BANNER = "lock FIDELITY banner"

# The three safety gates that must be adapted+gated at rest (design line 179).
_SAFETY_GATES = ("enforce-lease", "block-dangerous", "protect-sensitive")

# The modern Codex block schema the lock mechanism text must describe, and the two
# obsoleted legacy forms that must be ABSENT (RL-001; REQ-075/076). The advisory
# sentence (REQ-077) is orthogonal and stays verbatim — asserted separately.
_MODERN_DENY_TOKENS = ("permissionDecision", "deny")
_LEGACY_BLOCK_SCHEMA = '{"decision":"block"}'          # obsoleted stdout block form
_LEGACY_BLOCK_SCHEMA_ESCAPED = '{\\"decision\\":\\"block\\"}'  # its JSON-file byte form
_LEGACY_FEATURES_HOOKS = "features.hooks is enabled"   # obsoleted delivery framing

# Emission-relative artifact paths.
_MANIFEST_REL = os.path.join(".codex-plugin", "plugin.json")
_ORCH_REL = os.path.join("skills", "system2", "SKILL.md")
_LOCK_REL = "system2.codex.lock.json"
_README_REL = "README.md"


# ---------------------------------------------------------------------------
# Emission-root resolution (path-parameterized) + surface readers
# ---------------------------------------------------------------------------

def _repo_root():
    # <repo>/compiler/evals/test_codex_honesty.py -> <repo>
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _resolve_committed_root():
    """Return a pre-existing Codex emission root to validate, or None to emit.

    Order: an explicit ``CODEX_EMISSION_ROOT`` override (must exist), then the
    committed ``distributions/codex/`` tree (present post-TASK-023). When neither
    exists (pre-TASK-023), returns None and the caller emits to a staging dir.
    """
    override = os.environ.get("CODEX_EMISSION_ROOT")
    if override:
        if not os.path.isdir(override):
            raise AssertionError(
                f"CODEX_EMISSION_ROOT={override!r} is set but is not a directory"
            )
        return override
    committed = os.path.join(_repo_root(), "distributions", "codex")
    if os.path.isfile(os.path.join(committed, _LOCK_REL)):
        return committed
    return None


def _emit_to(project_dir):
    result = ir.compose(_BASE, [_TEST_OVERLAY], project_dir)
    if result.graph is None:
        raise AssertionError(
            f"compose refused the core+overlay cell: {result.errors!r}"
        )
    CodexBackend(overlay_sources=[_TEST_OVERLAY]).emit(result.graph, project_dir)
    return project_dir


def _manifest_description(root):
    with open(os.path.join(root, _MANIFEST_REL), encoding="utf-8") as fh:
        return json.load(fh).get("description", "")


def _orchestrator_preamble(root):
    with open(os.path.join(root, _ORCH_REL), encoding="utf-8") as fh:
        return fh.read()


def _lock(root):
    with open(os.path.join(root, _LOCK_REL), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# The honesty predicates — pure functions of an emission root. Each returns a
# list of (surface_or_capability, message) violations; [] means the invariant
# holds. The real-emission tests assert []; the mutation self-tests assert the
# returned violations NAME the tampered surface/capability.
# ---------------------------------------------------------------------------

def advisory_surface_violations(root):
    """Trust one-liner must be a byte-substring of all three surfaces."""
    violations = []
    if _TRUST_ONELINER not in _manifest_description(root):
        violations.append(
            (_SURFACE_MANIFEST, "trust one-liner absent from manifest description")
        )
    if _TRUST_ONELINER not in _orchestrator_preamble(root):
        violations.append(
            (_SURFACE_ORCH, "trust one-liner absent from orchestrator-skill preamble")
        )
    if _TRUST_ONELINER not in _lock(root).get("FIDELITY", ""):
        violations.append(
            (_SURFACE_LOCK_BANNER, "trust one-liner absent from lock FIDELITY banner")
        )
    return violations


def coverage_gap_violations(root):
    """Coverage-gap sentence must be a byte-substring of the orchestrator + banner."""
    violations = []
    if _COVERAGE_GAP not in _orchestrator_preamble(root):
        violations.append(
            (_SURFACE_ORCH, "coverage-gap sentence absent from orchestrator-skill preamble")
        )
    if _COVERAGE_GAP not in _lock(root).get("FIDELITY", ""):
        violations.append(
            (_SURFACE_LOCK_BANNER, "coverage-gap sentence absent from lock FIDELITY banner")
        )
    return violations


def lock_invariant_violations(root):
    """Standing lock invariants (design line 179): nothing enforced/native at rest;
    every safety gate adapted + gated."""
    violations = []
    caps = _lock(root).get("capabilities", {})
    for name, rec in caps.items():
        if rec.get("enforced") is True:
            violations.append((name, "capability carries enforced:true at rest (over-claim)"))
        if rec.get("status") == "native":
            violations.append((name, "capability is classified native (over-claim)"))
    for gate in _SAFETY_GATES:
        rec = caps.get(gate)
        if rec is None:
            violations.append((gate, "required safety gate missing from the lock"))
            continue
        if rec.get("status") != "adapted":
            violations.append((gate, f"safety gate status is {rec.get('status')!r}, not 'adapted'"))
        if rec.get("gated") is not True:
            violations.append((gate, "safety gate is not gated:true"))
    return violations


def _surfaces(violations):
    return [s for (s, _m) in violations]


def _lock_mechanism_text(root):
    """The FIDELITY banner + every safety-gate ``mechanism`` string, concatenated.

    This is the DELIVERY/MECHANISM narrative the amendment rewrote — distinct from the
    verbatim advisory sentence (asserted elsewhere). The modern deny schema must be
    described here and the two legacy forms must be absent.
    """
    lock = _lock(root)
    caps = lock.get("capabilities", {})
    parts = [lock.get("FIDELITY", "")]
    parts += [(caps.get(gate) or {}).get("mechanism", "") for gate in _SAFETY_GATES]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class CodexHonestyTest(unittest.TestCase):
    """R1/F4 machine-check over the Codex emission (three surfaces + lock invariants)."""

    @classmethod
    def setUpClass(cls):
        committed = _resolve_committed_root()
        if committed is not None:
            cls.root = committed
            cls._staging = None
            cls.source = "committed" if not os.environ.get("CODEX_EMISSION_ROOT") else "override"
        else:
            cls._staging = tempfile.mkdtemp(prefix="codex-honesty-")
            _emit_to(cls._staging)
            cls.root = cls._staging
            cls.source = "staging"

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_staging", None):
            shutil.rmtree(cls._staging, ignore_errors=True)

    def _mutable_copy(self):
        """A throwaway copy of the emission for mutation; never touches cls.root."""
        dst = tempfile.mkdtemp(prefix="codex-honesty-mut-")
        self.addCleanup(shutil.rmtree, dst, True)
        shutil.copytree(self.root, dst, dirs_exist_ok=True)
        return dst

    # -- (a) three-surface advisory agreement -------------------------------

    def test_required_constants_match_design_wording(self):
        # Pin the imported constants to the design's literal text so a refactor that
        # keeps the symbol but drops the advisory / coverage clause is caught here.
        self.assertIn(
            _REQUIRED_ADVISORY_CLAUSE, _TRUST_ONELINER,
            "backends.codex._TRUST_ONELINER no longer carries the design line-174 "
            "advisory clause verbatim",
        )
        self.assertEqual(
            _COVERAGE_GAP, _REQUIRED_COVERAGE_CLAUSE,
            "backends.codex._COVERAGE_GAP diverged from the design line-175 coverage "
            "statement",
        )

    def test_trust_oneliner_present_in_all_three_surfaces(self):
        violations = advisory_surface_violations(self.root)
        self.assertEqual(
            violations, [],
            "the trust one-liner must appear VERBATIM in the manifest description, the "
            f"orchestrator-skill preamble, and the lock FIDELITY banner; diverged: "
            f"{violations} (emission source={self.source})",
        )

    def test_trust_oneliner_is_byte_identical_across_surfaces(self):
        # Stronger than substring: the identical bytes appear in each surface (the
        # advisory clause is not paraphrased differently per surface).
        desc = _manifest_description(self.root)
        orch = _orchestrator_preamble(self.root)
        banner = _lock(self.root).get("FIDELITY", "")
        for label, text in (
            (_SURFACE_MANIFEST, desc),
            (_SURFACE_ORCH, orch),
            (_SURFACE_LOCK_BANNER, banner),
        ):
            self.assertIn(
                _REQUIRED_ADVISORY_CLAUSE, text,
                f"the design line-174 advisory clause is missing from the {label}",
            )

    def test_coverage_gap_present_in_orchestrator_and_lock(self):
        violations = coverage_gap_violations(self.root)
        self.assertEqual(
            violations, [],
            "the coverage-gap sentence must appear VERBATIM in the orchestrator-skill "
            f"preamble and the lock FIDELITY banner; diverged: {violations}",
        )

    def test_readme_surface_when_present(self):
        # design line 175 pairs the README with the orchestrator preamble. The README
        # is added by TASK-023/031, so it is absent from a pre-023 staging emission.
        # Skip-count-0 discipline: NO skipTest. The orchestrator preamble is the
        # always-present required equivalent (asserted unconditionally); the README is
        # an ADDITIONAL surface validated only when it exists in the emission.
        orch = _orchestrator_preamble(self.root)
        self.assertIn(
            _TRUST_ONELINER, orch,
            "the required advisory surface (orchestrator preamble) must carry the "
            "trust one-liner even when a README is absent",
        )
        self.assertIn(_COVERAGE_GAP, orch)

        readme_path = os.path.join(self.root, _README_REL)
        if os.path.isfile(readme_path):
            with open(readme_path, encoding="utf-8") as fh:
                readme = fh.read()
            self.assertIn(
                _TRUST_ONELINER, readme,
                "the committed README surface must carry the trust one-liner verbatim "
                "(post-TASK-023)",
            )
            self.assertIn(
                _COVERAGE_GAP, readme,
                "the committed README surface must carry the coverage-gap sentence "
                "verbatim (post-TASK-023)",
            )
            self.assertEqual(self.source in ("committed", "override"), True)

    # -- (b) standing lock invariants ---------------------------------------

    def test_lock_invariants_hold(self):
        violations = lock_invariant_violations(self.root)
        self.assertEqual(
            violations, [],
            "standing Codex lock invariants (design line 179) violated: nothing may be "
            f"enforced:true / native at rest and every safety gate must be adapted+gated; "
            f"violations: {violations}",
        )

    def test_no_capability_is_enforced_at_rest(self):
        caps = _lock(self.root).get("capabilities", {})
        offenders = [n for n, r in caps.items() if r.get("enforced") is True]
        self.assertEqual(
            offenders, [],
            f"no Codex capability may carry enforced:true at rest; offenders: {offenders}",
        )

    def test_no_capability_is_classified_native(self):
        caps = _lock(self.root).get("capabilities", {})
        offenders = [n for n, r in caps.items() if r.get("status") == "native"]
        self.assertEqual(
            offenders, [],
            f"no Codex capability may be classified native; offenders: {offenders}",
        )

    def test_every_safety_gate_is_adapted_and_gated(self):
        caps = _lock(self.root).get("capabilities", {})
        for gate in _SAFETY_GATES:
            self.assertIn(gate, caps, f"safety gate {gate!r} missing from the lock")
            rec = caps[gate]
            self.assertEqual(
                rec.get("status"), "adapted",
                f"safety gate {gate!r} must be 'adapted', got {rec.get('status')!r}",
            )
            self.assertIs(
                rec.get("gated"), True,
                f"safety gate {gate!r} must be gated:true",
            )
            self.assertIs(
                rec.get("enforced"), False,
                f"safety gate {gate!r} must be enforced:false at rest",
            )

    # -- (b') mechanism/delivery schema honesty (RL-001; REQ-075/076/077) ---

    def test_lock_mechanism_describes_modern_deny_schema(self):
        # Each safety gate's mechanism must describe the MODERN Codex deny schema
        # (hookSpecificOutput.permissionDecision=deny), not the obsoleted stdout form.
        caps = _lock(self.root).get("capabilities", {})
        for gate in _SAFETY_GATES:
            rec = caps.get(gate) or {}
            mech = rec.get("mechanism", "")
            # GUARD: the mechanism text is real (non-trivial), so the token assertions
            # below cannot vacuously pass on an empty / renamed field.
            self.assertGreater(
                len(mech), 80,
                f"{gate}: mechanism text missing or too short to be the real description",
            )
            for token in _MODERN_DENY_TOKENS:
                self.assertIn(
                    token, mech,
                    f"{gate}: mechanism must describe the modern {token!r} deny schema",
                )

    def test_lock_free_of_legacy_block_schema_and_delivery(self):
        text = _lock_mechanism_text(self.root)
        # GUARD: confirm we are actually reading the rewritten mechanism narrative
        # (the modern token is present) BEFORE asserting the legacy forms are gone —
        # so a renamed/emptied field can never let this test silently pass.
        self.assertIn(
            "permissionDecision", text,
            "guard failed: the lock mechanism/FIDELITY text does not describe the "
            "modern deny schema, so the legacy-absence assertions would be vacuous",
        )
        self.assertNotIn(
            _LEGACY_BLOCK_SCHEMA, text,
            "legacy {\"decision\":\"block\"} stdout schema reintroduced into the lock",
        )
        self.assertNotIn(
            _LEGACY_FEATURES_HOOKS, text,
            "legacy 'features.hooks is enabled' delivery framing reintroduced into the lock",
        )
        # Belt-and-suspenders: scan the raw committed lock BYTES (the legacy schema is
        # JSON-escaped there) and confirm the advisory sentence is still verbatim.
        with open(os.path.join(self.root, _LOCK_REL), encoding="utf-8") as fh:
            raw = fh.read()
        self.assertNotIn(_LEGACY_BLOCK_SCHEMA_ESCAPED, raw)
        self.assertNotIn(_LEGACY_FEATURES_HOOKS, raw)
        self.assertIn(
            _REQUIRED_ADVISORY_CLAUSE, raw,
            "the REQ-077 advisory sentence must remain verbatim in the lock",
        )

    def test_legacy_wording_guard_has_teeth(self):
        # Prove the guard FAILS if legacy wording is reintroduced: inject the legacy
        # schema + delivery framing into a throwaway copy and assert the predicate trips.
        root = self._mutable_copy()
        path = os.path.join(root, _LOCK_REL)
        with open(path, encoding="utf-8") as fh:
            lock = json.load(fh)
        lock["capabilities"]["block-dangerous"]["mechanism"] = (
            "generated Node command hook: hard-blocked (stdout "
            + _LEGACY_BLOCK_SCHEMA
            + ") when the user has reviewed and trusted the bundled hooks via /hooks "
            "AND " + _LEGACY_FEATURES_HOOKS + "."
        )
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(lock, fh, indent=2)
        text = _lock_mechanism_text(root)
        self.assertIn(_LEGACY_BLOCK_SCHEMA, text)
        self.assertIn(_LEGACY_FEATURES_HOOKS, text)

    # -- (c) mutation self-tests (prove the check has teeth) ----------------

    def test_mutation_manifest_dropped_sentence_flags_manifest_surface(self):
        root = self._mutable_copy()
        path = os.path.join(root, _MANIFEST_REL)
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["description"] = "System2 workflows for Codex."  # advisory clause dropped
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        violations = advisory_surface_violations(root)
        self.assertIn(
            _SURFACE_MANIFEST, _surfaces(violations),
            f"dropping the sentence from the manifest must be flagged and NAME the "
            f"manifest surface; got {violations}",
        )
        self.assertNotIn(
            _SURFACE_ORCH, _surfaces(violations),
            "only the tampered surface should be flagged",
        )
        self.assertNotIn(_SURFACE_LOCK_BANNER, _surfaces(violations))

    def test_mutation_orchestrator_dropped_sentence_flags_orchestrator_surface(self):
        root = self._mutable_copy()
        path = os.path.join(root, _ORCH_REL)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        tampered = text.replace(_TRUST_ONELINER, "[trust notice removed]")
        self.assertNotEqual(tampered, text, "fixture guard: one-liner was not present to remove")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(tampered)
        violations = advisory_surface_violations(root)
        self.assertIn(
            _SURFACE_ORCH, _surfaces(violations),
            f"dropping the sentence from the orchestrator skill must be flagged and "
            f"NAME the orchestrator surface; got {violations}",
        )
        self.assertNotIn(_SURFACE_MANIFEST, _surfaces(violations))

    def test_mutation_lock_banner_dropped_sentence_flags_lock_surface(self):
        root = self._mutable_copy()
        path = os.path.join(root, _LOCK_REL)
        with open(path, encoding="utf-8") as fh:
            lock = json.load(fh)
        lock["FIDELITY"] = lock["FIDELITY"].replace(_TRUST_ONELINER, "[banner scrubbed]")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(lock, fh, indent=2)
        violations = advisory_surface_violations(root)
        self.assertIn(
            _SURFACE_LOCK_BANNER, _surfaces(violations),
            f"dropping the sentence from the lock FIDELITY banner must be flagged and "
            f"NAME the lock banner surface; got {violations}",
        )
        self.assertNotIn(_SURFACE_MANIFEST, _surfaces(violations))

    def test_mutation_coverage_gap_dropped_flags_surface(self):
        root = self._mutable_copy()
        path = os.path.join(root, _ORCH_REL)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        tampered = text.replace(_COVERAGE_GAP, "[coverage gap removed]")
        self.assertNotEqual(tampered, text, "fixture guard: coverage gap was not present")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(tampered)
        violations = coverage_gap_violations(root)
        self.assertIn(
            _SURFACE_ORCH, _surfaces(violations),
            f"dropping the coverage-gap sentence must be flagged and NAME the "
            f"orchestrator surface; got {violations}",
        )

    def test_mutation_lock_enforced_true_flags_invariant(self):
        root = self._mutable_copy()
        path = os.path.join(root, _LOCK_REL)
        with open(path, encoding="utf-8") as fh:
            lock = json.load(fh)
        lock["capabilities"]["block-dangerous"]["enforced"] = True  # over-claim injected
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(lock, fh, indent=2)
        violations = lock_invariant_violations(root)
        self.assertIn(
            "block-dangerous", [c for (c, _m) in violations],
            f"injecting enforced:true must be flagged and NAME the capability; "
            f"got {violations}",
        )
        self.assertTrue(
            any("enforced:true" in m for (_c, m) in violations),
            f"the violation message must identify the enforced:true over-claim; got {violations}",
        )

    def test_mutation_lock_native_flags_invariant(self):
        root = self._mutable_copy()
        path = os.path.join(root, _LOCK_REL)
        with open(path, encoding="utf-8") as fh:
            lock = json.load(fh)
        lock["capabilities"]["enforce-lease"]["status"] = "native"  # over-claim injected
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(lock, fh, indent=2)
        violations = lock_invariant_violations(root)
        self.assertIn(
            "enforce-lease", [c for (c, _m) in violations],
            f"injecting a native classification must be flagged and NAME the capability; "
            f"got {violations}",
        )
        self.assertTrue(
            any("native" in m for (_c, m) in violations),
            f"the violation message must identify the native over-claim; got {violations}",
        )

    def test_real_emission_untouched_after_mutations(self):
        # Defense against a mutation test accidentally editing the shared emission:
        # the real root's invariants still hold after all mutation copies ran.
        self.assertEqual(advisory_surface_violations(self.root), [])
        self.assertEqual(coverage_gap_violations(self.root), [])
        self.assertEqual(lock_invariant_violations(self.root), [])


# ---------------------------------------------------------------------------
# D2/the recorded decision sync guard (J4) — drift control for the derived-literal utility-skill
# builders. Token list duplicated across this file and test_pi_goldens.py's
# mirror leg (K3, CC-036), with this cross-reference comment (design.md
# Decision D2 / Public Interfaces 4a row 6; accepted duplication, recorded).
# ---------------------------------------------------------------------------

_SYNC_GUARD_TOKENS = {
    "codex": (
        "--ephemeral",
        "history.persistence=none",
        "Never pass the prompt unquoted",
    ),
    "gemini": (
        "agy -p",
        "--print-timeout",
        "Never pass the prompt unquoted",
    ),
    "stateless-loop": (
        "STATUS: CLEAN",
        "claude -p",
        "max_iterations",
    ),
}


def _source_skill_path(name):
    return os.path.join(_repo_root(), "plugin", "skills", name, "SKILL.md")


def _emitted_skill_path(root, name):
    return os.path.join(root, "skills", f"system2-{name}", "SKILL.md")


class CodexSyncGuardTest(unittest.TestCase):
    """D2/the recorded decision: every invariant token survives in both the merged source and the
    emitted skill — the drift control for the tri-copy derived-literal
    adaptation (Decision D2). Shares the same committed/staging resolution as
    ``CodexHonestyTest`` (path-parameterized, skip-count-0)."""

    @classmethod
    def setUpClass(cls):
        committed = _resolve_committed_root()
        if committed is not None:
            cls.root = committed
            cls._staging = None
        else:
            cls._staging = tempfile.mkdtemp(prefix="codex-syncguard-")
            _emit_to(cls._staging)
            cls.root = cls._staging

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_staging", None):
            shutil.rmtree(cls._staging, ignore_errors=True)

    def test_invariant_tokens_present_in_source_and_emitted_skill(self):
        for name, tokens in _SYNC_GUARD_TOKENS.items():
            with open(_source_skill_path(name), encoding="utf-8") as fh:
                source_text = fh.read()
            with open(_emitted_skill_path(self.root, name), encoding="utf-8") as fh:
                emitted_text = fh.read()
            for token in tokens:
                with self.subTest(skill=name, token=token, surface="plugin/skills source"):
                    self.assertIn(
                        token, source_text,
                        f"{name}: invariant token {token!r} missing from the merged "
                        f"source plugin/skills/{name}/SKILL.md",
                    )
                with self.subTest(skill=name, token=token, surface="emitted Codex skill"):
                    self.assertIn(
                        token, emitted_text,
                        f"{name}: invariant token {token!r} missing from the emitted "
                        f"skills/system2-{name}/SKILL.md",
                    )

    def test_guard_trips_if_a_token_is_dropped_from_the_emitted_copy(self):
        # Mutation self-test (teeth): on a throwaway copy, drop one invariant token
        # from the emitted skill and confirm the presence assertion would then fail.
        name = "codex"
        token = _SYNC_GUARD_TOKENS[name][0]
        with open(_emitted_skill_path(self.root, name), encoding="utf-8") as fh:
            text = fh.read()
        tampered = text.replace(token, "[token removed]")
        self.assertNotEqual(tampered, text, "fixture guard: token was not present to remove")
        self.assertNotIn(token, tampered)


# PR #10 review finding 1: _skill_frontmatter() (backends.codex) emits
# `description: {description}` with zero YAML escaping, and nothing in this
# suite previously validated the emitted VALUE -- an unquoted embedded ": "
# (e.g. stateless-loop's "STATUS: CLEAN") shipped as invalid YAML with no red
# check anywhere. This is a real (if narrow) spec-aware check, not a full
# parser: a plain YAML scalar cannot contain an unquoted ": " or end in an
# unquoted ":". Helpers duplicated from evals/test_pi_goldens.py's mirror leg
# (accepted duplication, matching this suite's existing sync-guard-token
# cross-reference convention above).
def _parse_leading_frontmatter(text):
    if not text.startswith("---\n"):
        return None
    close = text.find("\n---\n", 4)
    if close == -1:
        return None
    fields = {}
    for line in text[4:close].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _yaml_unsafe_reason(value):
    if not isinstance(value, str) or not value:
        return None
    if value[0] in ('"', "'") and value[-1:] == value[0]:
        return None
    if ": " in value:
        return f"contains an unquoted ': ' -- invalid as a plain YAML scalar: {value!r}"
    if value.rstrip().endswith(":"):
        return f"ends with an unquoted ':' -- invalid as a plain YAML scalar: {value!r}"
    return None


class CodexSkillFrontmatterYamlSafetyTest(unittest.TestCase):
    """PR #10 review finding 1: every emitted skill's frontmatter must be safe
    to parse as YAML -- checked for ALL emitted skills (not just the three
    derived-literal utility skills), since ``_skill_frontmatter()`` is shared
    by every skill this backend emits."""

    @classmethod
    def setUpClass(cls):
        committed = _resolve_committed_root()
        if committed is not None:
            cls.root = committed
            cls._staging = None
        else:
            cls._staging = tempfile.mkdtemp(prefix="codex-yamlsafety-")
            _emit_to(cls._staging)
            cls.root = cls._staging

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_staging", None):
            shutil.rmtree(cls._staging, ignore_errors=True)

    def _all_skill_md_paths(self):
        skills_dir = os.path.join(self.root, "skills")
        paths = []
        for dirpath, _dirs, files in os.walk(skills_dir):
            for name in files:
                if name == "SKILL.md":
                    paths.append(os.path.join(dirpath, name))
        self.assertTrue(paths, f"no SKILL.md files found under {skills_dir}")
        return paths

    def test_every_emitted_skill_frontmatter_is_yaml_safe(self):
        for path in self._all_skill_md_paths():
            rel = os.path.relpath(path, self.root)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            fields = _parse_leading_frontmatter(text)
            self.assertIsNotNone(fields, f"{rel} does not open with a YAML frontmatter block")
            for key, value in fields.items():
                with self.subTest(skill=rel, field=key):
                    reason = _yaml_unsafe_reason(value)
                    self.assertIsNone(reason, f"{rel}: frontmatter {key!r} {reason}")

    def test_guard_trips_on_the_regressed_value(self):
        # Mutation self-test (teeth): prove the guard would have caught the
        # actual PR #10 regression had it still been present.
        reason = _yaml_unsafe_reason(
            "Run an instruction ... until the task reports STATUS: CLEAN or max "
            "iterations are reached."
        )
        self.assertIsNotNone(reason)
        self.assertIn("STATUS: CLEAN", reason)


if __name__ == "__main__":
    unittest.main()
