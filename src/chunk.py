from __future__ import annotations

import json
import re
import os
from typing import List, Tuple, Optional, Set, Dict

from src.models import Chunk, ToCEntry, Caption
from src.utils import normalize_text, strip_dot_leaders, HEADING_RE, looks_like_heading

SEP = r"[.\:\-\u2010\u2011\u2012\u2013\u2014\u2212]"
ID = r"(?:(?:\d+|[A-Z])(?:\.\d+)*[a-z]?)"
CAPTION_SEP = r"(?:\s*[:.\-–—]?\s*)"

FIGURE_RE = re.compile(rf"\bFigure\s+({ID})\b", re.IGNORECASE)
TABLE_RE = re.compile(rf"\bTable\s+({ID})\b", re.IGNORECASE)

_CLEAN_TRAILING_PAGE = re.compile(r"[.·•]{2,}\s*\d+\s*$")
PUNCT_RUN = re.compile(r"[.\u00B7•]{3,}")
ISOLATED_LETTERS = re.compile(r"(?:\b[A-Za-z]\b[.\s]*){6,}")
PAGE_NO_NOISY = re.compile(r"P\s*a\s*g\s*e\s*\d+", re.IGNORECASE)
DOT_LEADERS_RUN = re.compile(r"(?:\s*[.\u00B7•]\s*){3,}")
TRAILING_LEADERS_PAGE = re.compile(r"(?:\s*[.\u00B7•]\s*){2,}\s*\d+\s*$")

NBSP_FIX = re.compile(r"[\u00A0\u202F]")
DASH_NORMALIZE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
MULTI_SPACE_RE = re.compile(r"\s{2,}")

USB_SPEC_PATTERN = re.compile(
    r"Universal Serial Bus Power Delivery Specification", re.IGNORECASE
)
TABLE_FIGURE_LOOKAHEAD = r"(?=(?:\s*[A-Z]\.)|\s*\d)"


class _Cleaner:
    """Encapsulate caption/line/content cleaning utilities."""

    @staticmethod
    def norm_caption_line(s: str) -> str:
        s = NBSP_FIX.sub(" ", s)
        s = DASH_NORMALIZE.sub("-", s)
        s = re.sub(r"(?i)\bT\s*a\s*b\s*l\s*e\b", "Table", s)
        s = re.sub(r"(?i)\bF\s*i\s*g\s*u\s*r\s*e\b", "Figure", s)

        s = re.sub(r"(?i)(\S)(?=Table)", r"\1 ", s)
        s = re.sub(r"(?i)(\S)(?=Figure)", r"\1 ", s)

        s = re.sub(rf"(?i)(Table){TABLE_FIGURE_LOOKAHEAD}", r"\1 ", s)
        s = re.sub(rf"(?i)(Figure){TABLE_FIGURE_LOOKAHEAD}", r"\1 ", s)

        s = MULTI_SPACE_RE.sub(" ", s).strip()
        return s

    @staticmethod
    def looks_like_running_header_noisy(s: str) -> bool:
        norm = re.sub(r"[\s.\-·•_]", "", s).lower()
        return (
            "universalserialbuspowerdeliveryspecification" in norm
            or "revision32" in norm
            or "version11" in norm
        )

    _DEHYPHEN = re.compile(r"(\S)-\n([a-z])")
    _DEHYPHEN_GENERIC = re.compile(
        r"(\S)[\-\u2010\u2011\u2012\u2013\u2014\u2212]\n(\S)"
    )
    _MULTI_NL = re.compile(r"\n{3,}")

    @classmethod
    def clean_content(cls, text: str) -> str:
        if not text:
            return ""

        for b in ("", "●", "▪", "", "", ""):
            text = text.replace(b, "- ")
        text = text.replace("•", "- ")

        text = cls._DEHYPHEN.sub(r"\1\2", text)
        text = cls._DEHYPHEN_GENERIC.sub(r"\1 \2", text)

        cleaned_lines: List[str] = []
        for raw in text.splitlines():
            s = raw.rstrip()
            s = TRAILING_LEADERS_PAGE.sub("", s)
            s = DOT_LEADERS_RUN.sub(" ", s)
            s = MULTI_SPACE_RE.sub(" ", s).strip()
            if not s:
                continue
            cleaned_lines.append(s)

        text = "\n".join(cleaned_lines)
        text = cls._MULTI_NL.sub("\n\n", text)
        return text.strip()

    @staticmethod
    def clean_heading_title(title: str) -> str:
        t = strip_dot_leaders(title).strip()
        t = _CLEAN_TRAILING_PAGE.sub("", t).strip()
        return t


