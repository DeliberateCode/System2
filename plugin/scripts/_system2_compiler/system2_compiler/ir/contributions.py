"""Contribution indexing and within-scope topological ordering."""

from typing import Dict, List, Optional, Tuple


def build_contribution_index(
    manifests: List[dict],
    valid_anchors_by_agent: Optional[Dict[str, List[str]]] = None,
) -> dict:
    """Build a map of (type, target) -> [(overlay_name, contribution)]."""
    index: Dict[Tuple[str, str], List[Tuple[str, dict]]] = {}

    for manifest in manifests:
        overlay_name = manifest.get("name", "<unknown>")
        contribs = manifest.get("contributions", {})

        # orchestrator.principles
        orch = contribs.get("orchestrator", {})
        for entry in orch.get("principles", []):
            key = ("orchestrator.principles", "orchestrator.principles")
            index.setdefault(key, []).append((overlay_name, entry))

        # orchestrator.gates.<N>.consultation
        for gate_num, gate_obj in orch.get("gates", {}).items():
            scope = f"orchestrator.gates.{gate_num}.consultation"
            for entry in gate_obj.get("consultation", []):
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))

        # delegation.advisory_sources
        deleg = contribs.get("delegation", {})
        for entry in deleg.get("advisory_sources", []):
            key = ("delegation.advisory_sources", "delegation.advisory_sources")
            index.setdefault(key, []).append((overlay_name, entry))

        # agents.<name>.prompt_sections.<anchor>
        agents_block = contribs.get("agents", {})
        for agent_name, agent_obj in agents_block.items():
            for anchor_name, entries in agent_obj.get("prompt_sections", {}).items():
                if valid_anchors_by_agent is not None:
                    agent_anchors = set(valid_anchors_by_agent.get(agent_name, []))
                    if anchor_name not in agent_anchors:
                        continue
                scope = f"agents.{agent_name}.prompt_sections.{anchor_name}"
                for entry in entries:
                    key = (scope, scope)
                    index.setdefault(key, []).append((overlay_name, entry))
            # agents.<name>.tools
            for entry in agent_obj.get("tools", []):
                scope = f"agents.{agent_name}.tools"
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))
            # agents.<name>.hooks
            for entry in agent_obj.get("hooks", []):
                scope = f"agents.{agent_name}.hooks"
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))

        # spec.<artifact>.required_sections
        spec_block = contribs.get("spec", {})
        for artifact_name, artifact_obj in spec_block.items():
            for entry in artifact_obj.get("required_sections", []):
                scope = f"spec.{artifact_name}.required_sections"
                key = (scope, scope)
                index.setdefault(key, []).append((overlay_name, entry))

        # auxiliary_agents — keyed by agent name for collision detection
        for entry in contribs.get("auxiliary_agents", []):
            agent_name = entry.get("name", "<unnamed>")
            key = ("auxiliary_agents", agent_name)
            index.setdefault(key, []).append((overlay_name, entry))

        # mcp_servers
        for entry in contribs.get("mcp_servers", []):
            key = ("mcp_servers", "mcp_servers")
            index.setdefault(key, []).append((overlay_name, entry))

        # permissions
        for entry in contribs.get("permissions", []):
            key = ("permissions", "permissions")
            index.setdefault(key, []).append((overlay_name, entry))

    return index


# Topological sorting within a scope

def topological_sort(
    contributions: List[Tuple[str, dict]], scope: str
) -> List[Tuple[str, dict]]:
    """Topologically sort contributions using after-declarations."""
    # Pre-sort entries so dependency resolution is independent of CLI order.
    def _sort_key(idx: int) -> Tuple[str, str]:
        oname, entry = contributions[idx]
        return (oname, entry.get("id", ""))

    sorted_indices = sorted(range(len(contributions)), key=_sort_key)

    id_to_idx: Dict[str, int] = {}
    unresolved_after: List[str] = []
    for i in sorted_indices:
        oname, entry = contributions[i]
        cid = entry.get("id")
        if cid is not None:
            if cid in id_to_idx:
                prev_oname = contributions[id_to_idx[cid]][0]
                if prev_oname != oname:
                    unresolved_after.append(
                        f"contribution ID {cid!r} in scope {scope!r} appears "
                        f"in overlays {prev_oname!r} and {oname!r}; 'after' "
                        f"references to this ID will resolve to {prev_oname!r}"
                    )
            else:
                id_to_idx[cid] = i

    n = len(contributions)
    # Build adjacency: edges[i] = list of j where j must come after i
    edges: Dict[int, List[int]] = {i: [] for i in range(n)}
    in_degree = [0] * n

    for i, (oname, entry) in enumerate(contributions):
        after = entry.get("after")
        if after is None:
            continue
        if after in id_to_idx:
            dep_idx = id_to_idx[after]
            edges[dep_idx].append(i)
            in_degree[i] += 1
        else:
            cid = entry.get("id", f"index-{i}")
            unresolved_after.append(
                f"{oname}/{cid}: after target {after!r} not found in scope "
                f"{scope!r}; ordering will fall back to lexicographic"
            )

    # Kahn's algorithm with stable tie-breaking.
    def sort_key(idx: int) -> Tuple[str, str]:
        overlay_name, entry = contributions[idx]
        return (overlay_name, entry.get("id", ""))

    # Start with nodes that have no dependencies
    queue = sorted(
        [i for i in range(n) if in_degree[i] == 0],
        key=sort_key,
    )
    result: List[Tuple[str, dict]] = []

    while queue:
        idx = queue.pop(0)
        result.append(contributions[idx])
        for neighbor in sorted(edges[idx], key=sort_key):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                # Insert into queue maintaining sorted order
                queue.append(neighbor)
                queue.sort(key=sort_key)

    if len(result) != n:
        # Find the contributions participating in the cycle.
        cycle_ids = [
            contributions[i][1].get("id", f"<index-{i}>")
            for i in range(n)
            if in_degree[i] > 0
        ]
        raise ValueError(
            f"Cycle detected in after-declarations within scope "
            f"{scope!r}: {cycle_ids}"
        )

    return result, unresolved_after
