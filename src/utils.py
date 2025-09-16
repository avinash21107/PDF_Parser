import io
import re
from typing import List, Optional, Tuple
import pdfplumber

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

HEADING_RE = re.compile(r"^(?P<num>(?:[1-9]\d*(?:\.\d+)*))\s+(?P<title>.+)$")


def normalize_text(s: str) -> str:
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def strip_dot_leaders(s: str) -> str:
    return re.sub(r"\.{3,}", " ", s)


def autodetect_toc_range(pdf_path: str) -> Optional[Tuple[int, int]]:
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        search_limit = min(n, 30)
        start = None
        for i in range(search_limit):
            txt = pdf.pages[i].extract_text() or ""
            if TOC_START_PAT.search(normalize_text(txt)):
                start = i + 1
                break
        if start is None:
            return None
        end = None
        for p in range(start + 1, min(start + 12, n) + 1):
            txt = pdf.pages[p - 1].extract_text() or ""
            if LIST_STOP_PAT.search(normalize_text(txt)):
                end = p - 1
                break
        if end is None:
            end = min(start + 7, n)
        return (start, end)


def parse_page_range(s: str) -> Tuple[int, int]:
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if not m:
        raise ValueError("Page range must be like '13-18'")
    return int(m.group(1)), int(m.group(2))


def extract_text_lines(pdf_path: str, start: int, end: int) -> List[str]:
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
    return lines


def extract_all_pages(pdf_path: str) -> List[Tuple[int, str]]:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            txt = page.extract_text() or ""
            pages.append((i, txt))
    return pages


def looks_like_heading(num: str, title: str) -> bool:
    """
    Heuristics to avoid table rows and bit/hex dumps:
    """
    if num == "0":
        return False
    t = title.strip()
    if len(t) < 3:
        return False
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    if letters == 0:
        return False
    if digits > letters:
        return False

    if re.search(r"\b(0|1){4,}\b", t):
        return False
    return True
