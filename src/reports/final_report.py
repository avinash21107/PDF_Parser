from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from src.logger import get_logger
from ..models import ValidationReport

LOG = get_logger(__name__)


def generate_final_report(val: ValidationReport, metrics: Dict[str, Any]) -> Dict[str, Any]:

    matched = len(val.matched_sections)
    total = val.toc_section_count
    pct = round((matched / total * 100.0), 1) if total else 0.0

    discrepancies: List[str] = []
    for s in val.missing_sections:
        discrepancies.append(f"Missing in chunks: {s}")
    for s in val.extra_sections:
        discrepancies.append(f"Extra (not in ToC): {s}")
    for s in val.out_of_order_sections:
        discrepancies.append(f"Out of order: {s}")

    recs: List[str] = []
    if val.missing_sections:
        recs.append("Re-parse pages around missing sections; verify ToC page bounds and OCR.")
    if val.extra_sections:
        recs.append("Tighten heading detector or gate strictly by ToC IDs.")
    if metrics.get("total_figures", 0) == 0 and metrics.get("total_tables", 0) == 0:
        recs.append("Figure/Table captions may be missed—relax caption regex or post-OCR cleanup.")
    if metrics.get("avg_tokens_per_section", 0) < 300:
        recs.append("Many short chunks detected; consider merging consecutive small sections.")
    if metrics.get("avg_tokens_per_section", 0) > 9000:
        recs.append("Very large chunks; consider splitting by subheadings or page breaks.")

    report: Dict[str, Any] = {
        "summary": f"Matched {matched} of {total} ToC sections ({pct}% match).",
        "metrics": {
            "toc_sections": metrics.get("total_sections"),
            "parsed_sections": val.parsed_section_count,
            "figures": metrics.get("total_figures"),
            "tables": metrics.get("total_tables"),
            "missing_sections": val.missing_sections[:50],
        },
        "discrepancies": discrepancies[:200],
        "recommendations": recs,
    }

    return report


def write_final_report(out_path: str, report: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    LOG.info("Wrote final report to %s", out_path)
