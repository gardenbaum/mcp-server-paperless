"""Correcting the text of a document (decision-48), on the fake.

Calls that must not write run on a fake that refuses writes: a write would fail the test.
"""

import json
import re
from typing import TYPE_CHECKING, override

import pytest
from fakes.paperless import FakeDocument, FakeFile, FakePaperless, FakeState, seeded
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.documents import DocumentVersion
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    Conflict,
    NotFound,
    PermissionDenied,
    Validation,
    accepting,
)
from mcp_server_paperless.domain.versions import TextEdit
from mcp_server_paperless.services.documents import text_state
from mcp_server_paperless.services.text_edits import (
    MAX_BODY,
    MAX_PASSAGE,
    MAX_TEXT,
    PART,
    TextEditor,
    changed_region,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp_server_paperless.domain.documents import DocumentText
    from mcp_server_paperless.domain.versions import TextCorrected

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-48")]

WEB = "https://paperless.example"
DOC = 50
TEXT = "Invoice of ACME, total amuont of 12.50 EUR, due in May."


def state_with(text: str = TEXT, *older: str, title: str = "Invoice") -> FakeState:
    """A document whose newest text is ``text``; ``older`` are the older versions, root last."""
    state = seeded()
    document = FakeDocument(id=DOC, title=title, owner=2, content=text)
    [root] = document.versions
    count = len(older)
    ids = [DOC + count - n for n in range(count + 1)]  # the newest first, the root's is DOC
    document.versions = [
        DocumentVersion(id=n, added=root.added, version_label=None, is_root=n == DOC) for n in ids
    ]
    if older:
        document.version_files = {
            n: FakeFile(b"%d" % n, None, content, "text/plain")
            for n, content in zip(ids, (text, *older), strict=True)
        }
    state.documents.append(document)
    return state


def fake(state: FakeState, *, writes: bool = False, user: str = "admin") -> FakePaperless:
    return FakePaperless(state, user=user, allow_writes=writes)


def editor(source: FakePaperless) -> TextEditor:
    return TextEditor(source, source, web_url=WEB)


def swap(passage: str, replacement: str, version_id: int | None = None) -> TextEdit:
    return TextEdit(version_id=version_id, passage=passage, replacement=replacement)


def whole(text: str, version_id: int | None = None) -> TextEdit:
    return TextEdit(version_id=version_id, text=text)


async def correct(  # noqa: PLR0913 -- what a call says
    source: FakePaperless,
    edit: TextEdit,
    *,
    state: str | None = None,
    confirm: bool = False,
    dry_run: bool = False,
    offset: int = 0,
) -> TextCorrected:
    """Read the state like a caller, unless ``state`` is given, and correct the text."""
    if state is None:
        state = await read_state(source, edit.version_id)
    return await editor(source).correct(
        DOC, state, edit, dry_run=dry_run, offset=offset, confirm=confirm
    )


async def read_state(source: FakePaperless, version_id: int | None = None) -> str:
    found = await source.get_document_content(DOC, version_id=version_id)
    return text_state(DOC, found.version_id, found.content)


async def preview(text: str, edit: TextEdit, *older: str, title: str = "Invoice") -> str:
    source = fake(state_with(text, *older, title=title))
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await correct(source, edit)
    return raised.value.message


async def corrected(text: str, edit: TextEdit, *older: str) -> tuple[TextCorrected, FakePaperless]:
    source = fake(state_with(text, *older), writes=True)
    return await correct(source, edit, confirm=True), source


async def text_of(source: FakePaperless, version_id: int | None = None) -> str:
    return (await source.get_document_content(DOC, version_id=version_id)).content


# --- what is replaced -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "edit", "after"),
    [
        ("amuont due", swap("amuont", "amount"), "amount due"),
        ("the amuont due", swap("amuont", "amount"), "the amount due"),
        ("the amuont", swap("amuont", "amount"), "the amount"),
        ("the bad amount", swap("bad ", ""), "the amount"),
        ("one\r\ntwo", swap("one\r\ntwo", "one two"), "one two"),
        ("Aa", swap("a", "b"), "Ab"),
        ("e\u0301 \u00e9", swap("\u00e9", "e"), "e\u0301 e"),
        ("anything", whole("Something else"), "Something else"),
        ("anything", whole(""), ""),
        ("", whole("typed from the page"), "typed from the page"),
    ],
)
async def test_a_passage_or_the_whole_text_is_replaced(
    before: str, edit: TextEdit, after: str
) -> None:
    result, source = await corrected(before, edit)

    assert await text_of(source) == after
    assert (result.document_id, result.version_id, result.is_newest) == (DOC, DOC, True)
    assert (result.chars_before, result.chars_after) == (len(before), len(after))
    assert result.text_state == text_state(DOC, DOC, after)
    assert (result.dry_run, result.change) == (False, None)
    assert result.web_url == f"{WEB}/documents/{DOC}/details"
    assert result.message == (
        f"The text of version {DOC} was replaced. Reprocessing overwrites the correction."
    )


