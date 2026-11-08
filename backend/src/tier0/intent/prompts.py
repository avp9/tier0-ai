from __future__ import annotations

from tier0.intent.types import Continuity, Domain, Intent, Turn

MAX_HISTORY_TURNS = 6
MAX_TURN_CHARS = 500

INTENT_DEFINITIONS: dict[Intent, str] = {
    Intent.KNOWLEDGE_QUESTION: (
        "The employee asks a question about IT or HR matters that company documentation could "
        "answer: how-to steps, policies, benefits, leave, payroll, onboarding, passwords, VPN, "
        "email, software or equipment. This includes describing an IT or HR problem in a "
        "sentence ('my monitor keeps flickering', 'the projector will not turn on'), and "
        "questions about the workplace itself (office parking, cafeteria or canteen, shuttle "
        "buses, vending machines, meeting rooms): the company may or may not have an answer on "
        "file, and the help desk can raise a ticket if not."
    ),
    Intent.CREATE_TICKET: (
        "The employee asks to raise, open, log, file or escalate a support ticket, or agrees to "
        "raise one that was just offered."
    ),
    Intent.TICKET_STATUS: (
        "The employee asks about the progress, status or updates of a ticket they already raised."
    ),
    Intent.SMALLTALK: "A greeting, thanks, goodbye or other pleasantry with no request in it.",
    Intent.OUT_OF_SCOPE: (
        "A request unrelated to the company, its workplace, IT or HR, such as recipes, weather, "
        "sport, trivia or writing code."
    ),
    Intent.AMBIGUOUS: (
        "Too vague to tell what the employee wants, or it could reasonably mean more than one "
        "of the other intents."
    ),
}

DOMAIN_DEFINITIONS: dict[Domain, str] = {
    Domain.IT: "Computers, software, accounts, passwords, network, VPN, email or equipment.",
    Domain.HR: "Leave, benefits, payroll, onboarding, policies, conduct or performance reviews.",
    Domain.OTHER: "Neither IT nor HR, or impossible to tell.",
}

CONTINUITY_DEFINITIONS: dict[Continuity, str] = {
    Continuity.NEW: "The first message, or a request with no relevant earlier context.",
    Continuity.CONTINUATION: "A follow-up on the same topic as the earlier conversation.",
    Continuity.SWITCH: "The employee has clearly moved to a different topic or request.",
}


def recent_history(history: list[Turn]) -> list[dict[str, str]]:
    """The last few turns, each shortened, as plain role/content pairs."""
    return [
        {"role": turn.role, "content": turn.content[:MAX_TURN_CHARS]}
        for turn in history[-MAX_HISTORY_TURNS:]
    ]
