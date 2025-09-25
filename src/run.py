from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
import json
import os
from typing import Any, Callable

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
from src.graph.toc_graph_simple import TocGraphBuilder, TocGraphWriter
from src.graph.kg_simple import extract_triples
from src.reports.final_report import FinalReport

console = Console()
LOG = get_logger(__name__)


class AbstractCommand(ABC):
    """Abstract command contract for CLI commands."""

    @abstractmethod
    def run(self, args: argparse.Namespace) -> None:
        raise NotImplementedError


class TocCommand(AbstractCommand):
    def __init__(
        self,
        extract_text_lines_fn: Callable[..., list] = extract_text_lines,
        autodetect_fn: Callable[..., Any] = autodetect_toc_range,
        parse_toc_fn: Callable[..., list] = parse_toc,
        write_fn: Callable[..., int] = write_toc_jsonl,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.extract_text_lines = extract_text_lines_fn
        self.autodetect = autodetect_fn
        self.parse_toc = parse_toc_fn
        self.write_toc = write_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "TocCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TocCommand)

    def run(self, args: argparse.Namespace) -> None:
        pdf = args.pdf
        doc_title = args.doc_title

        if args.toc_pages:
            pstart, pend = parse_page_range(args.toc_pages)
            self.log.debug("User provided ToC pages: %s-%s", pstart, pend)
        else:
            rng = self.autodetect(pdf)
            if not rng:
                self.console.print(
                    "[red]Failed to autodetect ToC. Pass --toc-pages, e.g., 13-18."
                )
                self.log.error("Auto-detection of ToC pages failed for %s", pdf)
                raise SystemExit(2)
            pstart, pend = rng
            self.console.log(f"[green]Auto-detected ToC pages: {pstart}-{pend}")
            self.log.info("Auto-detected ToC pages %d-%d for %s", pstart, pend, pdf)

        lines = self.extract_text_lines(pdf, pstart, pend)
        entries = self.parse_toc(
            lines, doc_title=doc_title, min_dots=0, strip_dots=args.strip_dot_leaders
        )

        if not entries:
            self.console.print(
                "[red]No ToC entries parsed. Try --strip-dot-leaders and/or adjust --toc-pages."
            )
            self.log.error(
                "No ToC entries parsed from PDF %s (pages %s-%s)", pdf, pstart, pend
            )
            raise SystemExit(1)

        count = self.write_toc(entries, args.out)
        self.console.print(f"[green]Wrote {count} ToC entries → {args.out}")
        self.log.info("Wrote %d ToC entries to %s", count, args.out)


class ChunkCommand(AbstractCommand):
    def __init__(
        self,
        extract_all_pages_fn: Callable[..., list] = extract_all_pages,
        autodetect_fn: Callable[..., Any] = autodetect_toc_range,
        load_toc_fn: Callable[..., list] = load_toc,
        build_chunks_fn: Callable[..., list] = build_chunks,
        build_chunks_from_toc_fn: Callable[..., list] = build_chunks_from_toc,
        write_chunks_fn: Callable[..., int] = write_chunks_jsonl,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.extract_all_pages = extract_all_pages_fn
        self.autodetect = autodetect_fn
        self.load_toc = load_toc_fn
        self.build_chunks = build_chunks_fn
        self.build_chunks_from_toc = build_chunks_from_toc_fn
        self.write_chunks = write_chunks_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "ChunkCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ChunkCommand)

    def run(self, args: argparse.Namespace) -> None:
        pdf = args.pdf
        pages = self.extract_all_pages(pdf)

        rng = self.autodetect(pdf)
        skip_pages = set(range(rng[0], rng[1] + 1)) if rng else set()
        if rng:
            self.console.log(f"Skipping ToC pages {rng[0]}-{rng[1]} during chunking")
            self.log.info(
                "Skipping ToC pages %d-%d during chunking for %s", rng[0], rng[1], pdf
            )

        toc_entries = None
        if args.toc:
            toc_entries = self.load_toc(args.toc)
            if rng:
                before = len(toc_entries)
                toc_entries = [e for e in toc_entries if e.page > rng[1]]
                after = len(toc_entries)
                if after < before:
                    self.console.log(
                        f"Filtered {before - after} ToC rows with page ≤ {rng[1]} (inside ToC)"
                    )
                    self.log.debug(
                        "Filtered %d ToC entries inside ToC pages", before - after
                    )

        if toc_entries:
            chunks = self.build_chunks_from_toc(
                pages, toc_entries, skip_pages=skip_pages
            )
            self.log.debug("Built %d chunks using provided ToC", len(chunks))
        else:
            chunks = self.build_chunks(
                pages, toc_ids=None, skip_pages=skip_pages, toc_map=None
            )
            self.log.debug("Built %d chunks using automatic chunking", len(chunks))

        count = self.write_chunks(chunks, args.out)
        self.console.print(
            f"[green]Wrote {count} chunks →\n{os.path.abspath(args.out)}"
        )
        self.log.info("Wrote %d chunks to %s", count, args.out)


class ValidateCommand(AbstractCommand):
    def __init__(
        self,
        load_toc_fn: Callable[..., list] = load_toc,
        load_chunks_fn: Callable[..., list] = load_chunks,
        match_sections_fn: Callable[..., tuple] = match_sections,
        write_report_fn: Callable[..., None] = write_report,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.load_toc = load_toc_fn
        self.load_chunks = load_chunks_fn
        self.match_sections = match_sections_fn
        self.write_report = write_report_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "ValidateCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ValidateCommand)

    def run(self, args: argparse.Namespace) -> None:
        toc = self.load_toc(args.toc)
        chunks = self.load_chunks(args.chunks)

        fuzzy_threshold = getattr(args, "fuzzy_threshold", 0.90)
        prefer_section_id = getattr(args, "prefer_section_id", True)
        self.log.debug(
            "Validation fuzzy_threshold=%s prefer_section_id=%s",
            fuzzy_threshold,
            prefer_section_id,
        )

        missing, extra, out_of_order, matched = self.match_sections(
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
        self.write_report(args.out, report)
        self.log.info("Wrote validation report to %s", args.out)


class MetricsCommand(AbstractCommand):
    def __init__(
        self,
        load_toc_fn: Callable[..., list] = load_toc,
        load_chunks_fn: Callable[..., list] = load_chunks,
        compute_metrics_fn: Callable[..., dict] = compute_metrics,
        write_metrics_fn: Callable[..., None] = write_metrics,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.load_toc = load_toc_fn
        self.load_chunks = load_chunks_fn
        self.compute_metrics = compute_metrics_fn
        self.write_metrics = write_metrics_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "MetricsCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, MetricsCommand)

    def run(self, args: argparse.Namespace) -> None:
        toc = self.load_toc(args.toc)
        chunks = self.load_chunks(args.chunks)
        m = self.compute_metrics(toc, chunks)
        self.write_metrics(args.out, m)
        self.console.print(f"[green]Metrics → \n{os.path.abspath(args.out)}")
        self.console.print(m)
        self.log.info("Wrote metrics to %s", args.out)


class TocGraphCommand(AbstractCommand):
    def __init__(
        self,
        load_toc_fn: Callable[..., list] = load_toc,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.load_toc = load_toc_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "TocGraphCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TocGraphCommand)

    def run(self, args: argparse.Namespace) -> None:
        toc = self.load_toc(args.toc)
        G = TocGraphBuilder(toc)
        TocGraphWriter(args.out, G)
        self.console.print(f"[green]ToC graph → {os.path.abspath(args.out)}")
        self.log.info("Wrote ToC graph to %s", args.out)


class KGCommand(AbstractCommand):
    def __init__(
        self,
        load_chunks_fn: Callable[..., list] = load_chunks,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.load_chunks = load_chunks_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "KGCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, KGCommand)

    def run(self, args: argparse.Namespace) -> None:
        chunks = self.load_chunks(args.chunks)
        triples = extract_triples(chunks, include_meta=False)  # strict 3-field triples

        if args.array:
            from src.graph.kg_simple import write_triples_json

            write_triples_json(args.out, triples)
            self.log.debug("Wrote triples as single JSON array to %s", args.out)
        else:
            from src.graph.kg_simple import write_triples_jsonl

            write_triples_jsonl(args.out, triples)
            self.log.debug("Wrote triples as JSONL to %s", args.out)

        self.console.print(f"[green]Wrote triples → {os.path.abspath(args.out)}")
        self.log.info("Wrote triples to %s", args.out)


class ReportCommand(AbstractCommand):
    def __init__(self, console_obj: Console = console, logger=LOG) -> None:
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "ReportCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ReportCommand)

    def run(self, args: argparse.Namespace) -> None:
        with open(args.validation, "r", encoding="utf-8") as f:
            val_json = json.load(f)
        val = ValidationReport.model_validate(val_json)

        with open(args.metrics, "r", encoding="utf-8") as f:
            metrics = json.load(f)

        rep = FinalReport.generate_final_report(val, metrics)
        FinalReport.write_final_report(args.out, rep)
        self.console.print(f"[green]Final report → {os.path.abspath(args.out)}")
        self.log.info("Wrote final report to %s", args.out)


class GraphCommand(AbstractCommand):
    def __init__(
        self,
        load_toc_fn: Callable[..., list] = load_toc,
        console_obj: Console = console,
        logger=LOG,
    ) -> None:
        self.load_toc = load_toc_fn
        self.console = console_obj
        self.log = logger

    def __str__(self) -> str:
        return "GraphCommand()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, GraphCommand)

    def run(self, args: argparse.Namespace) -> None:
        toc = self.load_toc(args.toc)
        G = TocGraphBuilder(toc)
        TocGraphWriter(args.out, G)
        self.console.print(f"[green]Graph JSON → {os.path.abspath(args.out)}")
        self.log.info("Wrote graph JSON to %s", args.out)


def cmd_toc(args: argparse.Namespace) -> None:
    TocCommand().run(args)


def cmd_chunk(args: argparse.Namespace) -> None:
    ChunkCommand().run(args)


def cmd_validate(args: argparse.Namespace) -> None:
    ValidateCommand().run(args)


def cmd_metrics(args: argparse.Namespace) -> None:
    MetricsCommand().run(args)


def cmd_toc_graph(args: argparse.Namespace) -> None:
    TocGraphCommand().run(args)


def cmd_kg(args: argparse.Namespace) -> None:
    KGCommand().run(args)


def cmd_report(args: argparse.Namespace) -> None:
    ReportCommand().run(args)


def cmd_graph(args: argparse.Namespace) -> None:
    GraphCommand().run(args)


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
