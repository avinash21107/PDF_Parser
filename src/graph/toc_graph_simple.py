from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from src.logger import get_logger
from src.models import ToCEntry

LOG = get_logger(__name__)


def _infer_parent(sec_id: str) -> Optional[str]:
    """Infer parent section from dotted section numbering (e.g., 1.2 → 1)."""
    if "." not in sec_id:
        return None
    return ".".join(sec_id.split(".")[:-1]) or None


class TocGraphBuilder:
    """
    Builds a directed graph from ToCEntry objects.

    Graph representation:
    - nodes: sections with id, title, page, level
    - links: parent-child relationships inferred from explicit parent_id
             or dotted section numbering.
    """

    def __init__(self, toc: List[ToCEntry]) -> None:
        self.toc = toc
        self.nodes: Dict[str, Dict] = {}
        self.links: List[Tuple[str, str]] = []

    def build(self) -> Dict[str, object]:
        """Construct the graph dictionary with nodes and links."""
        self._add_nodes()
        self._add_links()
        graph = {
            "directed": True,
            "multigraph": False,
            "graph": {},
            "nodes": list(self.nodes.values()),
            "links": [{"source": s, "target": t} for s, t in self.links],
        }
        LOG.info(
            "Built ToC graph with %d nodes and %d links",
            len(self.nodes),
            len(self.links),
        )
        return graph

    def _add_nodes(self) -> None:
        """Add nodes for each ToCEntry."""
        for entry in self.toc:
            self.nodes[entry.section_id] = {
                "id": entry.section_id,
                "title": entry.title or entry.section_id,
                "page": entry.page,
                "level": entry.level,
            }

    def _add_links(self) -> None:
        """Add parent-child links based on parent_id or inferred parent."""
        for entry in self.toc:
            parent = entry.parent_id or _infer_parent(entry.section_id)
            if parent:
                if parent not in self.nodes:
                    self.nodes[parent] = {
                        "id": parent,
                        "title": parent,
                        "page": None,
                        "level": parent.count(".") + 1,
                    }
                self.links.append((parent, entry.section_id))


class TocGraphWriter:
    """Writes a ToC graph dictionary to JSON file."""

    @staticmethod
    def write_graph_json(out_path: str, graph: Dict[str, object]) -> None:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        LOG.info("Wrote ToC graph to %s", out_path)
