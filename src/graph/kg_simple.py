
import os, json, re
from typing import List, Dict
from src.models import Chunk

DEVICES = r"(?:Source|Sink|DRP|DFP|UFP|Port|Cable|Device)"
REQ_MOD_MAP = {"shall": "requires", "must": "requires", "should": "recommends", "may": "permits"}

REQ_PAT = re.compile(
    fr"\b({DEVICES})\b\s+(shall|must|should|may)\s+([^.;]+?)(?:\s+(?:when|if|under)\s+([^.;]+))?(?=[.;])",
    re.IGNORECASE,
)
PARAM_PAT = re.compile(
    r"\b(VBUS|VCONN|Rp|Rd|PDO|APDO|EPR|SPR|current|voltage|power|t[A-Za-z0-9_]+)\b"
    r"\s*(?:=|is|shall be|must be|should be|set to)\s*([^.;]+)",
    re.IGNORECASE,
)
CAP_PAT = re.compile(
    fr"\b({DEVICES})\b\s+(?:supports?|is capable of)\s+([^.;]+)",
    re.IGNORECASE,
)
STATES = r"(?:Attach|Detach|Negotiation|Contract|Hard Reset|Soft Reset|PR_Swap|DR_Swap|FR_Swap|ErrorRecovery|Role\s*Swap)"
STATE_PAT = re.compile(
    fr"\b({DEVICES})\b\s+(?:enters|transitions to|leaves|exits)\s+({STATES})\b",
    re.IGNORECASE,
)

def extract_triples(chunks: List[Chunk], include_meta: bool = True) -> List[Dict]:
    out: List[Dict] = []
    for ch in chunks:
        text = (ch.content or "").replace("\n", " ")

        for m in REQ_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": REQ_MOD_MAP.get(m.group(2).lower(), "states"),
                "object": m.group(3).strip(),
            }
            if include_meta and m.group(4):
                triple["condition"] = m.group(4).strip()
            if include_meta:
                triple["section"], triple["title"] = ch.section_id, ch.title
            out.append(triple)

        for m in CAP_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": "supports",
                "object": m.group(2).strip(),
            }
            if include_meta:
                triple["section"], triple["title"] = ch.section_id, ch.title
            out.append(triple)

        for m in STATE_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": "transitions_to",
                "object": m.group(2),
            }
            if include_meta:
                triple["section"], triple["title"] = ch.section_id, ch.title
            out.append(triple)

        for m in PARAM_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": "equals",
                "object": m.group(2).strip(),
            }
            if include_meta:
                triple["section"], triple["title"] = ch.section_id, ch.title
            out.append(triple)

        if ch.figures:
            out.append({
                "subject": f"{(ch.section_id or '').strip()} {ch.title}".strip(),
                "relation": "has_diagram",
                "object": str(len(ch.figures)),
            })
        if ch.tables:
            out.append({
                "subject": f"{(ch.section_id or '').strip()} {ch.title}".strip(),
                "relation": "has_table",
                "object": str(len(ch.tables)),
            })

    return out

def write_triples_jsonl(path: str, triples: List[Dict]) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for t in triples:
            f.write(json.dumps(t, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n

def write_triples_json(path: str, triples: List[Dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(triples, f, ensure_ascii=False, indent=2)