class _HeadingDetector:
    """Encapsulate heading extraction and noisy-heading filtering."""

    FILTER_PATTERNS_HEADING = {
        "punct_run": PUNCT_RUN,
        "isolated_letters": ISOLATED_LETTERS,
        "page_no_noisy": PAGE_NO_NOISY,
        "running_header": _Cleaner.looks_like_running_header_noisy,
        "dot_leaders": re.compile(r"[.·•]{3,}"),
        "page_number": re.compile(r"^\s*Page\s*\d+\s*$", re.I),
        "only_digits": re.compile(r"^\d{1,4}$"),
        "usb_spec": USB_SPEC_PATTERN,
        "revisions": re.compile(r"Revision\s*3\.2|Version\s*1\.1|2024-10", re.I),
    }

    @classmethod
    def _heading_is_noisy(cls, line: str, title: str) -> bool:
        if cls.FILTER_PATTERNS_HEADING["punct_run"].search(title):
            return True
        if cls.FILTER_PATTERNS_HEADING["isolated_letters"].search(title):
            return True
        if cls.FILTER_PATTERNS_HEADING["page_no_noisy"].search(title):
            return True
        if cls.FILTER_PATTERNS_HEADING["running_header"](title):
            return True
        if cls.FILTER_PATTERNS_HEADING["dot_leaders"].search(title):
            return True
        if cls.FILTER_PATTERNS_HEADING["only_digits"].fullmatch(line):
            return True
        if cls.FILTER_PATTERNS_HEADING["page_number"].match(line):
            return True
        if cls.FILTER_PATTERNS_HEADING["usb_spec"].search(line):
            return True
        if cls.FILTER_PATTERNS_HEADING["revisions"].search(line):
            return True
        if not re.search(r"[A-Za-z]", title):
            return True
        if not looks_like_heading(num=title, title=title):
            return True
        return False

    @classmethod
    def _extract_heading(
        cls,
        line: str,
        toc_ids: Optional[Set[str]] = None,
        toc_map: Optional[Dict[str, str]] = None,
    ) -> Optional[Tuple[str, str]]:
        s = normalize_text(line)
        m = HEADING_RE.match(s)
        if not m:
            return None

        num = m.group("num")
        raw_title = m.group("title").strip()
        title = _Cleaner.clean_heading_title(raw_title)

        if cls._heading_is_noisy(s, title):
            return None

        if toc_ids is not None and num not in toc_ids:
            return None
        if toc_map and num in toc_map:
            title = toc_map[num]

        return num, title

    def detect_headings(
        self,
        pages: List[Tuple[int, str]],
        toc_ids: Optional[Set[str]] = None,
        skip_pages: Optional[Set[int]] = None,
        toc_map: Optional[Dict[str, str]] = None,
    ) -> List[Tuple[int, str, str]]:
        skip_pages = skip_pages or set()
        heads: List[Tuple[int, str, str]] = []
        for pno, text in pages:
            if pno in skip_pages:
                continue
            for line in (text or "").splitlines():
                heading = self._extract_heading(line, toc_ids, toc_map)
                if heading:
                    heads.append((pno, *heading))
        return heads