async def test_a_version_is_corrected_and_the_others_stay() -> None:
    result, source = await corrected("newest x", swap("x", "y", DOC + 1), "middle x", "root x")

    assert await text_of(source, DOC + 2) == "newest x"
    assert await text_of(source, DOC + 1) == "middle y"
    assert await text_of(source, DOC) == "root x"
    assert (result.version_id, result.is_newest) == (DOC + 1, False)
    assert result.message.endswith("Search does not find it: it is not the newest version.")


async def test_without_a_version_the_newest_is_corrected() -> None:
    result, source = await corrected("newest x", swap("x", "y"), "root x")

    assert (result.version_id, result.is_newest) == (DOC + 1, True)
    assert await text_of(source) == "newest y"
    assert await text_of(source, DOC) == "root x"


@given(
    prefix=st.text(max_size=30),
    passage=st.text(min_size=1, max_size=10),
    replacement=st.text(max_size=10),
    suffix=st.text(max_size=30),
)
def test_the_shown_region_rebuilds_both_texts(
    prefix: str, passage: str, replacement: str, suffix: str
) -> None:
    before, after = prefix + passage + suffix, prefix + replacement + suffix

    start, end_before, end_after = changed_region(before, after)

    assert before[:start] + after[start:end_after] + before[end_before:] == after
    assert after[:start] + before[start:end_before] + after[end_after:] == before
    assert 0 <= start <= min(end_before, end_after)


# --- refused before anything else -----------------------------------------------------------------

FORM = "Pass either text, or passage and replacement."


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (TextEdit(), FORM),
        (TextEdit(passage="a"), FORM),
        (TextEdit(replacement="a"), FORM),
        (TextEdit(passage="a", replacement="b", text="c"), FORM),
        (TextEdit(passage="a", text="c"), FORM),
        (TextEdit(replacement="a", text="c"), FORM),
        (swap("", "b"), "passage is empty."),
        (swap("x" * (MAX_PASSAGE + 1), ""), "passage holds at most 20000 characters"),
        (swap("x", "y" * (MAX_PASSAGE + 1)), "replacement holds at most 20000 characters"),
        (whole("x" * (MAX_TEXT + 1)), "text holds at most 1000000 characters"),
    ],
)
async def test_wrong_arguments_are_refused_without_their_content(
    edit: TextEdit, message: str
) -> None:
    source = fake(state_with())

    with pytest.raises(Validation) as raised:
        _ = await correct(source, edit, state="irrelevant", confirm=True)

    assert message in raised.value.message
    assert "xx" not in raised.value.describe()
    assert "yy" not in raised.value.describe()


async def test_the_limits_themselves_are_allowed() -> None:
    _, source = await corrected("x" * MAX_PASSAGE, swap("x" * MAX_PASSAGE, "y" * MAX_PASSAGE))
    assert await text_of(source) == "y" * MAX_PASSAGE
    _, source = await corrected("", whole("z" * MAX_TEXT))
    assert await text_of(source) == "z" * MAX_TEXT


