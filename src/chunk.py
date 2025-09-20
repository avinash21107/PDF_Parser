from __future__ import annotations
import re
import os
import json
from typing import List, Tuple, Optional, Set, Dict
from src.models import Chunk, ToCEntry, Caption
from src.utils import normalize_text, strip_dot_leaders, looks_like_heading, PDFUtils


class PDFRegexes:
    """Encapsulate all PDF-related regex patterns."""

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
    MULTI_SPACE_RE = re.compile(r"\s{2,}")
    DASH_NORMALIZE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
    NBSP_FIX = re.compile(r"[\u00A0\u202F]")
    USB_SPEC_PATTERN = re.compile(
        r"Universal Serial Bus Power Delivery Specification", re.IGNORECASE
    )
    TABLE_FIGURE_LOOKAHEAD = r"(?=(?:\s*[A-Z]\.)|\s*\d)"


class Cleaner:
    """Encapsulate all text cleaning utilities."""

    def __init__(self):
        self.regex = PDFRegexes()

    def norm_caption_line(self, s: str) -> str:
        s = self.regex.NBSP_FIX.sub(" ", s)
        s = self.regex.DASH_NORMALIZE.sub("-", s)
        s = re.sub(r"(?i)\bT\s*a\s*b\s*l\s*e\b", "Table", s)
        s = re.sub(r"(?i)\bF\s*i\s*g\s*u\s*r\s*e\b", "Figure", s)
        s = re.sub(rf"(?i)(Table){PDFRegexes.TABLE_FIGURE_LOOKAHEAD}", r"\1 ", s)
        s = re.sub(rf"(?i)(Figure){PDFRegexes.TABLE_FIGURE_LOOKAHEAD}", r"\1 ", s)
        s = self.regex.MULTI_SPACE_RE.sub(" ", s).strip()
        return s

    def looks_like_running_header_noisy(self, s: str) -> bool:
        norm = re.sub(r"[\s.\-·•_]", "", s).lower()
        return (
            "universalserialbuspowerdeliveryspecification" in norm
            or "revision32" in norm
            or "version11" in norm
        )

    def clean_content(self, text: str) -> str:
        if not text:
            return ""

        for b in ("", "●", "▪", "", "", "", "•"):
            text = text.replace(b, "- ")

        text = re.sub(r"(\S)-\n([a-z])", r"\1\2", text)
        text = re.sub(
            r"(\S)[\-\u2010\u2011\u2012\u2013\u2014\u2212]\n(\S)", r"\1 \2", text
        )

        cleaned_lines: List[str] = []
        for line in text.splitlines():
            s = line.rstrip()
            s = PDFRegexes.TRAILING_LEADERS_PAGE.sub("", s)
            s = PDFRegexes.DOT_LEADERS_RUN.sub(" ", s)
            s = PDFRegexes.MULTI_SPACE_RE.sub(" ", s).strip()
            if s:
                cleaned_lines.append(s)

        text = "\n".join(cleaned_lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def clean_heading_title(self, title: str) -> str:
        t = strip_dot_leaders(title).strip()
        t = PDFRegexes._CLEAN_TRAILING_PAGE.sub("", t).strip()
        return t

    @staticmethod
    def normalize_sentences(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"\n+", " ", text)
        text = re.sub(r"\s+([,.])", r"\1", text)
        text = PDFRegexes.MULTI_SPACE_RE.sub(" ", text).strip()
        return text.strip()


class HeadingDetector:
    """Encapsulate heading detection logic."""

    def __init__(self, cleaner: Cleaner):
        self.cleaner = cleaner
        self.noise_patterns = [
            PDFRegexes.PUNCT_RUN,
            PDFRegexes.ISOLATED_LETTERS,
            PDFRegexes.PAGE_NO_NOISY,
            PDFRegexes.USB_SPEC_PATTERN,
        ]

    def _heading_is_noisy(self, line: str, title: str) -> bool:
        for pat in self.noise_patterns:
            if isinstance(pat, re.Pattern):
                if pat.search(title) or pat.search(line):
                    return True
        if self.cleaner.looks_like_running_header_noisy(title):
            return True
        if not re.search(r"[A-Za-z]", title):
            return True
        if not looks_like_heading(num=title, title=title):
            return True
        return False

    def extract_heading(
        self,
        line: str,
        toc_ids: Optional[Set[str]] = None,
        toc_map: Optional[Dict[str, str]] = None,
    ) -> Optional[Tuple[str, str]]:
        s = normalize_text(line)
        m = PDFUtils.HEADING_RE.match(s)
        if not m:
            return None
        num, raw_title = m.group("num"), m.group("title").strip()
        title = self.cleaner.clean_heading_title(raw_title)

        if self._heading_is_noisy(s, title):
            return None
        if toc_ids and num not in toc_ids:
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
                heading = self.extract_heading(line, toc_ids, toc_map)
                if heading:
                    heads.append((pno, *heading))
        return heads


class ChunkBuilder:
    """Encapsulate chunk building from TOC or headings."""

    def __init__(self, cleaner: Cleaner, detector: HeadingDetector):
        self.cleaner = cleaner
        self.detector = detector

    def _filter_content_line(self, line: str) -> bool:
        s = line.strip()
        if re.search(r"\b(Table|Figure)\b", s, re.IGNORECASE):
            return True
        if re.match(r"^\d+(?:\.\d+)*\s+.+", s):
            return False
        if PDFRegexes.USB_SPEC_PATTERN.search(s):
            return False
        if re.match(r"^Page\s+\d+\s*$", s, re.I):
            return False
        return True

    def _build_chunk(
        self,
        lines: List[str],
        section_id: str,
        title: str,
        pstart: int,
        pend: int,
    ) -> Chunk:
        content = self.cleaner.clean_content("\n".join(lines))
        return Chunk(
            section_path=f"{section_id} {title}",
            section_id=section_id,
            title=title,
            page_range=f"{pstart},{pend}",
            content=content,
            tables=[],
            figures=[],
        )

    def enrich_with_figures_tables(self, chunks: List[Chunk]) -> None:
        for ch in chunks:
            ch.figures, ch.tables = [], []
            if not ch.content:
                continue
            lines = ch.content.splitlines()
            for line in lines:
                ln = self.cleaner.norm_caption_line(line)
                if m := PDFRegexes.FIGURE_RE.search(ln):
                    ch.figures.append(Caption(id=m.group(1)))
                    continue
                if m := PDFRegexes.TABLE_RE.search(ln):
                    ch.tables.append(Caption(id=m.group(1)))

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

        # Compute TOC bounds
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

        chunks = [
            self._build_chunk(
                [
                    line
                    for p in range(pstart, pend + 1)
                    if p not in skip_pages
                    for line in page_map.get(p, "").splitlines()
                    if self._filter_content_line(line)
                ],
                sec,
                title,
                pstart,
                pend,
            )
            for pstart, pend, sec, title in bounds
        ]

        self.enrich_with_figures_tables(chunks)
        for ch in chunks:
            ch.content = self.cleaner.normalize_sentences(ch.content)

        return chunks

    def build_chunks(
        self,
        pages: List[Tuple[int, str]],
        toc_ids: Optional[Set[str]] = None,
        skip_pages: Optional[Set[int]] = None,
        toc_map: Optional[Dict[str, str]] = None,
    ) -> List[Chunk]:
        skip_pages = skip_pages or set()
        heads = self.detector.detect_headings(
            pages, toc_ids=toc_ids, skip_pages=skip_pages, toc_map=toc_map
        )
        if not heads:
            return []

        last_page = pages[-1][0]
        heads_sorted = sorted(
            heads, key=lambda x: (tuple(map(int, x[1].split("."))), x[0])
        )

        bounds = []
        for i, (pno, sec, title) in enumerate(heads_sorted):
            next_p = (
                heads_sorted[i + 1][0] if i + 1 < len(heads_sorted) else last_page + 1
            )
            bounds.append((pno, next_p - 1, sec, title))

        page_map = dict(pages)
        chunks = [
            self._build_chunk(
                [
                    line
                    for p in range(pstart, pend + 1)
                    if p not in skip_pages
                    for line in page_map.get(p, "").splitlines()
                    if self._filter_content_line(line)
                ],
                sec,
                title,
                pstart,
                pend,
            )
            for pstart, pend, sec, title in bounds
        ]

        self.enrich_with_figures_tables(chunks)
        for ch in chunks:
            ch.content = self.cleaner.normalize_sentences(ch.content)

        return chunks

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


_cleaner = Cleaner()
_detector = HeadingDetector(_cleaner)
_builder = ChunkBuilder(_cleaner, _detector)


def norm_caption_line(s: str) -> str:
    return _cleaner.norm_caption_line(s)


def enrich_with_figures_tables(chunks: List[Chunk]) -> None:
    _builder.enrich_with_figures_tables(chunks)


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
    return _cleaner.normalize_sentences(text)


def write_jsonl(chunks: List[Chunk], out_path: str) -> int:
    return _builder.write_jsonl(chunks, out_path)
