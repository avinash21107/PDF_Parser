import json
import re, os
from typing import List, Dict, Tuple
from src.models import ToCEntry, Chunk, ValidationReport,Caption
from Levenshtein import ratio as lev_ratio
from rich.table import Table
from rich.console import Console
from src.models import ValidationReport, Chunk

_ID_SEP = r"[.\-\u2010\u2011\u2012\u2013\u2014\u2212]"
_ID_RX = rf"(?:[A-Z]{{1,3}}{_ID_SEP})?\d+(?:{_ID_SEP}\d+)*(?:[a-z])?"
TABLE_STR_RX = re.compile(rf"(?i)\btable\s+({_ID_RX})\b")
FIGURE_STR_RX = re.compile(rf"(?i)\bfigure\s+({_ID_RX})\b")

FOOTER_BRAND_RX = re.compile(
    r"Universal\s+Serial\s+Bus\s+Power\s+Delivery\s+Specification.*?(Revision|Version).*$",
    re.IGNORECASE,
)
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

ISOLATED_LETTERS_RUN_RX = re.compile(r'(?:\b[A-Za-z]\b[.\s]*){6,}')

DOT_LEADERS_RX = re.compile(r'(?:\s*[.\u00B7•\u2022]\s*){3,}')
NBSP_RX = re.compile(r"[\u00A0\u202F]")  # NBSP variants
DASH_RX = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")

def norm_id(s: str) -> str:
    if not s:
        return ""
    s = NBSP_RX.sub("", s)
    s = DASH_RX.sub("-", s)
    return s.strip()

HEADING_NUM_TITLE_RX = re.compile(r'^\s*\d+(?:[.\-]\d+)*\s+(?P<title>.+?)\s*$')


def clean_toc_title(title: str) -> str:
    if not title:
        return ""

    s = NBSP_RX.sub(" ", title)  # was NBSP_FIX_RX
    s = DASH_RX.sub("-", s)

    s = FOOTER_BRAND_RX.sub("", s)
    s = FOOTER_PAGE_RX.sub("", s)
    s = FUZZY_BRAND_RX.sub("", s)

    s = DOT_LEADERS_RX.split(s)[0]

    s = ISOLATED_LETTERS_RUN_RX.sub("", s)

    m = HEADING_NUM_TITLE_RX.match(s)
    if m:
        s = m.group("title")

    s = re.sub(r'[,;]\s*(?:\d[\s.\-]*){2,}$', '', s)

    s = re.sub(r"\s{2,}", " ", s).strip()

    norm = re.sub(r'[\s.\-]+', '', s).lower()
    if 'universalserialbuspowerdeliveryspecification' in norm:
        parts = s.split()
        s = " ".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")

    return s




def load_toc(path: str) -> List[ToCEntry]:
    vals = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            e = ToCEntry.model_validate_json(line)
            e.title = clean_toc_title(e.title)

            # DROP rows that are now empty or contain no letters (footer garbage)
            if not e.title or not re.search(r"[A-Za-z]", e.title):
                continue

            vals.append(e)
    return vals


def _coerce_export_record_to_chunk(obj: dict) -> Chunk:
    """
    Convert the 'export' format:
      {
        "section_path": "4.3.2 Device Role Swap Behavior",
        "start_heading": "4.3.2 Device Role Swap Behavior",
        "content": "...",
        "tables": ["Table 4-12"],
        "figures": ["Figure 4-8"],
        "page_range": [47, 49]
      }
    …into a canonical Chunk that the rest of the pipeline expects.
    """
    section_path = obj.get("section_path") or obj.get("start_heading") or ""

    if " " in section_path:
        section_id, title = section_path.split(" ", 1)
    else:

        section_id = obj.get("section_id") or ""
        title = obj.get("title") or section_path

    content = obj.get("content", "")

    pr = obj.get("page_range", "")
    if isinstance(pr, list) and len(pr) == 2:
        page_range = f"{int(pr[0])},{int(pr[1])}"
    elif isinstance(pr, str):
        page_range = pr
    else:
        page_range = ""

    tables_raw = obj.get("tables") or []
    figures_raw = obj.get("figures") or []

    def _to_captions(items, rx):
        caps: List[Caption] = []
        for it in items:
            if isinstance(it, dict) and "id" in it:  # already Caption-ish
                caps.append(Caption(id=str(it["id"])))
            elif isinstance(it, str):
                m = rx.search(it)
                if m:
                    caps.append(Caption(id=m.group(1)))

        return caps

    tables = _to_captions(tables_raw, TABLE_STR_RX)
    figures = _to_captions(figures_raw, FIGURE_STR_RX)

    return Chunk(
        section_path=section_path or f"{section_id} {title}".strip(),
        section_id=section_id,
        title=title,
        page_range=page_range,
        content=content,
        tables=tables,
        figures=figures,
    )


