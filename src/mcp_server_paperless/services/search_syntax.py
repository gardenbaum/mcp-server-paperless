"""What a full-text query uses that the Paperless search index silently misreads (PLMCP-62).

Paperless searches these constructs as plain text and never reports an error. Presence (type:*)
is valid syntax and needs no notice.
"""

import re
from typing import TYPE_CHECKING, final

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

# The fields of the Paperless search index; any other prefix is searched as text.
SEARCH_FIELDS = (
    *("title", "content", "correspondent", "document_type", "type", "storage_path", "path"),
    *("tag", "original_filename", "checksum", "asn", "page_count", "num_notes"),
    *("created", "added", "modified", "notes", "notes.note", "notes.user"),
    *("custom_fields", "custom_fields.name", "custom_fields.value"),
    *("barcodes", "barcodes.value", "barcodes.format"),
)
OPERATORS = ("AND", "OR", "NOT")
SYMBOLS = {"&&": "AND", "||": "OR"}

# Phrases and field ranges (added:[-2 weeks to -1 week]), whose inside is no query syntax.
_OPAQUE = re.compile(
    r""""[^"]*"|(?<!\S)'[^']*'(?!\S)|(?<=:)(?:\[[^\[\]]*\]|\{[^{}]*\})""",
)
_TOKEN = re.compile(r"[()]|[^\s()]+")
_FIELD = re.compile(r"([A-Za-z][\w.]*):(?!//)")
# A minus before a number is an amount ("CHF -50"), not an attempt to exclude.
_EXCLUDE = re.compile(r"[-!\N{EN DASH}\N{MINUS SIGN}](?=[^\W\d]|\"|$)")
_GLUED = re.compile(r"(?:AND|OR|NOT)[-!]\w")


@final
class _Word:
    """One token of the query with what stands next to it."""

    def __init__(self, tokens: list[re.Match[str]], at: int) -> None:
        self.text = tokens[at][0]
        self.start, self.end = tokens[at].span()
        self.before = tokens[at - 1][0] if at else "("
        following = tokens[at + 1] if at + 1 < len(tokens) else None
        self.after = following[0] if following else ")"
        self.opens_group = following is not None and following.span() == (self.end, self.end + 1)
        self.opens_group = self.opens_group and self.after == "("
        left = self.text.upper() == "NOT" or self.before != "("
        self.can_operate = left and self.after != ")"


def _named(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in dict.fromkeys(values))


def _group_ends(plain: str) -> dict[int, int]:
    """Where each group ends, by where it opens; a group left open has no entry."""
    ends: dict[int, int] = {}
    opened: list[int] = []
    for paren in re.finditer(r"[()]", plain):
        if paren[0] == "(":
            opened.append(paren.start())
        elif opened:
            ends[opened.pop()] = paren.end()
    return ends


def _fields(words: list[_Word]) -> Iterator[str]:
    found = [(word, match) for word in words if (match := _FIELD.match(word.text))]
    unknown: list[str] = []
    detached: list[str] = []
    for word, match in found:
        has_value = match.end() < len(word.text) or word.opens_group
        if match[1] not in SEARCH_FIELDS and has_value:
            unknown.append(match[0])
        elif match[1] in SEARCH_FIELDS and not has_value:
            detached.append(match[0])
    if unknown:
        rule = "an unknown field is searched as plain text and names are case-sensitive"
        known = ", ".join(SEARCH_FIELDS)
        yield f"Not a search field known here: {_named(unknown)}; {rule}; use {known}."
    if detached:
        rule = "has no value; write it right after the colon"
        yield f"{_named(detached)} {rule}."


def _operators(words: list[_Word]) -> Iterator[str]:
    cased: dict[str, bool] = {}
    for word in words:
        if word.text.upper() in OPERATORS and word.text not in OPERATORS:
            cased[word.text] = cased.get(word.text, False) or word.can_operate
    for text, operator in cased.items():
        if operator:
            advice = f"write {text.upper()} if you mean the operator"
        else:
            advice = "leave it out unless you mean the word"
        yield f"'{text}' is searched as a word, not as an operator; {advice}."
    for word in words:
        if word.text in OPERATORS and word.opens_group:
            yield f"'{word.text}(' is not an operator; put a space after {word.text}."
        elif word.text in OPERATORS and not word.can_operate:
            yield f"'{word.text}' has nothing to combine here and is searched as a word."
        elif _GLUED.match(word.text):
            yield f"'{word.text}' is searched as text; put a space after the operator."
        elif word.text in SYMBOLS:
            yield f"'{word.text}' is not an operator; write {SYMBOLS[word.text]}."


def _exclusions(query: str, plain: str, words: list[_Word]) -> Iterator[str]:
    excluded: dict[str, str] = {}
    ends = _group_ends(plain)
    inside = 0  # the end of the last named group: what is nested in it is not named again
    for word in words:
        if word.start < inside or word.before == "NOT":
            continue
        if not _EXCLUDE.match(query, word.start, word.end):
            continue
        if len(word.text) > 1 or word.opens_group:
            end = word.end if len(word.text) > 1 else ends.get(word.end, word.end + 1)
            inside = end
            excluded[query[word.start : end]] = query[word.start + 1 : end]
    if excluded:
        advice = ", ".join(f"NOT {term}" for term in excluded.values())
        rule = "A leading minus or ! does not exclude and is read as text"
        yield f"{rule}: {_named(excluded)}; write {advice}."


def query_notices(query: str) -> tuple[str, ...]:
    """Name each misread construct once, with the working alternative."""
    # Same length as the query, so a match position is also one in the query.
    plain = _OPAQUE.sub(lambda m: "x" * len(m[0]), query)
    tokens = list(_TOKEN.finditer(plain))
    words = [_Word(tokens, at) for at, token in enumerate(tokens) if token[0] not in "()"]
    notices = (*_fields(words), *_operators(words), *_exclusions(query, plain, words))
    return tuple(dict.fromkeys(notices))
