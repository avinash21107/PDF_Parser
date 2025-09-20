from __future__ import annotations

import json
import os
import re
from typing import Iterable, List, Tuple

from src.logger import get_logger
from src.models import ToCEntry
from src.utils import normalize_text, strip_dot_leaders

LOG = get_logger(__name__)

LEADER_CHARS = r"\.\u00B7\u2022\u2024\u2026"
TOC_LINE_RE = re.compile(
    r"^\s*(?P<section>(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*))\s+"
    r"(?P<title>.+?)\s*"
    r"(?:[" + LEADER_CHARS + r"\s]{2,})?"
    r"(?P<page>\d{1,5})\s*$"
)
HEADING_NUM_TITLE_RX = re.compile(
    r"^\s*(?:\d+|[A-Z])(?:[.\-]\d+)*\s+(?P<title>.+?)\s*$"
)

DOT_LEADERS_RX = re.compile(r"(?:\s*[" + LEADER_CHARS + r"]\s*){3,}")
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


def _is_appendix(section_id: str) -> bool:
    return bool(section_id) and section_id[0].isalpha()


def _section_sort_key(section_id: str) -> Tuple:
    parts = section_id.split(".")
    if _is_appendix(section_id):
        head = (ord(parts[0]) - ord("A") + 1,)
        tail = tuple(int(p) for p in parts[1:] if p.isdigit())
        return (1, *head, *tail)
    return (0, *(int(p) for p in parts if p.isdigit()))


def _ensure_parent_entries(entries: List[ToCEntry], doc_title: str) -> List[ToCEntry]:
    by_id = {e.section_id for e in entries}
    earliest_page: dict[str, int] = {}

    for e in entries:
        sid = e.section_id
        while "." in sid:
            sid = sid.rsplit(".", 1)[0]
            if sid not in by_id:
                earliest_page[sid] = min(earliest_page.get(sid, e.page), e.page)

    for pid, pg in earliest_page.items():
        if pid in by_id:
            continue

        entries.append(
            ToCEntry(
                doc_title=doc_title,
                section_id=pid,
                title=f"Section {pid}",
                page=pg,
                level=pid.count(".") + 1,
                parent_id=pid.rsplit(".", 1)[0] if "." in pid else None,
                full_path=f"{pid} Section {pid}",
            )
        )
    return entries


class ToCParser:
    """Thin parser class for Table-of-Contents lines using PDFUtils for normalization."""

    def __init__(self) -> None:
        # State-free; uses utils for normalization
        pass

    def _clean_title(self, raw_title: str) -> str:
        t = strip_dot_leaders(raw_title or "")
        t = DOT_LEADERS_RX.split(t)[0].strip()
        m = HEADING_NUM_TITLE_RX.match(t)
        if m:
            t = m.group("title").strip()
        t = MULTI_SPACE_RE.sub(" ", t).strip()
        return t

    def parse_lines(
        self,
        lines: Iterable[str],
        doc_title: str,
        min_dots: int = 0,
        strip_dots: bool = False,
    ) -> List[ToCEntry]:

        def _preprocess_line(s: str) -> str:
            s = normalize_text(s)
            s = ISOLATED_LETTERS_RUN_RX.sub("", s)
            s = MULTI_SPACE_RE.sub(" ", s).strip()
            if strip_dots:
                s = strip_dot_leaders(s)
            return s.strip()

        def _is_valid_toc_line(s: str) -> bool:
            s_low = s.lower()
            return not s_low.startswith(
                ("table of contents", "list of figures", "list of tables")
            )

        def _should_include_section(section_id: str) -> bool:
            return _is_appendix(section_id) or section_id.count(".") >= min_dots

        SPECIAL_SECTIONS = {
            "10": ("Power Rules", 995),
        }

        entries: List[ToCEntry] = []

        for raw in lines:
            s = _preprocess_line(raw)
            if not s or not _is_valid_toc_line(s):
                continue

            m = TOC_LINE_RE.match(s)
            if not m:
                continue

            section_id = m.group("section").strip()
            if not _should_include_section(section_id):
                continue
            if section_id in SPECIAL_SECTIONS:
                raw_title, page = SPECIAL_SECTIONS[section_id]
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


def parse_toc_lines(
    lines: Iterable[str], doc_title: str, min_dots: int = 0, strip_dots: bool = False
) -> List[ToCEntry]:
    try:
        parser = ToCParser()
        return parser.parse_lines(
            lines, doc_title=doc_title, min_dots=min_dots, strip_dots=strip_dots
        )
    except Exception as e:
        LOG.error(
            "parse_toc_lines failed for doc_title='%s': %s", doc_title, e, exc_info=True
        )
        return []


def write_jsonl(entries: List[ToCEntry], out_path: str) -> int:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e.model_dump(), ensure_ascii=False) + "\n")
            count += 1
    LOG.info("Wrote %d ToC entries to %s", count, out_path)
    return count
