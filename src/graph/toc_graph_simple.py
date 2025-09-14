
import os, json
from typing import List, Dict, Tuple, Optional
from src.models import ToCEntry

def _infer_parent(sec_id: str) -> Optional[str]:
    return ".".join(sec_id.split(".")[:-1]) or None if "." in sec_id else None

def build_toc_graph(toc: List[ToCEntry]) -> Dict:
    nodes: Dict[str, Dict] = {}
    links: List[Tuple[str, str]] = []

    for e in toc:
        nodes[e.section_id] = {
            "id": e.section_id,
            "title": e.title or e.section_id,
            "page": e.page,
            "level": e.level,
        }

    for e in toc:
        parent = e.parent_id or _infer_parent(e.section_id)
        if parent:
            if parent not in nodes:
                nodes[parent] = {
                    "id": parent,
                    "title": parent,
                    "page": None,
                    "level": parent.count(".") + 1,
                }
            links.append((parent, e.section_id))

    return {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": list(nodes.values()),
        "links": [{"source": s, "target": t} for s, t in links],
    }

def write_graph_json(out_path: str, G: Dict) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(G, f, ensure_ascii=False, indent=2)
