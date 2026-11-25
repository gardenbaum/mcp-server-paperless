"""Custom field values are checked and put into Paperless's form before anything is sent."""

import re
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.choices import Choice, Question, picking
from mcp_server_paperless.domain.errors import Ambiguous, Conflict, Validation
from mcp_server_paperless.domain.taxonomy import SelectOption, TaxonomyItem
from mcp_server_paperless.services.custom_values import normalise

if TYPE_CHECKING:
    from pydantic import JsonValue

PAPERLESS_MONEY = re.compile(r"([A-Z]{3})?-?\d+\.\d{2}")


def field(data_type: str, *options: tuple[str, str]) -> TaxonomyItem:
    return TaxonomyItem(
        id=1,
        name=f"my-{data_type}",
        document_count=0,
        data_type=data_type,
        select_options=tuple(SelectOption(id=i, label=label) for i, label in options) or None,
    )


@pytest.mark.parametrize(
    ("data_type", "value", "stored"),
    [
        ("string", "hello", "hello"),
        ("string", "", ""),
        ("longtext", "a\nb", "a\nb"),
        ("url", "https://example.com/a?b=1", "https://example.com/a?b=1"),
        ("url", "mailto:me@example.com", "mailto:me@example.com"),
        ("date", "2026-02-03", "2026-02-03"),
        ("date", date(2026, 2, 3), "2026-02-03"),
        ("boolean", True, True),
        ("boolean", False, False),
        ("integer", 7, 7),
        ("integer", -3, -3),
        ("integer", 2_147_483_647, 2_147_483_647),
        ("integer", -2_147_483_648, -2_147_483_648),
        ("url", "a:b", "a:b"),
        ("monetary", "9999999999.99", "9999999999.99"),
        ("float", 2.5, 2.5),
        ("float", 3, 3),
        ("monetary", "EUR12.50", "EUR12.50"),
        ("monetary", "EUR 12.50", "EUR12.50"),
        ("monetary", "12.50 eur", "EUR12.50"),
        ("monetary", "eur12.5", "EUR12.50"),
        ("monetary", "12.5", "12.50"),
        ("monetary", 12, "12.00"),
        ("monetary", 7.25, "7.25"),
        ("monetary", "USD-3", "USD-3.00"),
        ("documentlink", [3, 4], [3, 4]),
        ("documentlink", [3, 3, 4], [3, 4]),
        ("documentlink", [], []),
    ],
)
def test_a_valid_value_is_put_into_the_stored_form(
    data_type: str, value: JsonValue, stored: JsonValue
) -> None:
    assert normalise(field(data_type), value) == stored


def test_a_select_takes_its_label_and_stores_the_option_id() -> None:
    select = field("select", ("id-a", "Alpha"), ("id-b", "Beta"))

    assert normalise(select, "Beta") == "id-b"
    assert normalise(select, "beta") == "id-b"
    assert normalise(select, "id-a") == "id-a"


def test_an_unknown_select_label_lists_the_options() -> None:
    select = field("select", ("id-a", "Alpha"), ("id-b", "Beta"))

    with pytest.raises(Validation, match="'Gamma'") as raised:
        _ = normalise(select, "Gamma")

    assert "Alpha" in raised.value.describe()
    assert "Beta" in raised.value.describe()
    assert raised.value.hint


def test_labels_that_differ_only_in_case_are_ambiguous_unless_one_is_exact() -> None:
    select = field("select", ("id-a", "Open"), ("id-b", "OPEN"))

    assert normalise(select, "OPEN") == "id-b"
    with pytest.raises(Ambiguous):
        _ = normalise(select, "open")


@pytest.mark.parametrize(
    ("data_type", "value", "message"),
    [
        ("string", 5, "text"),
        ("string", "x" * 129, "128"),
        ("longtext", 5, "text"),
        ("url", "not a url", "URL"),
        ("url", 5, "URL"),
        ("date", "03.02.2026", "YYYY-MM-DD"),
        ("date", "2026-13-01", "YYYY-MM-DD"),
        ("date", "20260203", "YYYY-MM-DD"),
        ("date", "2026-02-03T10:00:00", "YYYY-MM-DD"),
        ("date", 5, "YYYY-MM-DD"),
        ("boolean", "true", "true or false"),
        ("boolean", 1, "true or false"),
        ("integer", 1.5, "whole number"),
        ("integer", 2_147_483_648, "whole number"),
        ("integer", -2_147_483_649, "whole number"),
        ("url", "http:", "URL"),
        ("url", "http://", "URL"),
        ("url", "a b:c", "URL"),
        ("monetary", "EUR99999999999", "currency"),
        ("monetary", 10**11, "currency"),
        ("integer", "7", "whole number"),
        ("integer", True, "whole number"),
        ("float", "2.5", "number"),
        ("float", True, "number"),
        ("float", float("inf"), "number"),
        ("monetary", "12.505", "two decimals"),
        ("monetary", "twelve", "currency"),
        ("monetary", "12,50", "currency"),
        ("monetary", "EUR12.50 USD", "currency"),
        ("monetary", float("nan"), "currency"),
        ("monetary", float("inf"), "currency"),
        ("monetary", 1e-7, "two decimals"),
        ("monetary", True, "currency"),
        ("documentlink", "1", "document ids"),
        ("documentlink", [1, "2"], "document ids"),
        ("documentlink", [True], "document ids"),
        ("documentlink", [0], "document ids"),
        ("documentlink", [-1], "document ids"),
        ("select", 5, "label"),
    ],
)
def test_a_value_of_the_wrong_kind_is_rejected_and_says_what_the_field_takes(
    data_type: str, value: JsonValue, message: str
) -> None:
    with pytest.raises(Validation, match=message) as raised:
        _ = normalise(field("select" if data_type == "select" else data_type), value)

    assert f"my-{data_type}" in raised.value.message
    assert raised.value.hint


