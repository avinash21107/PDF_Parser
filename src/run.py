from __future__ import annotations

import argparse
import json
import os
from typing import Any

from rich.console import Console

from src.logger import get_logger
from src.utils import (
    autodetect_toc_range,
    extract_all_pages,
    extract_text_lines,
    parse_page_range,
)
from src.toc import parse_toc_lines as parse_toc, write_jsonl as write_toc_jsonl
from src.chunk import (
    build_chunks,
    build_chunks_from_toc,
    write_jsonl as write_chunks_jsonl,
)
from src.validate import load_chunks, load_toc, match_sections, write_report
from src.models import ValidationReport
from src.reports.metrics import compute_metrics, write_metrics
from src.graph.toc_graph_simple import TocGraphBuilder, write_graph_json
from src.graph.kg_simple import extract_triples
from src.reports.final_report import generate_final_report, write_final_report

console = Console()
LOG = get_logger(__name__)


def cmd_toc(args: argparse.Namespace) -> None:
    pdf = args.pdf
    doc_title = args.doc_title

    if args.toc_pages:
        pstart, pend = parse_page_range(args.toc_pages)
        LOG.debug("User provided ToC pages: %s-%s", pstart, pend)
    else:
        rng = autodetect_toc_range(pdf)
        if not rng:
            console.print(
                "[red]Failed to autodetect ToC. Pass --toc-pages, e.g., 13-18."
            )
            LOG.error("Auto-detection of ToC pages failed for %s", pdf)
            raise SystemExit(2)
        pstart, pend = rng
        console.log(f"[green]Auto-detected ToC pages: {pstart}-{pend}")
        LOG.info("Auto-detected ToC pages %d-%d for %s", pstart, pend, pdf)

    lines = extract_text_lines(pdf, pstart, pend)
    entries = parse_toc(
        lines, doc_title=doc_title, min_dots=0, strip_dots=args.strip_dot_leaders
    )

    if not entries:
        console.print(
            "[red]No ToC entries parsed. Try --strip-dot-leaders and/or adjust --toc-pages."
        )
        LOG.error("No ToC entries parsed from PDF %s (pages %s-%s)", pdf, pstart, pend)
        raise SystemExit(1)

    count = write_toc_jsonl(entries, args.out)
    console.print(f"[green]Wrote {count} ToC entries → {args.out}")
    LOG.info("Wrote %d ToC entries to %s", count, args.out)


def cmd_chunk(args: argparse.Namespace) -> None:
    pdf = args.pdf
    pages = extract_all_pages(pdf)

    rng = autodetect_toc_range(pdf)
    skip_pages = set(range(rng[0], rng[1] + 1)) if rng else set()
    if rng:
        console.log(f"Skipping ToC pages {rng[0]}-{rng[1]} during chunking")
        LOG.info("Skipping ToC pages %d-%d during chunking for %s", rng[0], rng[1], pdf)

    toc_entries = None
    if args.toc:
        toc_entries = load_toc(args.toc)
        if rng:
            before = len(toc_entries)
            toc_entries = [e for e in toc_entries if e.page > rng[1]]
            after = len(toc_entries)
            if after < before:
                console.log(
                    f"Filtered {before - after} ToC rows with page ≤ {rng[1]} (inside ToC)"
                )
                LOG.debug("Filtered %d ToC entries inside ToC pages", before - after)

    if toc_entries:
        chunks = build_chunks_from_toc(pages, toc_entries, skip_pages=skip_pages)
        LOG.debug("Built %d chunks using provided ToC", len(chunks))
    else:
        chunks = build_chunks(pages, toc_ids=None, skip_pages=skip_pages, toc_map=None)
        LOG.debug("Built %d chunks using automatic chunking", len(chunks))

    count = write_chunks_jsonl(chunks, args.out)
    console.print(f"[green]Wrote {count} chunks →\n{os.path.abspath(args.out)}")
    LOG.info("Wrote %d chunks to %s", count, args.out)


def cmd_validate(args: argparse.Namespace) -> None:
    toc = load_toc(args.toc)
    chunks = load_chunks(args.chunks)

    fuzzy_threshold = getattr(args, "fuzzy_threshold", 0.90)
    prefer_section_id = getattr(args, "prefer_section_id", True)
    LOG.debug(
        "Validation fuzzy_threshold=%s prefer_section_id=%s",
        fuzzy_threshold,
        prefer_section_id,
    )

    missing, extra, out_of_order, matched = match_sections(
        toc,
        chunks,
        fuzzy_threshold=fuzzy_threshold,
        prefer_section_id=prefer_section_id,
    )

    report = ValidationReport(
        toc_section_count=len(toc),
        parsed_section_count=len(chunks),
        missing_sections=missing,
        extra_sections=extra,
        out_of_order_sections=out_of_order,
        matched_sections=matched,
    )
    write_report(args.out, report)
    LOG.info("Wrote validation report to %s", args.out)


