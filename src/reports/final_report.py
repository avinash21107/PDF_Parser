from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.logger import get_logger
from ..models import ValidationReport

LOG = get_logger(__name__)


class AbstractReportGenerator(ABC):
    """Abstract base class for all report generators."""

    @abstractmethod
    def generate(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def write(self, out_path: str) -> None:
        raise NotImplementedError


class FinalReport(AbstractReportGenerator):
    """
    Generates and writes the final validation report,
    combining ValidationReport and metrics data.
    """

    def __init__(self, val: ValidationReport, metrics: Dict[str, Any]):
        self._val = val
        self._metrics = metrics
        self._report: Dict[str, Any] = {}

    def __str__(self) -> str:
        return (
            f"FinalReport(matched_sections={len(self._val.matched_sections)}, "
            f"total_sections={self._val.toc_section_count})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FinalReport):
            return NotImplemented
        return self._report == other._report

    def generate(self) -> Dict[str, Any]:
        """Generate the final report dictionary."""
        self._compute_summary()
        self._collect_discrepancies()
        self._generate_recommendations()
        return self._report

    def write(self, out_path: str) -> None:
        """Write the report to a JSON file."""
        if not self._report:
            LOG.warning("Report is empty. Call generate() first.")
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self._report, f, ensure_ascii=False, indent=2)
        LOG.info("Wrote final report to %s", out_path)

    def _compute_summary(self) -> None:
        matched = len(self._val.matched_sections)
        total = self._val.toc_section_count
        pct = round((matched / total * 100.0), 1) if total else 0.0
        self._report["summary"] = (
            f"Matched {matched} of {total} ToC sections ({pct}% match)."
        )

    def _collect_discrepancies(self) -> None:
        discrepancies: List[str] = [
            *(f"Missing in chunks: {s}" for s in self._val.missing_sections),
            *(f"Extra (not in ToC): {s}" for s in self._val.extra_sections),
            *(f"Out of order: {s}" for s in self._val.out_of_order_sections),
        ]
        self._report["discrepancies"] = discrepancies[:200]

        self._report["metrics"] = {
            "toc_sections": self._metrics.get("total_sections"),
            "parsed_sections": self._val.parsed_section_count,
            "figures": self._metrics.get("total_figures"),
            "tables": self._metrics.get("total_tables"),
            "missing_sections": self._val.missing_sections[:50],
        }

    def _generate_recommendations(self) -> None:
        recs: List[str] = []
        if self._val.missing_sections:
            recs.append(
                "Re-parse pages around missing sections; verify ToC page bounds and OCR."
            )
        if self._val.extra_sections:
            recs.append("Tighten heading detector or gate strictly by ToC IDs.")
        if (
            self._metrics.get("total_figures", 0) == 0
            and self._metrics.get("total_tables", 0) == 0
        ):
            recs.append(
                "Figure/Table captions may be missed—relax caption regex or post-OCR cleanup."
            )
        avg_tokens = self._metrics.get("avg_tokens_per_section", 0)
        if avg_tokens < 300:
            recs.append(
                "Many short chunks detected; consider merging consecutive small sections."
            )
        if avg_tokens > 9000:
            recs.append(
                "Very large chunks; consider splitting by subheadings or page breaks."
            )
        self._report["recommendations"] = recs


_report_generator: Optional[AbstractReportGenerator] = None


def generate_report(val: ValidationReport, metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience wrapper for module-level report generation."""
    global _report_generator
    _report_generator = FinalReport(val, metrics)
    return _report_generator.generate()


def write_report(out_path: str) -> None:
    """Convenience wrapper for module-level report writing."""
    if _report_generator is None:
        LOG.warning("No report generated yet. Call generate_report() first.")
        return
    _report_generator.write(out_path)