class _ChunkBuilder:
    """Build chunks from pages either using TOC entries or detected headings."""

    FILTER_PATTERNS = {
        "table_figure": re.compile(r"\b(Table|Figure)\b", re.IGNORECASE),
        "numbered_heading": re.compile(r"^\d+(?:\.\d+)*\s+.+"),
        "usb_spec": USB_SPEC_PATTERN,
        "page_number": re.compile(r"^\s*Page\s*\d+\s*$", re.I),
        "only_digits": re.compile(r"^\d{1,4}$"),
    }

    def __init__(self):
        self.cleaner = _Cleaner()
        self.heading_detector = _HeadingDetector()

    # --- build_chunks_from_toc helpers --- #
    @staticmethod
    def _compute_toc_bounds(entries_sorted: List[ToCEntry], last_pdf_page: int):
        bounds = []
        for i, e in enumerate(entries_sorted):
            pstart = e.page
            pend = (
                entries_sorted[i + 1].page - 1
                if i + 1 < len(entries_sorted)
                else last_pdf_page
            )
            pend = max(pstart, pend)
            bounds.append((pstart, pend, e.section_id, e.title))
        return bounds

    @classmethod
    def _should_keep_line(cls, line: str) -> bool:
        s = line.strip()
        if cls.FILTER_PATTERNS["table_figure"].search(s):
            return True
        if cls.FILTER_PATTERNS["numbered_heading"].match(s):
            return False
        if cls.FILTER_PATTERNS["usb_spec"].search(s):
            return False
        if cls.FILTER_PATTERNS["page_number"].match(s) or cls.FILTER_PATTERNS[
            "only_digits"
        ].fullmatch(s):
            return False
        return True

    @classmethod
    def _build_chunk_from_bounds(
        cls,
        pstart: int,
        pend: int,
        sec: str,
        title: str,
        page_map: Dict[int, str],
        skip_pages: Set[int],
    ) -> Chunk:
        lines: List[str] = []
        for p in range(pstart, pend + 1):
            if p in skip_pages:
                continue
            page_text = page_map.get(p, "")
            lines.extend(
                line for line in page_text.splitlines() if cls._should_keep_line(line)
            )
        content_clean = _Cleaner.clean_content("\n".join(lines))
        return Chunk(
            section_path=f"{sec} {title}",
            section_id=sec,
            title=title,
            page_range=f"{pstart},{pend}",
            content=content_clean,
            tables=[],
            figures=[],
        )

    def build_chunks_from_toc(
        self,
        pages: List[Tuple[int, str]],
        toc_entries: List[ToCEntry],
        skip_pages: Optional[Set[int]] = None,
    ) -> List[Chunk]:
        skip_pages = skip_pages or set()
        page_map = dict(pages)
        entries_sorted = sorted(toc_entries, key=lambda e: e.page)
        last_pdf_page = pages[-1][0] if pages else 0
        bounds = self._compute_toc_bounds(entries_sorted, last_pdf_page)

        chunks = [
            self._build_chunk_from_bounds(
                pstart, pend, sec, title, page_map, skip_pages
            )
            for pstart, pend, sec, title in bounds
        ]

        enrich_with_figures_tables(chunks)

        for ch in chunks:
            ch.content = normalize_sentences(ch.content)

        return chunks

    @staticmethod
    def _compute_bounds(
        heads_sorted: List[Tuple[int, str, str]], last_page: int
    ) -> List[Tuple[int, int, str, str]]:
        bounds = []
        for i, (pno, sec, title) in enumerate(heads_sorted):
            next_p = (
                heads_sorted[i + 1][0] if i + 1 < len(heads_sorted) else last_page + 1
            )
            bounds.append((pno, next_p - 1, sec, title))
        return bounds

    @staticmethod
    def _filter_content_line(line: str) -> bool:
        s = line.strip()
        if re.search(r"\b(Table|Figure)\b", s, re.IGNORECASE):
            return True
        if re.match(r"^\d+(?:\.\d+)*\s+.+", s):
            return False
        if USB_SPEC_PATTERN.search(s):
            return False
        if re.match(r"^Page\s+\d+\s*$", s, re.I):
            return False
        return True

    @classmethod
    def _build_single_chunk(
        cls,
        pstart: int,
        pend: int,
        sec: str,
        title: str,
        page_map: Dict[int, str],
        skip_pages: Optional[Set[int]] = None,
    ) -> Chunk:
        lines: List[str] = []
        for p in range(pstart, pend + 1):
            if skip_pages and p in skip_pages:
                continue
            page_text = page_map.get(p, "")
            lines.extend(
                [
                    line
                    for line in page_text.splitlines()
                    if cls._filter_content_line(line)
                ]
            )

        content_clean = _Cleaner.clean_content("\n".join(lines))
        return Chunk(
            section_path=f"{sec} {title}",
            section_id=sec,
            title=title,
            page_range=f"{pstart},{pend}",
            content=content_clean,
            tables=[],
            figures=[],
        )

    def build_chunks(
        self,
        pages: List[Tuple[int, str]],
        toc_ids: Optional[Set[str]] = None,
        skip_pages: Optional[Set[int]] = None,
        toc_map: Optional[Dict[str, str]] = None,
    ) -> List[Chunk]:
        heads = _HeadingDetector().detect_headings(
            pages, toc_ids=toc_ids, skip_pages=skip_pages, toc_map=toc_map
        )
        if not heads:
            return []

        last_page = pages[-1][0]
        heads_sorted = sorted(
            heads, key=lambda x: (tuple(map(int, x[1].split("."))), x[0])
        )
        bounds = self._compute_bounds(heads_sorted, last_page)

        page_map = dict(pages)
        chunks = [
            self._build_single_chunk(pstart, pend, sec, title, page_map, skip_pages)
            for pstart, pend, sec, title in bounds
        ]

        enrich_with_figures_tables(chunks)

        for ch in chunks:
            ch.content = normalize_sentences(ch.content)

        return chunks

    @staticmethod
    def normalize_sentences(text: str) -> str:
        if not text:
            return ""
        s = re.sub(r"\n+", " ", text)
        s = re.sub(r"\s+([,.])", r"\1", s)
        s = MULTI_SPACE_RE.sub(" ", s).strip()
        return s.strip()

    def write_jsonl(self, chunks: List[Chunk], out_path: str) -> int:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        count = 0
        with open(out_path, "w", encoding="utf-8") as f:
            for c in chunks:
                obj = {
                    "section_path": c.section_path,
                    "start_heading": f"{c.section_id} {c.title}",
                    "content": c.content,
                    "tables": [f"Table {t.id}" for t in (c.tables or [])],
                    "figures": [f"Figure {fg.id}" for fg in (c.figures or [])],
                    "page_range": [int(x) for x in c.page_range.split(",")],
                }
                json.dump(obj, f, ensure_ascii=False)
                f.write("\n")
                count += 1
        return count


