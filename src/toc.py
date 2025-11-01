from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Optional

from src.logger import get_logger
from src.models import ToCEntry
from src.utils import normalize_text, strip_dot_leaders

LOG = get_logger(__name__)

_LEADER_CHARS = r"\.\u00B7\u2022\u2024\u2026"

TOC_LINE_RE = re.compile(
    r"^\s*(?P<section>(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*))\s+"
    r"(?P<title>.+?)\s*"
    r"(?:[" + _LEADER_CHARS + r"\s]{2,})?"
    r"(?P<page>\d{1,5})\s*$"
)

HEADING_NUM_TITLE_RX = re.compile(r"^\s*(?:\d+|[A-Z])(?:[.\-]\d+)*\s+(?P<title>.+?)\s*$")
DOT_LEADERS_RX = re.compile(r"(?:\s*[" + _LEADER_CHARS + r"]\s*){3,}")
ISOLATED_LETTERS_RUN_RX = re.compile(r"(?:\b[A-Za-z]\b[.\s]*){6,}")
MULTI_SPACE_RE = re.compile(r"\s{2,}")

BRAND_TOKENS = {
    "universal",
    "serial",
    "bus",
    "delivery",
    "specification",
    "revision",
    "version",
    "page",
}

_SPECIAL_SECTIONS: Dict[str, Tuple[str, int]] = {
    "10": ("Power Rules", 995),
}

def _is_appendix(section_id: str) -> bool:
    """Return True if section id represents an appendix (starts with a letter)."""
    return bool(section_id) and section_id[0].isalpha()


def _section_sort_key(section_id: str) -> Tuple[int, ...]:
    """
    Produce a tuple key for sorting section ids.
    Appendix sections are sorted after numeric sections.
    Examples:
      "1.2" -> (0, 1, 2)
      "A.1" -> (1, 1, 1)
    """
    parts = section_id.split(".")
    if _is_appendix(section_id):
        head = (ord(parts[0]) - ord("A") + 1,)
        tail = tuple(int(p) for p in parts[1:] if p.isdigit())
        return (1, *head, *tail)
    return (0, *(int(p) for p in parts if p.isdigit()))


def _ensure_parent_entries(entries: List[ToCEntry], doc_title: str) -> List[ToCEntry]:
    """
    Ensure that parent section IDs are present as ToCEntry items.
    If a child section "2.3.1" exists but "2.3" does not, create a synthetic
    parent entry with the earliest child page as its page.
    """
    by_id = {e.section_id for e in entries}
    earliest_page: Dict[str, int] = {}

    for e in entries:
        sid = e.section_id
        while "." in sid:
            sid = sid.rsplit(".", 1)[0]
            earliest_page[sid] = min(earliest_page.get(sid, e.page), e.page)

    for pid, pg in earliest_page.items():
        if pid in by_id:
            continue
        parent_id = pid.rsplit(".", 1)[0] if "." in pid else None
        entries.append(
            ToCEntry(
                doc_title=doc_title,
                section_id=pid,
                title=f"Section {pid}",
                page=pg,
                level=pid.count(".") + 1,
                parent_id=parent_id,
                full_path=f"{pid} Section {pid}",
            )
        )
    return entries


class AbstractToCParser(ABC):
    """Abstract contract for ToC parsers."""

    @abstractmethod
    def parse_lines(
        self,
        lines: Iterable[str],
        doc_title: str,
        min_dots: int = 0,
        strip_dots: bool = False,
    ) -> List[ToCEntry]:
        raise NotImplementedError


