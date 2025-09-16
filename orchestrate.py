from openpyxl.styles import Font
from PyPDF2 import PdfReader
from src.run import cmd_toc, cmd_chunk
from src.validate import load_toc, load_chunks, match_sections
from src.models import ValidationReport
import argparse
import os
import sys
from types import SimpleNamespace
import json
import re
import pandas as pd
from collections import defaultdict

import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
    sys.path.insert(0, ROOT)

VENV_SP = os.path.join(ROOT, ".venv", "Lib", "site-packages")
if os.path.isdir(VENV_SP) and VENV_SP not in sys.path:
    sys.path.insert(0, VENV_SP)

ID_LIST_RX = r"((?:\d+|[A-Z])(?:\.\d+)*[a-z]?)"
FIG_LIST_RE = re.compile(rf"\bFigure\s+{ID_LIST_RX}\b", re.IGNORECASE)
TAB_LIST_RE = re.compile(rf"\bTable\s+{ID_LIST_RX}\b", re.IGNORECASE)

ID_STRICT_RE = re.compile(r"(?:(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)+)[a-z]?)")


def _extract_text_range(reader: PdfReader, start_idx: int, end_idx_excl: int) -> str:
    parts = []
    for i in range(start_idx, min(end_idx_excl, len(reader.pages))):
        try:
            parts.append(reader.pages[i].extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts)


def _maxima_total(ids: set[str]) -> int:
    """Range-style total: sum of the max leaf per prefix ('8'->217, '6'->67, etc.)."""
    mx = defaultdict(int)
    for s in ids:
        head = s.split(".", 1)[0]
        tail = re.match(r"(\d+)", s.split(".")[-1])
        if tail:
            mx[head] = max(mx[head], int(tail.group(1)))
    return sum(mx.values())


def figure_table_metrics_from_pdf(
    pdf_path: str,
    lof_range: tuple[int, int] = (18, 26),
    lot_range: tuple[int, int] = (26, 33),
) -> tuple[set[str], set[str]]:
    """Return (figure_ids_from_list_pages, table_ids_from_list_pages).
    Ranges are 0-based page indices."""

    r = PdfReader(pdf_path)
    lof_text = _extract_text_range(r, *lof_range)  # default: pages 19–26
    lot_text = _extract_text_range(r, *lot_range)  # default: pages 27–33
    figs = {m.group(1) for m in FIG_LIST_RE.finditer(lof_text)}
    tabs = {m.group(1) for m in TAB_LIST_RE.finditer(lot_text)}
    return figs, tabs


def figure_table_ids_from_jsonl(chunks_path: str) -> tuple[set[str], set[str]]:
    figs, tabs = set(), set()
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


def read_jsonl(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


TABLE_RX = re.compile(r"\bTable\s+\d+(\.\d+)?", re.IGNORECASE)


def count_tables_in_chunk(rec):
    """Prefer explicit metadata; fallback to regex on content."""
    if isinstance(rec.get("tables"), list):
        return len(rec["tables"])
    if isinstance(rec.get("tables_count"), int):
        return rec["tables_count"]
    txt = rec.get("content") or rec.get("text") or ""
    return len(TABLE_RX.findall(txt))


def title_looks_like_table(t):
    return bool(re.match(r"^\s*Table\s+\d+", (t or ""), flags=re.IGNORECASE))


def _autofit(ws, max_width=60):
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font
    for col in ws.columns:
        values = [str(c.value) if c.value is not None else "" for c in col]
        width = min(max((len(v) for v in values), default=0) + 2, max_width)
        ws.column_dimensions[col[0].column_letter].width = width


def write_validation_xls_from_validate(
    toc_path: str, chunks_path: str, xls_path: str, pdf_path: str
):
    toc = load_toc(toc_path)
    chunks = load_chunks(chunks_path)

    missing, extra, out_of_order, matched = match_sections(
        toc, chunks, fuzzy_threshold=0.90, prefer_section_id=True
    )
    report = ValidationReport(
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

    import os

    os.makedirs(os.path.dirname(xls_path) or ".", exist_ok=True)
    target = xls_path
    try:
        if os.path.exists(target):
            try:
                os.remove(target)
            except PermissionError:
                pass

        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            overview_df.to_excel(writer, sheet_name="Overview", index=False)
            missing_df.to_excel(writer, sheet_name="MissingSections", index=False)
            extra_df.to_excel(writer, sheet_name="ExtraSections", index=False)
            ooo_df.to_excel(writer, sheet_name="OutOfOrder", index=False)
            matched_df.to_excel(writer, sheet_name="MatchedSections", index=False)

            miss_fig_df.to_excel(writer, sheet_name="MissingFigureIDs", index=False)
            miss_tab_df.to_excel(writer, sheet_name="MissingTableIDs", index=False)
            extra_fig_df.to_excel(writer, sheet_name="ExtraFigureIDs", index=False)
            extra_tab_df.to_excel(writer, sheet_name="ExtraTableIDs", index=False)

            wb = writer.book
            for name in [
                "Overview",
                "MissingSections",
                "ExtraSections",
                "OutOfOrder",
                "MatchedSections",
                "MissingFigureIDs",
                "MissingTableIDs",
                "ExtraFigureIDs",
                "ExtraTableIDs",
            ]:
                _autofit(wb[name])

    except PermissionError:
        alt = os.path.join(
            os.path.dirname(target),
            f"ValidationReport_{time.strftime('%Y%m%d_%H%M%S')}.xlsx",
        )
        with pd.ExcelWriter(alt, engine="openpyxl") as writer:
            overview_df.to_excel(writer, sheet_name="Overview", index=False)
            missing_df.to_excel(writer, sheet_name="MissingSections", index=False)
            extra_df.to_excel(writer, sheet_name="ExtraSections", index=False)
            ooo_df.to_excel(writer, sheet_name="OutOfOrder", index=False)
            matched_df.to_excel(writer, sheet_name="MatchedSections", index=False)

            miss_fig_df.to_excel(writer, sheet_name="MissingFigureIDs", index=False)
            miss_tab_df.to_excel(writer, sheet_name="MissingTableIDs", index=False)
            extra_fig_df.to_excel(writer, sheet_name="ExtraFigureIDs", index=False)
            extra_tab_df.to_excel(writer, sheet_name="ExtraTableIDs", index=False)

            wb = writer.book
            for name in [
                "Overview",
                "MissingSections",
                "ExtraSections",
                "OutOfOrder",
                "MatchedSections",
                "MissingFigureIDs",
                "MissingTableIDs",
                "ExtraFigureIDs",
                "ExtraTableIDs",
            ]:
                _autofit(wb[name])
        print(f"[warn] Excel was open; wrote to → {alt}")


def main():
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
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    toc_path = os.path.join(args.outdir, "usb_pd_toc.jsonl")
    cmd_toc(
        SimpleNamespace(
            pdf=args.pdf,
            toc_pages=args.toc_pages,
            doc_title=args.doc_title,
            out=toc_path,
            min_dots=0,
            strip_dot_leaders=True,
        )
    )
    print(f"[1/3] ToC → {toc_path}")

    chunks_path = os.path.join(args.outdir, "usb_pd_spec.jsonl")
    cmd_chunk(SimpleNamespace(pdf=args.pdf, toc=toc_path, out=chunks_path))
    print(f"[2/3] Chunks → {chunks_path}")

    xls_path = os.path.join(args.outdir, "ValidationReport.xlsx")
    write_validation_xls_from_validate(toc_path, chunks_path, xls_path, args.pdf)
    print(f"[3/3] Validation Excel → {xls_path}")


if __name__ == "__main__":
    main()