def test_no_value_is_rejected_with_a_pointer_to_removal() -> None:
    with pytest.raises(Validation) as raised:
        _ = normalise(field("string"), None)

    assert "remove_custom_fields" in raised.value.describe()


def test_a_field_of_an_unknown_type_is_rejected_rather_than_sent_blind() -> None:
    with pytest.raises(Validation, match="data type") as raised:
        _ = normalise(field("holographic"), "x")

    assert raised.value.hint


@given(
    cents=st.integers(min_value=-(10**9), max_value=10**9),
    currency=st.one_of(st.none(), st.from_regex(r"[A-Z]{3}", fullmatch=True)),
)
def test_a_monetary_amount_keeps_its_value_in_paperless_form(
    cents: int, currency: str | None
) -> None:
    amount = Decimal(cents) / 100
    given = f"{currency or ''}{amount:.2f}"

    stored = normalise(field("monetary"), given)

    assert isinstance(stored, str)
    assert PAPERLESS_MONEY.fullmatch(stored)
    assert stored.removeprefix(currency or "") == f"{amount:.2f}"
    assert stored.startswith(currency or "")


@given(links=st.lists(st.integers(min_value=1, max_value=10**6), max_size=20))
def test_document_links_lose_only_duplicates(links: list[int]) -> None:
    stored = normalise(field("documentlink"), list(links))

    assert stored == list(dict.fromkeys(links))
    assert normalise(field("documentlink"), stored) == stored


@given(day=st.dates(min_value=date(1000, 1, 1), max_value=date(9999, 12, 31)))
def test_a_date_is_stored_as_its_iso_form(day: date) -> None:
    assert normalise(field("date"), day) == day.isoformat()
    assert normalise(field("date"), day.isoformat()) == day.isoformat()


# --- the user's pick (decision-109) ---------------------------------------------------------------

DOUBLES = field("select", ("o1", "Paid"), ("o2", "paid"), ("o3", "Open"), ("o4", "Open"))


def asked(word: str, of: TaxonomyItem = DOUBLES) -> Question:
    with pytest.raises(Ambiguous) as raised:
        _ = normalise(of, word)
    assert raised.value.question is not None
    return raised.value.question


@pytest.mark.decision("decision-109")
def test_an_ambiguous_label_asks_with_label_and_position() -> None:
    question = asked("PAID")

    assert (question.what, question.word) == ("options of 'my-select'", "PAID")
    assert question.choices == (
        Choice("o1", "'Paid' (option 1 of 4)"),
        Choice("o2", "'paid' (option 2 of 4)"),
    )
    assert [c.title for c in asked("Open").choices] == [
        "'Open' (option 3 of 4)",
        "'Open' (option 4 of 4)",
    ]


@pytest.mark.decision("decision-109")
@pytest.mark.parametrize(("word", "option"), [("PAID", "o2"), ("Open", "o3"), ("Open", "o4")])
def test_the_picked_option_is_stored_also_of_two_with_one_label(word: str, option: str) -> None:
    with picking([asked(word).picking(option)]):
        assert normalise(DOUBLES, word) == option
        assert normalise(DOUBLES, "Paid") == "o1"


@pytest.mark.decision("decision-109")
def test_a_pick_of_another_field_or_of_no_match_does_nothing() -> None:
    other = DOUBLES.model_copy(update={"name": "other"})
    for pick in (asked("Open", other).picking("o3"), asked("Open").picking("o1")):
        with picking([pick]), pytest.raises(Ambiguous):
            _ = normalise(DOUBLES, "Open")


@pytest.mark.decision("decision-109")
def test_a_picked_option_that_is_gone_is_never_replaced_by_the_other_one() -> None:
    left = field("select", ("o3", "Open"))
    with picking([asked("Open").picking("o4")]):
        with pytest.raises(Conflict, match="The option the user picked does not match 'Open' any"):
            _ = normalise(left, "Open")
        assert normalise(field("select", ("o4", "Open")), "Open") == "o4"
