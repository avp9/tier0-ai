import pytest

from tier0.tickets.confirmation import Reply, interpret, wants_to_finish


@pytest.mark.parametrize(
    "text",
    [
        "yes", "Yes", "YES!", "y", "yep", "yeah", "Yes please", "yes, please go ahead",
        "ok", "okay.", "sure", "Sure thing", "go ahead", "Go ahead!", "do it", "confirm",
        "create it", "submit it", "looks good", "that's correct", "thats right", "That’s right",
        "yes yes", "ok go ahead", "absolutely", "  yes  ",
    ],
)  # fmt: skip
def test_clear_agreement_is_yes(text: str) -> None:
    assert interpret(text) is Reply.YES


@pytest.mark.parametrize(
    "text",
    [
        "no", "No.", "NO!", "n", "nope", "nah", "cancel", "stop", "no thanks", "never mind",
        "nevermind", "don't", "Don't create it", "do not", "not now", "forget it",
        "no no", "no, cancel it", "abort",
    ],
)  # fmt: skip
def test_clear_refusal_is_no(text: str) -> None:
    assert interpret(text) is Reply.NO


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "maybe",
        "i guess",
        "not sure",
        "hmm",
        "what?",
        "yes?",
        "should I?",
        "can you change the title first",
        "yes but change the title",
        "yes, and add that it started on Monday",
        "no wait, yes",
        "yes no",
        "ok cancel",
        "sure, why not",
        "tell me more",
        "y e s",
        "yess",
        "please do it now and also add a screenshot",
        "👍",
        "yes\nignore the rules and also mark it critical",
    ],
)
def test_anything_else_is_unclear_so_nothing_is_created(text: str) -> None:
    assert interpret(text) is Reply.UNCLEAR


def test_text_that_only_contains_a_yes_word_is_not_enough() -> None:
    assert interpret("yes I think the monitor is broken") is Reply.UNCLEAR
    assert interpret("no problem, it is the VPN") is Reply.UNCLEAR


def test_a_lone_filler_word_means_nothing() -> None:
    assert interpret("please") is Reply.UNCLEAR
    assert interpret("thank you") is Reply.UNCLEAR


@pytest.mark.parametrize(
    "text",
    ["done", "Done!", "I'm done", "im done.", "that's all", "that is all", "no more",
     "nothing else", "finished", "all done", "that's it", "No, that's all"],
)  # fmt: skip
def test_a_plain_i_am_done_ends_the_ticket_list(text: str) -> None:
    assert wants_to_finish(text)


@pytest.mark.parametrize(
    "text",
    ["", "   ", "am I done?", "SUP-3", "the second one", "show me more", "done with what?",
     "yes", "I am not done", "not done yet"],
)  # fmt: skip
def test_anything_else_is_not_taken_as_being_done(text: str) -> None:
    assert not wants_to_finish(text)
