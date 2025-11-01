
from __future__ import annotations

"""
utils.py

PDF utilities for normalization, ToC detection, and page extraction.

Refactor notes:
- Lazy-import heavy PDF libraries (pdfplumber, fitz / PyMuPDF) to avoid import-time failures.
- Added clearer docstrings and error logging.
- Preserved public API and function names for backward compatibility.
- Kept an abstract contract + concrete PDFUtils implementation.
"""

from abc import ABC, abstractmethod
import io
import re
import importlib
from typing import Generator, List, Optional, Tuple, Iterable, Dict, Any

from src.logger import get_logger

LOG = get_logger(__name__)


class AbstractPDFUtils(ABC):
    """Abstract contract for PDF utilities."""

    LIGATURES: Dict[str, str]
    TOC_START_PAT: re.Pattern
    LIST_STOP_PAT: re.Pattern
    HEADING_RE: re.Pattern
    DOT_LEADERS_RX: re.Pattern

    @abstractmethod
    def normalize_text(self, s: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def strip_dot_leaders(self, s: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def autodetect_toc_range(self, pdf_path: str) -> Optional[Tuple[int, int]]:
        raise NotImplementedError

    @abstractmethod
    def parse_page_range(self, s: str) -> Tuple[int, int]:
        raise NotImplementedError

    @abstractmethod
    def extract_text_lines(self, pdf_path: str, start: int, end: int) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def extract_all_pages(self, pdf_path: str) -> List[Tuple[int, str]]:
        raise NotImplementedError

    @abstractmethod
    def looks_like_heading(self, num: str, title: str) -> bool:
        raise NotImplementedError


class PDFUtils(AbstractPDFUtils):
    """Encapsulate PDF text normalization, ToC detection, and page extraction utilities."""

    LIGATURES = {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "–": "-",
        "—": "-",
        "·": ".",
        "•": ".",
    }

    TOC_START_PAT = re.compile(r"\bTable Of Contents\b", re.IGNORECASE)
    LIST_STOP_PAT = re.compile(r"\bList of (Figures|Tables)\b", re.IGNORECASE)
    HEADING_RE = re.compile(r"^(?P<num>[1-9]\d*(?:\.\d+)*)\s+(?P<title>.+)$")

    DOT_LEADERS_RX = re.compile(r"\.{3,}")

    NBSP_RX = re.compile(r"[\u00A0\u202F]")
    DASH_RX = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")

    def __init__(self) -> None:
        pass

    def __str__(self) -> str:
        return "PDFUtils()"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PDFUtils):
            return NotImplemented

        return (
            self.TOC_START_PAT.pattern == other.TOC_START_PAT.pattern
            and self.LIST_STOP_PAT.pattern == other.LIST_STOP_PAT.pattern
        )

    def normalize_text(self, s: str) -> str:
        """Replace ligatures and normalize spaces and dash/nbsp variants."""
        if not s:
            return ""
        s = self.NBSP_RX.sub(" ", s)
        s = self.DASH_RX.sub("-", s)
        for k, v in self.LIGATURES.items():
            s = s.replace(k, v)
        s = re.sub(r"[ \t]+", " ", s)
        return s.strip()

    def strip_dot_leaders(self, s: str) -> str:
        """Replace long dot leader runs with a single space."""
        return self.DOT_LEADERS_RX.sub(" ", s or "")

    def autodetect_toc_range(self, pdf_path: str) -> Optional[Tuple[int, int]]:
        """Detect start/end pages of the Table of Contents in a PDF.

        Returns 1-based page numbers (start, end) or None if not found.
        This method lazy-imports pdfplumber and returns None (logged) if unavailable.
        """
        pdf_path = str(pdf_path)
        pdfplumber = _lazy_import("pdfplumber")
        if pdfplumber is None:
            LOG.warning("pdfplumber not installed; autodetect_toc_range unavailable")
            return None

        LOG.debug("Autodetecting ToC range in %s", pdf_path)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                n = len(pdf.pages)
                start: Optional[int] = None
                # limit search to first 30 pages for performance
                for i in range(min(n, 30)):
                    txt = pdf.pages[i].extract_text() or ""
                    if self.TOC_START_PAT.search(self.normalize_text(txt)):
                        start = i + 1  # 1-based
                        LOG.debug("Found ToC start marker on page %d", start)
                        break
                if start is None:
                    LOG.debug("No ToC start marker found in first 30 pages")
                    return None

                end: Optional[int] = None
                for p in range(start + 1, min(start + 12, n) + 1):
                    txt = pdf.pages[p - 1].extract_text() or ""
                    if self.LIST_STOP_PAT.search(self.normalize_text(txt)):
                        end = p - 1
                        LOG.debug("Found ToC end marker near page %d -> end=%d", p, end)
                        break

                if end is None:
                    end = min(start + 7, n)
                    LOG.debug("Defaulting ToC end to %d", end)

                return start, end
        except Exception as exc:
            LOG.exception("autodetect_toc_range failed for %s: %s", pdf_path, exc)
            return None

    def parse_page_range(self, s: str) -> Tuple[int, int]:
        """Parse a string like '13-18' into a tuple of integers."""
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s or "")
        if not m:
            raise ValueError("Page range must be like '13-18'")
        return int(m.group(1)), int(m.group(2))

    def _iter_lines_in_pages(
        self, pdf_path: str, start: int, end: int
    ) -> Generator[str, None, None]:
        """Yield text lines from pages start..end (inclusive) as a streaming generator.

        Uses pdfplumber (lazy import). If pdfplumber is unavailable, yields nothing.
        """
        pdfplumber = _lazy_import("pdfplumber")
        if pdfplumber is None:
            LOG.warning("pdfplumber not installed; extract_text_lines unavailable")
            return
            yield

        pdf_path = str(pdf_path)
        try:
            with pdfplumber.open(pdf_path) as pdf:
                n = len(pdf.pages)
                start = max(1, start)
                end = min(end, n)
                for pno in range(start, end + 1):
                    page = pdf.pages[pno - 1]
                    txt = page.extract_text() or ""
                    for line in io.StringIO(txt).read().splitlines():
                        yield line
        except Exception as exc:
            LOG.exception(
                "Error iterating lines for %s pages %d-%d: %s", pdf_path, start, end, exc
            )

    def extract_text_lines(self, pdf_path: str, start: int, end: int) -> List[str]:
        """Extract text lines from a PDF between start and end pages (inclusive)."""
        LOG.debug("Extracting text lines from %s pages %d-%d", pdf_path, start, end)
        lines = list(self._iter_lines_in_pages(pdf_path, start, end))
        LOG.debug("Extracted %d lines from %s", len(lines), pdf_path)
        return lines

    def extract_all_pages(self, pdf_path: str) -> List[Tuple[int, str]]:
        """Extract all pages from a PDF as a list of (page_number, text) tuples.

        This uses PyMuPDF (fitz). If fitz isn't available, returns [] and logs a warning.
        """
        pdf_path = str(pdf_path)
        fitz = _lazy_import("fitz")  # PyMuPDF
        if fitz is None:
            LOG.warning("PyMuPDF (fitz) not installed; extract_all_pages unavailable")
            return []

        LOG.debug("Extracting all pages from %s using PyMuPDF", pdf_path)
        pages: List[Tuple[int, str]] = []
        try:
            with fitz.open(pdf_path) as doc:
                for page_no, page in enumerate(doc, start=1):
                    blocks = page.get_text("blocks")
                    blocks = sorted(blocks, key=lambda b: (b[1], b[0]))  # sort top-down
                    text = "\n".join(b[4] for b in blocks if b[4].strip())
                    pages.append((page_no, text))
        except Exception as exc:
            LOG.exception("extract_all_pages failed for %s: %s", pdf_path, exc)
        LOG.debug("Extracted %d pages from %s", len(pages), pdf_path)
        return pages

    def looks_like_heading(self, num: str, title: str) -> bool:
        """Determine if a section number/title resembles a heading."""
        if num == "0":
            return False
        t = (title or "").strip()
        if len(t) < 3:
            return False
        letters = sum(c.isalpha() for c in t)
        digits = sum(c.isdigit() for c in t)
        if letters == 0 or digits > letters:
            return False
        if re.search(r"\b[01]{4,}\b", t):
            return False
        return True

