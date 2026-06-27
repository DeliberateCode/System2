"""Front-end assembly: build a ``System2Graph`` from validated inputs.

Lifts the front-end half of ``composer.compose()`` (load / validate / conflict /
index / topological sort / base-template read / version read) and the resolution
half of ``_activate_profile`` (profile name -> ordered overlay paths). Produces the
neutral graph **without invoking any backend** (REQ-010/013/040): this module
imports no ``backends/`` package and no ``cli``.

The structured inventory fields (``roles``, ``gate_graph``, ``delegation_contract``,
``post_execution``, ``maintenance_loop``, ``spec_artifacts``) are derived inventories
for structural assertions and future backends. The byte-output path for the Claude
backend in this cycle is ``base_template`` (opaque base CLAUDE.md text + located
section offsets) plus the ordered ``contributions`` — NOT these structured fields.
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from . import anchors as _anchors
from . import capabilities as _capabilities
from . import contributions as _contributions
from .anchors import AnchorTable
from .capabilities import CapabilitySet
from .graph import (
    BaseTemplate,
    Contribution,
    DelegationContract,
    GateGraph,
    GateNode,
    MaintenanceLoop,
    OrderedContributions,
    OverlayInput,
    PostExecution,
    ProfileRef,
    Role,
    SpecArtifact,
    System2Graph,
    TriggerRule,
    Warnings,
)

SCHEMA_VERSION = "system2-graph/1.0.0"

# Suffixes whose contributions are deferred (rendered by the backend, not
# enumerated as applied IDs). Mirrors composer._DEFERRED_SUFFIXES.
_DEFERRED_SUFFIXES = (".tools", ".hooks")

_SECTION_RE = re.compile(r"^## (.+)$")
_GATE_CHECKLIST_RE = re.compile(r"^- Gate (\d+) \(([^)]+)\): (.+)$")
_DELEGATION_RE = re.compile(r"^\d+\) (?:system2:)?([a-z0-9-]+):")


# ---------------------------------------------------------------------------
# Base template + version (lifted verbatim from composer.compose / version read)
# ---------------------------------------------------------------------------

def _load_base_template(base_path: str) -> str:
    """Read the base CLAUDE.md template text.

    Tries the init skill template first (works in an installed plugin), then
    falls back to the sibling CLAUDE.md (works in a repo checkout). Returns the
    empty string when neither is available.
    """
    base_claude_md = ""
    init_skill_path = os.path.join(base_path, "skills", "init", "SKILL.md")
    repo_claude_path = os.path.join(os.path.dirname(base_path), "CLAUDE.md")

    if os.path.isfile(init_skill_path):
        try:
            with open(init_skill_path, "r", encoding="utf-8") as fh:
                skill_content = fh.read()
            begin = skill_content.find("---BEGIN TEMPLATE---")
            end = skill_content.find("---END TEMPLATE---")
            if begin != -1 and end != -1:
                begin += len("---BEGIN TEMPLATE---\n")
                base_claude_md = skill_content[begin:end].rstrip("\n") + "\n"
        except OSError:
            pass

    if not base_claude_md and os.path.isfile(repo_claude_path):
        try:
            with open(repo_claude_path, "r", encoding="utf-8") as fh:
                base_claude_md = fh.read()
        except OSError:
            pass

    return base_claude_md


def _read_system2_version(base_path: str) -> str:
    """Read the System2 version from plugin.json (installed) or VERSION (repo)."""
    system2_version = "unknown"
    plugin_json_path = os.path.join(base_path, ".claude-plugin", "plugin.json")
    repo_version_path = os.path.join(os.path.dirname(base_path), "VERSION")
    if os.path.isfile(plugin_json_path):
        try:
            with open(plugin_json_path, "r", encoding="utf-8") as fh:
                pj = json.load(fh)
            system2_version = pj.get("version", "unknown")
        except (OSError, json.JSONDecodeError):
            pass
    if system2_version == "unknown" and os.path.isfile(repo_version_path):
        try:
            with open(repo_version_path, "r", encoding="utf-8") as fh:
                system2_version = fh.read().strip()
        except OSError:
            pass
    return system2_version


def _section_offsets(text: str) -> Dict[str, int]:
    """Locate each ``## <Section>`` heading's line index in the base template.

    These are the located offsets the Claude backend uses for insertion; the IR
    carries them but never renders them (that is the backend's job, TASK-108).
    """
    offsets: Dict[str, int] = {}
    for idx, line in enumerate(text.splitlines()):
        m = _SECTION_RE.match(line)
        if m and m.group(1) not in offsets:
            offsets[m.group(1)] = idx
    return offsets


def _section_text(text: str, heading: str) -> str:
    """Return the verbatim text of the ``## <heading>`` section (heading to next
    same-or-higher level heading), or the empty string if absent."""
    lines = text.splitlines(keepends=True)
    start = None
    for idx, line in enumerate(lines):
        m = _SECTION_RE.match(line.rstrip("\n"))
        if m and m.group(1) == heading:
            start = idx
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        s = lines[idx]
        if s.startswith("## ") or s.startswith("# "):
            end = idx
            break
    return "".join(lines[start:end])


# ---------------------------------------------------------------------------
# Ordered contributions (lifted index + topological sort)
# ---------------------------------------------------------------------------

_ANCHOR_SCOPE_RE = re.compile(r"^agents\.([^.]+)\.prompt_sections\.(.+)$")


def _build_ordered_contributions(
    validated_manifests: List[dict],
    overlay_path_map: Dict[str, str],
    anchor_table: AnchorTable,
) -> Tuple[OrderedContributions, List[str]]:
    """Build the per-scope, topologically-sorted ``OrderedContributions``.

    Mirrors the index + topo-sort the oracle performs in ``compose``: unknown
    anchors are filtered by the identity-keyed ``anchor_table`` (so a contribution
    to a non-existent anchor is silently excluded exactly as the oracle excludes
    it); ordering cycles surface as a skipped scope (the structural-conflict
    refusal already fired upstream). Contributions in an ``agents.<agent>.
    prompt_sections.<anchor>`` scope carry the resolved ``(agent, anchor)``
    ``AnchorRef`` identity (REQ-025). Returns the ordered contributions plus any
    unresolved-after sort warnings.
    """
    index = _contributions.build_contribution_index(
        validated_manifests, anchor_table.anchors_by_agent()
    )
    scopes: Dict[Tuple[str, str], List[Contribution]] = {}
    sort_warnings: List[str] = []

    for (type_path, target), entries in index.items():
        try:
            sorted_entries, sw = _contributions.topological_sort(entries, type_path)
        except ValueError:
            continue
        sort_warnings.extend(sw)

        anchor_ref = None
        m = _ANCHOR_SCOPE_RE.match(type_path)
        if m:
            anchor_ref = anchor_table.resolve(m.group(1), m.group(2))

        records = [
            Contribution(
                overlay_name=oname,
                contribution_type=type_path,
                raw=contrib,
                overlay_path=overlay_path_map.get(oname, ""),
                contribution_id=contrib.get("id"),
                anchor=anchor_ref,
            )
            for oname, contrib in sorted_entries
        ]
        scopes[(type_path, target)] = records

    return OrderedContributions(scopes=scopes), sort_warnings


# ---------------------------------------------------------------------------
# Structured inventory derivation
# ---------------------------------------------------------------------------

# Explicit role -> allowlist-filename map. Agent names are NOT 1:1 with allowlist
# filenames, so the mapping is encoded rather than naive-filename-matched. A role
# absent from this map has no dedicated path allowlist and is read-only: its
# write_scope stays empty (no broad fallback that would over-permit). The source
# files live read-only under ``<base_path>/allowlists/*.regex``; ``write_scope``
# is a SINGLE-line path-allow regex (multi-line allowlists are OR-joined exactly as
# the reference ``_hook_utils.load_patterns`` does) the Pi lease check compiles into
# its per-path block.
_ROLE_ALLOWLISTS = {
    "repo-governor": "repo-governor.regex",
    "spec-coordinator": "spec-context.regex",
    "requirements-engineer": "spec-requirements.regex",
    "design-architect": "spec-design.regex",
    "task-planner": "spec-tasks.regex",
    "executor": "executor.regex",
    "test-engineer": "test-engineer.regex",
    "security-sentinel": "spec-security.regex",
    "eval-engineer": "spec-evals.regex",
    "docs-release": "docs-release.regex",
    "postmortem-scribe": "postmortems.regex",
    "mcp-toolsmith": "mcp.regex",
}


def _load_write_scope(name: str, base_path: str) -> str:
    """Return the path-allow regex for *name* from its mapped allowlist file.

    Read-only source under ``<base_path>/allowlists/``. Returns the empty string
    for a role with no dedicated allowlist (read-only role) or a missing file —
    never a broad fallback. Mirrors the reference ``_hook_utils.load_patterns``:
    blank lines and ``#``-comment lines are dropped, a single remaining pattern is
    returned verbatim, and multiple patterns are OR-joined into one valid regex as
    ``(?:p1)|(?:p2)|...``. So a multi-line allowlist (e.g. design-architect's three
    spec paths) becomes ONE alternation the Pi lease can compile, not a ``\\n``-joined
    string that ``new RegExp`` would brick into a deny-all.
    """
    filename = _ROLE_ALLOWLISTS.get(name)
    if not filename:
        return ""
    path = os.path.join(base_path, "allowlists", filename)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    patterns = [
        stripped
        for raw in lines
        if (stripped := raw.strip()) and not stripped.startswith("#")
    ]
    if not patterns:
        return ""
    if len(patterns) == 1:
        return patterns[0]
    return "|".join(f"(?:{pattern})" for pattern in patterns)


def _derive_roles(
    anchor_map: dict, capabilities: CapabilitySet, base_path: str
) -> List[Role]:
    """Derive the 13-agent role inventory from ``anchor-map.json`` (REQ-009/050).

    Roles are an intent inventory: name + the per-agent intent ``capabilities``
    (Phase 2, from ``capabilities.by_agent``) + ``write_scope`` sourced read-only
    from the mapped Claude per-agent ``.regex`` path allowlist (OQ-P3) so Pi's
    ``enforce-lease`` becomes a genuinely-scoped native lease. ``gate_role`` /
    ``model_hint`` stay neutral; no Claude mechanism fields appear (REQ-028/040).
    The claude backend never reads ``write_scope``, so its bytes are unchanged.
    """
    names = sorted(anchor_map.get("agents", {}).keys())
    return [
        Role(
            name=name,
            gate_role="",
            write_scope=_load_write_scope(name, base_path),
            model_hint=None,
            capabilities=list(capabilities.by_agent.get(name, [])),
            pipeline=True,
        )
        for name in names
    ]


def _derive_gate_graph(
    base_text: str, ordered: OrderedContributions
) -> GateGraph:
    """Derive Gate 0->5 nodes (+ ordered edges) from the base CLAUDE.md gate
    checklist, attaching per-gate overlay consultation contributions."""
    gates: List[GateNode] = []
    for line in base_text.splitlines():
        m = _GATE_CHECKLIST_RE.match(line)
        if not m:
            continue
        number = int(m.group(1))
        scope = f"orchestrator.gates.{number}.consultation"
        consultations = ordered.scopes.get((scope, scope), [])
        gates.append(
            GateNode(
                number=number,
                name=m.group(2),
                checklist_text=m.group(3),
                consultations=list(consultations),
            )
        )
    gates.sort(key=lambda g: g.number)
    edges = [
        (gates[i].number, gates[i + 1].number)
        for i in range(len(gates) - 1)
    ]
    return GateGraph(gates=gates, edges=edges)


def _derive_delegation_contract(
    base_text: str, ordered: OrderedContributions
) -> DelegationContract:
    """Derive the delegation contract: required fields (from the 'Delegation
    contract' section), the preferred 13-agent order (from the delegation map),
    and overlay-contributed advisory sources."""
    required_fields: List[str] = []
    section = _section_text(base_text, "Delegation contract")
    for line in section.splitlines():
        sm = re.match(r"^- ([A-Z][A-Za-z /-]+?)(?: \(| \[|:|$)", line.strip())
        if sm:
            required_fields.append(sm.group(1).strip())

    preferred_order: List[str] = []
    for line in base_text.splitlines():
        dm = _DELEGATION_RE.match(line.strip())
        if dm:
            preferred_order.append(dm.group(1))

    advisory_sources = list(
        ordered.scopes.get(
            ("delegation.advisory_sources", "delegation.advisory_sources"), []
        )
    )
    return DelegationContract(
        required_fields=required_fields,
        preferred_order=preferred_order,
        advisory_sources=advisory_sources,
    )


def _derive_post_execution(base_text: str) -> PostExecution:
    """Derive the post-execution policy as neutral structure + opaque section text.

    The opaque_text is the verbatim '## Post-Execution Workflow' section the
    Claude backend reproduces via base_template; the structured fields exist for
    structural assertions and future backends (design rationale)."""
    opaque = _section_text(base_text, "Post-Execution Workflow")
    boomerang_cap = 3
    cap_m = re.search(r"boomerang count[^\n]*reaches (\d+)", base_text)
    if cap_m:
        boomerang_cap = int(cap_m.group(1))
    execution_order = [
        "test-engineer",
        "code-reviewer (simplification)",
        "security-sentinel",
        "eval-engineer",
        "docs-release",
        "code-reviewer",
    ]
    trigger_rules = [
        TriggerRule(agent="test-engineer", condition="", always=True),
        TriggerRule(
            agent="code-reviewer (simplification)",
            condition="diff >50 lines or >2 files",
            always=False,
        ),
        TriggerRule(
            agent="security-sentinel",
            condition="changed path/content matches security patterns",
            always=False,
        ),
        TriggerRule(
            agent="eval-engineer",
            condition="changed file matches agent definitions / agentic patterns",
            always=False,
        ),
        TriggerRule(
            agent="docs-release",
            condition="changed file matches user-facing patterns",
            always=False,
        ),
        TriggerRule(agent="code-reviewer", condition="", always=True),
    ]
    blocker_policy = {
        "boomerang_cap": boomerang_cap,
        "on_blockers": "user-gate",
        "options": ["delegate-fix", "override", "abort"],
    }
    return PostExecution(
        trigger_rules=trigger_rules,
        execution_order=execution_order,
        blocker_policy=blocker_policy,
        boomerang_cap=boomerang_cap,
        opaque_text=opaque,
    )


def _derive_maintenance_loop(base_text: str) -> MaintenanceLoop:
    """Derive the maintenance/regression loop policy as neutral structure +
    opaque section text."""
    opaque = _section_text(base_text, "Maintenance / Regression Loop")
    corrective_cap = 3
    cap_m = re.search(r"After \*\*(\d+)\*\* corrective cycles", base_text)
    if cap_m:
        corrective_cap = int(cap_m.group(1))
    regression_ledger_fields = [
        "previously passing tests now failing",
        "newly passing tests",
        "unchanged failures",
        "likely failure cluster / root-cause area",
        "changed-file summary (files modified since last green run)",
    ]
    classification = ["Local", "Non-local"]
    return MaintenanceLoop(
        regression_ledger_fields=regression_ledger_fields,
        corrective_cycle_cap=corrective_cap,
        classification=classification,
        opaque_text=opaque,
    )


def _derive_capabilities(anchor_table: AnchorTable) -> CapabilitySet:
    """Derive the per-agent intent-capability set (REQ-028).

    Every enforced intent capability in ``INTENT_CAPABILITIES`` is native on the
    fully-faithful (Claude) target per the design's mechanism->capability table:
    the enforcement hooks (dangerous-command, sensitive-file/boundary, path/lease
    validation, formatter, type-checker, change-budget) are registered globally
    and apply to every pipeline agent's tool use — the composed surface does not
    differentiate them per agent. The per-agent set is therefore the full enforced
    vocabulary for each pipeline agent. This is IR-internal: the backend does not
    read it this cycle, so it changes no emitted bytes.
    """
    caps = list(_capabilities.INTENT_CAPABILITIES)
    by_agent = {agent: list(caps) for agent in anchor_table.by_agent}
    return CapabilitySet(by_agent=by_agent)


def _collect_declared_capabilities(validated_manifests: List[dict]) -> List[str]:
    """Collect intent capabilities an overlay explicitly declares, if any.

    Overlays may declare capabilities under a top-level ``capabilities`` list or a
    ``contributions.capabilities`` list; both are scanned so an unknown declared
    capability surfaces a validation warning (REQ-039). Absent in today's overlays;
    deterministic ordering by manifest order then declaration order.
    """
    declared: List[str] = []
    for man in validated_manifests:
        for cap in man.get("capabilities", []) or []:
            if isinstance(cap, str):
                declared.append(cap)
        contribs = man.get("contributions", {})
        for cap in contribs.get("capabilities", []) or []:
            if isinstance(cap, str):
                declared.append(cap)
    return declared


def _derive_spec_artifacts(
    schema: dict, ordered: OrderedContributions
) -> List[SpecArtifact]:
    """Derive the spec-artifact inventory (context/requirements/design/tasks +
    overlay-required sections)."""
    meta = schema.get("_meta", {})
    valid_artifacts = meta.get("valid_spec_artifacts", [])
    artifacts: List[SpecArtifact] = []
    for name in valid_artifacts:
        scope = f"spec.{name}.required_sections"
        required = list(ordered.scopes.get((scope, scope), []))
        artifacts.append(SpecArtifact(name=name, required_sections=required))
    return artifacts


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph(
    base_path: str,
    validated_manifests: List[dict],
    overlay_path_map: Dict[str, str],
    anchor_map: dict,
    schema: dict,
    project_path: str,
    profile: Optional[ProfileRef],
    warnings: Warnings,
) -> System2Graph:
    """Assemble a ``System2Graph`` from validated inputs.

    Builds the ordered contributions, the base template (text + located section
    offsets), the version, and all structured inventory fields, and (Phase 2)
    the identity-keyed ``anchors`` table, the per-agent ``capabilities`` set, and
    the ``blocking_semantics`` records. Anchored contributions carry their
    ``(agent, anchor)`` ``AnchorRef`` identity. Invokes no backend; these Phase-2
    fields are IR-internal and change no emitted bytes this cycle.
    """
    anchor_table = _anchors.build_anchor_table(anchor_map)
    ordered, sort_warnings = _build_ordered_contributions(
        validated_manifests, overlay_path_map, anchor_table
    )
    for sw in sort_warnings:
        if sw not in warnings.validation:
            warnings.validation.append(sw)

    capabilities = _derive_capabilities(anchor_table)
    declared = _collect_declared_capabilities(validated_manifests)
    for cw in _capabilities.validate_declared_capabilities(declared):
        if cw not in warnings.validation:
            warnings.validation.append(cw)

    base_text = _load_base_template(base_path)
    base_template = BaseTemplate(
        text=base_text,
        section_offsets=_section_offsets(base_text),
    )
    system2_version = _read_system2_version(base_path)

    overlay_inputs = [
        OverlayInput(
            manifest=man,
            source_path=overlay_path_map.get(man.get("name", "<unknown>"), ""),
        )
        for man in validated_manifests
    ]

    return System2Graph(
        schema_version=SCHEMA_VERSION,
        system2_version=system2_version,
        roles=_derive_roles(anchor_map, capabilities, base_path),
        gate_graph=_derive_gate_graph(base_text, ordered),
        delegation_contract=_derive_delegation_contract(base_text, ordered),
        post_execution=_derive_post_execution(base_text),
        maintenance_loop=_derive_maintenance_loop(base_text),
        spec_artifacts=_derive_spec_artifacts(schema, ordered),
        contributions=ordered,
        active_profile=profile,
        anchors=anchor_table,
        capabilities=capabilities,
        blocking_semantics=_capabilities.blocking_semantics(),
        warnings=warnings,
        base_template=base_template,
        overlay_inputs=overlay_inputs,
    )
