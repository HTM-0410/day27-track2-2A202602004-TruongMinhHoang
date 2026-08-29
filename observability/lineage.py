from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def load_graph(path: str | Path) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    graph = payload["dataset_lineage"] if "dataset_lineage" in payload else payload
    _validate_graph(graph)
    return {str(node): list(children) for node, children in graph.items()}


def _validate_graph(graph: Any) -> None:
    if not isinstance(graph, Mapping):
        raise TypeError("graph must be a mapping of node to child sequence")
    for node, children in graph.items():
        if not isinstance(node, str):
            raise TypeError("graph node names must be strings")
        if isinstance(children, (str, bytes)) or not isinstance(children, Sequence):
            raise TypeError(f"adjacency for {node!r} must be a sequence of strings")
        if any(not isinstance(child, str) for child in children):
            raise TypeError(f"adjacency for {node!r} contains a non-string node")


def _unwrap(graph: dict[str, Any], key: str) -> dict[str, list[str]]:
    if not isinstance(graph, Mapping):
        raise TypeError("graph must be a mapping of node to child sequence")
    nested = graph.get(key)
    candidate: Any = nested if isinstance(nested, Mapping) else graph
    _validate_graph(candidate)
    return candidate


def _transitive_bfs(graph: dict[str, list[str]], start: str) -> list[str]:
    if not isinstance(start, str):
        raise TypeError("start node must be a string")
    if start not in graph:
        return []
    seen = {start}
    queue: deque[str] = deque([start])
    out: list[str] = []
    while queue:
        node = queue.popleft()
        for child in graph.get(node, []):
            if child not in seen:
                seen.add(child)
                out.append(child)
                queue.append(child)
    return out


def get_downstream_assets(graph: dict[str, list[str]], start: str) -> list[str]:
    """Return unique transitive downstream assets in deterministic BFS order."""
    return _transitive_bfs(_unwrap(graph, "dataset_lineage"), start)


def get_column_downstream(
    column_graph: dict[str, list[str]], start_column: str
) -> list[str]:
    """Return transitive downstream columns, cycle-safe and in BFS order."""
    return _transitive_bfs(_unwrap(column_graph, "column_lineage"), start_column)


def extract_dbt_dataset_graph(manifest_path: str | Path) -> dict[str, list[str]]:
    """Extract a validated dataset dependency graph from a dbt manifest."""
    path = Path(manifest_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    child_map = manifest.get("child_map", {})
    _validate_graph(child_map)
    return {parent: list(children) for parent, children in child_map.items()}
