from tier0.conversations import TITLE_CHARS, make_title


def test_the_title_is_the_first_message_on_one_line() -> None:
    assert make_title("  How do I\n set up   the VPN?  ") == "How do I set up the VPN?"


def test_a_long_message_is_cut_to_fit_a_sidebar_row() -> None:
    title = make_title("word " * 100)
    assert len(title) == TITLE_CHARS and title.endswith("…")


def test_a_message_of_exactly_the_limit_is_kept_whole() -> None:
    text = "x" * TITLE_CHARS
    assert make_title(text) == text


def test_a_blank_message_still_gets_a_title() -> None:
    assert make_title("   ") == "New chat"
