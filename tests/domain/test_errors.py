import json
from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    Validation,
    accepting,
    both_ends,
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


ESCAPED = BACKSLASH + "u05f4"
LOOK_ALIKE_QUOTES = (
    *(0x02BA, 0x02DD, 0x02EE, 0x02F5, 0x02F6, 0x05F2, 0x05F4, 0x201C, 0x201D, 0x201E, 0x201F),
    *(0x2033, 0x2034, 0x2036, 0x2037, 0x2057, 0x275D, 0x275E, 0x2760, 0x2E42, 0x3003, 0x301D),
    *(0x301E, 0x301F, 0xFF02, 0x1F676, 0x1F677, 0x1F678, 0x030E, 0x1CD3, 0x1425),
)


@pytest.mark.decision("decision-48")
@pytest.mark.parametrize("code", LOOK_ALIKE_QUOTES)
def test_an_excerpt_escapes_what_reads_as_its_closing_quote(code: int) -> None:
    shown = excerpt(f"a{chr(code)}b")

    assert shown.isascii()
    assert shown[2:].startswith(r"\u")
    assert json.loads(shown) == f"a{chr(code)}b"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize("code", sorted({*LOOK_ALIKE_QUOTES} - {0x05F2}))
def test_quoted_text_escapes_what_reads_as_a_double_quote(code: int) -> None:
    escape = json.dumps(chr(code))[1:-1]

    assert quoted(f"a{chr(code)}b") == f"'a{escape}b'"
    # In double quotes it would seem to close them.
    assert quoted(f"Bob's{chr(code)} b") == f'"Bob\'s{escape} b"'


@pytest.mark.decision("decision-64")
def test_quoted_text_escapes_two_single_quotes_side_by_side() -> None:
    assert quoted("a''b") == r'''"a'\u0027b"'''
    assert quoted(f"a{chr(0x2019)}'b") == f"'a{chr(0x2019)}{chr(92)}u0027b'"
    assert quoted("a' 'b") == '''"a' 'b"'''


@pytest.mark.decision("decision-64")
def test_quoted_text_keeps_plain_quotes_backslashes_and_single_apostrophes() -> None:
    for text in ('the "best"', r"C:\x\u201d", f"it{chr(0x2019)}s", f"{chr(0xAB)}x{chr(0xBB)}"):
        assert quoted(text) == f"'{text}'"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize(
    "name",
    [
        "Z\u00fcrich \u00e9t\u00e9",
        "\u0421\u0447\u0451\u0442",
        "\u0627\u0644\u0645\u0644\u0641",
        "\u65e5\u672c\u8a9e\u300c\u66f8\u300d",
        "\u05e6\u05d4\u05f4\u05dc",  # an acronym, with gershayim between its letters
        "\u05e6\u05d5\u05f2",  # a Yiddish word that ends in its double yod
        "\u0928\u092e\u0938\u094d\u0924\u0947",
        "\u0301x",
        "\u0645\u200c\u0646",
    ],
)
def test_names_in_other_scripts_stay_readable(name: str) -> None:
    assert quoted(name) == f"'{name}'"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize(
    "text", ["\u05d0\u05f4", "\u05f4\u05d0", "\u05d0\u05f4 \u05d0", "x\u05f4\u05d0"]
)
def test_gershayim_that_stand_at_the_edge_of_a_word_are_escaped(text: str) -> None:
    assert quoted(text) == "'" + text.replace("\u05f4", r"\u05f4") + "'"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize(
    "between",
    [
        "\u200c",
        "\u200d",
        "\ufe0f",
        "\u180b",
        "\u17b4",
        "\U000e0080",
        "\u2065",
        "\ue000",
        "\U0001d159",
        "\u0301",
        "\u200d\ufe0f",
    ],
)
@pytest.mark.parametrize("single", ["'", "\u2019", "\u02bc"])
def test_quotes_with_nothing_visible_between_them_are_side_by_side(
    single: str, between: str
) -> None:
    shown = quoted(f"a{single}{between}{single}b")

    assert f"\\u{ord(single):04x}b" in shown
    assert shown[1:-1].startswith(f"a{single}{between}")


