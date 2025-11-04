from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any, Any as TypingAny
from openpyxl.styles import Font
import pandas as pd
from pathlib import Path

class AbstractWriter(ABC):
    """Abstract base class for writers (e.g. ExcelWriter)."""

    @abstractmethod
    def write(self, target: str | Path, sheets: Dict[str, pd.DataFrame]) -> None:
        raise NotImplementedError


class ExcelWriter(AbstractWriter):
    """Concrete Excel writer implementing AbstractWriter."""

    def __init__(self, max_width: int = 60) -> None:
        self.max_width = max_width
        self.header_font = Font(bold=True)

    def __str__(self) -> str:
        return f"ExcelWriter(max_width={self.max_width})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExcelWriter):
            return NotImplemented
        return self.max_width == other.max_width

    def _autofit(self, ws: TypingAny) -> None:
        # style header row
        try:
            for cell in ws[1]:
                cell.font = self.header_font
        except Exception:
            pass

        for col in ws.columns:
            values = [str(c.value) if c.value is not None else "" for c in col]
            width = min(max((len(v) for v in values), default=0) + 2, self.max_width)
            try:
                ws.column_dimensions[col[0].column_letter].width = width
            except Exception:
                # best-effort; don't fail the whole write
                continue

    def write(self, target: str | Path, sheets: Dict[str, pd.DataFrame]) -> None:
        target_path = Path(target)
        if target_path.exists():
            try:
                target_path.unlink()
            except PermissionError:
                pass
        with pd.ExcelWriter(target_path, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=False)
            wb = writer.book
            for name in sheets.keys():
                self._autofit(wb[name])