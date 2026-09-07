"""IR-level anchor model."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class AnchorRef:
    """Identity reference to a per-agent anchor."""

    agent: str
    anchor_name: str


@dataclass(frozen=True)
class AnchorDef:
    """A single named insertion point on an agent."""

    agent: str
    anchor_name: str
    purpose: str


@dataclass(frozen=True)
class AnchorTable:
    """Per-agent table of named insertion points."""

    by_agent: Dict[str, Dict[str, AnchorDef]] = field(default_factory=dict)

    def has_anchor(self, agent: str, anchor_name: str) -> bool:
        """Return ``True`` iff ``(agent, anchor_name)`` is a defined anchor."""
        return anchor_name in self.by_agent.get(agent, {})

    def resolve(self, agent: str, anchor_name: str) -> "AnchorRef | None":
        """Return the identity ``AnchorRef`` for a defined anchor, else ``None``."""
        if self.has_anchor(agent, anchor_name):
            return AnchorRef(agent=agent, anchor_name=anchor_name)
        return None

    def anchors_by_agent(self) -> Dict[str, List[str]]:
        """Return ``{agent: [anchor_name, ...]}`` for the index/filter passes."""
        return {
            agent: list(anchors.keys())
            for agent, anchors in self.by_agent.items()
        }


def build_anchor_table(anchor_map: dict) -> AnchorTable:
    """Build the ``AnchorTable`` from a loaded ``anchor-map.json`` dict."""
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