@pytest.mark.decision("decision-64")
def test_quotes_with_something_visible_between_them_are_not() -> None:
    for between in (" ", "x", "\u0600"):
        assert quoted(f"a\u2019{between}\u2019b") == f"'a\u2019{between}\u2019b'"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize("mark", ["\u030b", "\u030f"])
def test_double_accents_that_no_letter_carries_are_escaped(mark: str) -> None:
    escape = f"\\u{ord(mark):04x}"
    for before in ("", "a.", "a\u200c", "a\ufe0f", "a\u201d"):
        assert quoted(f"{before}{mark}b") == quoted(before)[:-1] + f"{escape}b'"
    assert quoted(f"a\u00a0{mark}b") == f"'a {escape}b'"
    assert quoted(f"{mark}{mark}") == f"'{escape}{escape}'"
    for carried in (f"o{mark}", f"o\u0301{mark}", f"\u0443{mark}", f"7{mark}"):
        assert quoted(carried) == f"'{carried}'"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("\u05d0\u05b0\u05f4\u05d0", "\u05d0\u05b0\u05f4\u05d0"),  # after niqqud
        ("\u05d0\u05f4\u05f4\u05d0", f"\u05d0{ESCAPED}{ESCAPED}\u05d0"),
        ("\u05d0\u05f3\u05f4\u05d0", f"\u05d0\u05f3{ESCAPED}\u05d0"),
        ("\u05d0\u05f4\u05f3\u05d0", f"\u05d0{ESCAPED}\u05f3\u05d0"),
        ("\u05f2\u05f4\u05dc", "\u05f2\u05f4\u05dc"),
        ("\u05dc\u05f4\u05f2", "\u05dc\u05f4\u05f2"),
        ("\u05b0\u05f4\u05d0", f"\u05b0{ESCAPED}\u05d0"),  # niqqud on nothing
    ],
)
def test_gershayim_stand_between_letters_only(text: str, shown: str) -> None:
    assert quoted(text) == f"'{shown}'"


