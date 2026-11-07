from __future__ import annotations

import re
from enum import StrEnum


class Reply(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


AFFIRMATIVE = {
    "yes", "y", "yep", "yeah", "yup", "sure", "ok", "okay", "confirm", "confirmed", "correct",
    "go ahead", "do it", "create it", "create the ticket", "submit", "submit it", "send it",
    "looks good", "that is correct", "thats correct", "that is right", "thats right",
    "sounds good", "absolutely", "definitely", "of course", "go for it", "sure thing",
}  # fmt: skip
NEGATIVE = {
    "no", "n", "nope", "nah", "cancel", "stop", "abort", "discard", "never mind", "nevermind",
    "dont", "do not", "dont create it", "do not create it", "dont submit", "not now",
    "no thanks", "forget it", "leave it", "scrap it", "cancel it", "stop it", "dont do it",
}  # fmt: skip
# Phrases that abandon a task. A bare "no" is excluded: it is a fine answer to
# "anything you've already tried?".
CANCEL_PHRASES = {
    "cancel", "cancel it", "never mind", "nevermind", "forget it", "stop", "stop it", "abort",
    "scrap it", "discard", "leave it", "not now", "dont bother", "do not bother",
}  # fmt: skip
FILLER = {"please", "thanks", "thank", "you", "kindly", "then", "just"}
_LONGEST = max(len(phrase.split()) for phrase in AFFIRMATIVE | NEGATIVE)


def _tokens(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("'", "")
    return [t for t in re.findall(r"[a-z]+", text) if t not in FILLER]


def interpret(text: str) -> Reply:
    """Read the employee's answer to "create this ticket?".

    Deliberately strict: only a reply made entirely of known yes-phrases is YES, and only one
    made entirely of known no-phrases is NO. Questions, mixtures ("yes but change it"),
    hedges ("maybe") and anything unrecognised are UNCLEAR, so a ticket is never created on a
    guess.
    """
    if "?" in text:
        return Reply.UNCLEAR
    tokens = _tokens(text)
    if not tokens:
        return Reply.UNCLEAR

    polarities: set[Reply] = set()
    position = 0
    while position < len(tokens):
        for length in range(min(_LONGEST, len(tokens) - position), 0, -1):
            phrase = " ".join(tokens[position : position + length])
            if phrase in AFFIRMATIVE:
                polarities.add(Reply.YES)
            elif phrase in NEGATIVE:
                polarities.add(Reply.NO)
            else:
                continue
            position += length
            break
        else:
            return Reply.UNCLEAR
    return next(iter(polarities)) if len(polarities) == 1 else Reply.UNCLEAR


# Phrases that end a list of tickets the employee has been looking through.
FINISHED_PHRASES = {
    "done", "im done", "i am done", "all done", "finished", "that is all", "thats all",
    "that will be all", "no more", "nothing else", "no thats all", "no thats it", "thats it",
}  # fmt: skip


def wants_to_finish(text: str) -> bool:
    """True only when the whole message is an unmistakable "I'm done looking"."""
    if "?" in text:
        return False
    tokens = _tokens(text)
    return bool(tokens) and " ".join(tokens) in FINISHED_PHRASES


def wants_to_cancel(text: str) -> bool:
    """True only when the whole message is an unmistakable "forget it"."""
    if "?" in text:
        return False
    tokens = _tokens(text)
    phrase = " ".join(tokens)
    return bool(tokens) and phrase in CANCEL_PHRASES
