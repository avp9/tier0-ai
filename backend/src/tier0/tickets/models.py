from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMAIL = re.compile(r"^[^@\s\"'\\<>]+@[^@\s\"'\\<>]+\.[^@\s\"'\\<>]+$")
TICKET_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
SUBMISSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

Category = Literal["IT", "HR"]
Priority = Literal["Low", "Medium", "High", "Critical"]


def validate_email(value: str) -> str:
    value = value.strip()
    if len(value) > 254 or not EMAIL.match(value):
        raise ValueError("not a valid email address")
    return value


def validate_ticket_key(value: str) -> str:
    if not TICKET_KEY.match(value):
        raise ValueError("not a valid ticket key")
    return value


def validate_submission_id(value: str) -> str:
    if not SUBMISSION_ID.match(value):
        raise ValueError("not a valid submission id")
    return value


class TicketDraft(BaseModel):
    """A support request ready to send to the ticketing platform."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    requester_email: str
    summary: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=30000)
    category: Category
    priority: Priority | None = None
    submission_id: str | None = None

    @field_validator("requester_email")
    @classmethod
    def _email(cls, value: str) -> str:
        return validate_email(value)

    @field_validator("summary")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("summary must be a single line")
        return value

    @field_validator("submission_id")
    @classmethod
    def _submission(cls, value: str | None) -> str | None:
        return None if value is None else validate_submission_id(value)


class CreatedTicket(BaseModel):
    key: str
    url: str


class TicketSummary(BaseModel):
    key: str
    title: str
    status: str
    updated_at: datetime | None = None
    url: str


class TicketComment(BaseModel):
    author: str
    body: str
    created_at: datetime | None = None


class TicketDetails(BaseModel):
    summary: TicketSummary
    comments: list[TicketComment] = Field(default_factory=list)
