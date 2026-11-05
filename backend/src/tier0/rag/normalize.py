from __future__ import annotations

import re

BORROWED_SOURCE_PREFIX = "strova-ai/"
FICTIONAL_EMAIL_DOMAIN = "buzztech.example"

# Entries naming real individuals of the original company, or document sign-off boilerplate.
_PERSON = re.compile(
    r"Saurabh|Bhansali|Anishri|Masal|Gholap|\b(Mr|Mrs|Ms)\.|Founder|Sr\. HR",
    re.IGNORECASE,
)
# Topics the synthetic BuzzTech handbook covers; borrowed answers would contradict it.
_HANDBOOK_TOPICS = re.compile(
    r"harass|\bPOSH\b|\bPCC\b|internal committee|compensatory|comp[- ]off|work from home|"
    r"\bWFH\b|remote|hybrid|\bleaves?\b|working hours|overtime",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b([\w.+-]+)@kreedalabs\.com\b", re.IGNORECASE)
_COMPANY = re.compile(r"Kreeda\s*Labs", re.IGNORECASE)


def is_borrowed_source(source: str) -> bool:
    return source.startswith(BORROWED_SOURCE_PREFIX)


def should_drop(question: str, answer: str) -> bool:
    text = f"{question} {answer}"
    return bool(_PERSON.search(text) or _HANDBOOK_TOPICS.search(text))


def rewrite_company(text: str) -> str:
    text = _EMAIL.sub(rf"\1@{FICTIONAL_EMAIL_DOMAIN}", text)
    return _COMPANY.sub("BuzzTech Solutions", text)
