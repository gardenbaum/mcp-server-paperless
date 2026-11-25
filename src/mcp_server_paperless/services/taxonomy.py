"""Name resolution for taxonomy (decision-5) over a per-credential cache.

Callers pass names where Paperless wants ids. A wrong guess files a document under the wrong
tag or correspondent without anyone noticing, so resolution never guesses: a numeric input is an
id, a name must match exactly or, failing that, ignoring case and accents, and it must match one
object, or the one the user picked (decision-109). Everything else is an error that names the
candidates or the closest names.
"""

import difflib
import time
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING

from mcp_server_paperless.domain.choices import Choice, Question, contradicts, picked
from mcp_server_paperless.domain.errors import (
    Ambiguous,
    Conflict,
    NotFound,
    PermissionDenied,
    quoted,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mcp_server_paperless.domain.credentials import CredentialProvider
    from mcp_server_paperless.domain.ports import TaxonomyReader
    from mcp_server_paperless.domain.taxonomy import SelectOption, TaxonomyItem, TaxonomyKind

SUGGESTIONS = 5


def fold(text: str) -> str:
    """The case- and accent-insensitive form of ``text`` ("Ärzte" and "ARZTE" fold alike)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def pick(
    kind: TaxonomyKind | str,
    items: Sequence[TaxonomyItem],
    value: str | int,
    *,
    listing: str | None = None,
    role: str = "",
) -> TaxonomyItem:
    """The one item of ``items`` that ``value`` names (rules in the module docstring).

    ``kind`` names the sort of object in messages; ``listing`` is the tool call that lists them
    (default: ``list_taxonomy`` for ``kind``), for objects that taxonomy does not hold. ``role``
    is the argument that holds ``value``, for the user who is asked: a pick counts for it only.

    Raises:
        NotFound: No item has the id or name; the hint lists the closest names.
        Ambiguous: More than one item matches; the message lists them.
        Conflict: The user picked an item that does not match any more.
    """
    label = kind.replace("_", " ")
    call = listing or f"list_taxonomy(kind={kind!r})"
    text = str(value).strip()
    numeric = isinstance(value, int) and not isinstance(value, bool)
    if numeric or (text.isascii() and text.isdigit()):
        wanted = int(text)
        found = [item for item in items if item.id == wanted]
        if found:
            return found[0]
        named = [item for item in items if item.name == text]
        hint = (
            f"A {label} is named {text!r}; pass its id {named[0].id} instead."
            if named
            else f"Call {call} to see the ids."
        )
        message = f"No {label} has id {wanted}."
        raise NotFound(message, hint=hint)
    exact = [item for item in items if item.name == text]
    folded = [item for item in items if fold(item.name) == fold(text)]
    for matches in (exact, folded):
        question = Question(f"{label}s", text, tuple(map(_choice, matches)), role)
        if len(matches) == 1:
            _not_contradicted(question, label)
            return matches[0]
        if matches:
            answer = picked(question)
            for item in matches:
                if str(item.id) == answer:
                    return item
            candidates = ", ".join(f"{item.name!r} (id {item.id})" for item in matches)
            message = f"{len(matches)} {label}s match {text!r}: {candidates}."
            raise Ambiguous(message, hint="Pass the id of the one you mean.", question=question)
    _not_contradicted(Question(f"{label}s", text, (), role), label)
    message = f"No {label} is named {text!r}."
    raise NotFound(message, hint=_suggest(items, text, call))


def names_apart(rows: Sequence[TaxonomyItem]) -> dict[int, str]:
    """The quoted names by id; with the id where another row would be shown alike."""
    shown = {row.id: quoted(row.name) for row in rows}
    times = Counter(shown.values())
    return {n: f"{text} (id {n})" if times[text] > 1 else text for n, text in shown.items()}


def _not_contradicted(left: Question, label: str) -> None:
    """Refuse what is left of the matches if the user picked another one.

    Raises:
        Conflict: They did.
    """
    other = contradicts(left)
    if other is not None:
        message = f"The {label} the user picked (id {other}) does not match {left.word!r} any more."
        raise Conflict(message, hint="Nothing was changed. Read the names again and ask.")


def pick_option(
    field: str, options: Sequence[SelectOption], word: str, matches: Sequence[SelectOption]
) -> SelectOption | Question:
    """The one match, or the one the user picked; else the question which of them is meant.

    Raises:
        Conflict: The user picked an option that does not match any more.
    """
    places = {option.id: place for place, option in enumerate(options, start=1)}
    choices = tuple(
        Choice(o.id, f"{quoted(o.label)} (option {places[o.id]} of {len(options)})")
        for o in matches
    )
    question = Question(f"options of {quoted(field)}", word, choices)
    if len(matches) == 1:
        if contradicts(question) is not None:
            message = f"The option the user picked does not match {word!r} any more."
            raise Conflict(message, hint="Nothing was changed. Read the names again and ask.")
        return matches[0]
    answer = picked(question)
    return next((option for option in matches if option.id == answer), question)


def _choice(item: TaxonomyItem) -> Choice:
    count = item.document_count
    used = "" if count is None else f"{count} document{'' if count == 1 else 's'}"
    return Choice(str(item.id), f"{quoted(item.name)} (id {item.id})", item.owner, used)


def _suggest(items: Sequence[TaxonomyItem], text: str, call: str) -> str:
    names: dict[str, list[str]] = {}
    for item in items:
        names.setdefault(fold(item.name), []).append(item.name)
    close = difflib.get_close_matches(fold(text), names, n=SUGGESTIONS)
    suggestions = [name for key in close for name in names[key]][:SUGGESTIONS]
    if not suggestions:
        return f"Call {call} to see the names."
    return f"Did you mean {', '.join(map(repr, suggestions))}? Or call {call} to see all names."


class TaxonomyResolver:
    """Taxonomy rows and name resolution, cached per credential and kind for a TTL.

    The cache key holds the credential fingerprint, so one user's taxonomy never answers
    another's. Writes of this server call ``invalidate``; changes made elsewhere show after the
    TTL.
    """

    def __init__(
        self,
        reader: TaxonomyReader,
        credentials: CredentialProvider,
        *,
        ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Load through ``reader``; ``ttl`` is in seconds of ``clock``."""
        self._reader: TaxonomyReader = reader
        self._credentials: CredentialProvider = credentials
        self._ttl: float = ttl
        self._clock: Callable[[], float] = clock
        # shortcut: entries are replaced, never evicted; bound the cache once many credentials
        # exist (PLMCP-37).
        self._cache: dict[
            tuple[str, TaxonomyKind], tuple[float, list[TaxonomyItem] | PermissionDenied]
        ] = {}

    async def items(
        self, kind: TaxonomyKind, name_contains: str | None = None
    ) -> list[TaxonomyItem]:
        """Every visible object of ``kind``, optionally only names containing ``name_contains``.

        The filter ignores case and accents.

        Raises:
            PermissionDenied: The user may not view ``kind``.
        """
        key = (self._credentials.current().fingerprint, kind)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is None or now - cached[0] >= self._ttl:
            try:
                cached = (now, await self._reader.list_taxonomy(kind))
            except PermissionDenied as error:
                cached = (now, error)  # a denial holds as long as rows do: do not ask again
            self._cache[key] = cached
        rows = cached[1]
        if isinstance(rows, PermissionDenied):
            raise PermissionDenied(rows.message, hint=rows.hint)
        if name_contains is None:
            return list(rows)
        needle = fold(name_contains)
        return [row for row in rows if needle in fold(row.name)]

    async def resolve(
        self, kind: TaxonomyKind, value: str | int, *, role: str = ""
    ) -> TaxonomyItem:
        """The object of ``kind`` that ``value`` (an id or a name) names; see ``pick``."""
        return pick(kind, await self.items(kind), value, role=role)

    def invalidate(self, kind: TaxonomyKind) -> None:
        """Forget ``kind`` for every credential: a write can change what each user sees."""
        for key in [key for key in self._cache if key[1] == kind]:
            del self._cache[key]
