from typing import Optional, List
from pydantic import BaseModel, Field


class Caption(BaseModel):
    id: str
    name: Optional[str] = None


class ToCEntry(BaseModel):
    doc_title: str
    section_id: str
    title: str
    page: int = Field(ge=1)
    level: int = Field(ge=1)
    parent_id: Optional[str] = None
    full_path: str


class Chunk(BaseModel):
    section_path: str
    section_id: Optional[str] = None
    title: str
    page_range: str
    content: str
    tables: List[Caption] = Field(default_factory=list)
    figures: List[Caption] = Field(default_factory=list)


class ValidationReport(BaseModel):
    toc_section_count: int
    parsed_section_count: int
    missing_sections: List[str]
    extra_sections: List[str]
    out_of_order_sections: List[str]
    matched_sections: List[str]