_builder = _ChunkBuilder()
_detector = _HeadingDetector()
_cleaner = _Cleaner()


def norm_caption_line(s: str) -> str:
    return _cleaner.norm_caption_line(s)


def enrich_with_figures_tables(chunks: List[Chunk]) -> None:

    for ch in chunks:
        ch.figures, ch.tables = [], []
        if not ch.content:
            continue
        lines = ch.content.splitlines()
        for idx, line in enumerate(lines):
            ln = norm_caption_line(line)
            if m := FIGURE_RE.search(ln):
                ch.figures.append(Caption(id=m.group(1)))
                continue
            if m := TABLE_RE.search(ln):
                ch.tables.append(Caption(id=m.group(1)))


def build_chunks_from_toc(
    pages: List[Tuple[int, str]],
    toc_entries: List[ToCEntry],
    skip_pages: Optional[Set[int]] = None,
) -> List[Chunk]:
    return _builder.build_chunks_from_toc(pages, toc_entries, skip_pages=skip_pages)


def build_chunks(
    pages: List[Tuple[int, str]],
    toc_ids: Optional[Set[str]] = None,
    skip_pages: Optional[Set[int]] = None,
    toc_map: Optional[Dict[str, str]] = None,
) -> List[Chunk]:
    return _builder.build_chunks(
        pages, toc_ids=toc_ids, skip_pages=skip_pages, toc_map=toc_map
    )


def normalize_sentences(text: str) -> str:
    return _builder.normalize_sentences(text)


def write_jsonl(chunks: List[Chunk], out_path: str) -> int:
    return _builder.write_jsonl(chunks, out_path)