_utils: AbstractPDFUtils = PDFUtils()


def _lazy_import(name: str) -> Optional[Any]:
    """Attempt to import a module by name and return it, or None if unavailable."""
    try:
        return importlib.import_module(name)
    except Exception as exc:
        LOG.debug("Lazy import failed for %s: %s", name, exc)
        return None

def normalize_text(s: str) -> str:
    try:
        return _utils.normalize_text(s)
    except Exception as e:
        LOG.error("normalize_text failed: %s", e, exc_info=True)
        return ""


def strip_dot_leaders(s: str) -> str:
    try:
        return _utils.strip_dot_leaders(s)
    except Exception as e:
        LOG.error("strip_dot_leaders failed: %s", e, exc_info=True)
        return ""


def autodetect_toc_range(pdf_path: str) -> Optional[Tuple[int, int]]:
    try:
        return _utils.autodetect_toc_range(pdf_path)
    except Exception as e:
        LOG.error("autodetect_toc_range failed for %s: %s", pdf_path, e, exc_info=True)
        return None


def parse_page_range(s: str) -> Tuple[int, int]:
    return _utils.parse_page_range(s)


def extract_text_lines(pdf_path: str, start: int, end: int) -> List[str]:
    try:
        return _utils.extract_text_lines(pdf_path, start, end)
    except Exception as e:
        LOG.error("extract_text_lines failed for %s pages %s-%s: %s", pdf_path, start, end, e, exc_info=True)
        return []


def extract_all_pages(pdf_path: str) -> List[Tuple[int, str]]:
    try:
        return _utils.extract_all_pages(pdf_path)
    except Exception as e:
        LOG.error("extract_all_pages failed for %s: %s", pdf_path, e, exc_info=True)
        return []


def looks_like_heading(num: str, title: str) -> bool:
    try:
        return _utils.looks_like_heading(num, title)
    except Exception as e:
        LOG.error("looks_like_heading failed: %s", e, exc_info=True)
        return False
