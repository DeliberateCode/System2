"""IR-level anchor model (IR/capability implementation).

Builds a per-agent ``AnchorTable`` from ``anchor-map.json`` for all pipeline
agents and resolves contributions by **anchor identity** ``(agent, anchor_name)``
— not by literal-heading string matching. The literal ``after_section`` heading
in the anchor map is a Claude-targeted *rendering location* the backend owns; the
IR's resolution mechanism is identity (the requirement).

Filtering of contributions to non-existent anchors is performed against this table
(the requirement), preserving the oracle's silent exclusion exactly: a ``prompt_sections``
contribution to an ``(agent, anchor_name)`` not present here is dropped without a
warning, just as ``composer._build_contribution_index(..., valid_anchors_by_agent)``
drops it today.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class AnchorRef:
    """Identity reference to a per-agent anchor.

    Attached to an anchored ``Contribution`` so the backend can resolve it to its
    rendering location by identity, not by heading string.
    """

    agent: str
    anchor_name: str


@dataclass(frozen=True)
class AnchorDef:
    """A single named insertion point on an agent.

    ``purpose`` is the neutral description from the anchor map; the literal
    rendering heading (``after_section``) is intentionally **not** carried here —
    it is a backend-owned rendering location, not part of the IR resolution
    mechanism (the requirement/040).
    """

    agent: str
    anchor_name: str
    purpose: str


@dataclass(frozen=True)
class AnchorTable:
    """Per-agent table of named insertion points.

    ``by_agent[agent][anchor_name] -> AnchorDef``. Resolution and filtering are
    identity-keyed lookups against this structure.
    """

    by_agent: Dict[str, Dict[str, AnchorDef]] = field(default_factory=dict)

    def has_anchor(self, agent: str, anchor_name: str) -> bool:
        """Return ``True`` iff ``(agent, anchor_name)`` is a defined anchor."""
        return anchor_name in self.by_agent.get(agent, {})

    def resolve(self, agent: str, anchor_name: str) -> "AnchorRef | None":
        """Return the identity ``AnchorRef`` for a defined anchor, else ``None``.

        A ``None`` result is the IR signal for the oracle's silent exclusion of a
        contribution to a non-existent anchor.
        """
        if self.has_anchor(agent, anchor_name):
            return AnchorRef(agent=agent, anchor_name=anchor_name)
        return None

    def anchors_by_agent(self) -> Dict[str, List[str]]:
        """Return ``{agent: [anchor_name, ...]}`` for the index/filter passes.

        This is the exact shape ``build_contribution_index`` and the injection
        scan consume as ``valid_anchors_by_agent``, so filtering goes through the
        single identity-keyed table rather than re-deriving from the raw map.
        """
        return {
            agent: list(anchors.keys())
            for agent, anchors in self.by_agent.items()
        }


def build_anchor_table(anchor_map: dict) -> AnchorTable:
    """Build the ``AnchorTable`` from a loaded ``anchor-map.json`` dict.

    For each agent, every named anchor under ``agents.<agent>.anchors`` becomes an
    ``AnchorDef`` keyed by ``(agent, anchor_name)``. Every ``(agent, anchor)`` pair
    in the map is representable and resolvable (the requirement).
    """
    by_agent: Dict[str, Dict[str, AnchorDef]] = {}
    for agent, info in anchor_map.get("agents", {}).items():
        anchors: Dict[str, AnchorDef] = {}
        for anchor_name, spec in info.get("anchors", {}).items():
            purpose = ""
            if isinstance(spec, dict):
                purpose = spec.get("purpose", "")
            anchors[anchor_name] = AnchorDef(
                agent=agent,
                anchor_name=anchor_name,
                purpose=purpose,
            )
        by_agent[agent] = anchors
    return AnchorTable(by_agent=by_agent)
