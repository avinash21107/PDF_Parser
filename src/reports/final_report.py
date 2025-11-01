from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from src.logger import get_logger
from src.models import ValidationReport

LOG = get_logger(__name__)


class AbstractReportGenerator(ABC):
    """Abstract base class for all report generators."""

    @abstractmethod
    def generate(self) -> Dict[str, Any]:
        """Generate the report as a dictionary."""
        raise NotImplementedError

    @abstractmethod
    def write(self, out_path: str) -> None:
        """Write the report to a file (or other sink)."""
        raise NotImplementedError


class _DefaultFileWriter:
    """Small internal writer used by FinalReport when no custom writer is provided."""

    def write(self, data: Dict[str, Any], out_path: str) -> None:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


class FinalReport(AbstractReportGenerator):
    """
    Generates and writes the final validation report, combining a ValidationReport and metrics.

    Improvements over previous implementation:
    - small writer abstraction (DI) so tests can inject an in-memory writer.
    - robust handling when metrics or validation data are partial.
    - helper methods broken into clear responsibilities.
    """

    def __init__(
        self,
        val: ValidationReport,
        metrics: Optional[Dict[str, Any]] = None,
        max_discrepancies: int = 200,
        max_missing_sections: int = 50,
        *,
        writer: Optional[Any] = None,
    ) -> None:
        self._val = val
        self._metrics = metrics or {}
        self._report: Dict[str, Any] = {}
        self._max_discrepancies = int(max_discrepancies)
        self._max_missing_sections = int(max_missing_sections)
        self._writer = writer or _DefaultFileWriter()

    def __str__(self) -> str:
        try:
            matched = len(self._val.matched_sections or [])
            total = int(getattr(self._val, "toc_section_count", 0) or 0)
            return f"FinalReport(matched_sections={matched}, total_sections={total})"
        except Exception:
            return "FinalReport(<invalid ValidationReport>)"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FinalReport):
            return NotImplemented
        return self._report == other._report

    def generate(self) -> Dict[str, Any]:
        """Generate the final report dictionary and return it."""
        self._report.clear()
        self._compute_summary()
        self._collect_discrepancies()
        self._generate_recommendations()
        return self._report

    def write(self, out_path: str) -> None:
        """Write the generated report to a file using the configured writer."""
        if not self._report:
            LOG.warning("FinalReport.write called with empty report — calling generate() first.")
            self.generate()
        try:
            self._writer.write(self._report, out_path)
            LOG.info("Wrote final report to %s", out_path)
        except Exception as exc:
            LOG.exception("Failed to write final report to %s: %s", out_path, exc)
            raise

    def _safe_list(self, seq: Optional[Sequence[Any]]) -> List[Any]:
        """Ensure a sequence is returned as a list (empty when None)."""
        return list(seq) if seq is not None else []

    def _truncate(self, items: Sequence[Any], limit: int) -> List[Any]:
        """Return a truncated list of items limited to 'limit' elements."""
        if not items:
            return []
        return list(items)[: max(0, int(limit))]

    def _item_to_str(self, item: Any) -> str:
        """Return a human readable string for discrepancy items."""
        try:
            return str(item)
        except Exception:
            return "<unserializable>"

    def _compute_summary(self) -> None:
        matched = len(self._safe_list(self._val.matched_sections))
        total = int(getattr(self._val, "toc_section_count", 0) or 0)
        pct = round((matched / total * 100.0), 1) if total else 0.0
        self._report["summary"] = {
            "text": f"Matched {matched} of {total} ToC sections ({pct}% match).",
            "matched": matched,
            "total": total,
            "percent": pct,
        }

    def _collect_discrepancies(self) -> None:
        missing = self._safe_list(self._val.missing_sections)
        extra = self._safe_list(self._val.extra_sections)
        out_of_order = self._safe_list(self._val.out_of_order_sections)

        discrepancies = [
            *(f"Missing in chunks: {self._item_to_str(s)}" for s in missing),
            *(f"Extra (not in ToC): {self._item_to_str(s)}" for s in extra),
            *(f"Out of order: {self._item_to_str(s)}" for s in out_of_order),
        ]
        self._report["discrepancies"] = self._truncate(discrepancies, self._max_discrepancies)

        metrics_out = {
            "toc_sections": int(self._metrics.get("total_sections") or getattr(self._val, "toc_section_count", 0) or 0),
            "parsed_sections": int(self._metrics.get("parsed_sections") or getattr(self._val, "parsed_section_count", 0) or 0),
            "figures": int(self._metrics.get("total_figures") or 0),
            "tables": int(self._metrics.get("total_tables") or 0),
            "avg_tokens_per_section": float(self._metrics.get("avg_tokens_per_section") or 0.0),
            "missing_sections_sample": self._truncate([self._item_to_str(s) for s in missing], self._max_missing_sections),
        }
        self._report["metrics"] = metrics_out

    def _generate_recommendations(self) -> None:
        recs: List[str] = []
        missing = self._safe_list(self._val.missing_sections)
        extra = self._safe_list(self._val.extra_sections)

        if missing:
            recs.append("Re-parse pages around missing sections; verify ToC page bounds and OCR.")
        if extra:
            recs.append("Tighten heading detection heuristics or gate by ToC IDs to reduce extras.")
        if int(self._metrics.get("total_figures") or 0) == 0 and int(self._metrics.get("total_tables") or 0) == 0:
            recs.append("Figure/Table captions may be missed — consider relaxing caption regex or improving OCR cleanup.")

        avg_tokens = float(self._metrics.get("avg_tokens_per_section") or 0.0)
        if avg_tokens and avg_tokens < 300:
            recs.append("Many short chunks detected; consider merging consecutive small sections.")
        if avg_tokens and avg_tokens > 9000:
            recs.append("Very large chunks detected; consider splitting by subheadings or page breaks.")

        if not recs:
            recs.append("No automatic recommendations — review report for edge cases.")

        self._report["recommendations"] = recs

def generate_report(
    val: ValidationReport,
    metrics: Optional[Dict[str, Any]] = None,
    max_discrepancies: int = 200,
    max_missing_sections: int = 50,
) -> FinalReport:
    """
    Create and generate a FinalReport instance.
    Returns the instance for further use (writing, inspection).
    """
    report = FinalReport(val, metrics or {}, max_discrepancies, max_missing_sections)
    report.generate()
    return report


def write_report(report: FinalReport, out_path: str) -> None:
    """
    Write a previously generated FinalReport instance to a JSON file.
    """
    if not report:
        LOG.warning("No report instance provided.")
        return
    report.write(out_path)