@pytest.mark.decision("decision-64")
def test_the_cut_takes_the_same_characters_of_a_name_whatever_is_escaped() -> None:
    mark, escape = chr(0x201D), BACKSLASH + "u201d"

    assert quoted(f"ab{mark}", 3) == f"'ab{escape}'"
    assert quoted(f"a{mark}bcd", 3) == f"'a{escape}\u2026'"
    assert quoted("\u05d0\u05f4\u05d0\u05d0", 3) == f"'\u05d0{ESCAPED}\u2026'"
    assert both_ends(f"{mark}abcdefg{mark}", 9) == f"'{escape}abcdefg{escape}'"
    assert both_ends(f"a{mark}b{'x' * 20}{mark}yz", 9) == f"'a{escape}b\u2026xx{escape}yz'"
    assert both_ends("abcdefghij", 9) == "'abc\u2026fghij'"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize(
    ("character", "escaped"),
    [(chr(0x1F676), 12), (chr(0x201D), 6)],
)
def test_a_name_made_of_escapes_takes_at_most_240_characters(character: str, escaped: int) -> None:
    fit = (240 - 2 - 1) // escaped  # without the quotes and the cut mark
    escape = json.dumps(character)[1:-1]
    for text in (character * 60, character * 900):
        for limit in (60, 80):
            assert quoted(text, limit) == f"'{escape * fit}…'"
    exact = "x" * (238 % escaped) + character * (238 // escaped)
    assert len(quoted(exact)) == 240
    assert len(exact) < 60
    assert quoted(exact)[-2:] != "…'"
    assert quoted("x" + exact)[-2:] == "…'"
    assert len(quoted("x" + exact)) <= 240


@pytest.mark.decision("decision-64")
def test_a_text_shown_with_both_ends_takes_at_most_240_or_600_characters() -> None:
    widest = chr(0x1F676)
    for limit, most in ((60, 240), (254, 600)):
        for text in (widest * limit, widest * 900, "a" + widest * 300 + ".end", "'" + widest * 900):
            shown = both_ends(text, limit)
            assert most - 24 < len(shown) <= most
            head, tail = shown[1:-1].split("…")
            assert head
            assert head.count("\\") % 2 == tail.count("\\") % 2 == 0  # no half escape
            assert tail.endswith(".end" if text[0] == "a" else "ude76")
            assert len(tail) > len(head)


@pytest.mark.decision("decision-64")
def test_no_accent_is_carried_by_a_letter_that_is_escaped_or_invisible() -> None:
    accent = chr(0x30B)
    escapes = [f"{BACKSLASH}u{code:04x}" for code in (0x2BA, 0x30B)]

    assert quoted(chr(0x2BA) + accent) == f"'{''.join(escapes)}'"
    blank = chr(0x13441)
    assert quoted(f"o{blank}{accent}") == f"'o{blank}{escapes[1]}'"


@pytest.mark.decision("decision-64")
def test_a_normal_name_with_a_few_quotes_is_cut_where_it_always_was() -> None:
    low, high = chr(0x201E), chr(0x201C)
    name = f"{low}{'x' * 20}{high}, {low}{'y' * 20}{high} {'z' * 40}"
    expected = name[:59].replace(low, BACKSLASH + "u201e").replace(high, BACKSLASH + "u201c")

    assert quoted(name) == f"'{expected}{chr(0x2026)}'"
    shown = both_ends(high * 5 + "x" * 300, 254)
    assert shown == f"'{(BACKSLASH + 'u201c') * 5}{'x' * 79}{chr(0x2026)}{'x' * 169}'"


@pytest.mark.decision("decision-64")
def test_what_a_cut_leaves_at_its_edges_is_judged_by_what_stands_there() -> None:
    letter = "o" + chr(0x30B)

    shown = both_ends(letter * 100, 9)

    assert shown == f"'{letter}o{chr(0x2026)}{BACKSLASH}u030b{letter * 2}'"


@pytest.mark.decision("decision-48")
def test_two_single_quotes_side_by_side_do_not_read_as_a_double_quote() -> None:
    for code in (
        *(0x27, 0x60, 0xB4, 0x2B9, 0x2BC, 0x2018, 0x2019, 0x201B, 0x2032, 0xFF07, 0x1FEF, 0xA78B),
        *(0xA78C, 0x374, 0x7F4, 0x7F5, 0x1FBD, 0x2BE, 0x2BF, 0x2F4, 0x144A, 0x16CC),
    ):
        single = chr(code)
        assert excerpt(f"it{single}s") == f'"it{single}s"'
        assert excerpt(f"a{single}{single}{single}b").count(single) == 1
        assert excerpt(f"a{single}'b") == rf'"a{single}\u0027b"'


@pytest.mark.decision("decision-48")
def test_an_excerpt_keeps_guillemets_and_ordinary_letters() -> None:
    text = "".join(map(chr, (0xAB, 0xE9, 0xBB, 0x203A, 0x65E5, 0x627, 0x5D0, 0x2026)))

    assert excerpt(text) == f'"{text}"'


@pytest.mark.decision("decision-48")
@pytest.mark.parametrize("code", [0x1D159, 0x13441, 0x13442])
def test_an_excerpt_escapes_signs_that_show_as_nothing(code: int) -> None:
    assert excerpt(f"a{chr(code)}b").isascii()


@pytest.mark.decision("decision-48")
def test_an_excerpt_escapes_a_mark_that_has_no_letter_to_stand_on() -> None:
    mark = chr(0x301)

    assert excerpt(f"e{mark}{mark}x") == f'"e{mark}{mark}x"'
    assert excerpt(f"e{chr(0)}{mark}{mark}") == r'"e\u0000\u0301\u0301"'  # not on an escape
    for before in ("", " ", "."):
        assert excerpt(f"{before}{mark}x") == rf'"{before}\u0301x"'
