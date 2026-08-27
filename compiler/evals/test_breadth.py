"""TASK-320 — Eval-breadth hardening: arg-ordering determinism + anchor-exclusion.

Folds in the two cheap, high-value eval-breadth gaps the eval-engineer queued
before Phase 3 (``spec/evals.md`` G1 + G2), asserted **directly** rather than only
transitively via the golden byte-diff:

* **G1 — argument-ordering determinism (REQ-041).** Composing the same overlay set
  with ``--overlays`` in two different orders yields the *same composition*. The
  substantive REQ-041 guarantee — contribution ordering is independent of CLI
  argument order (governed by the front-end ``(overlay_name, id)`` pre-sort) — is
  asserted at the IR level (``ir.contributions`` ordered output identical under
  input reorder) AND at the emitted ``CLAUDE.md`` level modulo the oracle-faithful
  provenance header.

  Note (oracle-faithful, reported as a finding, not a bug): the ``<!-- COMPOSED:
  ... overlays: ... -->`` provenance header echoes overlays in *input order*, and
  the ``<!-- Composed at: ... -->`` line carries a timestamp. Both are present in
  the frozen oracle's own output (verified by parity), so byte-identity of the
  *composed body* (everything except those two provenance lines) is the correct
  encoding of REQ-041; reordering the header is not a composition regression.

* **G2 — anchor-exclusion (REQ-025/027).** A ``prompt_sections`` contribution to a
  non-existent ``(agent, anchor)`` is silently excluded from the IR exactly as the
  frozen oracle excludes it, while a known anchor resolves to an identity
  ``AnchorRef``. Asserted directly against ``ir.anchors.build_anchor_table`` /
  ``AnchorTable.resolve`` and against a real ``ir.compose`` graph using the
  ``anchorfile`` fixture (known anchor ``implementation_discipline`` resolves;
  unknown anchor ``nonexistent_anchor_zzz`` is dropped).

Stdlib ``unittest``; runs under ``python3 -m unittest``. No product code or
``System2/`` is modified. All overlay/fixture contents are untrusted data.
"""

import os
import tempfile
import unittest

from system2_compiler import ir
from evals import matrix, oracle
from system2_compiler.ir.anchors import AnchorRef, build_anchor_table
from system2_compiler.ir.manifest import load_anchor_map
from system2_compiler.backends.claude_code import ClaudeCodeBackend

_PROVENANCE_PREFIXES = ("<!-- COMPOSED", "<!-- Composed at:")


def _composed_scopes(order):
    """Return a normalized, hashable view of the IR's ordered contributions.

    Keyed by ``(type_path, target_key)``; each value is the ordered list of
    ``(overlay_name, contribution_id, repr(raw), anchor)`` so reordering inputs
    that changed any per-scope order or membership would surface as inequality.
    """
    project = tempfile.mkdtemp(prefix="breadth-ir-")
    result = ir.compose(oracle.PLUGIN_ROOT, list(order), project)
    if result.graph is None:
        raise AssertionError(
            f"compose refused a clean multi-overlay cell: {result.errors!r}"
        )
    out = {}
    for key, contribs in result.graph.contributions.scopes.items():
        out[key] = tuple(
            (c.overlay_name, c.contribution_id, repr(c.raw), c.anchor)
            for c in contribs
        )
    return out


def _claude_lines(order):
    """Emit CLAUDE.md for an overlay order and return its full line list."""
    project = tempfile.mkdtemp(prefix="breadth-emit-")
    result = ir.compose(oracle.PLUGIN_ROOT, list(order), project)
    if result.graph is None:
        raise AssertionError(
            f"compose refused a clean multi-overlay cell: {result.errors!r}"
        )
    ClaudeCodeBackend().emit(result.graph, project)
    with open(os.path.join(project, "CLAUDE.md"), "r", encoding="utf-8") as fh:
        return fh.read().splitlines(keepends=True)


def _body(lines):
    """Strip the two oracle-faithful provenance lines (overlay list + timestamp)."""
    return [
        ln for ln in lines
        if not any(ln.startswith(p) for p in _PROVENANCE_PREFIXES)
    ]


# Two overlays that compose cleanly together and produce multiple scopes:
# - test-overlay (principles, gate-3 consultation, advisory source, an anchored
#   prompt_sections contribution, a spec required-section, an auxiliary agent), and
# - anchorfile (a known + an unknown anchored prompt_sections contribution).
_ORDER_AB = (matrix.ANCHORFILE, matrix.TEST_OVERLAY)
_ORDER_BA = (matrix.TEST_OVERLAY, matrix.ANCHORFILE)


