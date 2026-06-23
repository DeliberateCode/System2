"""Intent-capability vocabulary, role attributes, and blocking semantics (Phase 2).

Defines the fixed six-term intent-capability vocabulary and the three role
attributes, the ``BlockingSemantic`` record (an honest description of what each
enforced capability does on a fully-faithful target), ``CapabilitySet`` (per-agent
intent capabilities), and unknown-capability validation.

Contains **no** Claude mechanism fields (``tools`` / ``hooks`` / ``permissionMode``):
the mechanism→capability lowering lives in the backend and its descriptor; the IR
carries only neutral intent (REQ-028/040). The ``BlockingSemantic`` records below
are derived from the design's mechanism→capability mapping table (REQ-029): every
enforced capability appears with its enforcement point and blocking flag, with a
neutral description that names no Claude hook.
"""

from dataclasses import dataclass, field
from typing import Dict, List


# The fixed intent-capability vocabulary (REQ-028). Exactly these six terms.
INTENT_CAPABILITIES = (
    "enforce-lease",
    "block-dangerous",
    "protect-sensitive",
    "format",
    "typecheck",
    "budget",
)

# The role attribute vocabulary. These are role *attributes* (carried as the
# Role.write_scope / model_hint / gate_role fields), not intent capabilities.
ROLE_ATTRIBUTES = (
    "write-scope",
    "model-hint",
    "gate-role",
)


@dataclass(frozen=True)
class BlockingSemantic:
    """Honest description of an enforced capability's blocking behavior.

    ``enforcement_point`` is one of ``PreToolUse`` / ``PostToolUse`` /
    ``SubagentStop`` / ``orchestrator-lifecycle``. ``blocking`` is ``True`` when the
    disallowed action is actually blocked (not merely described). ``description``
    is neutral and names no harness mechanism.
    """

    capability: str
    enforcement_point: str
    blocking: bool
    description: str


@dataclass(frozen=True)
class CapabilitySet:
    """Per-agent intent capabilities.

    ``by_agent[agent] -> list[str]`` where each entry is a member of
    ``INTENT_CAPABILITIES``.
    """

    by_agent: Dict[str, List[str]] = field(default_factory=dict)


# Blocking-semantics records, one per enforced intent capability, derived from the
# design's mechanism→capability mapping table. Order follows INTENT_CAPABILITIES.
# Every enforced capability is blocking on a fully-faithful target; the descriptions
# are neutral (no hook/allowlist names). enforce-lease participates in two arms
# (the per-task lease lifecycle and the per-agent path scope); its defining
# enforcement is the lease lifecycle, recorded here as orchestrator-lifecycle.
_BLOCKING_SEMANTICS = (
    BlockingSemantic(
        capability="enforce-lease",
        enforcement_point="orchestrator-lifecycle",
        blocking=True,
        description=(
            "Edits are confined to the task's write lease and the agent's "
            "declared write scope; out-of-scope writes are blocked."
        ),
    ),
    BlockingSemantic(
        capability="block-dangerous",
        enforcement_point="PreToolUse",
        blocking=True,
        description="Dangerous commands are blocked before execution.",
    ),
    BlockingSemantic(
        capability="protect-sensitive",
        enforcement_point="PreToolUse",
        blocking=True,
        description=(
            "Access to sensitive files and cross-boundary edits is blocked."
        ),
    ),
    BlockingSemantic(
        capability="format",
        enforcement_point="PostToolUse",
        blocking=False,
        description="Edited files are formatted after a successful edit.",
    ),
    BlockingSemantic(
        capability="typecheck",
        enforcement_point="PostToolUse",
        blocking=False,
        description="Edited files are type-checked after a successful edit.",
    ),
    BlockingSemantic(
        capability="budget",
        enforcement_point="SubagentStop",
        blocking=False,
        description="The change budget is reported when a subagent stops.",
    ),
)


def blocking_semantics() -> List[BlockingSemantic]:
    """Return the enforced-capability blocking-semantics records (REQ-029)."""
    return list(_BLOCKING_SEMANTICS)


def validate_declared_capabilities(declared: List[str]) -> List[str]:
    """Return warnings for any declared capability outside the vocabulary (REQ-039).

    A declared capability not in ``INTENT_CAPABILITIES`` yields one warning; valid
    capabilities yield none. Deterministic: warnings follow input order.
    """
    warnings: List[str] = []
    for cap in declared:
        if cap not in INTENT_CAPABILITIES:
            warnings.append(
                f"unknown intent capability {cap!r}; expected one of "
                f"{', '.join(INTENT_CAPABILITIES)}"
            )
    return warnings
