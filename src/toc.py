from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

import pdfplumber

from src.logger import get_logger

LOG = get_logger(__name__)

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


class _PDFUtils:
    """Encapsulate pdf text normalization and extraction utilities."""

    def __init__(self) -> None:
        # keeps state-free; exists to group related helpers together
        pass

    def normalize_text(self, s: str) -> str:
        if s is None:
            return ""
        for k, v in LIGATURES.items():
            s = s.replace(k, v)
        s = re.sub(r"[ \t]+", " ", s)
        return s.strip()

    def strip_dot_leaders(self, s: str) -> str:
        return re.sub(r"\.{3,}", " ", (s or ""))

    def autodetect_toc_range(self, pdf_path: str) -> Optional[Tuple[int, int]]:
        LOG.debug("Autodetecting ToC range in %s", pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages)
            search_limit = min(n, 30)
            start: Optional[int] = None
            for i in range(search_limit):
                txt = pdf.pages[i].extract_text() or ""
                if TOC_START_PAT.search(self.normalize_text(txt)):
                    start = i + 1  # return 1-based page numbers
                    LOG.debug("Found ToC start marker on page %d", start)
                    break
            if start is None:
                LOG.debug("No ToC start marker found in first %d pages", search_limit)
                return None

            end: Optional[int] = None
            for p in range(start + 1, min(start + 12, n) + 1):
                txt = pdf.pages[p - 1].extract_text() or ""
                if LIST_STOP_PAT.search(self.normalize_text(txt)):
                    end = p - 1
                    LOG.debug("Found ToC end marker near page %d -> end=%d", p, end)
                    break

            if end is None:
                end = min(start + 7, n)
                LOG.debug("Defaulting ToC end to %d", end)

            return (start, end)

    def parse_page_range(self, s: str) -> Tuple[int, int]:
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", (s or ""))
        if not m:
            raise ValueError("Page range must be like '13-18'")
        return int(m.group(1)), int(m.group(2))

    def extract_text_lines(self, pdf_path: str, start: int, end: int) -> List[str]:
        LOG.debug("Extracting text lines from %s pages %d-%d", pdf_path, start, end)
        lines: List[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages)
            start = max(1, start)
            end = min(end, n)
            for pno in range(start, end + 1):
                page = pdf.pages[pno - 1]
                txt = page.extract_text() or ""
                for ln in io.StringIO(txt).read().splitlines():
                    lines.append(ln)
        LOG.debug("Extracted %d lines from %s", len(lines), pdf_path)
        return lines

    def extract_all_pages(self, pdf_path: str) -> List[Tuple[int, str]]:
        LOG.debug("Extracting all pages from %s", pdf_path)
        pages: List[Tuple[int, str]] = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                txt = page.extract_text() or ""
                pages.append((i, txt))
        LOG.debug("Extracted %d pages from %s", len(pages), pdf_path)
        return pages

    def looks_like_heading(self, num: str, title: str) -> bool:
        if num == "0":
            return False
        t = (title or "").strip()
        if len(t) < 3:
            return False
        letters = sum(c.isalpha() for c in t)
        digits = sum(c.isdigit() for c in t)
        if letters == 0:
            return False
        if digits > letters:
            return False

        if re.search(r"\b[01]{4,}\b", t):
            return False
        return True


_utils = _PDFUtils()


def normalize_text(s: str) -> str:
    try:
        return _utils.normalize_text(s)
    except Exception as e:
        LOG.error("normalize_text failed: %s", e, exc_info=True)
        return ""


def strip_dot_leaders(s: str) -> str:
    return _utils.strip_dot_leaders(s)


def autodetect_toc_range(pdf_path: str) -> Optional[Tuple[int, int]]:
    try:
        return _utils.autodetect_toc_range(pdf_path)
    except Exception as e:
        LOG.error("autodetect_toc_range failed for %s: %s", pdf_path, e, exc_info=True)
        return None


def parse_page_range(s: str) -> Tuple[int, int]:
    return _utils.parse_page_range(s)


def extract_text_lines(pdf_path: str, start: int, end: int) -> List[str]:
    return _utils.extract_text_lines(pdf_path, start, end)


def extract_all_pages(pdf_path: str) -> List[Tuple[int, str]]:
    try:
        return _utils.extract_all_pages(pdf_path)
    except Exception as e:
        LOG.error("extract_all_pages failed for %s: %s", pdf_path, e, exc_info=True)
        return []


def looks_like_heading(num: str, title: str) -> bool:
    return _utils.looks_like_heading(num, title)