class ArgOrderingDeterminismTest(unittest.TestCase):
    """G1 / REQ-041 — composition is independent of CLI overlay ordering."""

    def test_ir_ordered_contributions_identical_under_reorder(self):
        scopes_ab = _composed_scopes(_ORDER_AB)
        scopes_ba = _composed_scopes(_ORDER_BA)
        # The composition exercises real multi-scope content (not a trivial pair).
        self.assertGreaterEqual(
            len(scopes_ab), 2,
            msg="arg-ordering test must exercise >=2 contribution scopes",
        )
        self.assertEqual(
            scopes_ab, scopes_ba,
            msg=(
                "ir.contributions ordered output changed when --overlays order was "
                "reversed; REQ-041 requires order-independence (front-end "
                "(overlay_name, id) pre-sort)"
            ),
        )

    def test_claude_body_identical_under_reorder(self):
        body_ab = _body(_claude_lines(_ORDER_AB))
        body_ba = _body(_claude_lines(_ORDER_BA))
        self.assertEqual(
            body_ab, body_ba,
            msg=(
                "composed CLAUDE.md body (excluding the provenance header) differs "
                "under --overlays reorder; the substantive composition must be "
                "order-independent (REQ-041)"
            ),
        )

    def test_only_provenance_header_is_order_dependent(self):
        # Document the oracle-faithful delta: the ONLY order-dependent lines are the
        # provenance header (overlay list echoes input order) and the timestamp.
        lines_ab = _claude_lines(_ORDER_AB)
        lines_ba = _claude_lines(_ORDER_BA)
        diff_ab = [ln for ln in lines_ab if ln not in lines_ba]
        for ln in diff_ab:
            self.assertTrue(
                any(ln.startswith(p) for p in _PROVENANCE_PREFIXES),
                msg=(
                    "an order-dependent CLAUDE.md line is NOT a provenance/timestamp "
                    f"line: {ln!r} — this would be a real REQ-041 regression"
                ),
            )

    def test_negative_control_reorder_detection_has_teeth(self):
        # Prove the comparison would catch a real order-dependence: an artificially
        # order-sensitive normalization (tagging each scope with its input position)
        # must differ under reorder, so the real equality check above is meaningful.
        def positional(order):
            project = tempfile.mkdtemp(prefix="breadth-neg-")
            result = ir.compose(oracle.PLUGIN_ROOT, list(order), project)
            # Tag each scope's first contribution with the *input* index of its
            # source overlay — a deliberately order-sensitive view.
            input_index = {os.path.basename(p): i for i, p in enumerate(order)}
            tagged = {}
            for key, contribs in result.graph.contributions.scopes.items():
                tagged[key] = tuple(
                    (input_index.get(_overlay_basename(c.overlay_name), -1), c.contribution_id)
                    for c in contribs
                )
            return tagged
        # test-overlay and anchorfile occupy different input positions in the two
        # orders, so a position-tagged view MUST differ — proving the teeth.
        self.assertNotEqual(
            positional(_ORDER_AB), positional(_ORDER_BA),
            msg=(
                "the negative control failed to distinguish input orders; the "
                "real order-independence assertion would be vacuous"
            ),
        )


def _overlay_basename(overlay_name):
    # Overlays are keyed by manifest 'name'; map back to the fixture dir basename
    # used as the input-position key. test-overlay's manifest name is 'test-overlay'
    # and anchorfile's is 'anchorfile', so the names already match the basenames.
    return overlay_name


