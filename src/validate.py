from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Set

from difflib import SequenceMatcher
from rich.console import Console
from rich.table import Table

from src.logger import get_logger
from src.models import Caption, Chunk, ToCEntry, ValidationReport
from src.utils import normalize_text, strip_dot_leaders

LOG = get_logger(__name__)
CONSOLE = Console()

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
    r"U[\s.\-]*n[\s.\-]*i[\s.\-]*v[\s.\-]*e[\s.\-]*r[\s.\-]*s[\s.\-]*a"
    r"[\s.\-]+S[\s.\-]*e[\s.\-]*r[\s.\-]*i[\s.\-]*a[\s.\-]*l"
    r"[\s.\-]+B[\s.\-]*u[\s.\-]*s"
    r"[\s.\-]+P[\s.\-]*o[\s.\-]*w[\s.\-]*e[\s.\-]*r"
    r"[\s.\-]+D[\s.\-]*e[\s.\-]*l[\s.\-]*i[\s.\-]*v[\s.\-]*e[\s.\-]*r[\s.\-]*y"
    r"[\s.\-]+S[\s.\-]*p[\s.\-]*e[\s.\-]*c[\s.\-]*i[\s.\-]*f[\s.\-]*i[\s.\-]*c[\s.\-]*a[\s.\-]*t[\s.\-]*i[\s.\-]*o[\s.\-]*n",
    re.IGNORECASE,
)

ISOLATED_LETTERS_RUN_RX = re.compile(r"(?:\b[A-Za-z]\b[.\s]*){6,}")
DOT_LEADERS_RX = re.compile(r"(?:\s*[.\u00B7•\u2022]\s*){3,}")
NBSP_RX = re.compile(r"[\u00A0\u202F]")
DASH_RX = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2212]")
HEADING_NUM_TITLE_RX = re.compile(r"^\s*\d+(?:[.\-]\d+)*\s+(?P<title>.+?)\s*$")

def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    """Yield parsed JSON objects from a JSONL file (streaming)."""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                LOG.exception("Skipping malformed JSON line in %s: %s", path, line[:200])


def _short_chunk_repr(obj: Dict[str, Any], max_title: int = 80) -> str:
    """Compact single-line chunk summary safe for logging (never includes full content)."""
    try:
        sid = obj.get("section_id") or obj.get("section_path") or ""
        title = str(obj.get("title") or "")
        if len(title) > max_title:
            title = title[: max_title - 3] + "..."
        pr = obj.get("page_range") or obj.get("page") or ""
        c_len = len(str(obj.get("content") or "")) if obj.get("content") else 0
        tbls = len(obj.get("tables") or [])
        figs = len(obj.get("figures") or [])
        return f"Chunk(section_id={sid!r}, title={title!r}, pages={pr}, content_len={c_len}, tables={tbls}, figures={figs})"
    except Exception:
        return "Chunk(<unprintable>)"


def _norm_id(s: str) -> str:
    """Normalise dash/nbsp characters for section IDs (keeps digits/letters intact)."""
    if not s:
        return ""
    s = NBSP_RX.sub("", s)
    s = DASH_RX.sub("-", s)
    return s.strip()


def _lev_ratio(a: str, b: str) -> float:
    """Try to use python-Levenshtein.ratio; fallback to difflib.SequenceMatcher if not installed."""
    try:
        from Levenshtein import ratio as lev_ratio_fn  # type: ignore

        return lev_ratio_fn(a, b)
    except Exception:
        return SequenceMatcher(None, a, b).ratio()


