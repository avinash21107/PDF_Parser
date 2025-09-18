from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Callable, Iterable, List, Optional, Set, Tuple

import pandas as pd
from openpyxl.styles import Font
from PyPDF2 import PdfReader

from src.logger import get_logger
from src.models import ValidationReport
from src.run import cmd_chunk, cmd_toc
from src.validate import load_chunks, load_toc, match_sections

LOG = get_logger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))

ID_LIST_RX = r"((?:\d+|[A-Z])(?:\.\d+)*[a-z]?)"
FIG_LIST_RE = re.compile(rf"\bFigure\s+{ID_LIST_RX}\b", re.IGNORECASE)
TAB_LIST_RE = re.compile(rf"\bTable\s+{ID_LIST_RX}\b", re.IGNORECASE)
ID_STRICT_RE = re.compile(r"(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)+)[a-z]?")
TABLE_RX = re.compile(r"\bTable\s+\d+(\.\d+)?", re.IGNORECASE)


def read_jsonl(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _extract_text_range(reader: PdfReader, start_idx: int, end_idx_excl: int) -> str:
    parts: List[str] = []
    for i in range(start_idx, min(end_idx_excl, len(reader.pages))):
        try:
            parts.append(reader.pages[i].extract_text() or "")
        except Exception:
            LOG.exception("Failed to extract text from page %d", i)
            parts.append("")
    return "\n".join(parts)


def _maxima_total(ids: Iterable[str]) -> int:

    mx: dict[str, int] = defaultdict(int)
    for s in ids:
        head = s.split(".", 1)[0]
        tail = re.match(r"(\d+)", s.split(".")[-1])
        if tail:
            mx[head] = max(mx[head], int(tail.group(1)))
    return sum(mx.values())


def figure_table_metrics_from_pdf(
    pdf_path: str,
    lof_range: Tuple[int, int] = (18, 26),
    lot_range: Tuple[int, int] = (26, 33),
) -> Tuple[Set[str], Set[str]]:

    reader = PdfReader(pdf_path)
    lof_text = _extract_text_range(reader, *lof_range)
    lot_text = _extract_text_range(reader, *lot_range)
    figs = {m.group(1) for m in FIG_LIST_RE.finditer(lof_text)}
    tabs = {m.group(1) for m in TAB_LIST_RE.finditer(lot_text)}
    return figs, tabs


def figure_table_ids_from_jsonl(chunks_path: str) -> Tuple[Set[str], Set[str]]:
    """Extract figure/table ids from a JSONL chunks file using strict id regex."""
    figs: Set[str] = set()
    tabs: Set[str] = set()
    for rec in read_jsonl(chunks_path):
        for s in rec.get("figures", []) or []:
            m = ID_STRICT_RE.search(s)
            if m:
                figs.add(m.group(0))
        for s in rec.get("tables", []) or []:
            m = ID_STRICT_RE.search(s)
            if m:
                tabs.add(m.group(0))
    return figs, tabs


def count_tables_in_chunk(rec: dict) -> int:
    if isinstance(rec.get("tables"), list):
        return len(rec["tables"])
    if isinstance(rec.get("tables_count"), int):
        return rec["tables_count"]
    txt = rec.get("content") or rec.get("text") or ""
    return len(TABLE_RX.findall(txt))


def title_looks_like_table(t: Optional[str]) -> bool:
    return bool(re.match(r"^\s*Table\s+\d+", (t or ""), flags=re.IGNORECASE))


def _autofit(ws: Any, max_width: int = 60) -> None:
    header_font = Font(bold=True)
    # Header row (first row)
    for cell in ws[1]:
        cell.font = header_font

    for col in ws.columns:
        values = [str(c.value) if c.value is not None else "" for c in col]
        width = min(max((len(v) for v in values), default=0) + 2, max_width)
        ws.column_dimensions[col[0].column_letter].width = width


class Orchestrator:
    """Encapsulates orchestration of ToC extraction, chunking and validation Excel reporting.

    This class is intentionally a thin wrapper around the existing command functions
    (`cmd_toc`, `cmd_chunk`) and validation helpers (`load_toc`, `load_chunks`, `match_sections`).
    """

    def __init__(
        self,
        cmd_toc_fn: Callable[..., None] = cmd_toc,
        cmd_chunk_fn: Callable[..., None] = cmd_chunk,
        load_toc_fn: Callable[[str], list] = load_toc,
        load_chunks_fn: Callable[[str], list] = load_chunks,
        match_sections_fn: Callable[..., tuple] = match_sections,
        validation_report_class: Any = ValidationReport,
    ) -> None:
        self.cmd_toc_fn = cmd_toc_fn
        self.cmd_chunk_fn = cmd_chunk_fn
        self.load_toc_fn = load_toc_fn
        self.load_chunks_fn = load_chunks_fn
        self.match_sections_fn = match_sections_fn
        self.validationreport = validation_report_class

        if any(
            fn is None
            for fn in (
                cmd_toc_fn,
                cmd_chunk_fn,
                load_toc_fn,
                load_chunks_fn,
                match_sections_fn,
                validation_report_class,
            )
        ):
            LOG.warning(
                "One or more runtime dependencies were not found; tests should inject mocks."
            )

    def run_toc(
        self, pdf: str, toc_pages: Optional[str], doc_title: str, out_path: str) -> None:
        ns = SimpleNamespace(
            pdf=pdf,
            toc_pages=toc_pages,
            doc_title=doc_title,
            out=out_path,
            min_dots=0,
            strip_dot_leaders=True,
        )
        LOG.info("Running ToC extraction -> %s", out_path)
        if self.cmd_toc_fn is None:
            raise RuntimeError("cmd_toc function not available")
        self.cmd_toc_fn(ns)

    def run_chunk(self, pdf: str, toc_path: str, out_path: str) -> None:

        ns = SimpleNamespace(pdf=pdf, toc=toc_path, out=out_path)
        LOG.info("Running chunk extraction -> %s", out_path)
        if self.cmd_chunk_fn is None:
            raise RuntimeError("cmd_chunk function not available")
        self.cmd_chunk_fn(ns)

    def write_validation_xls_from_validate(
        self, toc_path: str, chunks_path: str, xls_path: str, pdf_path: str
    ) -> None:

        LOG.info("Loading ToC from %s", toc_path)
        toc = self.load_toc_fn(toc_path)
        LOG.info("Loading chunks from %s", chunks_path)
        chunks = self.load_chunks_fn(chunks_path)

        missing, extra, out_of_order, matched = self.match_sections_fn(
            toc, chunks, fuzzy_threshold=0.90, prefer_section_id=True
        )

        report = self.validationreport(
            toc_section_count=len(toc),
            parsed_section_count=len(chunks),
            missing_sections=missing,
            extra_sections=extra,
            out_of_order_sections=out_of_order,
            matched_sections=matched,
        )

        figs_toc, tabs_toc = figure_table_metrics_from_pdf(pdf_path)
        figs_chunks, tabs_chunks = figure_table_ids_from_jsonl(chunks_path)

        fig_matched = len(figs_toc & figs_chunks)
        tab_matched = len(tabs_toc & tabs_chunks)
        fig_missing = sorted(figs_toc - figs_chunks)
        tab_missing = sorted(tabs_toc - tabs_chunks)
        fig_extra = sorted(figs_chunks - figs_toc)
        tab_extra = sorted(tabs_chunks - tabs_toc)

        figs_range_total = _maxima_total(figs_toc)
        tabs_range_total = _maxima_total(tabs_toc)

        overview_rows = [
            ("Total sections (ToC)", report.toc_section_count),
            ("Total sections (ToC_Specs)", report.parsed_section_count),
            ("Matched sections", len(report.matched_sections)),
            ("Missing sections", len(report.missing_sections)),
            ("Extra sections", len(report.extra_sections)),
            ("Out-of-order sections", len(report.out_of_order_sections)),
            ("ToC figures (unique IDs)", len(figs_toc)),
            ("ToC tables (unique IDs)", len(tabs_toc)),
            ("ToC figures (range total)", figs_range_total),
            ("ToC tables (range total)", tabs_range_total),
            ("Matched figure IDs", fig_matched),
            ("Matched table IDs", tab_matched),
            ("Missing figure IDs in ToC_Specs", len(fig_missing)),
            ("Missing table IDs in ToC_Specs", len(tab_missing)),
        ]
        overview_df = pd.DataFrame(overview_rows, columns=["Metric", "Value"])

        missing_df = pd.DataFrame({"section": report.missing_sections})
        extra_df = pd.DataFrame({"section": report.extra_sections})
        ooo_df = pd.DataFrame({"section": report.out_of_order_sections})
        matched_df = pd.DataFrame({"section": report.matched_sections})

        miss_fig_df = pd.DataFrame({"figure_id_missing": fig_missing})
        miss_tab_df = pd.DataFrame({"table_id_missing": tab_missing})
        extra_fig_df = pd.DataFrame({"figure_id_extra": fig_extra})
        extra_tab_df = pd.DataFrame({"table_id_extra": tab_extra})

        os.makedirs(os.path.dirname(xls_path) or ".", exist_ok=True)
        try:
            self._write_excel_with_autofit(
                xls_path,
                {
                    "Overview": overview_df,
                    "MissingSections": missing_df,
                    "ExtraSections": extra_df,
                    "OutOfOrder": ooo_df,
                    "MatchedSections": matched_df,
                    "MissingFigureIDs": miss_fig_df,
                    "MissingTableIDs": miss_tab_df,
                    "ExtraFigureIDs": extra_fig_df,
                    "ExtraTableIDs": extra_tab_df,
                },
            )
            LOG.info("Wrote validation Excel -> %s", xls_path)
        except PermissionError:
            alt = os.path.join(
                os.path.dirname(xls_path),
                f"ValidationReport_{time.strftime('%Y%m%d_%H%M%S')}.xlsx",
            )
            LOG.warning(
                "Could not write %s (maybe file open). Writing to %s", xls_path, alt
            )
            self._write_excel_with_autofit(
                alt,
                {
                    "Overview": overview_df,
                    "MissingSections": missing_df,
                    "ExtraSections": extra_df,
                    "OutOfOrder": ooo_df,
                    "MatchedSections": matched_df,
                    "MissingFigureIDs": miss_fig_df,
                    "MissingTableIDs": miss_tab_df,
                    "ExtraFigureIDs": extra_fig_df,
                    "ExtraTableIDs": extra_tab_df,
                },
            )
            LOG.info("Wrote validation Excel -> %s", alt)

    def _write_excel_with_autofit(self, target: str, sheets: dict) -> None:

        if os.path.exists(target):
            try:
                os.remove(target)
            except PermissionError:
                LOG.debug(
                    "Permission to remove existing file %s denied; will try writing anyway",
                    target,
                )
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=False)
            wb = writer.book
            for name in sheets.keys():
                _autofit(wb[name])

    def run_all(
        self, pdf: str, doc_title: str, outdir: str, toc_pages: Optional[str] = None) -> Tuple[str, str, str]:

        os.makedirs(outdir, exist_ok=True)
        toc_path = os.path.join(outdir, "usb_pd_toc.jsonl")
        self.run_toc(
            pdf=pdf, toc_pages=toc_pages, doc_title=doc_title, out_path=toc_path
        )
        LOG.info("[1/3] ToC -> %s", toc_path)

        chunks_path = os.path.join(outdir, "usb_pd_spec.jsonl")
        self.run_chunk(pdf=pdf, toc_path=toc_path, out_path=chunks_path)
        LOG.info("[2/3] Chunks -> %s", chunks_path)

        xls_path = os.path.join(outdir, "ValidationReport.xlsx")
        self.write_validation_xls_from_validate(toc_path, chunks_path, xls_path, pdf)
        LOG.info("[3/3] Validation Excel -> %s", xls_path)
        return toc_path, chunks_path, xls_path


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    ap = argparse.ArgumentParser(
        description="USB-PD parser orchestrator (ToC + Chunks + XLS validation)"
    )
    ap.add_argument("--pdf", required=True, help="Path to the USB-PD PDF")
    ap.add_argument(
        "--doc-title", default="Universal Serial Bus Power Delivery Specification"
    )
    ap.add_argument("--outdir", default=os.path.join("data", "output"))
    ap.add_argument(
        "--toc-pages", default=None, help="Optional ToC page range like '13-18'"
    )
    args = ap.parse_args(argv)

    orchestrator = Orchestrator()
    try:
        orchestrator.run_all(
            pdf=args.pdf,
            doc_title=args.doc_title,
            outdir=args.outdir,
            toc_pages=args.toc_pages,
        )
        return 0
    except Exception:
        LOG.exception("Orchestration failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