class AnchorExclusionTest(unittest.TestCase):
    """G2 / REQ-025/027 — anchor identity resolution + non-existent-anchor exclusion."""

    @classmethod
    def setUpClass(cls):
        cls.anchor_map = load_anchor_map(oracle.PLUGIN_ROOT)
        cls.table = build_anchor_table(cls.anchor_map)

    def test_known_anchor_resolves_to_identity_ref(self):
        # executor.implementation_discipline is a real anchor (the test-overlay /
        # anchorfile fixtures both contribute to it).
        self.assertTrue(
            self.table.has_anchor("executor", "implementation_discipline"),
            msg="executor.implementation_discipline must be a defined anchor",
        )
        ref = self.table.resolve("executor", "implementation_discipline")
        self.assertEqual(
            ref, AnchorRef(agent="executor", anchor_name="implementation_discipline")
        )

    def test_unknown_anchor_resolves_to_none(self):
        self.assertFalse(
            self.table.has_anchor("executor", "nonexistent_anchor_zzz"),
            msg="a fabricated anchor name must not be present in the table",
        )
        self.assertIsNone(
            self.table.resolve("executor", "nonexistent_anchor_zzz"),
            msg=(
                "resolution of a non-existent anchor must return None (the IR "
                "signal for the oracle's silent exclusion, REQ-027)"
            ),
        )

    def test_per_agent_scoping_is_independent(self):
        # The same anchor name on a different agent resolves independently: an
        # anchor defined for one agent is NOT silently shared with another.
        present = {
            agent: list(anchors.keys())
            for agent, anchors in self.table.by_agent.items()
        }
        # Find an anchor defined for exactly one agent (the common case) and assert
        # it does not resolve on a different agent.
        owner = None
        anchor_name = None
        for agent, names in present.items():
            for nm in names:
                holders = [a for a, ns in present.items() if nm in ns]
                if len(holders) == 1:
                    owner, anchor_name = agent, nm
                    break
            if owner is not None:
                break
        self.assertIsNotNone(owner, msg="expected at least one single-agent anchor")
        other_agents = [a for a in present if a != owner]
        self.assertTrue(other_agents, msg="expected >1 agent in the anchor table")
        for other in other_agents:
            self.assertIsNone(
                self.table.resolve(other, anchor_name),
                msg=(
                    f"anchor {anchor_name!r} (defined only on {owner!r}) must not "
                    f"resolve on agent {other!r} — per-agent identity scoping "
                    "(REQ-025)"
                ),
            )

    def test_compose_excludes_unknown_anchor_contribution(self):
        # End-to-end: the anchorfile fixture contributes one known-anchor and one
        # unknown-anchor prompt_sections entry. The IR must carry the known one
        # (with an AnchorRef) and exclude the unknown one entirely.
        cell = matrix.get_cell("core+anchorfile")
        project = tempfile.mkdtemp(prefix="breadth-anchor-")
        result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project)
        self.assertIsNotNone(
            result.graph, msg=f"anchorfile cell refused: {result.errors!r}"
        )
        scopes = result.graph.contributions.scopes

        # The unknown-anchor scope must be wholly absent.
        unknown_scopes = [
            k for k in scopes if "nonexistent_anchor_zzz" in k[0]
        ]
        self.assertEqual(
            [], unknown_scopes,
            msg=(
                "a contribution to a non-existent anchor leaked into the IR; it "
                "must be silently excluded exactly as the oracle excludes it "
                "(REQ-027)"
            ),
        )

        # The known-anchor scope must be present, carrying the known contribution
        # with an identity AnchorRef, and NOT the unknown contribution id.
        known_key = (
            "agents.executor.prompt_sections.implementation_discipline",
            "agents.executor.prompt_sections.implementation_discipline",
        )
        self.assertIn(known_key, scopes, msg="known-anchor scope missing from IR")
        contribs = scopes[known_key]
        ids = [c.contribution_id for c in contribs]
        self.assertIn("af-known", ids)
        self.assertNotIn(
            "af-unknown", ids,
            msg="the unknown-anchor contribution must not appear under any scope",
        )
        for c in contribs:
            if c.contribution_id == "af-known":
                self.assertEqual(
                    c.anchor,
                    AnchorRef(agent="executor", anchor_name="implementation_discipline"),
                    msg="the known-anchor contribution must carry its identity AnchorRef",
                )

    def test_compose_excludes_unknown_anchor_from_claude_output(self):
        # The unknown-anchor content (extra.md) must not be reproduced into CLAUDE.md.
        cell = matrix.get_cell("core+anchorfile")
        project = tempfile.mkdtemp(prefix="breadth-anchor-emit-")
        result = ir.compose(oracle.PLUGIN_ROOT, list(cell.overlays), project)
        self.assertIsNotNone(result.graph)
        ClaudeCodeBackend().emit(result.graph, project)
        with open(os.path.join(project, "CLAUDE.md"), "r", encoding="utf-8") as fh:
            claude = fh.read()
        self.assertIn(
            "Known-anchor guidance",
            claude,
            msg="known-anchor content must be present in CLAUDE.md",
        )
        self.assertNotIn(
            "Unknown-anchor guidance",
            claude,
            msg=(
                "unknown-anchor content (extra.md) leaked into CLAUDE.md; it must "
                "be excluded from composition exactly as the oracle excludes it "
                "(REQ-027)"
            ),
        )


if __name__ == "__main__":
    unittest.main()
