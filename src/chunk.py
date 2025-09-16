import json
import re
import os
from typing import List, Tuple, Optional, Set, Dict

from src.models import Chunk, ToCEntry, Caption
from src.utils import (
    normalize_text,
    strip_dot_leaders,
    HEADING_RE,
    looks_like_heading,
)


SEP = r"[.\:\-\u2010\u2011\u2012\u2013\u2014\u2212]"
ID = r"(?:(?:\d+|[A-Z])(?:\.\d+)*[a-z]?)"
CAPTION_SEP = r"(?:\s*[:.\-–—]?\s*)"

FIGURE_RE = re.compile(rf"\bFigure\s+({ID})\b", re.IGNORECASE)
TABLE_RE = re.compile(rf"\bTable\s+({ID})\b", re.IGNORECASE)

_CLEAN_TRAILING_PAGE = re.compile(r"[.·•]{2,}\s*\d+\s*$")
PUNCT_RUN = re.compile(r"[.\u00B7•\u2022]{3,}")
ISOLATED_LETTERS = re.compile(r"(?:\b[A-Za-z]\b[.\s]*){6,}")
PAGE_NO_NOISY = re.compile(r"P\s*a\s*g\s*e\s*\d+", re.IGNORECASE)
DOT_LEADERS_RUN = re.compile(r"(?:\s*[.\u00B7•\u2022]\s*){3,}")
TRAILING_LEADERS_PAGE = re.compile(r"(?:\s*[.\u00B7•\u2022]\s*){2,}\s*\d+\s*$")

NBSP_FIX = re.compile(r"[\u00A0\u202F]")
DASH_NORMALIZE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")


def norm_caption_line(s: str) -> str:
    s = NBSP_FIX.sub(" ", s)
    s = DASH_NORMALIZE.sub("-", s)
    s = re.sub(r"(?i)\bT\s*a\s*b\s*l\s*e\b", "Table", s)
    s = re.sub(r"(?i)\bF\s*i\s*g\s*u\s*r\s*e\b", "Figure", s)

    s = re.sub(r"(?i)(\S)(?=Table)", r"\1 ", s)
    s = re.sub(r"(?i)(\S)(?=Figure)", r"\1 ", s)

    s = re.sub(r"(?i)(Table)(?=(?:\s*[A-Z]\.)|\s*\d)", r"\1 ", s)
    s = re.sub(r"(?i)(Figure)(?=(?:\s*[A-Z]\.)|\s*\d)", r"\1 ", s)

    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def looks_like_running_header_noisy(s: str) -> bool:
    norm = re.sub(r"[\s.\-·•_]", "", s).lower()
    return (
        "universalserialbuspowerdeliveryspecification" in norm
        or "revision32" in norm
        or "version11" in norm
    )


_DEHYPHEN = re.compile(r"(\S)-\n([a-z])")
_DEHYPHEN_GENERIC = re.compile(r"(\S)[\-\u2010\u2011\u2012\u2013\u2014\u2212]\n(\S)")
_MULTI_NL = re.compile(r"\n{3,}")


def clean_content(text: str) -> str:
    if not text:
        return ""

    for b in ("", "●", "▪", "", "", ""):
        text = text.replace(b, "- ")
    text = text.replace("•", "- ")

    text = _DEHYPHEN.sub(r"\1\2", text)
    text = _DEHYPHEN_GENERIC.sub(r"\1 \2", text)

    cleaned_lines = []
    for raw in text.splitlines():
        s = raw.rstrip()
        s = TRAILING_LEADERS_PAGE.sub("", s)
        s = DOT_LEADERS_RUN.sub(" ", s)
        s = re.sub(r"\s{2,}", " ", s).strip()
        if not s:
            continue
        cleaned_lines.append(s)

    text = "\n".join(cleaned_lines)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


def clean_heading_title(title: str) -> str:
    t = strip_dot_leaders(title).strip()
    t = _CLEAN_TRAILING_PAGE.sub("", t).strip()
    return t


def enrich_with_figures_tables(chunks: List[Chunk]) -> None:
    """
    Populate ch.figures and ch.tables by scanning caption lines.
    IMPORTANT: expects ch.content to retain line breaks.
    """
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


def detect_headings(
    pages: List[Tuple[int, str]],
    toc_ids: Optional[Set[str]] = None,
    skip_pages: Optional[Set[int]] = None,
    toc_map: Optional[Dict[str, str]] = None,
):
    """
    Return list of (page_no, section_id, title) for numbered headings.
    If toc_map is provided, override the detected title with the clean ToC title.
    """
    heads = []
    skip_pages = skip_pages or set()
    for pno, text in pages:
        if pno in skip_pages:
            continue
        for line in (text or "").splitlines():
            s = normalize_text(line)
            m = HEADING_RE.match(s)
            if not m:
                continue
            num = m.group("num")
            raw_title = m.group("title").strip()
            title = clean_heading_title(raw_title)
            if PUNCT_RUN.search(title):
                continue
            if ISOLATED_LETTERS.search(title):
                continue
            if PAGE_NO_NOISY.search(title):
                continue
            if looks_like_running_header_noisy(title):
                continue
            if re.search(r"[.·•]{3,}", title):
                continue
            if re.fullmatch(r"\d{1,4}", s):
                continue
            if re.match(r"^\s*Page\s*\d+\s*$", s, re.I):
                continue
            if re.search(r"Universal Serial Bus Power Delivery Specification", s, re.I):
                continue
            if re.search(r"Revision\s*3\.2|Version\s*1\.1|2024-10", s, re.I):
                continue
            if not re.search(r"[A-Za-z]", title):
                continue
            if not looks_like_heading(num, title):
                continue
            if toc_ids is not None and num not in toc_ids:
                continue
            if toc_map and num in toc_map:
                title = toc_map[num]
            heads.append((pno, num, title))
    return heads


