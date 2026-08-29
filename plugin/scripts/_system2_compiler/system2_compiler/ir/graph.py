"""The System2Graph IR schema: frozen, JSON-serializable dataclasses.

Harness-neutral. Contains no Claude mechanism fields (``tools`` / ``hooks`` /
``permissionMode``) on roles or contributions.

The Phase 2 types this schema references — ``AnchorTable`` / ``AnchorDef`` /
``AnchorRef`` and ``CapabilitySet`` / ``BlockingSemantic`` — are defined in their
owning sibling modules ``ir/anchors.py`` and ``ir/capabilities.py`` and imported
here (per ``module-boundaries.json``: ``ir/graph.py`` may import those two siblings
plus stdlib). They superseded the Phase-1 placeholders that briefly lived in this
module.
"""

import json
from dataclasses import dataclass, field, asdict
from typing import Optional

from .anchors import AnchorDef, AnchorRef, AnchorTable
from .capabilities import BlockingSemantic, CapabilitySet

__all__ = [
    "AnchorDef",
    "AnchorRef",
    "AnchorTable",
    "BlockingSemantic",
    "CapabilitySet",
    "Contribution",
    "Role",
    "GateNode",
    "GateGraph",
    "DelegationContract",
    "TriggerRule",
    "PostExecution",
    "MaintenanceLoop",
    "SpecArtifact",
    "OrderedContributions",
    "ProfileRef",
    "Warnings",
    "BaseTemplate",
    "OverlayInput",
    "System2Graph",
]


@dataclass(frozen=True)
class Contribution:
    overlay_name: str
    contribution_type: str
    raw: dict
    overlay_path: str
    contribution_id: Optional[str] = None
    anchor: Optional[AnchorRef] = None


@dataclass(frozen=True)
class Role:
    name: str
    gate_role: str
    write_scope: str
    model_hint: Optional[str]
    capabilities: list
    pipeline: bool


@dataclass(frozen=True)
class GateNode:
    number: int
    name: str
    checklist_text: str
    consultations: list


@dataclass(frozen=True)
class GateGraph:
    gates: list
    edges: list


@dataclass(frozen=True)
class DelegationContract:
    required_fields: list
    preferred_order: list
    advisory_sources: list


@dataclass(frozen=True)
class TriggerRule:
    agent: str
    condition: str
    always: bool


@dataclass(frozen=True)
class PostExecution:
    trigger_rules: list
    execution_order: list
    blocker_policy: dict
    boomerang_cap: int
    opaque_text: str


@dataclass(frozen=True)
class MaintenanceLoop:
    regression_ledger_fields: list
    corrective_cycle_cap: int
    classification: list
    opaque_text: str


@dataclass(frozen=True)
class SpecArtifact:
    name: str
    required_sections: list


@dataclass(frozen=True)
class OrderedContributions:
    # keyed by (type_path, target_key); each list already topologically sorted.
    scopes: dict


@dataclass(frozen=True)
class ProfileRef:
    name: str
    ordered_source_paths: list


@dataclass(frozen=True)
class Warnings:
    validation: list
    structural_conflicts: list
    additive_overlaps: list
    semantic_tensions: list
    injection: list


@dataclass(frozen=True)
class BaseTemplate:
    # CLAUDE-TARGETED: byte-fidelity mechanism for this cycle (design );
    # excluded from neutrality assertions.
    text: str
    section_offsets: dict


@dataclass(frozen=True)
class OverlayInput:
    # CLAUDE-TARGETED byte-fidelity carrier (same quarantine as base_template,
    # design ): the validated overlay manifest dict and its resolved source
    # directory, in front-end (validated_manifests) order. The lock's per-overlay
    # metadata, the content fingerprint, the content copies, and the auxiliary
    # agent file references are all derived from these by the backend exactly as
    # the oracle derives them. Excluded from neutrality assertions; carries no
    # Claude mechanism fields itself (the raw manifest is opaque overlay data).
    manifest: dict
    source_path: str


@dataclass(frozen=True)
class System2Graph:
    schema_version: str
    system2_version: str
    roles: list
    gate_graph: GateGraph
    delegation_contract: DelegationContract
    post_execution: PostExecution
    maintenance_loop: MaintenanceLoop
    spec_artifacts: list
    contributions: OrderedContributions
    active_profile: Optional[ProfileRef]
    anchors: AnchorTable
    capabilities: CapabilitySet
    blocking_semantics: list
    warnings: Warnings
    base_template: BaseTemplate
    overlay_inputs: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict.

        ``OrderedContributions.scopes`` is keyed by ``(type_path, target_key)``
        tuples, which JSON cannot represent as object keys; those scopes are
        emitted as a list of ``[key_pair, contributions]`` entries so the graph
        round-trips.
        """
        data = asdict(self)
        scopes = data.get("contributions", {}).get("scopes", {})
        data["contributions"]["scopes"] = [
            [list(key), value] for key, value in scopes.items()
        ]
        return data

    def to_json(self, *, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)
