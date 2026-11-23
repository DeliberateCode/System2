"""Conflict detection and ConflictReport."""

from typing import Dict, List

from .contributions import build_contribution_index as _build_contribution_index
from .contributions import topological_sort as _topological_sort


class ConflictReport:
    """Container for conflict detection outcomes."""

    __slots__ = ("structural_conflicts", "additive_overlaps", "semantic_tensions")

    def __init__(self) -> None:
        self.structural_conflicts: List[dict] = []
        self.additive_overlaps: List[dict] = []
        self.semantic_tensions: List[dict] = []

    @property
    def has_structural_conflicts(self) -> bool:
        return len(self.structural_conflicts) > 0


# Conflict detection

# High-leverage surfaces that trigger semantic tension warnings when
# multiple overlays contribute to them.
_HIGH_LEVERAGE_SURFACES = {
    "orchestrator.principles",
    "spec.requirements.required_sections",
    "spec.design.required_sections",
}

# High-leverage anchor name patterns (wildcard matching against
# agents.*.prompt_sections.<anchor>).
_HIGH_LEVERAGE_ANCHORS = {
    "safety_rules", "constraints", "guidelines",
    "design_constraints", "style_requirements",
    "guardrails", "planning_rules", "review_criteria",
}

# Pipeline agent names for auxiliary agent collision checks.
_PIPELINE_AGENTS = {
    "code-reviewer", "design-architect", "docs-release", "eval-engineer",
    "executor", "mcp-toolsmith", "postmortem-scribe", "repo-governor",
    "requirements-engineer", "security-sentinel", "spec-coordinator",
    "task-planner", "test-engineer",
}


def detect_conflicts(
    manifests: List[dict], anchor_map: dict
) -> ConflictReport:
    """Detect structural conflicts, additive overlaps, and semantic tensions."""
    report = ConflictReport()
    anchors_by_agent = {
        name: list(info.get("anchors", {}).keys())
        for name, info in anchor_map.get("agents", {}).items()
    }
    index = _build_contribution_index(manifests, anchors_by_agent)

    overlay_names = {m.get("name", "<unknown>") for m in manifests}
    overlay_tags: Dict[str, List[str]] = {}
    for m in manifests:
        overlay_tags[m.get("name", "<unknown>")] = m.get("tags", [])

    # --- Structural: known_conflicts declarations -------------------------

    for m in manifests:
        name = m.get("name", "<unknown>")
        compat = m.get("compatibility", {})
        for conflict_name in compat.get("known_conflicts", []):
            if conflict_name in overlay_names:
                report.structural_conflicts.append({
                    "type": "known_conflicts",
                    "message": (
                        f"Overlay {name!r} declares a known conflict with "
                        f"{conflict_name!r}, which is also being composed."
                    ),
                    "overlays": [name, conflict_name],
                    "contribution_type": "compatibility.known_conflicts",
                    "target": conflict_name,
                    "suggested_resolution": (
                        f"Remove {conflict_name!r} from the overlay set, "
                        f"or remove {name!r}'s known_conflicts declaration "
                        f"if the conflict has been resolved."
                    ),
                })

    # --- Structural: auxiliary agent name collision across overlays --------

    for (contrib_type, target), entries in index.items():
        if contrib_type != "auxiliary_agents":
            continue
        # Collect distinct overlay names contributing this agent name
        contributing_overlays = list({e[0] for e in entries})
        if len(contributing_overlays) > 1:
            report.structural_conflicts.append({
                "type": "auxiliary_agent_collision",
                "message": (
                    f"Auxiliary agent {target!r} is declared by multiple "
                    f"overlays: {contributing_overlays}. Each auxiliary "
                    f"agent name must be unique across overlays."
                ),
                "overlays": contributing_overlays,
                "contribution_type": "auxiliary_agents",
                "target": target,
                "suggested_resolution": (
                    f"Rename the auxiliary agent in one of the overlays "
                    f"so names are unique across overlays."
                ),
            })

    # --- Structural: cycles + Additive overlaps + Semantic tensions -------

    for (contrib_type, target), entries in index.items():
        if contrib_type == "auxiliary_agents":
            # Already handled above; not an additive scope.
            continue

        # Determine contributing overlay set
        contributing_overlays = list({e[0] for e in entries})

        # Cycles and duplicate contribution keys become structural conflicts.
        try:
            ordered, sort_warnings = _topological_sort(entries, f"{contrib_type}")
            for sw in sort_warnings:
                report.semantic_tensions.append({
                    "type": "unresolved_after",
                    "message": sw,
                    "overlays": contributing_overlays,
                })
        except ValueError as exc:
            report.structural_conflicts.append({
                "type": "ordering_cycle",
                "message": str(exc),
                "overlays": contributing_overlays,
                "contribution_type": contrib_type,
                "target": contrib_type,
                "suggested_resolution": (
                    f"Remove or adjust 'after' declarations in the overlay "
                    f"manifests to break the cycle in scope {contrib_type!r}."
                ),
            })
            continue

        # Record additive overlaps (multiple overlays in same scope)
        if len(contributing_overlays) > 1:
            report.additive_overlaps.append({
                "scope": contrib_type,
                "overlays": sorted(contributing_overlays),
                "order": ordered,
            })

        # Semantic tension: high-leverage surfaces
        if len(contributing_overlays) > 1:
            is_high_leverage = contrib_type in _HIGH_LEVERAGE_SURFACES
            # Check for high-leverage anchor patterns
            if not is_high_leverage and contrib_type.startswith("agents."):
                parts = contrib_type.split(".")
                if (
                    len(parts) == 4
                    and parts[2] == "prompt_sections"
                    and parts[3] in _HIGH_LEVERAGE_ANCHORS
                ):
                    is_high_leverage = True
            if is_high_leverage:
                report.semantic_tensions.append({
                    "type": "high_leverage_surface",
                    "scope": contrib_type,
                    "message": (
                        f"Overlays {sorted(contributing_overlays)} both "
                        f"contribute to {contrib_type} (high-leverage "
                        f"surface). Review for coherence."
                    ),
                    "overlays": sorted(contributing_overlays),
                })

    # --- Semantic tension: shared tags / review_when_combined_with_tags ----

    # Collect review tags declared by each overlay
    review_tags: Dict[str, List[str]] = {}
    for m in manifests:
        name = m.get("name", "<unknown>")
        compat = m.get("compatibility", {})
        rtags = compat.get("review_when_combined_with_tags", [])
        if rtags:
            review_tags[name] = rtags

    # For each overlay that declares review tags, check if any other
    # overlay has a matching tag.
    reported_tag_pairs: set = set()
    for declaring_name, rtags in review_tags.items():
        for other_name, other_tags in overlay_tags.items():
            if other_name == declaring_name:
                continue
            for tag in rtags:
                if tag in other_tags:
                    pair_key = tuple(sorted([declaring_name, other_name]))
                    tag_pair = (pair_key, tag)
                    if tag_pair not in reported_tag_pairs:
                        reported_tag_pairs.add(tag_pair)
                        report.semantic_tensions.append({
                            "type": "shared_review_tag",
                            "tag": tag,
                            "message": (
                                f"Overlays {sorted([declaring_name, other_name])} "
                                f"share tag {tag!r} which is listed in "
                                f"review_when_combined_with_tags. Review "
                                f"their combined behavior."
                            ),
                            "overlays": sorted([declaring_name, other_name]),
                        })

    return report
