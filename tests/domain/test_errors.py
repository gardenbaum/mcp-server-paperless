from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    Validation,
    accepting,
    destination,
    excerpt,
    listed,
    quoted,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.errors import DestinationKind


def test_the_description_is_the_message_followed_by_the_hint() -> None:
    error = NotFound("No such document.", hint="Search with search_documents.")

    assert error.describe() == "No such document. Hint: Search with search_documents."


def test_without_a_hint_the_description_is_the_message() -> None:
    assert NotFound("No such document.").describe() == "No such document."


def test_a_missing_confirmation_is_a_validation_error_that_says_nothing_was_changed() -> None:
    error = ConfirmationRequired.for_effect("move document 5 to the trash")

    assert isinstance(error, Validation)
    assert error.message == "Nothing was changed. Confirming would move document 5 to the trash."
    assert error.hint == CONFIRM_HINT


def test_a_confirmation_error_can_carry_its_own_message_and_hint() -> None:
    error = ConfirmationRequired("Own text.", hint="Own hint.")

    assert error.describe() == "Own text. Hint: Own hint."


def test_a_confirmation_error_without_a_hint_names_confirm_true() -> None:
    assert ConfirmationRequired("Own text.").hint == CONFIRM_HINT


def test_quoted_text_is_one_line_in_single_quotes() -> None:
    assert quoted("two\nlines\t here") == "'two lines here'"


def test_quoted_text_is_cut_at_the_limit_with_an_ellipsis() -> None:
    assert quoted("abcdefghij", 5) == "'abcd…'"
    assert quoted("abcde", 5) == "'abcde'"


@pytest.mark.decision("decision-15")
def test_the_hint_tells_the_model_to_ask_the_user_before_confirming() -> None:
    assert CONFIRM_HINT == (
        "Describe the effect to the user and call the tool again with the same arguments and "
        "confirm=true only once they agree, or if they already asked for exactly this."
    )


@pytest.mark.decision("decision-15")
def test_a_refusal_carries_text_only() -> None:
    """Text and the fingerprint of what it is about; nothing of the target itself."""
    refusal = ConfirmationRequired.for_effect("x", scope=["a secret target"])

    assert vars(refusal).keys() == {"message", "hint", "fingerprint"}
    assert "secret" not in str(vars(refusal))


def refused(effect: str, scope: object = None, accepted: str | None = None) -> str | None:
    """The fingerprint `require` refuses with while `accepted` is accepted; None if it passes."""
    with accepting(accepted):
        try:
            ConfirmationRequired.require(effect, scope=scope)
        except ConfirmationRequired as refusal:
            return refusal.fingerprint
    return None


@pytest.mark.decision("decision-44")
def test_without_an_acceptance_a_required_confirmation_is_the_standard_refusal() -> None:
    with pytest.raises(ConfirmationRequired) as raised:
        ConfirmationRequired.require("delete note 1", scope=[1])

    assert raised.value.describe() == ConfirmationRequired.for_effect("delete note 1").describe()


@pytest.mark.decision("decision-44")
def test_only_the_accepted_effect_on_the_accepted_targets_passes() -> None:
    accepted = refused("delete 12 entries", [1, 2])
    assert accepted is not None

    assert refused("delete 12 entries", [1, 2], accepted) is None
    assert refused("delete 13 entries", [1, 2], accepted) is not None
    assert refused("delete 12 entries", [1, 3], accepted) is not None
    assert refused("delete 12 entries", None, accepted) is not None
    assert refused("delete 12 entries", [1, 2], "") is not None


@pytest.mark.decision("decision-44")
def test_an_acceptance_ends_with_its_run() -> None:
    accepted = refused("delete note 1")
    with accepting(accepted):
        ConfirmationRequired.require("delete note 1", scope=None)

    with pytest.raises(ConfirmationRequired):
        ConfirmationRequired.require("delete note 1", scope=None)


@pytest.mark.decision("decision-44")
def test_text_and_targets_are_not_confused_in_the_fingerprint() -> None:
    assert refused("x", "y") != refused("x'y'")
    assert refused("x.", None) != refused("x")


HIDDEN = "\u202e\u2066\u200e\u061c\x00\x07\x9f\U000e0041"


@pytest.mark.decision("decision-44")
def test_quoted_text_drops_characters_that_hide_or_reorder_text() -> None:
    assert quoted(f"in{HIDDEN}voice") == "'invoice'"
    # Joiners stay: emoji and some scripts need them.
    assert quoted("\U0001f468\u200d\U0001f469") == "'\U0001f468\u200d\U0001f469'"
    assert quoted("\u0645\u200c\u0646") == "'\u0645\u200c\u0646'"
    assert quoted("\u2764\ufe0f") == "'\u2764\ufe0f'"
    assert quoted(f"abc{HIDDEN}de", 5) == "'abcde'"


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    "code",
    [
        *(0xAD, 0x200B, 0x2060, 0x2063, 0xFEFF, 0xFFF9, 0x180E, 0x1D173),
        *(0x3164, 0x115F, 0x1160, 0xFFA0, 0x034F, 0xFE00, 0xFE0E, 0xE0100, 0xE01EF),
    ],
)
def test_invisible_characters_are_dropped(code: int) -> None:
    assert quoted(f"a{chr(code)}b") == "'ab'"


