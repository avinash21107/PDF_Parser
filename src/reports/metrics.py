from __future__ import annotations

import json
import os
import re
from statistics import mean
from typing import Any, Dict, List

from src.logger import get_logger
from ..models import Chunk, ToCEntry

LOG = get_logger(__name__)

_CHAPTER_HEAD_RX = re.compile(r"^\s*(\d+)\b")
_ANY_INT_TOKEN_RX = re.compile(r"\b(\d{1,3})\b")


class MetricsCalculator:
    """Encapsulate metrics computation and IO for the reports.metrics module."""

    @staticmethod
    def _avg_words(chunks: List[Chunk]) -> float:
        words_per = [
            len((ch.content or "").split())
            for ch in chunks
            if (ch.content or "").strip()
        ]
        return mean(words_per) if words_per else 0.0

    @staticmethod
    def _approx_tokens_from_words(words: float) -> int:
        return int(round(words / 1.3)) if words else 0

    @staticmethod
    def _figure_is_table(fig: Any) -> bool:
        kind = getattr(fig, "kind", None) or getattr(fig, "type", None)
        if isinstance(kind, str) and kind.lower() == "table":
            return True
        if isinstance(fig, dict):
            k = fig.get("kind") or fig.get("type") or ""
            return isinstance(k, str) and k.lower() == "table"
        return False

    @classmethod
    def _count_tables_in_chunk(cls, ch: Chunk) -> int:
        tables_count = len(getattr(ch, "tables", None) or [])
        figs = getattr(ch, "figures", None) or []
        tables_count += sum(1 for f in figs if cls._figure_is_table(f))
        return tables_count

    @classmethod
    def _has_any_table(cls, ch: Chunk) -> bool:
        return cls._count_tables_in_chunk(ch) > 0

    @staticmethod
    def _has_any_figure(ch: Chunk) -> bool:
        return bool(getattr(ch, "figures", None) or [])

    @staticmethod
    def _chapter_bucket_from_fields(
        section_id: str, title: str = "", section_path: str = ""
    ) -> str | None:

        sid = (section_id or "").strip()
        ttl = (title or "").strip()
        sp = (section_path or "").strip()

        m = _CHAPTER_HEAD_RX.match(sid)
        if m:
            return m.group(1)

        for s in (ttl, sp):
            m = _CHAPTER_HEAD_RX.match(s)
            if m:
                return m.group(1)

        for s in (ttl, sp):
            m = _ANY_INT_TOKEN_RX.search(s)
            if m:
                return m.group(1)

        return None

    def compute(self, toc: List[ToCEntry], chunks: List[Chunk]) -> Dict[str, Any]:

        LOG.debug("Computing metrics: %d ToC entries, %d chunks", len(toc), len(chunks))

        toc_chapters = sorted(
            {
                b
                for b in (
                    self._chapter_bucket_from_fields(
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
                    self._chapter_bucket_from_fields(
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
        total_tables = sum(self._count_tables_in_chunk(ch) for ch in chunks)

        avg_words = self._avg_words(chunks)
        avg_tokens_per_section = self._approx_tokens_from_words(avg_words)

        sections_without_tables = [
            f"{ch.section_id} {ch.title}".strip()
            for ch in chunks
            if not self._has_any_table(ch)
        ]
        sections_without_diagrams = [
            f"{ch.section_id} {ch.title}".strip()
            for ch in chunks
            if (not self._has_any_figure(ch)) and (not self._has_any_table(ch))
        ]

        metrics: Dict[str, Any] = {
            "total_chapters": total_chapters,
            "total_sections": total_sections,
            "total_figures": total_figures,
            "total_tables": total_tables,
            "avg_tokens_per_section": avg_tokens_per_section,
            "sections_without_diagrams": sections_without_diagrams,
            "sections_without_tables": sections_without_tables,
        }

        LOG.info(
            "Metrics computed: chapters=%d sections=%d figures=%d tables=%d",
            total_chapters,
            total_sections,
            total_figures,
            total_tables,
        )
        return metrics

    @staticmethod
    def write(out_path: str, metrics: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)
        LOG.info("Wrote metrics to %s", out_path)


_calculator = MetricsCalculator()


def compute_metrics(toc: List[ToCEntry], chunks: List[Chunk]) -> Dict[str, Any]:
    return _calculator.compute(toc, chunks)


def write_metrics(out_path: str, metrics: Dict[str, Any]) -> None:
    return _calculator.write(out_path, metrics)