class ToCParser(AbstractToCParser):
    """Parser for Table-of-Contents lines.

    Stateless and focused on transforming raw lines into ToCEntry dataclasses.
    """

    def __init__(self) -> None:
        pass

    def _clean_title(self, raw_title: str) -> str:
        """Remove dot-leaders, numeric prefixes and collapse excess spacing."""
        t = strip_dot_leaders(raw_title or "")
        t = DOT_LEADERS_RX.split(t)[0].strip()
        m = HEADING_NUM_TITLE_RX.match(t)
        if m:
            t = m.group("title").strip()
        t = MULTI_SPACE_RE.sub(" ", t).strip()
        return t

    def _preprocess_line(self, s: str, strip_dots: bool) -> str:
        """Normalize whitespace and remove obvious noise from a raw ToC line."""
        s = normalize_text(s)
        s = ISOLATED_LETTERS_RUN_RX.sub("", s)
        s = MULTI_SPACE_RE.sub(" ", s).strip()
        if strip_dots:
            s = strip_dot_leaders(s)
        return s.strip()

    def _is_valid_toc_line(self, s: str) -> bool:
        """Reject header lines like 'Table of Contents' and lists-of-..."""
        s_low = s.lower()
        return not s_low.startswith(("table of contents", "list of figures", "list of tables"))

    def _should_include_section(self, section_id: str, min_dots: int) -> bool:
        """Decide whether to include a section ID based on min_dots or appendix flag."""
        return _is_appendix(section_id) or section_id.count(".") >= min_dots

    def parse_lines(
        self,
        lines: Iterable[str],
        doc_title: str,
        min_dots: int = 0,
        strip_dots: bool = False,
    ) -> List[ToCEntry]:
        """
        Parse ToC lines and return a list of ToCEntry objects.

        Parameters
        ----------
        lines : Iterable[str]
            Raw ToC text lines from PDF ToC pages.
        doc_title : str
            Document title to attach to each ToCEntry.
        min_dots : int
            Minimum number of dotted levels required (e.g., 1 to require '1.x').
        strip_dots : bool
            Whether to strip dot leaders prior to parsing.
        """
        entries: List[ToCEntry] = []

        for raw in lines:
            s = self._preprocess_line(raw, strip_dots)
            if not s or not self._is_valid_toc_line(s):
                continue

            m = TOC_LINE_RE.match(s)
            if not m:
                continue

            section_id = m.group("section").strip()
            if not self._should_include_section(section_id, min_dots):
                continue

            if section_id in _SPECIAL_SECTIONS:
                raw_title, page = _SPECIAL_SECTIONS[section_id]
            else:
                raw_title = m.group("title").strip()
                page = int(m.group("page"))

            title = self._clean_title(raw_title)
            parent_id = section_id.rsplit(".", 1)[0] if "." in section_id else None
            level = section_id.count(".") + 1

            entries.append(
                ToCEntry(
                    doc_title=doc_title,
                    section_id=section_id,
                    title=title,
                    page=page,
                    level=level,
                    parent_id=parent_id,
                    full_path=f"{section_id} {title}",
                )
            )

        entries = _ensure_parent_entries(entries, doc_title)
        entries.sort(key=lambda e: (_section_sort_key(e.section_id), e.page))
        return entries

_parser: AbstractToCParser = ToCParser()


def set_default_parser(parser: AbstractToCParser) -> None:
    """
    Replace the default parser instance with another implementing AbstractToCParser.
    Useful for injecting test doubles.
    """
    global _parser
    _parser = parser


def parse_toc_lines(
    lines: Iterable[str], doc_title: str, min_dots: int = 0, strip_dots: bool = False
) -> List[ToCEntry]:
    """Convenience wrapper that delegates to the configured parser instance.

    Returns an empty list and logs an error if parsing fails.
    """
    try:
        return _parser.parse_lines(lines, doc_title=doc_title, min_dots=min_dots, strip_dots=strip_dots)
    except Exception as exc:
        LOG.error("parse_toc_lines failed for doc_title=%r: %s", doc_title, exc, exc_info=True)
        return []


def write_jsonl(entries: List[ToCEntry], out_path: str) -> int:
    """Write ToCEntry objects to a JSONL file and return the written count."""
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out_file.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e.model_dump(), ensure_ascii=False) + "\n")
            count += 1
    LOG.info("Wrote %d ToC entries to %s", count, out_path)
    return count
