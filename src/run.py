from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
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
from src.validate import load_toc



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



def cmd_toc(args: argparse.Namespace) -> None:
    TocCommand().run(args)


def cmd_chunk(args: argparse.Namespace) -> None:
    ChunkCommand().run(args)


