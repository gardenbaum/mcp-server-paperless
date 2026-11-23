"""The notices on full-text syntax Paperless misreads (PLMCP-62).

Read off the parser of paperless-ngx 3.4.2; tests/e2e/test_search.py holds live proof.
"""

import time

import pytest

from mcp_server_paperless.services.search_syntax import SEARCH_FIELDS, query_notices


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ("rechnung not swisscom", ("'not'", "NOT")),
        ("rechnung and swisscom", ("'and'", "AND")),
        ("rechnung or swisscom", ("'or'", "OR")),
        ("(rechnung or swisscom)", ("'or'", "OR")),
        ('invoice -"secret phrase"', ('-"secret phrase"', 'NOT "secret phrase"')),
        ("rechnung -swisscom", ("-swisscom", "NOT swisscom")),
        ("-swisscom", ("-swisscom", "NOT swisscom")),
        ("rechnung Not swisscom", ("'Not'", "write NOT")),
        ("NOT(a)", ("'NOT('", "space after NOT")),
        ("NOT-a", ("'NOT-a'", "space")),
        ("rechnung AND", ("'AND'", "nothing to combine")),
        ("NOT", ("'NOT'", "nothing to combine")),
        ("x (OR y)", ("'OR'", "nothing to combine")),
        ("correspondent: acme", ("'correspondent:'", "right after the colon")),
        ("a \u2013b", ("'\u2013b'", "NOT b")),
        ("a \u2212b", ("'\u2212b'", "NOT b")),
        ("a -(b (c) d) e", ("'-(b (c) d)'", "NOT (b (c) d).")),
        ("a -(b", ("'-('", "NOT (.")),
        ("rechnung [swisscom and migros]", ("'and'", "AND")),
        ('"terms" -x', ("-x", "NOT x")),
        ("a (-b)", ("'-b'", "NOT b")),
        ("a -(b c)", ("'-(b c)'", "NOT (b c)")),
        ("a -b -c", ("'-b'", "'-c'", "NOT b", "NOT c")),
        ("!swisscom", ("'!swisscom'", "NOT swisscom")),
        ("a !b -c", ("'!b'", "'-c'", "NOT b", "NOT c")),
        ("a && b", ("'&&'", "AND")),
        ("a || b", ("'||'", "OR")),
        ("nosuch:foo", ("'nosuch:'", "document_type")),
        ("NOT nosuch:*", ("'nosuch:'", "plain text")),
        ("Title:foo (tags:x)", ("'Title:'", "'tags:'", "case-sensitive")),
        ('nosuch:"a b"', ("'nosuch:'",)),
        ("nosuch:[1 to 2]", ("'nosuch:'",)),
        ("nosuch:(a b)", ("'nosuch:'",)),
        ("custom_fields.nosuch:x", ("'custom_fields.nosuch:'",)),
    ],
)
def test_query_syntax_the_index_does_not_support_gets_a_notice(
    query: str, words: tuple[str, ...]
) -> None:
    [notice] = query_notices(query)

    assert all(word in notice for word in words)


@pytest.mark.parametrize(
    "query",
    [
        "rechnung",
        "type:*",
        "CHF -50",
        "a - b",
        "rechnung NOT -swisscom",
        "added:{-2 weeks to -1 week}",
        "title:(a) AND (b) OR NOT c",
        "NOT type:*",
        "rechnung AND NOT correspondent:*",
        "NOT (path:*)",
        "custom_fields:x custom_fields.name:x notes.user:x barcodes.value:x",
        "title:(a b)",
        "created:{2020 to 2021}",
        "'a -b'",
        "https://example.org 10:30 foo: a!b 20[!0]?",
        "NOT rechnung",
        "rechnung AND NOT swisscom",
        "a OR b",
        "created:[2024 to 2025]",
        "created:[2024-01-01 to 2025-12-31]",
        '"this is not a drill" AND acme',
        '"a -b" "x:*"',
        "added:[-2 weeks to -1 week]",
        "added:[-1 week to now]",
        "title:*voice",
        "title:rechnung-2024",
        "notes.note:paid",
        "e-mail",
        "",
    ],
)
def test_clean_queries_get_no_notice(query: str) -> None:
    assert query_notices(query) == ()


def test_several_constructs_give_one_notice_each() -> None:
    assert len(query_notices("nosuch:x rechnung not swisscom -x a && b")) == 4


@pytest.mark.parametrize("query", ["paid not", "(paid not) x", "and paid", "paid or", "x (or y)"])
def test_a_lowercase_word_that_cannot_be_an_operator_is_not_advised_as_one(query: str) -> None:
    [notice] = query_notices(query)

    assert "searched as a word" in notice
    assert "write" not in notice
    assert "leave it out" in notice


def test_a_lowercase_word_that_is_an_operator_once_is_advised() -> None:
    [notice] = query_notices("paid not x not")

    assert "write NOT if" in notice


def test_each_construct_is_named_once() -> None:
    assert len(query_notices("a && b && c -d -d")) == 2


def test_a_long_query_is_read_in_linear_time() -> None:
    query = "and " * 5_000 + "a" * 20_000 + ":[" * 5_000 + " '" * 5_000 + " -(" * 5_000

    started = time.perf_counter()
    _ = query_notices(query)

    assert time.perf_counter() - started < 2


def test_every_search_field_is_accepted() -> None:
    assert query_notices(" ".join(f"{field}:x" for field in SEARCH_FIELDS)) == ()


@pytest.mark.parametrize("query", ["-(" * 5_000, "-(" * 5_000 + "x" + ")" * 5_000])
def test_nested_exclusions_do_not_multiply_the_notice(query: str) -> None:
    assert sum(map(len, query_notices(query))) < 3 * len(query)


def test_only_the_outermost_excluded_group_is_named() -> None:
    [notice] = query_notices("-(a -b) -c")

    assert "'-(a -b)', '-c'; write NOT (a -b), NOT c." in notice
