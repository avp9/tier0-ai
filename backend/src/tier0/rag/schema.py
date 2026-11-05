from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Category = Literal["IT Support", "HR Policy", "Onboarding", "Payroll & Compensation"]


class KnowledgeRecord(BaseModel):
    """One searchable unit of the knowledge base."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: Category
    source: str = Field(min_length=1)
    content: str = Field(min_length=1)
    last_updated: date | None = None
    synthetic: bool = False
