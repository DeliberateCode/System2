"""Phase 4: the shared, descriptor-driven degradation helper.

Backend-agnostic; lifts the per-capability report-record assembly and the
status->flags rule out of each backend's own ``_build_degradation_report`` so all
backends (claude_code/pi/codex) share one source of truth.

Internal, stdlib-only, no I/O, no ``ir/*`` import: a backend hands it
``ir.capabilities.by_agent`` as plain data plus its own already-parsed descriptor
dict, so this stays a pure function and respects the module boundary.

BYTE-PRESERVING: each backend keeps its own envelope + ``fields`` selection, so the
serialized bytes of each backend's lock ``degradation_report`` are unchanged. The
status->flags rule is total over the
four-value enum (native => enforced:true, gated:false; adapted => enforced:false,
gated:true; advisory/unsupported => both false), which makes Pi's MIXED status
correct by construction.
"""

from typing import Dict, Set, Tuple

# The status -> (enforced, gated) flag derivation, total over the four-value enum.
# native: a deterministic pre-execution block. adapted: a gate, not a block.
# advisory / unsupported: neither.
_FLAG_RULE = {
    "native": (True, False),
    "adapted": (False, True),
    "advisory": (False, False),
    "unsupported": (False, False),
}


def ir_capability_union(capabilities_by_agent: Dict[str, list]) -> Set[str]:
    """The union of intent capabilities present in the IR.

    This is the same set both existing builders compute today: the union over
    ``ir.capabilities.by_agent`` values. Returned as a set; callers that need a
    deterministic order derive it from the descriptor order in
    ``build_capability_records``.
    """
    union: Set[str] = set()
    for caps in capabilities_by_agent.values():
        union.update(caps)
    return union


def _record_for(entry: dict, fields: Tuple[str, ...]) -> dict:
    """Assemble one capability record with keys inserted in ``fields`` order.

    Insertion order is the byte-fidelity crux: Python dicts are insertion-ordered
    and ``json.dumps`` serializes in that order, so the record's key order is
    exactly ``fields``. ``status``/``mechanism`` come straight from the descriptor
    entry; ``enforced``/``gated`` are derived from the status via ``_FLAG_RULE``.
    """
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
    """Build the ordered ``{cap: {<fields...>}}`` per-capability report records.

    Capability order is the descriptor's ``capabilities`` order filtered to the
    IR-present union (exactly today's iteration + filter for both backends). Each
    record's keys are inserted in ``fields`` order. ``status``/``mechanism`` come
    from the descriptor entry; ``enforced`` := status == "native"; ``gated`` :=
    status == "adapted".

    Raises ``ValueError`` (no silent drop) if an IR-present capability is absent
    from the descriptor. Raises ``ValueError`` when ``allow_native`` is False and a
    selected capability's status is ``native`` (Codex's nothing-native guard).
    """
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