@pytest.mark.parametrize(
    ("text", "passage", "message"),
    [
        (TEXT, "amount", "does not occur in the text of version 50"),
        ("Aa", "aA", "does not occur"),
        ("e\u0301", "\u00e9", "does not occur"),
        ("one\ntwo", "one two", "does not occur"),
        ("", "a", "does not occur"),
        ("ab ab", "ab", "occurs more than once, first at characters 0 and 3"),
        ("xaaa", "aa", "occurs more than once, first at characters 1 and 2"),
    ],
)
async def test_a_passage_that_is_missing_or_not_unique_changes_nothing(
    text: str, passage: str, message: str
) -> None:
    for confirm, dry_run in ((True, False), (False, True), (False, False)):
        with pytest.raises(Validation, match=message) as raised:
            _ = await correct(
                fake(state_with(text)), swap(passage, "b"), confirm=confirm, dry_run=dry_run
            )
        assert "Nothing was changed." in raised.value.message
        assert raised.value.hint


@pytest.mark.parametrize("confirm", [False, True])
async def test_text_that_changed_since_it_was_read_writes_nothing(*, confirm: bool) -> None:
    state = state_with()
    source = fake(state)
    read = await read_state(source)
    state.documents[-1].content = TEXT + " reprocessed"

    with pytest.raises(Conflict, match="changed since it was read") as raised:
        _ = await correct(source, swap("amuont", "amount"), state=read, confirm=confirm)

    assert "Nothing was changed." in raised.value.message
    assert raised.value.hint
    assert "get_document_content" in raised.value.hint


async def test_a_version_that_became_the_newest_since_the_read_is_not_corrected() -> None:
    state = state_with("same", "same")
    source = fake(state)
    read = await read_state(source, DOC)

    with pytest.raises(Conflict):  # the state of the root, but the newest version is meant
        _ = await correct(source, swap("same", "other"), state=read, confirm=True)


async def test_a_repeated_call_is_refused() -> None:
    source = fake(state_with(), writes=True)
    read = await read_state(source)
    _ = await correct(source, swap("amuont", "amount"), state=read, confirm=True)

    with pytest.raises(Conflict) as raised:
        _ = await correct(source, swap("amuont", "amount"), state=read, confirm=True)

    assert raised.value.hint
    assert "may already have corrected it" in raised.value.hint


async def test_a_missing_version_or_document_is_not_found() -> None:
    with pytest.raises(NotFound, match="No version 99 "):
        _ = await correct(fake(state_with()), swap("a", "b", 99), state="s", confirm=True)
    state = state_with()
    _ = state.documents.pop()
    with pytest.raises(NotFound):
        _ = await correct(fake(state), swap("a", "b"), state="s", confirm=True)


async def test_a_user_who_may_only_view_is_refused_before_the_preview() -> None:
    state = state_with()
    reader = next(u for u in state.users if u.username == "e2e-reader")
    state.documents[-1].viewers = {reader.id}

    with pytest.raises(PermissionDenied, match="may not change document 50"):
        _ = await correct(fake(state, user="e2e-reader"), swap("amuont", "amount"))


@pytest.mark.parametrize(
    ("edit", "text"),
    [
        (swap("a", "a"), "cab"),
        (whole("same"), "same"),
        (whole(" a "), "a"),
        # What Paperless would drop on its own is no correction the caller asked for.
        (whole("a\n"), "a\n"),
        (swap("b", "b"), " b "),
    ],
)
async def test_a_correction_that_changes_nothing_is_refused(edit: TextEdit, text: str) -> None:
    with pytest.raises(Validation, match="already reads like that"):
        _ = await correct(fake(state_with(text)), edit, confirm=True)


@pytest.mark.parametrize("wrong", ["a\x00b", "a\ud800b"])
async def test_text_paperless_cannot_store_is_refused(wrong: str) -> None:
    for edit in (whole(wrong), swap("cd", wrong)):
        with pytest.raises(Validation, match="NUL character or a lone surrogate"):
            _ = await correct(fake(state_with("cd")), edit, confirm=True)


