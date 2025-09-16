import os
import json
import re
from statistics import mean
from typing import List, Dict, Any
from ..models import (
    ToCEntry,
    Chunk,
)  # assumes pydantic-like models with .dict() accessors


def _avg_words(chunks: List[Chunk]) -> float:
    """
    Average word count across chunk.contents that are non-empty.
    """
    words_per = [
        len((ch.content or "").split()) for ch in chunks if (ch.content or "").strip()
    ]
    return mean(words_per) if words_per else 0.0


def _approx_tokens_from_words(words: float) -> int:
    """
    Quick words→tokens heuristic (~1 token per 1.3 words).
    """
    return int(round(words / 1.3)) if words else 0


def _figure_is_table(fig: Any) -> bool:
    """
    Treat a figure as a table if it exposes a 'kind' or 'type' attribute equal to 'table' (case-insensitive).
    Falls back to dict-style access if needed.
    """
    kind = getattr(fig, "kind", None) or getattr(fig, "type", None)
    if isinstance(kind, str) and kind.lower() == "table":
        return True

    # Dict-style access
    if isinstance(fig, dict):
        k = fig.get("kind") or fig.get("type") or ""
        return isinstance(k, str) and k.lower() == "table"

    return False


def _count_tables_in_chunk(ch: Chunk) -> int:
    """
    Count tables attached to this chunk from:
      1) ch.tables (preferred)
      2) ch.figures items flagged as tables
    """
    tables_count = len(getattr(ch, "tables", None) or [])
    figs = getattr(ch, "figures", None) or []
    tables_count += sum(1 for f in figs if _figure_is_table(f))
    return tables_count


def _has_any_table(ch: Chunk) -> bool:
    return _count_tables_in_chunk(ch) > 0


def _has_any_figure(ch: Chunk) -> bool:
    """
    True if there are any figures at all (regardless of type).
    """
    return bool(getattr(ch, "figures", None) or [])


CHAPTER_HEAD_RX = re.compile(r"^\s*(\d+)\b")
ANY_INT_TOKEN_RX = re.compile(r"\b(\d{1,3})\b")


def _chapter_bucket_from_fields(
    section_id: str, title: str = "", section_path: str = ""
) -> str | None:
    """
    Extract a chapter number using multiple fallbacks:
    1) Leading number from section_id (e.g., '10' from '10.3.2a')
    2) Leading number from title or section_path (e.g., '10' from '10 Power Rules')
    3) Any standalone integer token in title/section_path (last resort)
    """
    sid = (section_id or "").strip()
    ttl = (title or "").strip()
    sp = (section_path or "").strip()

    m = CHAPTER_HEAD_RX.match(sid)
    if m:
        return m.group(1)

    for s in (ttl, sp):
        m = CHAPTER_HEAD_RX.match(s)
        if m:
            return m.group(1)

    for s in (ttl, sp):
        m = ANY_INT_TOKEN_RX.search(s)
        if m:
            return m.group(1)

    return None


def compute_metrics(toc: List[ToCEntry], chunks: List[Chunk]) -> Dict[str, Any]:
    """
    Compute high-level document metrics from ToC and parsed chunks.

    Notes:
    - Tables are counted from both `ch.tables` and any `ch.figures` marked as tables.
    - 'sections_without_diagrams' means: no figures AND no tables (using the robust table detection).
    """
    toc_chapters = sorted(
        {
            b
            for b in (
                _chapter_bucket_from_fields(
                    getattr(e, "section_id", ""),
                    getattr(e, "title", ""),
                    getattr(e, "full_path", ""),
                )
                for e in toc
            )
            if b is not None
        }
    )

    chunk_chapters = sorted(
        {
            b
            for b in (
                _chapter_bucket_from_fields(
                    getattr(c, "section_id", ""),
                    getattr(c, "title", ""),
                    getattr(c, "section_path", ""),
                )
                for c in chunks
            )
            if b is not None
        }
    )

    total_chapters = max(len(toc_chapters), len(chunk_chapters))
    total_sections = len(toc)

    total_figures = sum(len(getattr(ch, "figures", None) or []) for ch in chunks)
    total_tables = sum(_count_tables_in_chunk(ch) for ch in chunks)

    avg_words = _avg_words(chunks)
    avg_tokens_per_section = _approx_tokens_from_words(avg_words)

    sections_without_tables = [
        f"{ch.section_id} {ch.title}".strip() for ch in chunks if not _has_any_table(ch)
    ]
    sections_without_diagrams = [
        f"{ch.section_id} {ch.title}".strip()
        for ch in chunks
        if (not _has_any_figure(ch)) and (not _has_any_table(ch))
    ]

    return {
        "total_chapters": total_chapters,
        "total_sections": total_sections,
        "total_figures": total_figures,
        "total_tables": total_tables,
        "avg_tokens_per_section": avg_tokens_per_section,
        "sections_without_diagrams": sections_without_diagrams,
        "sections_without_tables": sections_without_tables,
    }


def write_metrics(out_path: str, metrics: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
