from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from src.logger import get_logger
from ..models import ValidationReport

LOG = get_logger(__name__)


class FinalReport:
    """
    Handles generation and writing of final validation reports
    combining ValidationReport data and metrics.
    """

    def __init__(self, val: ValidationReport, metrics: Dict[str, Any]):
        self.val = val
        self.metrics = metrics
        self.report: Dict[str, Any] = {}

    def generate_final_report(self) -> Dict[str, Any]:
        """Generate the final report dictionary."""
        self._compute_summary()
        self._collect_discrepancies()
        self._generate_recommendations()
        return self.report

    def write_final_report(self, out_path: str) -> None:
        """Write the final report to a JSON file."""
        if not self.report:
            LOG.warning("Report is empty. Call generate_final_report() first.")
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(self.report, f, ensure_ascii=False, indent=2)
        LOG.info("Wrote final report to %s", out_path)


    def _compute_summary(self) -> None:
        matched = len(self.val.matched_sections)
        total = self.val.toc_section_count
        pct = round((matched / total * 100.0), 1) if total else 0.0
        self.report["summary"] = f"Matched {matched} of {total} ToC sections ({pct}% match)."

    def _collect_discrepancies(self) -> None:
        discrepancies: List[str] = []
        for s in self.val.missing_sections:
            discrepancies.append(f"Missing in chunks: {s}")
        for s in self.val.extra_sections:
            discrepancies.append(f"Extra (not in ToC): {s}")
        for s in self.val.out_of_order_sections:
            discrepancies.append(f"Out of order: {s}")
        self.report["discrepancies"] = discrepancies[:200]

        self.report["metrics"] = {
            "toc_sections": self.metrics.get("total_sections"),
            "parsed_sections": self.val.parsed_section_count,
            "figures": self.metrics.get("total_figures"),
            "tables": self.metrics.get("total_tables"),
            "missing_sections": self.val.missing_sections[:50],
        }

    def _generate_recommendations(self) -> None:
        recs: List[str] = []
        if self.val.missing_sections:
            recs.append(
                "Re-parse pages around missing sections; verify ToC page bounds and OCR."
            )
        if self.val.extra_sections:
            recs.append("Tighten heading detector or gate strictly by ToC IDs.")
        if self.metrics.get("total_figures", 0) == 0 and self.metrics.get("total_tables", 0) == 0:
            recs.append(
                "Figure/Table captions may be missed—relax caption regex or post-OCR cleanup."
            )
        avg_tokens = self.metrics.get("avg_tokens_per_section", 0)
        if avg_tokens < 300:
            recs.append(
                "Many short chunks detected; consider merging consecutive small sections."
            )
        if avg_tokens > 9000:
            recs.append(
                "Very large chunks; consider splitting by subheadings or page breaks."
            )
        self.report["recommendations"] = recs

