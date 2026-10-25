"""Intent-capability vocabulary, role attributes, and blocking semantics."""

from dataclasses import dataclass, field
from typing import Dict, List


# The fixed intent-capability vocabulary. Exactly these six terms.
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
    """Honest description of an enforced capability's blocking behavior."""

    capability: str
    enforcement_point: str
    blocking: bool
    description: str


@dataclass(frozen=True)
class CapabilitySet:
    """Per-agent intent capabilities."""

    by_agent: Dict[str, List[str]] = field(default_factory=dict)


# Keep blocking semantics harness-neutral and ordered with INTENT_CAPABILITIES.
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
    """Return the enforced-capability blocking-semantics records."""
    return list(_BLOCKING_SEMANTICS)


def validate_declared_capabilities(declared: List[str]) -> List[str]:
    """Return warnings for any declared capability outside the vocabulary."""
    warnings: List[str] = []
    for cap in declared:
        if cap not in INTENT_CAPABILITIES:
            warnings.append(
                f"unknown intent capability {cap!r}; expected one of "
                f"{', '.join(INTENT_CAPABILITIES)}"
            )
    return warnings