def build_chunks_from_toc(
    pages: List[Tuple[int, str]],
    toc_entries: List[ToCEntry],
    skip_pages: Optional[Set[int]] = None,
) -> List[Chunk]:
    skip_pages = skip_pages or set()
    page_map = {p: t for p, t in pages}
    entries = sorted(toc_entries, key=lambda e: e.page)

    last_pdf_page = pages[-1][0] if pages else 0
    bounds = []
    for i, e in enumerate(entries):
        pstart = e.page
        pend = (entries[i + 1].page - 1) if i + 1 < len(entries) else last_pdf_page
        if pend < pstart:
            pend = pstart
        bounds.append((pstart, pend, e.section_id, e.title))

    chunks: List[Chunk] = []
    for pstart, pend, sec, title in bounds:
        buf = []
        for p in range(pstart, pend + 1):
            if p in skip_pages:
                continue
            buf.append(page_map.get(p, ""))

        raw = "\n".join(buf)
        content_lines = []
        for line in raw.splitlines():
            s = line.strip()
            if re.search(r"\b(Table|Figure)\b", s, re.IGNORECASE):
                content_lines.append(line)
                continue
            if re.match(r"^\d+(?:\.\d+)*\s+.+", s):
                continue  # drop numbered headings
            if re.search(r"Universal Serial Bus Power Delivery Specification", s, re.I):
                continue
            if re.match(r"^\s*Page\s*\d+\s*$", s, re.I) or re.fullmatch(r"\d{1,4}", s):
                continue
            content_lines.append(line)

        content_clean = clean_content("\n".join(content_lines))

        chunks.append(
            Chunk(
                section_path=f"{sec} {title}",
                section_id=sec,
                title=title,
                page_range=f"{pstart},{pend}",
                content=content_clean,  # not normalized yet
                tables=[],
                figures=[],
            )
        )

    enrich_with_figures_tables(chunks)

    for ch in chunks:
        ch.content = normalize_sentences(ch.content)

    return chunks


def build_chunks(
    pages: List[Tuple[int, str]],
    toc_ids: Optional[Set[str]] = None,
    skip_pages: Optional[Set[int]] = None,
    toc_map: Optional[Dict[str, str]] = None,
) -> List[Chunk]:
    heads = detect_headings(
        pages, toc_ids=toc_ids, skip_pages=skip_pages, toc_map=toc_map
    )
    if not heads:
        return []

    last_page = pages[-1][0]
    heads_sorted = sorted(heads, key=lambda x: (tuple(map(int, x[1].split("."))), x[0]))

    bounds = []
    for i, (pno, sec, title) in enumerate(heads_sorted):
        next_p = heads_sorted[i + 1][0] if i + 1 < len(heads_sorted) else last_page + 1
        bounds.append((pno, next_p - 1, sec, title))

    page_map = {p: t for p, t in pages}
    chunks: List[Chunk] = []
    for pstart, pend, sec, title in bounds:
        buf = []
        for p in range(pstart, pend + 1):
            if skip_pages and p in skip_pages:
                continue
            buf.append(page_map.get(p, ""))

        raw = "\n".join(buf)
        content_lines = []
        for line in raw.splitlines():
            s = line.strip()
            if re.search(r"\b(Table|Figure)\b", s, re.IGNORECASE):
                content_lines.append(line)
                continue
            if re.match(r"^\d+(?:\.\d+)*\s+.+", s):
                continue
            if re.search(r"Universal Serial Bus Power Delivery Specification", s, re.I):
                continue
            if re.match(r"^Page\s+\d+\s*$", s, re.I):
                continue
            content_lines.append(line)

        content_clean = clean_content("\n".join(content_lines))

        chunks.append(
            Chunk(
                section_path=f"{sec} {title}",
                section_id=sec,
                title=title,
                page_range=f"{pstart},{pend}",
                content=content_clean,
                tables=[],
                figures=[],
            )
        )

    enrich_with_figures_tables(chunks)

    for ch in chunks:
        ch.content = normalize_sentences(ch.content)

    return chunks


def normalize_sentences(text: str) -> str:
    """
    Convert noisy PDF-extracted text into cleaner sentences.
    - Fix broken line breaks
    - Collapse multiple spaces
    - Ensure proper spacing around punctuation

    NOTE: This flattens line breaks; call it only AFTER we detect captions.
    """
    if not text:
        return ""
    s = re.sub(r"\n+", " ", text)  # kills line starts; fine post-enrichment
    s = re.sub(r"\s+([,.])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def write_jsonl(chunks: List[Chunk], out_path: str) -> int:
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
