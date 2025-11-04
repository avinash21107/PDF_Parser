from __future__ import annotations
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from src.models import Caption, Chunk, ToCEntry
from src.utils import looks_like_heading, normalize_text, strip_dot_leaders


class PDFRegexes:
    """Encapsulate all PDF-related regex patterns."""

    SEP = r"[.:\-\u2010\u2011\u2012\u2013\u2014\u2212]"
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
    HEADING_RE = re.compile(
        r"^\s*(?P<num>(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*))\s+(?P<title>.+?)\s*$"
    )
    BULLET_CHARS = ("", "", "●", "▪", "", "", "", "•")


class AbstractCleaner(ABC):
    """Contract for text cleaning/normalization."""

    @abstractmethod
    def norm_caption_line(self, s: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def looks_like_running_header_noisy(self, s: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def clean_content(self, text: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def clean_heading_title(self, title: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def normalize_sentences(self, text: str) -> str:
        raise NotImplementedError


class Cleaner(AbstractCleaner):
    """Text cleaning utilities used when building chunks."""

    def __init__(self) -> None:
        self.regex = PDFRegexes()

    def __str__(self) -> str:
        return "Cleaner()"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Cleaner)

    def norm_caption_line(self, s: str) -> str:
        s = self.regex.NBSP_FIX.sub(" ", s)
        s = self.regex.DASH_NORMALIZE.sub("-", s)
        s = re.sub(r"(?i)\bT\s*a\s*b\s*l\s*e\b", "Table", s)
        s = re.sub(r"(?i)\bF\s*i\s*g\s*u\s*r\s*e\b", "Figure", s)
        # Use lookahead constant from PDFRegexes
        s = re.sub(rf"(?i)(Table){PDFRegexes.TABLE_FIGURE_LOOKAHEAD}", r"\1 ", s)
        s = re.sub(rf"(?i)(Figure){PDFRegexes.TABLE_FIGURE_LOOKAHEAD}", r"\1 ", s)
        s = self.regex.MULTI_SPACE_RE.sub(" ", s).strip()
        return s

    def looks_like_running_header_noisy(self, s: str) -> bool:
        norm = re.sub(r"[\s.\-·•_]", "", s).lower()
        return any(
            term in norm for term in ("universalserialbuspowerdeliveryspecification", "revision32", "version11")
        )

    def clean_content(self, text: str) -> str:
        """Normalize PDF-extracted page content."""
        if not text:
            return ""
        for b in self.regex.BULLET_CHARS:
            text = text.replace(b, "- ")
        # Join hyphen-split words across linebreaks
        text = re.sub(r"(\S)-\n([a-z])", r"\1\2", text)
        # Replace hyphen+linebreak between words with a space
        text = re.sub(r"(\S)[\-\u2010-\u2014\u2212]\n(\S)", r"\1 \2", text)
        text = text.replace('\\"', '"').replace("\\'", "'")
        text = re.sub(r"(?<!\w)/(?!\w)", "", text)
        # CamelCase separation heuristics
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        text = re.sub(r'\s*"([^"]+)"\s*', r' "\1" ', text)

        cleaned_lines: List[str] = []
        for line in text.splitlines():
            s = line.rstrip()
            s = PDFRegexes.TRAILING_LEADERS_PAGE.sub("", s)
            s = PDFRegexes.DOT_LEADERS_RUN.sub(" ", s)
            s = PDFRegexes.MULTI_SPACE_RE.sub(" ", s).strip()
            if s:
                cleaned_lines.append(s)
        return "\n".join(cleaned_lines).strip()

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
    """Detect headings in page text using heuristics and Cleaner."""

    def __init__(self, cleaner: AbstractCleaner):
        self.cleaner = cleaner
        self.noise_patterns = [
            PDFRegexes.PUNCT_RUN,
            PDFRegexes.ISOLATED_LETTERS,
            PDFRegexes.PAGE_NO_NOISY,
            PDFRegexes.USB_SPEC_PATTERN,
        ]

    def __str__(self) -> str:
        return f"HeadingDetector(cleaner={self.cleaner!r})"

    def _heading_is_noisy(self, line: str, title: str) -> bool:
        """Return True if a heading is noisy."""
        if any(pat.search(title) or pat.search(line) for pat in self.noise_patterns):
            return True
        if self.cleaner.looks_like_running_header_noisy(title):
            return True
        if not re.search(r"[A-Za-z]", title):
            return True
        return not looks_like_heading(num=title, title=title)

    def extract_heading(
        self,
        line: str,
        toc_ids: Optional[Set[str]] = None,
        toc_map: Optional[Dict[str, str]] = None,
    ) -> Optional[Tuple[str, str]]:
        s = normalize_text(line)
        m = PDFRegexes.HEADING_RE.match(s)
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


class AbstractChunkBuilder(ABC):
    """Contract for chunk building and output writer."""

    @abstractmethod
    def build_chunks_from_toc(
        self, pages: List[Tuple[int, str]], toc_entries: List[ToCEntry], skip_pages: Optional[Set[int]] = None
    ) -> List[Chunk]:
        raise NotImplementedError

    @abstractmethod
    def build_chunks(
        self, pages: List[Tuple[int, str]], toc_ids: Optional[Set[str]] = None, skip_pages: Optional[Set[int]] = None, toc_map: Optional[Dict[str, str]] = None
    ) -> List[Chunk]:
        raise NotImplementedError

    @abstractmethod
    def enrich_with_figures_tables(self, chunks: List[Chunk]) -> None:
        raise NotImplementedError

    @abstractmethod
    def write_jsonl(self, chunks: List[Chunk], out_path: str) -> int:
        raise NotImplementedError


class ChunkBuilder(AbstractChunkBuilder):
    """Build chunks from pages using ToC or detected headings."""

    def __init__(self, cleaner: AbstractCleaner, detector: HeadingDetector):
        self.cleaner = cleaner
        self.detector = detector

    def __str__(self) -> str:
        return f"ChunkBuilder(cleaner={self.cleaner!r}, detector={self.detector!r})"

    def _filter_content_line(self, line: str) -> bool:
        s = line.strip()
        # Always keep explicit Table/Figure caption lines
        if re.search(r"\b(Table|Figure)\b", s, re.IGNORECASE):
            return True

        # Heuristic for pure heading-only lines:
        # - If the line looks like a heading AND it is short and contains no sentence punctuation,
        #   treat it as a heading-only line and exclude it from body content.
        # - Otherwise keep the line (this allows "2 Overview This section ..." lines).
        if re.match(r"^\d+(?:\.\d+)*\s+.+", s):
            if len(s) < 120 and not re.search(r"[.!?;:—-]", s):
                return False

        if PDFRegexes.USB_SPEC_PATTERN.search(s):
            return False
        if re.match(r"^Page\s+\d+\s*$", s, re.I):
            return False
        return True

    def _lines_for_page_range(
        self, page_map: Dict[int, str], pstart: int, pend: int, skip_pages: Set[int]
    ) -> List[str]:
        """Gather filtered lines across a page range."""
        lines: List[str] = []
        for p in range(pstart, pend + 1):
            if p in skip_pages:
                continue
            page_text = page_map.get(p, "")
            for line in page_text.splitlines():
                if self._filter_content_line(line):
                    lines.append(line)
        return lines

    def _build_chunk(self, lines: List[str], section_id: str, title: str, pstart: int, pend: int) -> Chunk:
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

    def _build_chunks_from_bounds(self, bounds: List[Tuple[int, int, str, str]], page_map: Dict[int, str], skip_pages: Set[int]) -> List[Chunk]:
        """Common logic to build chunks from bounds."""
        chunks = []
        for pstart, pend, sec, title in bounds:
            lines = self._lines_for_page_range(page_map, pstart, pend, skip_pages)
            chunks.append(self._build_chunk(lines, sec, title, pstart, pend))
        self.enrich_with_figures_tables(chunks)
        for ch in chunks:
            ch.content = self.cleaner.normalize_sentences(ch.content)
        return chunks

    def enrich_with_figures_tables(self, chunks: List[Chunk]) -> None:
        for ch in chunks:
            ch.figures = []
            ch.tables = []
            if not ch.content:
                continue
            for line in ch.content.splitlines():
                ln = self.cleaner.norm_caption_line(line)
                if m := PDFRegexes.FIGURE_RE.search(ln):
                    ch.figures.append(Caption(id=m.group(1)))
                    continue
                if m := PDFRegexes.TABLE_RE.search(ln):
                    ch.tables.append(Caption(id=m.group(1)))

    def build_chunks_from_toc(
        self, pages: List[Tuple[int, str]], toc_entries: List[ToCEntry], skip_pages: Optional[Set[int]] = None
    ) -> List[Chunk]:
        skip_pages = skip_pages or set()
        page_map = dict(pages)
        entries_sorted = sorted(toc_entries, key=lambda e: e.page)
        last_pdf_page = pages[-1][0] if pages else 0

        bounds: List[Tuple[int, int, str, str]] = []
        for i, e in enumerate(entries_sorted):
            pstart = e.page
            pend = entries_sorted[i + 1].page - 1 if i + 1 < len(entries_sorted) else last_pdf_page
            pend = max(pstart, pend)
            bounds.append((pstart, pend, e.section_id, e.title))

        return self._build_chunks_from_bounds(bounds, page_map, skip_pages)

    def build_chunks(
        self, pages: List[Tuple[int, str]], toc_ids: Optional[Set[str]] = None, skip_pages: Optional[Set[int]] = None, toc_map: Optional[Dict[str, str]] = None
    ) -> List[Chunk]:
        skip_pages = skip_pages or set()
        heads = self.detector.detect_headings(pages, toc_ids=toc_ids, skip_pages=skip_pages, toc_map=toc_map)
        if not heads:
            return []

        last_page = pages[-1][0]

        # sort by numeric section path (e.g., 2.1 before 10)
        def _section_key(item: Tuple[int, str, str]) -> Tuple[Tuple[int, ...], int]:
            pno, sec, _ = item
            try:
                key = tuple(int(x) for x in sec.split("."))
            except Exception:
                key = (0,)
            return key, pno

        heads_sorted = sorted(heads, key=_section_key)

        bounds: List[Tuple[int, int, str, str]] = []
        for i, (pno, sec, title) in enumerate(heads_sorted):
            next_p = heads_sorted[i + 1][0] if i + 1 < len(heads_sorted) else last_page + 1
            bounds.append((pno, next_p - 1, sec, title))

        page_map = dict(pages)
        return self._build_chunks_from_bounds(bounds, page_map, skip_pages)

    def write_jsonl(self, chunks: List[Chunk], out_path: str) -> int:
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out_file.open("w", encoding="utf-8") as fh:
            for c in chunks:
                pr_list: List[int] = []
                try:
                    parts = [p.strip() for p in (c.page_range or "").split(",") if p.strip()]
                    pr_list = [int(x) for x in parts] if parts else []
                except Exception:
                    pr_list = []

                obj = {
                    "section_path": c.section_path,
                    "start_heading": f"{c.section_id} {c.title}",
                    "content": c.content,
                    "tables": [f"Table {t.id}" for t in (c.tables or [])],
                    "figures": [f"Figure {fg.id}" for fg in (c.figures or [])],
                    "page_range": pr_list,
                }
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                count += 1
        return count


_cleaner: AbstractCleaner = Cleaner()
_detector = HeadingDetector(_cleaner)
_builder: AbstractChunkBuilder = ChunkBuilder(_cleaner, _detector)


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


def write_jsonl(chunks: List[Chunk], out_path: str) -> int:
    return _builder.write_jsonl(chunks, out_path)