class AbstractValidator(ABC):
    """Abstract contract for a validator."""

    @abstractmethod
    def load_toc(self, path: str) -> List[ToCEntry]:
        raise NotImplementedError

    @abstractmethod
    def load_chunks(self, path: str) -> List[Chunk]:
        raise NotImplementedError

    @abstractmethod
    def match_sections(
        self,
        toc: List[ToCEntry],
        chunks: List[Chunk],
        fuzzy_threshold: float = 0.90,
        prefer_section_id: bool = True,
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        raise NotImplementedError

    @abstractmethod
    def write_report(self, out_path: str, report: ValidationReport) -> None:
        raise NotImplementedError


class Validator(AbstractValidator):
    """
    Validator handles loading ToC / chunk records, matching sections, and writing summary reports.

    Improvements:
    - DI-friendly constructor.
    - Optional noisy-chunk filtering via `skip_noisy_chunks` flag.
    - Small helpers for coercion, caption parsing and chunk repr to avoid huge logs.
    """

    def __init__(
        self,
        table_rx: re.Pattern = TABLE_STR_RX,
        figure_rx: re.Pattern = FIGURE_STR_RX,
        footer_brand_rx: re.Pattern = FOOTER_BRAND_RX,
        footer_page_rx: re.Pattern = FOOTER_PAGE_RX,
        fuzzy_brand_rx: re.Pattern = FUZZY_BRAND_RX,
        normalize_fn=normalize_text,
        strip_fn=strip_dot_leaders,
        *,
        skip_noisy_chunks: bool = False,
        noisy_chunk_max_len: int = 10_000,
    ) -> None:
        self.table_rx = table_rx
        self.figure_rx = figure_rx
        self.footer_brand_rx = footer_brand_rx
        self.footer_page_rx = footer_page_rx
        self.fuzzy_brand_rx = fuzzy_brand_rx
        self.normalize = normalize_fn
        self.strip_dot_leaders = strip_fn

        # optional behaviour toggles
        self.skip_noisy_chunks = skip_noisy_chunks
        self.noisy_chunk_max_len = noisy_chunk_max_len

    def __str__(self) -> str:
        return f"Validator(table_rx={getattr(self.table_rx, 'pattern', '<rx>')}, skip_noisy={self.skip_noisy_chunks})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Validator):
            return NotImplemented
        return (
            self.table_rx.pattern == other.table_rx.pattern
            and self.figure_rx.pattern == other.figure_rx.pattern
            and self.skip_noisy_chunks == other.skip_noisy_chunks
        )


    def load_toc(self, path: str) -> List[ToCEntry]:
        """Load ToC entries from JSONL and normalise titles (streaming)."""
        vals: List[ToCEntry] = []
        p = Path(path)
        for obj in _iter_jsonl(p):
            try:
                e = ToCEntry.model_validate(obj) if isinstance(obj, dict) else ToCEntry.model_validate_json(json.dumps(obj))
            except Exception:
                LOG.exception("Failed to parse ToC record (skipping): %s", str(obj)[:200])
                continue
            e.title = self._clean_toc_title(e.title)
            if not e.title or not re.search(r"[A-Za-z]", e.title):
                continue
            vals.append(e)
        LOG.info("Loaded %d ToC entries from %s", len(vals), path)
        return vals

    def load_chunks(self, path: str) -> List[Chunk]:
        """Load chunk records from JSONL, coerce legacy formats to Chunk model (streaming)."""
        items: List[Chunk] = []
        p = Path(path)
        for obj in _iter_jsonl(p):
            try:
                chunk = self._load_single_chunk(obj)
            except Exception:
                LOG.exception("Failed to coerce chunk record (skipping): %s", str(obj)[:200])
                continue
            if self.skip_noisy_chunks and self._is_noisy_chunk(chunk):
                LOG.info("Skipping noisy chunk: %s", _short_chunk_repr(chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)))
                continue
            items.append(chunk)
        LOG.info("Loaded %d chunks from %s", len(items), path)
        return items

    def _load_single_chunk(self, obj: Dict[str, Any]) -> Chunk:
        """Coerce a JSON object into a Chunk model, accommodating legacy formats."""
        try:
            if "title" in obj and "section_id" in obj and isinstance(obj.get("page_range"), str):
                return Chunk.model_validate(obj)
        except Exception:
            LOG.debug("Preserving coercion path for chunk record (legacy-like)")

        coerced = self._coerce_export_record_to_chunk(obj)
        return Chunk.model_validate(coerced)

    def _extract_chunk_info(self, obj: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
        """Return (section_path, section_id, title, page_range, content) for a legacy or modern chunk record."""
        section_path = obj.get("section_path") or obj.get("start_heading") or ""
        if " " in section_path:
            section_id, title = section_path.split(" ", 1)
        else:
            section_id = obj.get("section_id") or ""
            title = obj.get("title") or section_path or ""
        content = obj.get("content", "")
        pr = obj.get("page_range", "")
        if isinstance(pr, list) and len(pr) == 2:
            page_range = f"{int(pr[0])},{int(pr[1])}"
        elif isinstance(pr, str):
            page_range = pr
        else:
            page_range = ""
        return section_path, section_id, title, page_range, content

    def _to_captions(self, items: Any, rx: re.Pattern) -> List[Caption]:
        """Convert a legacy caption list (strings or dicts) into Caption models using regexes."""
        caps: List[Caption] = []
        for it in items or []:
            if isinstance(it, dict) and "id" in it:
                caps.append(Caption(id=str(it["id"])))
            elif isinstance(it, str):
                m = rx.search(it)
                if m:
                    caps.append(Caption(id=m.group(1)))
        return caps

    def _coerce_export_record_to_chunk(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        """Convert legacy export records into the Chunk-compatible dict expected by the model."""
        section_path, section_id, title, page_range, content = self._extract_chunk_info(obj)
        tables = self._to_captions(obj.get("tables"), self.table_rx)
        figures = self._to_captions(obj.get("figures"), self.figure_rx)
        return {
            "section_path": section_path or f"{section_id} {title}".strip(),
            "section_id": section_id,
            "title": title,
            "page_range": page_range,
            "content": content,
            "tables": [c.model_dump() for c in tables],
            "figures": [c.model_dump() for c in figures],
        }

    def _clean_toc_title(self, title: str) -> str:
        """Clean and normalise ToC title strings for comparison and display."""
        if not title:
            return ""
        s = self.normalize(title)
        s = self.footer_brand_rx.sub("", s)
        s = self.footer_page_rx.sub("", s)
        s = self.fuzzy_brand_rx.sub("", s)
        s = self.strip_dot_leaders(s)
        s = ISOLATED_LETTERS_RUN_RX.sub("", s)
        m = HEADING_NUM_TITLE_RX.match(s)
        if m:
            s = m.group("title")
        s = re.sub(r"[,;]\s*(?:\d[\s.\-]*){2,}$", "", s)
        s = re.sub(r"\s{2,}", " ", s).strip()

        norm = re.sub(r"[\s.\-]+", "", s).lower()
        if "universalserialbuspowerdeliveryspecification" in norm:
            parts = s.split()
            s = " ".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")
        return s

    def _is_noisy_chunk(self, c: Chunk) -> bool:
        """Heuristic to detect overly noisy chunk records."""
        try:
            content = getattr(c, "content", "") or ""
            if not content:
                return False
            if len(content) > self.noisy_chunk_max_len:
                return True
            words = re.findall(r"\b[A-Za-z]{3,}\b", content)
            if len(words) > 2000:
                return True
            return False
        except Exception:
            return False

    def _find_matching_chunk(
        self,
        tid: str,
        ttitle_clean: str,
        chunk_by_id: Dict[str, int],
        chunk_titles: List[Tuple[int, Chunk, str]],
        used_chunk_idxs: Set[int],
        prefer_section_id: bool,
        fuzzy_threshold: float,
    ) -> Optional[int]:
        """Find best matching chunk index for a ToC entry using section_id or fuzzy title matching."""
        if prefer_section_id and tid in chunk_by_id:
            ci = chunk_by_id[tid]
            if ci not in used_chunk_idxs:
                return ci

        ttitle_l = ttitle_clean.lower()
        best_i: Optional[int] = None
        best_score = 0.0
        for i, _, ltitle in chunk_titles:
            if i in used_chunk_idxs:
                continue
            score = _lev_ratio(ttitle_l, ltitle)
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None and best_score >= fuzzy_threshold:
            return best_i
        return None

    def match_sections(
        self,
        toc: List[ToCEntry],
        chunks: List[Chunk],
        fuzzy_threshold: float = 0.90,
        prefer_section_id: bool = True,
    ) -> Tuple[List[str], List[str], List[str], List[str]]:
        """
        Match ToC entries to parsed chunks and return:
           (missing_labels, extra_labels, out_of_order_labels, matched_labels)
        """
        chunk_by_id: Dict[str, int] = {
            _norm_id(c.section_id): i for i, c in enumerate(chunks) if c.section_id
        }

        chunk_titles: List[Tuple[int, Chunk, str]] = [
            (i, c, self._clean_toc_title(c.title).lower()) for i, c in enumerate(chunks)
        ]

        used_chunk_idxs: Set[int] = set()
        matched_labels: List[str] = []
        matched_idx: List[Optional[int]] = []
        missing_labels: List[str] = []

        for t in toc:
            tid = _norm_id(t.section_id)
            ttitle_clean = self._clean_toc_title(t.title)
            chunk_i = self._find_matching_chunk(
                tid,
                ttitle_clean,
                chunk_by_id,
                chunk_titles,
                used_chunk_idxs,
                prefer_section_id,
                fuzzy_threshold,
            )
            if chunk_i is not None:
                used_chunk_idxs.add(chunk_i)
                matched_labels.append(f"{t.section_id} {ttitle_clean}")
                matched_idx.append(chunk_i)
            else:
                missing_labels.append(f"{t.section_id} {ttitle_clean}")
                matched_idx.append(None)

        extra_labels = [
            f"{c.section_id} {self._clean_toc_title(c.title)}"
            for i, c in enumerate(chunks)
            if i not in used_chunk_idxs
        ]

        out_of_order_labels: List[str] = []
        last_idx = -1
        for lbl, ci in zip(matched_labels, matched_idx):
            if ci is not None:
                if ci < last_idx:
                    out_of_order_labels.append(lbl)
                else:
                    last_idx = ci

        LOG.info(
            "Matching results: %d missing, %d extra, %d out-of-order, %d matched",
            len(missing_labels),
            len(extra_labels),
            len(out_of_order_labels),
            len(matched_labels),
        )
        return missing_labels, extra_labels, out_of_order_labels, matched_labels

    def _prepare_report_dict(self, report: ValidationReport) -> Dict[str, Any]:
        """Prepare a serializable dict from a ValidationReport for JSON output."""
        return report.model_dump()

    def write_report(self, out_path: str, report: ValidationReport) -> None:
        """Write the validation report to JSON and print a concise summary to console."""
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        data = self._prepare_report_dict(report)

        toc_cnt = data.get("toc_section_count", 0)
        parsed_cnt = data.get("parsed_section_count", 0)
        matched = data.get("matched_sections", [])
        missing = data.get("missing_sections", [])
        extra = data.get("extra_sections", [])
        out_of_order = data.get("out_of_order_sections", [])

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        table = Table(title="Validation Summary")
        table.add_column("Metric")
        table.add_column("Value")
        table.add_row("Total sections (ToC)", str(toc_cnt))
        table.add_row("Total sections (Chunks)", str(parsed_cnt))
        table.add_row("Matched sections", str(len(matched)))
        table.add_row("Missing sections", str(len(missing)))
        table.add_row("Extra sections", str(len(extra)))
        table.add_row("Out-of-order sections", str(len(out_of_order)))
        CONSOLE.print(table)
        LOG.info("Wrote validation report to %s", out_path)

_validator: AbstractValidator = Validator()


def load_toc(path: str) -> List[ToCEntry]:
    return _validator.load_toc(path)


def load_chunks(path: str) -> List[Chunk]:
    return _validator.load_chunks(path)


def match_sections(
    toc: List[ToCEntry],
    chunks: List[Chunk],
    fuzzy_threshold: float = 0.90,
    prefer_section_id: bool = True,
):
    return _validator.match_sections(toc, chunks, fuzzy_threshold, prefer_section_id)