def load_chunks(path: str) -> List[Chunk]:
    """
    Load chunks.jsonl that may be in either:
      - canonical Chunk schema (original), or
      - export schema (your new flattened format).
    Coerces export records to Chunk so downstream code (metrics) keeps working.
    """
    items: List[Chunk] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)

            if "title" in obj and "section_id" in obj and isinstance(obj.get("page_range"), str):
                items.append(Chunk.model_validate(obj))
            else:
                items.append(Chunk.model_validate(_coerce_export_record_to_chunk(obj)))

    return items


def match_sections(
    toc: List[ToCEntry],
    chunks: List[Chunk],
    fuzzy_threshold: float = 0.90,
    prefer_section_id: bool = True,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    # index chunks by *normalized* section_id
    chunk_by_id = {norm_id(c.section_id): c for c in chunks if c.section_id}

    # pre-clean chunk titles for fuzzy fallback
    chunk_titles = [(c, clean_toc_title(c.title).lower()) for c in chunks]

    matched, missing = [], []

    for t in toc:
        c = None
        tid = norm_id(t.section_id)  # normalize ToC id

        if prefer_section_id and tid in chunk_by_id:
            c = chunk_by_id[tid]
        else:
            # fuzzy title match as fallback
            best, best_score = None, 0.0
            ttitle = clean_toc_title(t.title).lower()
            for ch, ltitle in chunk_titles:
                score = lev_ratio(ttitle, ltitle)
                if score > best_score:
                    best, best_score = ch, score
            if best_score >= fuzzy_threshold:
                c = best

        ttitle_clean = clean_toc_title(t.title)
        if c:
            matched.append(f"{t.section_id} {ttitle_clean}")
        else:
            missing.append(f"{t.section_id} {ttitle_clean}")

    # build matched id set from *normalized* ids
    matched_ids = {
        norm_id(m.split(" ", 1)[0]) for m in matched if " " in m
    }

    # extras: chunks whose *normalized* id not in matched_ids
    extra = [
        f"{c.section_id} {clean_toc_title(c.title)}"
        for c in chunks
        if c.section_id and norm_id(c.section_id) not in matched_ids
    ]

    def skey(s: str):
        sid = s.split(" ", 1)[0]
        return tuple(int(x) for x in re.split(r"[.\-]", sid) if x.isdigit())

    out_of_order = []
    sorted_matched = sorted(matched, key=skey)
    if sorted_matched != matched:
        out_of_order = matched

    return (missing, extra, out_of_order, matched)





def write_report(out_path: str, report: ValidationReport) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # Persist JSON exactly as your model defines it
    data = report.model_dump()

    # Pull fields using the correct names
    toc_cnt        = data.get("toc_section_count", 0)
    parsed_cnt     = data.get("parsed_section_count", 0)
    matched        = data.get("matched_sections", [])
    missing        = data.get("missing_sections", [])
    extra          = data.get("extra_sections", [])
    out_of_order   = data.get("out_of_order_sections", [])

    # Save full report JSON
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Pretty console summary
    console = Console()
    table = Table(title="Validation Summary")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Total sections (ToC)", str(toc_cnt))
    table.add_row("Total sections (Chunks)", str(parsed_cnt))
    table.add_row("Matched sections", str(len(matched)))
    table.add_row("Missing sections", str(len(missing)))
    table.add_row("Extra sections", str(len(extra)))
    table.add_row("Out-of-order sections", str(len(out_of_order)))
    console.print(table)