async def test_a_body_over_the_limit_of_paperless_is_refused() -> None:
    empty = len(b'{"content":""}')
    fits = "\U0001f600" * ((MAX_BODY - empty) // 4)
    fits += "a" * (MAX_BODY - empty - len(fits.encode()))

    _, source = await corrected("", whole(fits))
    assert await text_of(source) == fits
    with pytest.raises(Validation, match="2500001 bytes of at most 2500000"):
        _ = await correct(fake(state_with("")), whole(fits + "a"), confirm=True)
    assert len(
        json.dumps({"content": fits}, ensure_ascii=False, separators=(",", ":")).encode()
    ) == (MAX_BODY)


# --- preview --------------------------------------------------------------------------------------

THEN = (
    ". Paperless then runs the workflows for updated documents and keeps the old text only in its "
    "audit log, if that is on; reprocessing overwrites the correction. The text "
)


async def test_the_refusal_names_the_consequences_and_ends_with_the_change() -> None:
    message = await preview(TEXT, swap("amuont", "amount"))

    start, shown = message.split(THEN)
    assert start == (
        "Nothing was changed. Confirming would replace the text of version 50 (the newest) of "
        "document 50 'Invoice' from character 25 on (its 55 characters become 55)"
    )
    old = '"Invoice of ACME, total amuont of 12.50 EUR, due in May."'
    assert shown == f"{old} becomes {old.replace('amuont', 'amount')}."


async def test_every_consequence_stands_before_the_text() -> None:
    message = await preview("newest", whole(" ", DOC), "o" * 400)

    consequences, excerpts = message.split(THEN)
    said = (
        "become 0); the change is longer than shown here, dry_run=true returns all of it; the text "
        "becomes empty; leading and trailing whitespace is dropped; search does not find it, "
        "because it is not the newest version"
    )
    assert consequences.endswith(said)
    assert excerpts == f'"{"o" * 150}" (left out: 100 characters) "{"o" * 150}" becomes "".'


async def test_more_text_around_is_cut_with_an_ellipsis() -> None:
    message = await preview("a" * 50 + "x" + "b" * 50, swap("x", "y"))

    assert f'…"{"a" * 40}x{"b" * 40}"… becomes …"{"a" * 40}y{"b" * 40}"…' in message
    exact = await preview("a" * 40 + "x" + "b" * 40, swap("x", "y"))
    assert f'text "{"a" * 40}x{"b" * 40}" becomes "{"a" * 40}y{"b" * 40}".' in exact


async def test_a_long_change_is_shown_at_both_ends_and_says_so() -> None:
    message = await preview("", whole("s" * 150 + "m" + "e" * 150))

    assert f'becomes "{"s" * 150}" (left out: 1 character) "{"e" * 150}".' in message
    assert "the change is longer than shown here, dry_run=true returns all of it" in message
    fits = await preview("", whole("s" * 300))
    assert f'becomes "{"s" * 300}".' in fits
    assert "dry_run" not in fits


async def test_an_empty_text_and_dropped_whitespace_are_named() -> None:
    assert "; the text becomes empty" in await preview("gone", whole(" \n"))
    dropped = "leading and trailing whitespace is dropped"
    assert dropped in await preview("a", whole("b\n"))
    assert dropped not in await preview("a", whole("b"))
    # Only the root's text loses it.
    assert dropped not in await preview("new", whole("b\n"), "root")


async def test_the_change_shown_is_the_one_that_is_stored() -> None:
    message = await preview("abc\n", whole("abc"))
    assert 'The text "abc\\n" becomes "abc".' in message
    # Asked for, but not stored: the added whitespace is in no excerpt and said to be dropped.
    message = await preview("abc", whole("abd \n"))
    assert 'The text "abc" becomes "abd".' in message
    assert "whitespace is dropped" in message
    # The change itself stays small, though the whitespace around it goes, too.
    message = await preview("  one two\n", swap("two", "2"))
    assert 'The text "one two" becomes "one 2".' in message
    assert "from character 6 on (its 10 characters become 5)" in message
    assert "whitespace is dropped" in message


async def test_the_dry_run_position_counts_in_the_text_as_it_was_read() -> None:
    found = await correct(fake(state_with("  one two\n")), swap("two", "2"), dry_run=True)

    assert found.change is not None
    assert (found.change.position, found.change.before, found.change.after) == (6, "two", "2")
    assert (found.chars_before, found.chars_after) == (10, 5)
    assert "whitespace is dropped" in found.message
    trimmed = await correct(fake(state_with("abc\n")), whole("abc"), dry_run=True)
    assert trimmed.change is not None
    assert (trimmed.change.position, trimmed.change.before, trimmed.change.after) == (3, "\n", "")


async def test_the_root_loses_outer_whitespace_and_other_versions_do_not() -> None:
    _, source = await corrected("a", whole(" b\n"))
    assert await text_of(source) == "b"
    _, source = await corrected("a", whole(" b\n"), "root")
    assert await text_of(source) == " b\n"
    _, source = await corrected("a", whole(" b\n", DOC), "root")
    assert await text_of(source, DOC) == "b"


async def test_hidden_characters_and_closing_quotes_are_visible_in_the_refusal() -> None:
    forged = f"pay {chr(0x201D)} becomes {chr(0x201C)}harmless{chr(0x202E)}"
    message = await preview(forged, whole("pay  me\tnow"))

    assert '"pay \\u201d becomes \\u201charmless\\u202e" becomes "pay \\u0020me\\tnow".' in message


async def test_the_title_is_quoted_and_cut() -> None:
    message = await preview("a", whole("b"), title="T" * 80)

    assert f"document 50 '{'T' * 59}…' from character" in message


async def test_no_number_stands_right_after_text_that_may_run_from_right_to_left() -> None:
    hebrew = chr(0x5D0)
    message = await preview(hebrew * 400, whole(hebrew * 500 + "x"), title=hebrew)

    # A number after such text, with no letter between, would be shown on its other side.
    assert not re.search(f"{hebrew}[^A-Za-z]*[0-9]", message)


# --- confirmation ---------------------------------------------------------------------------------


async def accepted(source: FakePaperless, edit: TextEdit, state: str) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await correct(source, edit, state=state)
    return raised.value.fingerprint


async def test_an_acceptance_runs_the_correction_it_was_given_for() -> None:
    source = fake(state_with(), writes=True)
    read = await read_state(source)
    fingerprint = await accepted(source, swap("amuont", "amount"), read)

    with accepting(fingerprint):
        result = await correct(source, swap("amuont", "amount"), state=read)

    assert "total amount" in await text_of(source)
    assert not result.dry_run


async def test_an_acceptance_does_not_cover_another_text_behind_the_same_words() -> None:
    source = fake(state_with(""))
    read = await read_state(source)
    first = whole("s" * 200 + "1" + "e" * 200)
    other = whole("s" * 200 + "2" + "e" * 200)
    fingerprint = await accepted(source, first, read)

    with accepting(fingerprint), pytest.raises(ConfirmationRequired) as raised:
        _ = await correct(source, other, state=read)

    with pytest.raises(ConfirmationRequired) as again:
        _ = await correct(source, first, state=read)
    assert raised.value.message == again.value.message
    assert raised.value.fingerprint != again.value.fingerprint


async def test_a_text_that_changed_while_the_dialog_was_open_is_a_conflict() -> None:
    state = state_with()
    source = fake(state)
    read = await read_state(source)
    fingerprint = await accepted(source, swap("amuont", "amount"), read)
    state.documents[-1].content = "reprocessed amuont"

    with accepting(fingerprint), pytest.raises(Conflict):
        _ = await correct(source, swap("amuont", "amount"), state=read)


# --- dry run --------------------------------------------------------------------------------------


async def test_a_dry_run_returns_the_change_and_writes_nothing() -> None:
    result = await correct(fake(state_with()), swap("amuont", "amount"), dry_run=True, confirm=True)

    assert result.dry_run
    assert result.change is not None
    assert (result.change.position, result.change.offset) == (25, 0)
    assert (result.change.before, result.change.after) == ("uo", "ou")
    assert (result.change.total_before, result.change.total_after) == (2, 2)
    assert result.change.next_offset is None
    assert result.text_state == text_state(DOC, DOC, TEXT)
    assert result.message.startswith(
        "Nothing was changed. Without dry_run and once confirmed, it would replace the text"
    )


async def test_an_offset_without_dry_run_is_refused() -> None:
    with pytest.raises(Validation, match="offset is only for dry_run"):
        _ = await correct(fake(state_with()), swap("amuont", "amount"), offset=1, confirm=True)


async def test_a_dry_run_reads_a_long_change_in_parts_that_rebuild_it() -> None:
    old, new = "o" * (PART + 5), "n" * (2 * PART + 1)
    before = after = ""
    offset: int | None = 0
    parts = 0
    while offset is not None:
        found = await correct(fake(state_with(old)), whole(new), dry_run=True, offset=offset)
        assert found.change is not None
        assert found.change.offset == offset
        assert max(len(found.change.before), len(found.change.after)) <= PART
        before, after = before + found.change.before, after + found.change.after
        offset, parts = found.change.next_offset, parts + 1

    assert (before, after, parts) == (old, new, 3)
    exact = await correct(fake(state_with("")), whole("n" * PART), dry_run=True)
    assert exact.change is not None
    assert exact.change.next_offset is None


# --- after the write ------------------------------------------------------------------------------


class Meddling(FakePaperless):
    """Something else happens right after the write."""

    after: Callable[[FakePaperless], None] | None = None

    @override
    async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
        stored = await super().set_document_text(document_id, version_id, content)
        assert self.after is not None
        self.after(self)
        return stored


async def meddled(after: Callable[[FakePaperless], None]) -> TextCorrected:
    source = Meddling(state_with(), user="admin", allow_writes=True)
    source.after = after
    return await correct(source, swap("amuont", "amount"), confirm=True)


async def test_a_text_that_is_another_one_after_the_write_is_reported() -> None:
    def overwrite(source: FakePaperless) -> None:
        source.state.documents[-1].content = "reprocessed"

    result = await meddled(overwrite)

    assert result.message.endswith(
        "The stored text is not the one that was sent: read it with get_document_content."
    )
    assert result.text_state == text_state(DOC, DOC, "reprocessed")
    assert result.chars_after == len("reprocessed")


async def test_a_text_paperless_stored_differently_is_reported_though_it_cannot_be_read() -> None:
    class Altering(FakePaperless):
        @override
        async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
            _ = await super().set_document_text(document_id, version_id, content)
            _ = self.state.documents.pop()
            return "altered"

    source = Altering(state_with(), user="admin", allow_writes=True)
    result = await correct(source, swap("amuont", "amount"), confirm=True)

    assert "not the one that was sent" in result.message
    assert result.text_state == text_state(DOC, DOC, "altered")


async def test_a_stored_text_that_differs_is_reported_though_the_read_agrees() -> None:
    class Answering(FakePaperless):
        @override
        async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
            _ = await super().set_document_text(document_id, version_id, content)
            return "altered"

    source = Answering(state_with(), user="admin", allow_writes=True)
    result = await correct(source, swap("amuont", "amount"), confirm=True)

    assert "not the one that was sent" in result.message
    assert "could not be read" not in result.message


async def test_a_correction_whose_document_is_gone_afterwards_is_still_a_success() -> None:
    def remove(source: FakePaperless) -> None:
        _ = source.state.documents.pop()  # e.g. a workflow moved it to the trash

    result = await meddled(remove)

    assert "The text could not be read again after the change." in result.message
    assert result.text_state == text_state(DOC, DOC, TEXT.replace("amuont", "amount"))
    assert result.is_newest


async def test_a_version_added_meanwhile_is_seen_in_the_result() -> None:
    def add(source: FakePaperless) -> None:
        document = source.state.documents[-1]
        document.version_files = {
            DOC: FakeFile(b"", None, document.content, "text/plain"),
            99: FakeFile(b"", None, "new", "text/plain"),
        }
        newer = document.versions[0].model_copy(update={"id": 99, "is_root": False})
        document.versions.insert(0, newer)
        document.content = "new"

    result = await meddled(add)

    assert (result.version_id, result.is_newest) == (DOC, False)
    assert "not the newest version" in result.message


async def test_the_text_is_read_right_before_and_right_after_the_write() -> None:
    calls: list[str] = []

    class Recording(FakePaperless):
        @override
        async def get_document_content(
            self, document_id: int, *, version_id: int | None = None
        ) -> DocumentText:
            calls.append(f"read {version_id}")
            return await super().get_document_content(document_id, version_id=version_id)

        @override
        async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
            calls.append(f"write {version_id}")
            return await super().set_document_text(document_id, version_id, content)

    source = Recording(state_with("x", "root"), user="admin", allow_writes=True)
    read = text_state(DOC, DOC + 1, "x")
    _ = await correct(source, whole("y"), state=read, confirm=True)

    assert calls == ["read None", f"write {DOC + 1}", f"read {DOC + 1}"]
