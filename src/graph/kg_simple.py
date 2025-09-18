from __future__ import annotations

import json
import os
import re
from typing import Dict, List

from src.logger import get_logger
from src.models import Chunk

LOG = get_logger(__name__)

DEVICES = r"(?:Source|Sink|DRP|DFP|UFP|Port|Cable|Device)"
REQ_MOD_MAP = {
    "shall": "requires",
    "must": "requires",
    "should": "recommends",
    "may": "permits",
}

REQ_PAT = re.compile(
    rf"\b({DEVICES})\b\s+(shall|must|should|may)\s+([^.;]+?)(?:\s+(?:when|if|under)\s+([^.;]+))?(?=[.;])",
    re.IGNORECASE,
)
PARAM_PAT = re.compile(
    r"\b(VBUS|VCONN|Rp|Rd|PDO|APDO|EPR|SPR|current|voltage|power|t[A-Za-z0-9_]+)\b"
    r"\s*(?:=|is|shall be|must be|should be|set to)\s*([^.;]+)",
    re.IGNORECASE,
)
CAP_PAT = re.compile(
    rf"\b({DEVICES})\b\s+(?:supports?|is capable of)\s+([^.;]+)", re.IGNORECASE
)
STATES = r"(?:Attach|Detach|Negotiation|Contract|Hard Reset|Soft Reset|PR_Swap|DR_Swap|FR_Swap|ErrorRecovery|Role\s*Swap)"
STATE_PAT = re.compile(
    rf"\b({DEVICES})\b\s+(?:enters|transitions to|leaves|exits)\s+({STATES})\b",
    re.IGNORECASE,
)


class TripleExtractor:
    """Encapsulate triple extraction logic from Chunk objects."""

    def __init__(self, include_meta: bool = True) -> None:
        self.include_meta = include_meta

    def _add_meta(self, triple: Dict, ch: Chunk) -> Dict:
        if self.include_meta:
            triple["section"] = ch.section_id
            triple["title"] = ch.title
        return triple

    def _extract_requirements(self, text: str, ch: Chunk) -> List[Dict]:
        out: List[Dict] = []
        for m in REQ_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": REQ_MOD_MAP.get(m.group(2).lower(), "states"),
                "object": m.group(3).strip(),
            }
            if self.include_meta and m.group(4):
                triple["condition"] = m.group(4).strip()
            out.append(self._add_meta(triple, ch))
        return out

    def _extract_capabilities(self, text: str, ch: Chunk) -> List[Dict]:
        out: List[Dict] = []
        for m in CAP_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": "supports",
                "object": m.group(2).strip(),
            }
            out.append(self._add_meta(triple, ch))
        return out

    def _extract_state_transitions(self, text: str, ch: Chunk) -> List[Dict]:
        out: List[Dict] = []
        for m in STATE_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": "transitions_to",
                "object": m.group(2),
            }
            out.append(self._add_meta(triple, ch))
        return out

    def _extract_parameters(self, text: str, ch: Chunk) -> List[Dict]:
        out: List[Dict] = []
        for m in PARAM_PAT.finditer(text):
            triple = {
                "subject": m.group(1),
                "relation": "equals",
                "object": m.group(2).strip(),
            }
            out.append(self._add_meta(triple, ch))
        return out

    def _extract_diagram_table_counts(self, ch: Chunk) -> List[Dict]:
        out: List[Dict] = []
        if ch.figures:
            out.append(
                {
                    "subject": f"{(ch.section_id or '').strip()} {ch.title}".strip(),
                    "relation": "has_diagram",
                    "object": str(len(ch.figures)),
                }
            )
        if ch.tables:
            out.append(
                {
                    "subject": f"{(ch.section_id or '').strip()} {ch.title}".strip(),
                    "relation": "has_table",
                    "object": str(len(ch.tables)),
                }
            )
        return out

    def extract(self, chunks: List[Chunk]) -> List[Dict]:
        triples: List[Dict] = []
        for ch in chunks:
            text = (ch.content or "").replace("\n", " ")
            triples.extend(self._extract_requirements(text, ch))
            triples.extend(self._extract_capabilities(text, ch))
            triples.extend(self._extract_state_transitions(text, ch))
            triples.extend(self._extract_parameters(text, ch))
            triples.extend(self._extract_diagram_table_counts(ch))
        LOG.info("Extracted %d triples from %d chunks", len(triples), len(chunks))
        return triples


class TripleWriter:
    """Write triples to JSONL/JSON files."""

    @staticmethod
    def write_jsonl(path: str, triples: List[Dict]) -> int:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        n = 0
        with open(path, "w", encoding="utf-8") as f:
            for t in triples:
                f.write(json.dumps(t, ensure_ascii=False))
                f.write("\n")
                n += 1
        LOG.info("Wrote %d triples to %s (jsonl)", n, path)
        return n

    @staticmethod
    def write_json(path: str, triples: List[Dict]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(triples, f, ensure_ascii=False, indent=2)
        LOG.info("Wrote %d triples to %s (json)", len(triples), path)


def extract_triples(chunks: List[Chunk], include_meta: bool = True) -> List[Dict]:
    extractor = TripleExtractor(include_meta=include_meta)
    return extractor.extract(chunks)


def write_triples_jsonl(path: str, triples: List[Dict]) -> int:
    return TripleWriter.write_jsonl(path, triples)


def write_triples_json(path: str, triples: List[Dict]) -> None:
    return TripleWriter.write_json(path, triples)
