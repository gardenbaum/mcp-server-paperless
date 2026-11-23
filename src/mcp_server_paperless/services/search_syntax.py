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

_OPEN = re.compile(r"\"|(?<!\w)'|[\[{]")
_CLOSE = {'"': re.compile(r"[\"\n]"), "'": re.compile(r"'(?=[\s\])}]|$)|\n")}
_BRACKETS = re.compile(r"[\[{][^\[\]{}]*[\]}]")
_TO = re.compile(r"\bto\b", re.IGNORECASE)
# Stands for the characters of a phrase or range, whose inside is no query syntax.
_OPAQUE = "\0"
_TOKEN = re.compile(r"[()]|\0+|[^\s()\0]+")
# A field prefix, also behind a sign; "10:30" and URLs are none.
_FIELD = re.compile(r"[-!\N{EN DASH}\N{MINUS SIGN}]*((?=\w*[^\W\d])\w[\w.]*):(?!//)")
_SIGN = re.compile(r"[-!\N{EN DASH}\N{MINUS SIGN}]+")
_GLUED = re.compile(r"(?:AND|OR|NOT)[-!']")
# The index drops the sign of such a term, so NOT in front of it excludes.
_BARE = re.compile(r"[^:*?]+")


def _masked(query: str) -> str:
    """The query with phrases and ranges blanked; positions stay the same."""
    parts: list[str] = []
    at = 0
    open_until = dict.fromkeys(_CLOSE, 0)  # no closing quote before this position
    while opened := _OPEN.search(query, at):
        mark, start = opened[0], opened.start()
        closed = None
        if mark not in _CLOSE:
            closed = _BRACKETS.match(query, start)
            closed = closed if closed and _TO.search(closed[0]) else None
        elif start >= open_until[mark]:
            closed = _CLOSE[mark].search(query, start + 1)
            if closed is None or closed[0] == "\n":
                open_until[mark] = closed.start() if closed else len(query)
                closed = None
        end = closed.end() if closed else start + 1
        parts += query[at:start], _OPAQUE * (end - start) if closed else mark
        at = end
    return "".join((*parts, query[at:]))


@final
class _Word:
    """One token of the query with what stands next to it."""

    def __init__(self, query: str, tokens: list[re.Match[str]], at: int) -> None:
        self.text = tokens[at][0]
        start, end = tokens[at].span()
        previous = tokens[at - 1] if at else None
        following = tokens[at + 1] if at + 1 < len(tokens) else None
        self.before = previous[0] if previous else "("
        self.after = following[0] if following else ")"
        touching = previous[0][0] if previous and previous.end() == start else ""
        self.follows_phrase = touching == _OPAQUE
        self.follows_group = touching == ")"
        # A group or phrase right behind the word, which it is meant to apply to.
        self.has_operand = False
        self.shown = query[start:end]
        if following and following.start() == end and following[0][0] in {"(", _OPAQUE}:
            self.has_operand = True
            self.shown = query[start : following.end()]
        left = self.text.upper() == "NOT" or self.before not in {"(", *OPERATORS}
        self.can_operate = left and self.after not in {")", "AND", "OR"}

    @property
    def has_unread_sign(self) -> bool:
        """Whether a sign in front is meant to exclude, but is read as text."""
        if not (sign := _SIGN.match(self.text)):
            return False
        term = self.text[sign.end() :]
        if not term:
            return self.has_operand or (sign[0] == "!" and self.after == "(")
        if self.before == "NOT" and _BARE.fullmatch(term) and not self.has_operand:
            return False
        # A minus before a number is an amount ("CHF -50"), not an attempt to exclude.
        return (term[0].isalnum() or term[0] in "'*?") and ("!" in sign[0] or not term[0].isdigit())

    @property
    def is_negated(self) -> bool:
        return self.before == "NOT" or _SIGN.sub("", self.text) == "NOT"

    @property
    def is_glued_operator(self) -> bool:
        if self.text not in OPERATORS:
            return bool(_GLUED.match(self.text))
        after_group = self.follows_group and self.text != "NOT"
        return self.has_operand or self.follows_phrase or after_group


def _named(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in dict.fromkeys(values))


def _fields(words: list[_Word]) -> Iterator[str]:
    unknown: list[str] = []
    detached: list[str] = []
    for word in words:
        if match := _FIELD.match(word.text):
            has_value = match.end() < len(word.text) or word.has_operand
            if match[1] not in SEARCH_FIELDS and has_value:
                unknown.append(f"{match[1]}:")
            elif match[1] in SEARCH_FIELDS and not has_value:
                detached.append(f"{match[1]}:")
    if unknown:
        rule = "an unknown field is searched as plain text and names are case-sensitive"
        known = ", ".join(SEARCH_FIELDS)
        yield f"Not a search field known here: {_named(unknown)}; {rule}; use {known}."
    if detached:
        yield f"{_named(detached)} has no value; write it right after the colon."


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
        if word.is_glued_operator:
            rule = "is read as text; put a space on both sides if you mean the operator"
            yield f"'{word.shown}' {rule}."
        elif word.text in OPERATORS and not word.can_operate:
            yield f"'{word.text}' has nothing to combine here and is not applied as an operator."
        for symbol, operator_name in SYMBOLS.items():
            if symbol in word.text:
                yield f"'{symbol}' is not an operator; write {operator_name} between spaces."


def _exclusions(words: list[_Word]) -> Iterator[str]:
    signs = [word for word in words if word.has_unread_sign]
    rule = "A leading minus or ! does not exclude and is read as text"
    if plain := [word.shown for word in signs if not word.is_negated]:
        advice = "write NOT and a space in its place, e.g. NOT swisscom"
        yield f"{rule}: {_named(plain)}; {advice}."
    if negated := [word.shown for word in signs if word.is_negated]:
        yield f"{rule}: {_named(negated)}; remove the sign, NOT alone excludes."


def query_notices(query: str) -> tuple[str, ...]:
    """Name each misread construct once, with the working alternative."""
    tokens = list(_TOKEN.finditer(_masked(query)))
    words = [
        _Word(query, tokens, at) for at, token in enumerate(tokens) if token[0][0] not in "()\0"
    ]
    return tuple(dict.fromkeys((*_fields(words), *_operators(words), *_exclusions(words))))
