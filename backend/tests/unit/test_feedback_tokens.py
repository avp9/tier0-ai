import uuid

import pytest

from tier0.feedback import SIGNATURE_CHARS, issue_token, read_token

SECRET = "s" * 40
TRACE = str(uuid.uuid4())
ALICE, BOB = "alice@buzztech.example", "bob@buzztech.example"


def test_a_token_gives_back_the_turn_it_was_issued_for() -> None:
    assert read_token(SECRET, issue_token(SECRET, TRACE, ALICE), ALICE) == TRACE


def test_the_email_may_differ_in_case_or_padding() -> None:
    token = issue_token(SECRET, TRACE, ALICE)
    assert read_token(SECRET, token, "  Alice@Buzztech.Example ") == TRACE


def test_a_token_is_useless_to_anyone_else() -> None:
    assert read_token(SECRET, issue_token(SECRET, TRACE, ALICE), BOB) is None


def test_a_token_from_another_server_secret_is_refused() -> None:
    assert read_token("t" * 40, issue_token(SECRET, TRACE, ALICE), ALICE) is None


def test_changing_the_turn_in_a_token_breaks_it() -> None:
    token = issue_token(SECRET, TRACE, ALICE)
    other = str(uuid.uuid4())
    assert read_token(SECRET, other + token[len(TRACE) :], ALICE) is None


def test_changing_the_signature_breaks_it() -> None:
    token = issue_token(SECRET, TRACE, ALICE)
    assert read_token(SECRET, token[:-1] + ("0" if token[-1] != "0" else "1"), ALICE) is None


@pytest.mark.parametrize(
    "token",
    [
        "",
        ".",
        "not-a-uuid.abc",
        f"{TRACE}",
        f"{TRACE}.",
        f"{TRACE}.zzzz",
        "x" * 200,
        "../../etc.passwd",
    ],
)
def test_garbage_is_refused_without_an_error(token: str) -> None:
    assert read_token(SECRET, token, ALICE) is None


def test_a_token_names_the_turn_and_a_short_signature() -> None:
    token = issue_token(SECRET, TRACE, ALICE)
    trace, _, signature = token.partition(".")
    assert trace == TRACE and len(signature) == SIGNATURE_CHARS
    assert ALICE not in token and SECRET not in token  # nothing private in it


def test_each_turn_gets_its_own_token() -> None:
    assert issue_token(SECRET, str(uuid.uuid4()), ALICE) != issue_token(SECRET, TRACE, ALICE)
