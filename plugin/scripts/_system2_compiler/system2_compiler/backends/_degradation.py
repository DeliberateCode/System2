"""Shared descriptor-driven degradation reporting."""

from typing import Dict, Set, Tuple

# Map each status to its enforced and gated flags.
_FLAG_RULE = {
    "native": (True, False),
    "adapted": (False, True),
    "advisory": (False, False),
    "unsupported": (False, False),
}


def ir_capability_union(capabilities_by_agent: Dict[str, list]) -> Set[str]:
    """The union of intent capabilities present in the IR."""
    union: Set[str] = set()
    for caps in capabilities_by_agent.values():
        union.update(caps)
    return union


def _record_for(entry: dict, fields: Tuple[str, ...]) -> dict:
    """Assemble one capability record with keys inserted in ``fields`` order."""
    status = entry.get("status")
    enforced, gated = _FLAG_RULE.get(status, (False, False))
    values = {
        "status": status,
        "mechanism": entry.get("mechanism"),
        "enforced": enforced,
        "gated": gated,
    }
    return {field: values[field] for field in fields}


def build_capability_records(
    descriptor: dict,
    ir_capability_union: Set[str],
    *,
    fields: Tuple[str, ...],
    allow_native: bool = True,
) -> Dict[str, dict]:
    """Build the ordered ``{cap: {<fields...>}}`` per-capability report records."""
    descriptor_caps = descriptor.get("capabilities", {})

    missing = sorted(ir_capability_union - set(descriptor_caps))
    if missing:
        raise ValueError(
            "degradation report would silently drop IR capabilities absent from "
            f"the descriptor: {missing}"
        )

    records: Dict[str, dict] = {}
    for cap, entry in descriptor_caps.items():
        if cap not in ir_capability_union:
            continue
        if not allow_native and entry.get("status") == "native":
            raise ValueError(
                f"descriptor reports {cap!r} as native but allow_native is False"
            )
        records[cap] = _record_for(entry, fields)
    return records
