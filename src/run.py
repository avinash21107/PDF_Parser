import argparse
import os
from rich.console import Console
from src.utils import (
    autodetect_toc_range,
    parse_page_range,
    extract_text_lines,
    extract_all_pages,
)
from src.toc import parse_toc_lines as parse_toc, write_jsonl as write_toc_jsonl
from src.chunk import (
    build_chunks,
    write_jsonl as write_chunks_jsonl,
    build_chunks_from_toc,
)
from src.validate import load_toc, load_chunks, match_sections, write_report
from src.models import ValidationReport
from src.reports.metrics import compute_metrics, write_metrics
from src.graph.toc_graph_simple import build_toc_graph, write_graph_json
from src.graph.kg_simple import extract_triples
from src.reports.final_report import generate_final_report, write_final_report
import json

console = Console()


def cmd_toc(args):
    pdf = args.pdf
    doc_title = args.doc_title
    if args.toc_pages:
        pstart, pend = parse_page_range(args.toc_pages)
    else:
        rng = autodetect_toc_range(pdf)
        if not rng:
            console.print(
                "[red]Failed to autodetect ToC. Pass --toc-pages, e.g., 13-18."
            )
            raise SystemExit(2)
        pstart, pend = rng
        console.log(f"[green]Auto-detected ToC pages: {pstart}-{pend}")

    lines = extract_text_lines(pdf, pstart, pend)
    entries = parse_toc(
        lines, doc_title=doc_title, min_dots=0, strip_dots=args.strip_dot_leaders
    )
    if not entries:
        console.print(
            "[red]No ToC entries parsed. Try --strip-dot-leaders and/or adjust --toc-pages."
        )
        raise SystemExit(1)
    count = write_toc_jsonl(entries, args.out)
    console.print(f"[green]Wrote {count} ToC entries → {args.out}")


def cmd_chunk(args):
    pdf = args.pdf
    pages = extract_all_pages(pdf)

    rng = autodetect_toc_range(pdf)
    skip_pages = set(range(rng[0], rng[1] + 1)) if rng else set()
    if rng:
        console.log(f"Skipping ToC pages {rng[0]}-{rng[1]} during chunking")

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

    if toc_entries:
        chunks = build_chunks_from_toc(pages, toc_entries, skip_pages=skip_pages)
    else:
        chunks = build_chunks(pages, toc_ids=None, skip_pages=skip_pages, toc_map=None)

    count = write_chunks_jsonl(chunks, args.out)
    console.print(f"[green]Wrote {count} chunks →\n{os.path.abspath(args.out)}")


def cmd_validate(args):
    toc = load_toc(args.toc)
    chunks = load_chunks(args.chunks)

    fuzzy_threshold = getattr(args, "fuzzy_threshold", 0.90)
    prefer_section_id = getattr(args, "prefer_section_id", True)

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


def cmd_metrics(args):
    toc = load_toc(args.toc)
    chunks = load_chunks(args.chunks)
    m = compute_metrics(toc, chunks)
    write_metrics(args.out, m)
    console.print(f"[green]Metrics → \n{os.path.abspath(args.out)}")
    console.print(m)


def cmd_toc_graph(args):
    toc = load_toc(args.toc)
    G = build_toc_graph(toc)
    write_graph_json(args.out, G)
    console.print(f"[green]ToC graph → {os.path.abspath(args.out)}")


def cmd_kg(args):
    chunks = load_chunks(args.chunks)
    triples = extract_triples(chunks, include_meta=False)  # strict 3-field triples
    if args.array:
        from src.graph.kg_simple import write_triples_json

        write_triples_json(args.out, triples)
    else:
        from src.graph.kg_simple import write_triples_jsonl

        write_triples_jsonl(args.out, triples)
    console.print(f"[green]Wrote triples → {os.path.abspath(args.out)}")


def cmd_report(args):
    with open(args.validation, "r", encoding="utf-8") as f:
        val_json = json.load(f)
    val = ValidationReport.model_validate(val_json)

    with open(args.metrics, "r", encoding="utf-8") as f:
        metrics = json.load(f)

    rep = generate_final_report(val, metrics)
    write_final_report(args.out, rep)
    console.print(f"[green]Final report → {os.path.abspath(args.out)}")


def cmd_graph(args):
    toc = load_toc(args.toc)
    G = build_toc_graph(toc)
    write_graph_json(args.out, G)
    console.print(f"[green]Graph JSON → {os.path.abspath(args.out)}")


def main():
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

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