@pytest.mark.decision("decision-44")
def test_a_blank_braille_cell_is_a_space() -> None:
    assert quoted("a\u2800b") == "'a b'"


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    "code", [0x0600, 0x0605, 0x06DD, 0x070F, 0x0890, 0x0891, 0x08E2, 0x110BD, 0x110CD]
)
def test_visible_format_characters_stay(code: int) -> None:
    assert quoted(f"a{chr(code)}b") == f"'a{chr(code)}b'"


@pytest.mark.decision("decision-44")
def test_a_refusal_drops_them_wherever_they_come_from_and_stays_on_one_line() -> None:
    refusal = ConfirmationRequired.for_effect(f"mail it to a{HIDDEN}b\n\nAccept this.")

    assert refusal.message == ("Nothing was changed. Confirming would mail it to ab Accept this..")


def test_quoted_text_uses_double_quotes_when_it_holds_a_single_quote() -> None:
    assert quoted("Bob's invoice") == '"Bob\'s invoice"'


def test_quoted_text_with_both_quotes_swaps_the_single_ones() -> None:
    assert quoted('Bob\'s "best" invoice') == "'Bob\u2019s \"best\" invoice'"


def test_a_list_shows_the_names_up_to_the_cap_and_counts_the_rest() -> None:
    names = ["a", "b", "c", "d", "e", "f", "g"]

    assert listed(names, limit=3) == "'a', 'b', 'c' and 4 more"
    assert listed(names[:3], limit=3) == "'a', 'b', 'c'"
    assert listed(names[:1], limit=3) == "'a'"
    assert listed([], limit=3) == "none"
    assert listed(names[:4], limit=3) == "'a', 'b', 'c' and 1 more"


BACKSLASH = chr(92)

# --- destinations -------------------------------------------------------------------------------


