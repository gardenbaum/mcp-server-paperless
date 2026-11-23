import pytest

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    Validation,
    accepting,
    destination,
    listed,
    quoted,
)


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


# --- destinations -------------------------------------------------------------------------------


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
    for text in (f"mail{chr(code)}.example", f"a{chr(code)}b@x.org", f"https://x{chr(code)}.org/"):
        with pytest.raises(Validation, match="cannot be shown to the user in full"):
            _ = destination(text, "mail server")


@pytest.mark.decision("decision-44")
def test_a_folder_may_hold_plain_spaces_and_letters_of_any_script() -> None:
    assert destination("Paperless/Erledigt \u00fcbrig", "folder", address=False) == (
        "'Paperless/Erledigt \u00fcbrig'"
    )
    with pytest.raises(Validation):
        _ = destination("a\u00a0b", "folder", address=False)
    with pytest.raises(Validation):
        _ = destination("a\u200db", "folder", address=False)


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("mail.example", "'mail.example'"),
        ("a+b@mail.example", "'a+b@mail.example'"),
        ("https://api.example/v1?x=1", "'https://api.example/v1?x=1'"),
        ("b\u00fccher.example", "'b\u00fccher.example' (xn--bcher-kva.example)"),
        ("B\u00dcCHER.example", "'B\u00dcCHER.example' (xn--bcher-kva.example)"),
        ("a@b\u00fccher.example", "'a@b\u00fccher.example' (xn--bcher-kva.example)"),
        ("fa\u00df.example", "'fa\u00df.example' (xn--fa-hia.example)"),
        (
            "https://b\u00fccher.example:8443/p",
            "'https://b\u00fccher.example:8443/p' (xn--bcher-kva.example)",
        ),
        ("https://x.example/\u00fc", "'https://x.example/\u00fc'"),
        # A Cyrillic a, which looks like the Latin one.
        ("b\u0430nk.example", "'b\u0430nk.example' (xn--bnk-6cd.example)"),
    ],
)
def test_letters_that_look_like_others_are_visible(text: str, shown: str) -> None:
    assert destination(text, "host") == shown


@pytest.mark.decision("decision-44")
@pytest.mark.parametrize(
    "text",
    [
        "\uff42ank.example",  # full-width letters resolve to plain ones
        "bank\u3002example",  # full-width dots separate labels, too
        "cafe\u0301.example",  # the same word may be stored in two forms
        "https://[bad",
        "https://\u2100.example/",
        "x" * 64 + "\u00fc.example",  # longer than a label may be
    ],
)
def test_a_host_that_resolves_to_something_else_is_refused(text: str) -> None:
    with pytest.raises(Validation, match="cannot be shown to the user in full"):
        _ = destination(text, "host")
