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
        ("rechnung Not swisscom", ("'Not'", "write NOT")),
        ("NOT(a)", ("'NOT('", "space on both sides")),
        ("NOT-a", ("'NOT-a'", "space")),
        ("rechnung AND", ("'AND'", "nothing to combine")),
        ("NOT", ("'NOT'", "nothing to combine")),
        ("x (OR y)", ("'OR'", "nothing to combine")),
        ("correspondent: acme", ("'correspondent:'", "right after the colon")),
        ("rechnung [swisscom and migros]", ("'and'", "AND")),
        ("a && b", ("'&&'", "AND")),
        ("a || b", ("'||'", "OR")),
        ("nosuch:foo", ("'nosuch:'", "document_type")),
        ("Title:foo (tags:x)", ("'Title:'", "'tags:'", "case-sensitive")),
        ('nosuch:"a b"', ("'nosuch:'",)),
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


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("rechnung -swisscom", ["-swisscom"]),
        ("a -b -c", ["-b", "-c"]),
        ("a !b -c", ["!b", "-c"]),
        ("a \N{EN DASH}b \N{MINUS SIGN}c", ["\N{EN DASH}b", "\N{MINUS SIGN}c"]),
        ("--b !!c -!d -*foo", ["--b", "!!c", "-!d", "-*foo"]),
        ("a (-b)", ["-b"]),
        ("-(a -b) -c", ["-(", "-b", "-c"]),
        ("a ! (b)", ["!"]),
        ('"a"-b "c"!d', ["-b", "!d"]),
        ("a -(b", ["-("]),
        ("a -type:(x OR y)", ["-type:("]),
        ("a -title:'b c'", ["-title:'b c'"]),
        ('invoice -"secret phrase"', ['-"secret phrase"']),
        ("-'b c'", ["-'b c'"]),
        ("!2024", ["!2024"]),
        ("-_x !#b", ["-_x", "!#b"]),
        ("-type:*", ["-type:*"]),
    ],
)
def test_every_sign_that_does_not_exclude_is_named(query: str, named: list[str]) -> None:
    [notice] = [n for n in query_notices(query) if "does not exclude" in n]

    assert f": {', '.join(f"'{sign}'" for sign in named)}; " in notice
    assert "write NOT and a space in its place, e.g. NOT swisscom" in notice


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("-Title:foo", "'Title:'"),
        ("a !nosuch:x", "'nosuch:'"),
        ("_x:foo 2foo:bar", "'_x:', '2foo:'"),
        ('"a"nosuch:x', "'nosuch:'"),
    ],
)
def test_an_unknown_field_behind_a_sign_is_named(query: str, named: str) -> None:
    notices = " ".join(query_notices(query))

    assert named in notices
    assert "\0" not in notices


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("a||b", "'||'"),
        ("a &&b", "'&&'"),
        ("a&& b", "'&&'"),
        ('NOT"a b"', "'NOT\"a b\"'"),
        ("NOT'a b'", "'NOT'a'"),
        ('a AND"b"', "'AND\"b\"'"),
        ('"a"AND "b"', "'AND'"),
        ("(a)AND b", "'AND'"),
        ('"a"NOT b', "'NOT'"),
        ('"p q"NOT(a)', "'NOT('"),
        ('or"p q"', "'or'"),
        ('title:"a\nnot b"', "'not'"),
        ("'a\nnot b'", "'not'"),
        ("(a)OR (b)", "'OR'"),
        ("NOT AND b", "'NOT'"),
        ("NOT OR b", "'NOT' has nothing"),
        ('"a -b\nc"', "'-b'"),
        ("a OR OR b", "'OR'"),
        ("a OR AND b", "'AND'"),
        ("title:[a and b]", "'and'"),
        ("type:{x not y}", "'not'"),
    ],
)
def test_an_operator_that_is_read_as_text_is_named(query: str, named: str) -> None:
    assert any(named in notice for notice in query_notices(query))


@pytest.mark.parametrize("query", ["OR-Tools", "NOT-Aufnahme"])
def test_a_hyphenated_word_is_only_advised_conditionally(query: str) -> None:
    [notice] = query_notices(query)

    assert "if you mean the operator" in notice


@pytest.mark.parametrize(
    "query",
    [
        "title:'a -b'",
        "('a not b')",
        "(x AND 'a -b')",
        "'it's not a'",
        "don't a AND b",
        "'a', not 'b'",
    ],
)
def test_single_quoted_text_is_no_syntax(query: str) -> None:
    assert query_notices(query) == ()


def test_the_search_fields_are_those_of_paperless() -> None:
    plain = {
        *("title", "content", "correspondent", "document_type", "type", "storage_path", "path"),
        *("original_filename", "tag", "checksum", "asn", "page_count", "num_notes"),
        *("created", "modified", "added", "notes", "custom_fields", "barcodes"),
    }
    nested = {"notes.user", "notes.note", "custom_fields.name", "custom_fields.value"}

    assert set(SEARCH_FIELDS) == plain | nested | {"barcodes.value", "barcodes.format"}


@pytest.mark.parametrize(
    ("query", "notices"),
    [
        ('"a" "b -c" "d -e"', 0),
        ("'a' 'b -c' 'd -e'", 0),
        ('"" -b', 1),
        ("'a\n'b -c'", 0),
        ('"a\n" "b -c"', 1),
        ('"" "-b"', 0),
        ("5\" 'b -c'", 0),
        ("title:[a to -b]", 0),
        ("title:{a to -b}", 0),
        ("[ -b", 1),
        ("[-b to c] -d", 1),
        ("[a b] -c", 1),
        ("x' -b", 1),
        ("'-b", 0),
        ('"-b', 0),
    ],
)
def test_only_closed_phrases_and_ranges_are_left_alone(query: str, notices: int) -> None:
    assert len(query_notices(query)) == notices


def test_what_follows_a_phrase_is_named_as_written() -> None:
    [notice] = query_notices('"abc" [x to y] -d')

    assert "'-d'" in notice


@pytest.mark.parametrize("bracket", ["[", "{"])
def test_an_unclosed_range_is_read_in_linear_time(bracket: str) -> None:
    query = bracket + "to " * 50_000

    started = time.perf_counter()
    _ = query_notices(query)

    assert time.perf_counter() - started < 2


@pytest.mark.parametrize(
    ("query", "sign"),
    [
        ("NOT -type:*", "-type:*"),
        ('NOT -"a b"', '-"a b"'),
        ("x NOT !(a b)", "!("),
        ("NOT -acme*", "-acme*"),
        ("-NOT b", "-NOT"),
    ],
)
def test_a_sign_at_not_is_to_be_removed_not_replaced(query: str, sign: str) -> None:
    [notice] = query_notices(query)

    assert f"'{sign}'" in notice
    assert "remove the sign" in notice
    assert "write NOT" not in notice


@pytest.mark.parametrize(
    "query",
    [
        "a -> b",
        "a --> b",
        "a -_- b",
        "hello !",
        "wow ! great",
        'a "b" ! c',
        "NOT -a/b",
        "NOT -a!5",
        "title:[a TO -b]",
        "[a to -b}",
        "title:(a) NOT (b)",
        "(a)NOT b",
    ],
)
def test_punctuation_and_valid_forms_get_no_notice(query: str) -> None:
    assert query_notices(query) == ()


def test_quotes_before_a_distant_newline_are_read_in_linear_time() -> None:
    started = time.perf_counter()
    _ = query_notices("'a " * 50_000 + "\n")

    assert time.perf_counter() - started < 2