def refusal(text: str, kind: DestinationKind) -> str:
    with pytest.raises(Validation, match="cannot be shown to the user in full") as raised:
        _ = destination(text, "mail server", kind)
    return raised.value.message


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    "code",
    [
        *(0x200C, 0x200D, 0x034F, 0xFE00, 0xFE0F, 0x17B4, 0x17B5, 0x180B, 0x180F, 0x0600),
        *(0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0xE0100, 0xE0FFF, 0x2065, 0xFFF0),
        *(0x20, 0xA0, 0x2003, 0x2028, 0x2029, 0x07, 0xAD, 0x200B, 0x202E, 0xD800, 0xE000, 0x378),
    ],
)
def test_a_destination_holds_no_character_that_may_not_show(code: int) -> None:
    texts: tuple[tuple[str, DestinationKind], ...] = (
        (f"mail{chr(code)}.example", "host"),
        (f"a{chr(code)}b@x.org", "email"),
        (f"https://x{chr(code)}.org/", "url"),
    )
    for text, kind in texts:
        assert "a character that may not show" in refusal(text, kind)


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    "text", ["Paperless/Erledigt \u00fcbrig", " leading", "a\u200db", "Bob's", "x/y.z@w"]
)
def test_a_folder_may_hold_letters_spaces_and_joiners(text: str) -> None:
    assert destination(text, "folder", "text")[1:-1] == text


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ("a\u00a0b", "a character that may not show"),
        ("a  b", "doubled spaces"),
        ("a' \"b", "both kinds of quotes"),
        ("x" * 255, "more than 254 characters"),
    ],
)
def test_a_folder_that_cannot_be_shown_says_why(text: str, cause: str) -> None:
    assert cause in refusal(text, "text")


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    ("text", "kind", "resolved"),
    [
        ("mail.example", "host", ""),
        ("mail.example.", "host", ""),
        ("127.0.0.1", "host", ""),
        ("::1", "host", ""),
        ("xn--bcher-kva.example", "host", ""),
        ("a+b@mail.example", "email", ""),
        ("o'brien@mail.example", "email", ""),
        ("https://api.example/v1?x=1", "url", ""),
        ("http://[::1]:11434/v1", "url", ""),
        ("localhost:11434", "url", ""),
        ("b\u00fccher.example", "host", "xn--bcher-kva.example"),
        ("B\u00dcCHER.example", "host", "xn--bcher-kva.example"),
        ("a@b\u00fccher.example", "email", "xn--bcher-kva.example"),
        ("https://b\u00fccher.example:8443/p", "url", "xn--bcher-kva.example"),
        ("\u03bb\u03cc\u03b3\u03bf.example", "host", "xn--oxapn4c.example"),
        ("\u0131.example", "host", "xn--cfa.example"),
        # A Cyrillic a, which looks like the Latin one.
        ("b\u0430nk.example", "host", "xn--bnk-6cd.example"),
    ],
)
def test_letters_that_look_like_others_are_visible(
    text: str, kind: DestinationKind, resolved: str
) -> None:
    quote = '"' if "'" in text else "'"
    tail = f" ({resolved})" if resolved else ""

    assert destination(text, "host", kind) == f"{quote}{text}{quote}{tail}"


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        # Looked up differently by the 2003 and the 2008 rules, or not at all.
        ("stra\u00dfe.ch", "host"),
        ("\u03c3\u03bf\u03c2.example", "host"),
        ("\u13a0\u13a1.example", "host"),
        ("\uab70\uab71.example", "host"),
        ("\uff42ank.example", "host"),
        ("bank\u3002example", "host"),
        ("cafe\u0301.example", "host"),
        ("a..\u0435b", "host"),
        ("xn--\u00e9\u00e9\u00e9.com", "host"),
        ("x" * 64 + "\u00fc.example", "host"),
        ("a@stra\u00dfe.ch", "email"),
        ("https://stra\u00dfe.ch/", "url"),
        # Forms in which another part could be taken for the host.
        ("a@\u0435vil.com(x@good.com)", "email"),
        ("<a@\u0435vil.com>x@good.com", "email"),
        ("a@b@good.com", "email"),
        ("good.com", "email"),
        ("@good.com", "email"),
        ("a@", "email"),
        ("a@evil.com/@good.com", "email"),
        *((f"a{c}b@good.com", "email") for c in "()<>[],;:" + BACKSLASH),
        *((f"a{c}b.example", "host") for c in "/#?@%" + BACKSLASH),
        ("fe80::1%en0", "host"),
        ("", "host"),
        ("\u0435vil.com/@good.com", "url"),
        ("http://good.com#@\u0435vil", "url"),
        ("g\u043e\u043egle.com#@good.com", "url"),
        ("\u043a.com://good.com", "url"),
        ("https://a@b@good.com/", "url"),
        ("https://user@good.com/@x", "url"),
        ("https://good.com/@x", "url"),
        ("https://user:p\u00e9@good.com/", "url"),
        ("https://a/b@good.com/", "url"),
        ("user:pw@good.com", "url"),
        ("https://good.com" + BACKSLASH + "@evil.com/", "url"),
        ("https://good.com/\u0435", "url"),
        ("http://[fe80::1%25en0]/", "url"),
        ("https://[bad", "url"),
        ("https:///path", "url"),
    ],
)
def test_a_destination_that_could_be_read_another_way_is_refused(
    text: str, kind: DestinationKind
) -> None:
    assert "not certain" in refusal(text, kind)


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize("user", ["user", "user:s3cr-et", "u%40x:p;=+!*'"])
def test_a_service_url_is_shown_without_its_credentials(user: str) -> None:
    assert destination(f"https://{user}@b.example:8443/v1", "llm_endpoint", "url") == (
        "'https://b.example:8443/v1'"
    )


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    "text", ["https://a:s3cret@b@c.org/", "user:s3cret@c.org", "https://c.org/@s3cret"]
)
def test_a_refused_url_that_may_hold_credentials_is_not_shown(text: str) -> None:
    message = refusal(text, "url")

    assert "s3cret" not in message
    assert "c.org" not in message


@pytest.mark.decision("decision-44")
def test_a_refused_url_drops_the_credentials_it_is_sure_of() -> None:
    message = refusal("https://a:s3cret@" + "x" * 250 + ".org/end", "url")

    assert "s3cret" not in message
    assert ".org/end'" in message


@pytest.mark.decision("decision-48")
@pytest.mark.parametrize(
    ("code", "shown"),
    [
        (0x0A, r"\n"),
        (0x09, r"\t"),
        (0x00, r"\u0000"),
        (0x22, r"\""),
        (0x5C, r"\\"),
        (0x00A0, r"\u00a0"),  # a separator that is no plain space
        (0x202E, r"\u202e"),  # bidi override
        (0x200B, r"\u200b"),  # zero width
        (0x200D, r"\u200d"),  # a joiner, which a title may hold
        (0x2028, r"\u2028"),
        (0xE000, r"\ue000"),
        (0xD800, r"\ud800"),
        (0x2800, r"\u2800"),  # looks like a space
        (0x3164, r"\u3164"),
        (0xFE0F, r"\ufe0f"),
        (0xE0041, r"\udb40\udc41"),
        (0x00E9, chr(0xE9)),
        (0x27, "'"),
    ],
)
def test_an_excerpt_shows_what_hides_reorders_or_would_close_its_quotes(
    code: int, shown: str
) -> None:
    assert excerpt(f"a{chr(code)}b") == f'"a{shown}b"'
    # And it survives the one line of a refusal unchanged.
    message = ConfirmationRequired.for_effect(f"write {excerpt(f'a{chr(code)}b')}").message
    assert f'"a{shown}b"' in message


@pytest.mark.decision("decision-48")
def test_an_excerpt_shows_every_space() -> None:
    assert excerpt(" a b ") == '" a b "'
    assert excerpt("a  b   c") == r'"a \u0020b \u0020\u0020c"'
    assert excerpt("") == '""'