def cmd_metrics(args: argparse.Namespace) -> None:
    toc = load_toc(args.toc)
    chunks = load_chunks(args.chunks)
    m = compute_metrics(toc, chunks)
    write_metrics(args.out, m)
    console.print(f"[green]Metrics → \n{os.path.abspath(args.out)}")
    console.print(m)
    LOG.info("Wrote metrics to %s", args.out)


def cmd_toc_graph(args: argparse.Namespace) -> None:
    """Build ToC graph JSON from ToC entries."""
    toc = load_toc(args.toc)
    G = TocGraphBuilder(toc)
    write_graph_json(args.out, G)
    console.print(f"[green]ToC graph → {os.path.abspath(args.out)}")
    LOG.info("Wrote ToC graph to %s", args.out)


def cmd_kg(args: argparse.Namespace) -> None:
    chunks = load_chunks(args.chunks)
    triples = extract_triples(chunks, include_meta=False)  # strict 3-field triples

    if args.array:
        from src.graph.kg_simple import write_triples_json

        write_triples_json(args.out, triples)
        LOG.debug("Wrote triples as single JSON array to %s", args.out)
    else:
        from src.graph.kg_simple import write_triples_jsonl

        write_triples_jsonl(args.out, triples)
        LOG.debug("Wrote triples as JSONL to %s", args.out)

    console.print(f"[green]Wrote triples → {os.path.abspath(args.out)}")
    LOG.info("Wrote triples to %s", args.out)


def cmd_report(args: argparse.Namespace) -> None:
    with open(args.validation, "r", encoding="utf-8") as f:
        val_json = json.load(f)
    val = ValidationReport.model_validate(val_json)

    with open(args.metrics, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    rep = generate_final_report(val, metrics)
    write_final_report(args.out, rep)
    console.print(f"[green]Final report → {os.path.abspath(args.out)}")
    LOG.info("Wrote final report to %s", args.out)


def cmd_graph(args: argparse.Namespace) -> None:
    toc = load_toc(args.toc)
    G = TocGraphBuilder(toc)
    write_graph_json(args.out, G)
    console.print(f"[green]Graph JSON → {os.path.abspath(args.out)}")
    LOG.info("Wrote graph JSON to %s", args.out)


def main(argv: Any | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usb-pd-parser")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("toc", help="Extract ToC → JSONL")
    a.add_argument("--pdf", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--doc-title", required=True)
    a.add_argument(
        "--toc-pages", help="Manual page range, e.g., 13-18 (1-based, inclusive)"
    )
    a.add_argument("--min-dots", type=int, default=0)
    a.add_argument("--strip-dot-leaders", action="store_true")
    a.set_defaults(func=cmd_toc)

    b = sub.add_parser("chunk", help="Chunk full PDF → JSONL")
    b.add_argument("--pdf", required=True, help="Path to USB PD PDF")
    b.add_argument("--out", required=True, help="Output JSONL path for chunks")
    b.add_argument(
        "--toc", help="(Optional) ToC JSONL path to gate headings by section_id"
    )
    b.set_defaults(func=cmd_chunk)

    c = sub.add_parser("validate", help="Validate ToC vs chunks → JSON")
    c.add_argument("--toc", required=True)
    c.add_argument("--chunks", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--fuzzy-threshold", type=float, default=0.90)
    c.add_argument("--prefer-section-id", action="store_true")
    c.set_defaults(func=cmd_validate)

    m = sub.add_parser("metrics", help="Compute section/table/figure metrics")
    m.add_argument("--toc", required=True)
    m.add_argument("--chunks", required=True)
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_metrics)

    g = sub.add_parser("toc-graph", help="Export ToC hierarchy as a graph JSON")
    g.add_argument("--toc", required=True)
    g.add_argument("--out", required=True)
    g.set_defaults(func=cmd_graph)

    k = sub.add_parser("kg", help="Extract triples from chunks")
    k.add_argument("--chunks", required=True)
    k.add_argument("--out", required=True)
    k.add_argument(
        "--array", action="store_true", help="Write a single JSON array (not JSONL)"
    )
    k.set_defaults(func=cmd_kg)

    r = sub.add_parser(
        "report", help="Generate final QA report from validation + metrics"
    )
    r.add_argument("--validation", required=True, help="Path to validation JSON")
    r.add_argument("--metrics", required=True, help="Path to metrics JSON")
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    try:
        args.func(args)
        return 0
    except SystemExit:
        raise
    except Exception:
        LOG.exception("Unhandled exception in CLI")
        console.print("[red]Error: see logs for details.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
