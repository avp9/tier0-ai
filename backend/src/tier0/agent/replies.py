from __future__ import annotations

import re

GREETING = (
    "Hello! I'm Tier0, BuzzTech's IT and HR help desk assistant. I can answer questions, "
    "raise a support ticket, or check on a ticket you've already raised. What do you need?"
)
THANKS = "You're welcome! Is there anything else I can help with?"
GOODBYE = "Goodbye! Come back any time you need IT or HR help."
OUT_OF_SCOPE = (
    "I can only help with IT and HR matters at BuzzTech, such as passwords, VPN, leave, "
    "payroll or onboarding. I can also raise a support ticket or check on one you've raised."
)
CLARIFY = (
    "I want to make sure I help with the right thing. Are you asking a question, would you "
    "like me to raise a support ticket, or are you checking on an existing ticket?"
)
CONFIRM_TICKET_INTENT = (
    "It sounds like you'd like to raise a support ticket. Shall I go ahead and start one? "
    "Reply yes to continue, or no if you'd rather not."
)
OFFER_DECLINED = "No problem. Let me know if there's anything else I can help with."
SELECTION_CANCELLED = "Okay, no problem. Let me know if you need anything else."
CLASSIFIER_UNAVAILABLE = (
    "I'm having trouble understanding requests right now. Please try again in a moment."
)
RESELECT = "I wasn't sure which ticket you meant. "

_THANKS = re.compile(r"\b(thanks|thank you|thx|cheers|much appreciated)\b", re.I)
_BYE = re.compile(r"\b(bye|goodbye|see you|see ya|good night)\b", re.I)


def smalltalk_reply(message: str) -> str:
    if _THANKS.search(message):
        return THANKS
    if _BYE.search(message):
        return GOODBYE
    return GREETING
