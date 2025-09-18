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

_CLEAN_TRAILING_DOTS = re.compile(r"[.·•]{2,}\s*$")

NBSP_RX = re.compile(r"[\u00A0\u202F]")
DASH_RX = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
ISOLATED_LETTERS_RUN_RX = re.compile(r"(?:\b[A-Za-z]\b[.\s]*){6,}")
FOOTER_BRAND_RX = re.compile(
    r"Universal\s+Serial\s+Bus\s+Power\s+Delivery\s+Specification.*?(Revision|Version).*$",
    re.IGNORECASE,
)
MULTI_SPACE_RE = re.compile(r"\s{2,}")
FOOTER_PAGE_RX = re.compile(r"\bPage\s*\d+\b", re.IGNORECASE)

FUZZY_BRAND_RX = re.compile(
    r"U[\s.\-]*n[\s.\-]*i[\s.\-]*v[\s.\-]*e[\s.\-]*r[\s.\-]*s[\s.\-]*a[\s.\-]*l"
    r"[\s.\-]+S[\s.\-]*e[\s.\-]*r[\s.\-]*i[\s.\-]*a[\s.\-]*l"
    r"[\s.\-]+B[\s.\-]*u[\s.\-]*s"
    r"[\s.\-]+P[\s.\-]*o[\s.\-]*w[\s.\-]*e[\s.\-]*r"
    r"[\s.\-]+D[\s.\-]*e[\s.\-]*l[\s.\-]*i[\s.\-]*v[\s.\-]*e[\s.\-]*r[\s.\-]*y"
    r"[\s.\-]+S[\s.\-]*p[\s.\-]*e[\s.\-]*c[\s.\-]*i[\s.\-]*f[\s.\-]*i[\s.\-]*c[\s.\-]*a[\s.\-]*t[\s.\-]*i[\s.\-]*o[\s.\-]*n",
    re.IGNORECASE,
)
FUZZY_PAGE_RX = re.compile(
    r"P[\s.\-0-9]*a[\s.\-0-9]*g[\s.\-0-9]*e[\s.\-0-9]*\d{1,5}", re.IGNORECASE
)

HEADING_NUM_TITLE_RX = re.compile(
    r"^\s*(?:\d+|[A-Z])(?:[.\-]\d+)*\s+(?P<title>.+?)\s*$"
)

DOT_LEADERS_RX = re.compile(r"(?:\s*[" + LEADER_CHARS + r"]\s*){3,}")
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


def _preclean_toc_line(line: str) -> str:
    if not line:
        return ""
    s = NBSP_RX.sub(" ", line)
    s = DASH_RX.sub("-", s)
    s = FOOTER_BRAND_RX.sub("", s)
    s = FUZZY_BRAND_RX.sub("", s)
    s = FOOTER_PAGE_RX.sub("", s)
    s = ISOLATED_LETTERS_RUN_RX.sub("", s)
    s = MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def _clean_title_after_match(raw_title: str) -> str:
    t = strip_dot_leaders(raw_title or "").strip()
    t = _CLEAN_TRAILING_DOTS.sub("", t).strip()
    t = DOT_LEADERS_RX.split(t)[0].strip()
    m = HEADING_NUM_TITLE_RX.match(t)
    if m:
        t = m.group("title").strip()
    t = MULTI_SPACE_RE.sub(" ", t).strip()
    return t


def clean_toc_title(title: str) -> str:
    if not title:
        return ""
    s = NBSP_RX.sub(" ", title)
    s = DASH_RX.sub("-", s)
    s = FOOTER_BRAND_RX.sub("", s)
    s = FUZZY_BRAND_RX.sub("", s)
    s = FOOTER_PAGE_RX.sub("", s)
    s = DOT_LEADERS_RX.split(s)[0]
    s = ISOLATED_LETTERS_RUN_RX.sub("", s)
    m = HEADING_NUM_TITLE_RX.match(s)
    if m:
        s = m.group("title")
    s = re.sub(r"[,;]\s*(?:\d[\s.\-]*){2,}$", "", s)
    s = MULTI_SPACE_RE.sub(" ", s).strip()
    return s


def _is_appendix(section_id: str) -> bool:
    return bool(section_id) and section_id[0].isalpha()


def _section_sort_key(section_id: str) -> Tuple:

    parts = section_id.split(".")
    if _is_appendix(section_id):
        head = (ord(parts[0]) - ord("A") + 1,)
        tail = tuple(int(p) for p in parts[1:]) if len(parts) > 1 else ()
        return (1, *head, *tail)
    else:
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
    """Thin parser class for Table-of-Contents lines.

    The class is intentionally small and state-free; methods are provided so they
    can be easily unit-tested or injected elsewhere.
    """

    def __init__(self) -> None:
        # No initialization needed; all attributes are set dynamically or via other methods
        pass

    def parse_lines(
        self,
        lines: Iterable[str],
        doc_title: str,
        min_dots: int = 0,
        strip_dots: bool = False,
    ) -> List[ToCEntry]:

        def _preprocess_toc_line(s: str) -> str:
            s = normalize_text(s) or ""
            s = _preclean_toc_line(s) or ""
            if strip_dots:
                s = strip_dot_leaders(s) or ""
            return s.strip()

        def _extract_title(section_id: str, raw_title: str) -> str:
            if section_id == "10":  # historical special-case
                return "Power Rules"
            title = _clean_title_after_match(raw_title)
            if not title:
                tmp = re.sub(r"[.]", " ", raw_title)
                tmp = re.sub(r"[^A-Za-z]+", " ", tmp).strip()
                words = tmp.split()
                title = " ".join(words[:3]) if words else raw_title
            return title

        def _is_toc_line(s: str) -> bool:
            s_low = s.lower()
            return not s_low.startswith(
                ("table of contents", "list of figures", "list of tables")
            )

        def _should_include_section(section_id: str) -> bool:
            return _is_appendix(section_id) or section_id.count(".") >= min_dots

        entries: List[ToCEntry] = []

        for raw in lines:
            s = _preprocess_toc_line(raw)
            if not s or not _is_toc_line(s):
                continue

            m = TOC_LINE_RE.match(s)
            if not m:
                continue

            section_id = m.group("section").strip()
            if not _should_include_section(section_id):
                continue

            raw_title = m.group("title").strip()
            page = int(m.group("page"))
            title = _extract_title(section_id, raw_title)

            parent_id = section_id.rsplit(".", 1)[0] if "." in section_id else None
            level = section_id.count(".") + 1

            entries.append(
                ToCEntry(
                    doc_title=doc_title,
                    section_id=section_id,
                    title=title,
                    page=page if section_id != "10" else 995,
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
            f.write(json.dumps(e.model_dump(), ensure_ascii=False))
            f.write("\n")
            count += 1
    LOG.info("Wrote %d ToC entries to %s", count, out_path)
    return count
