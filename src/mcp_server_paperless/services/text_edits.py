"""Correcting the OCR text of a document, whole or one exact passage (decision-48).

Paperless has no conditional write, so the caller passes the state of the text it read and nothing
is written if the text is another one by now. Between that check and the write one round trip is
left in which another writer can be overwritten; the text is read again afterwards.
"""

import json
import re
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    Conflict,
    PaperlessMcpError,
    Validation,
    excerpt,
    quoted,
)
from mcp_server_paperless.domain.versions import TextChange, TextCorrected
from mcp_server_paperless.services.documents import DEFAULT_SLICE, document_link, text_state
from mcp_server_paperless.services.versions import require_change

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentText
    from mcp_server_paperless.domain.ports import DocumentReader, DocumentWriter
    from mcp_server_paperless.domain.versions import TextEdit

# Most characters of a passage and of its replacement (one slice of the text), and of a whole text.
MAX_PASSAGE: Final = DEFAULT_SLICE
MAX_TEXT: Final = 1_000_000
# Most bytes of the request; Paperless refuses bodies over 2,621,440.
MAX_BODY: Final = 2_500_000
# Characters of each side of a change one dry run returns: both sides together are one slice.
PART: Final = DEFAULT_SLICE // 2
# What a confirmation shows: characters around the change, and of the change itself.
CONTEXT: Final = 40
SHOWN: Final = 300
# NUL and lone surrogates.
_UNSTORABLE: Final = re.compile("[\x00\ud800-\udfff]")


def _common(one: str, other: str) -> int:
    """How many characters two texts have in common at their start."""
    low, high = 0, min(len(one), len(other))
    while low < high:
        middle = (low + high + 1) // 2
        if one[low:middle] == other[low:middle]:
            low = middle
        else:
            high = middle - 1
    return low


def changed_region(before: str, after: str) -> tuple[int, int, int]:
    """Where two texts differ: the start in both, the end in ``before`` and the end in ``after``."""
    start = _common(before, after)
    tail = _common(before[start:][::-1], after[start:][::-1])
    return start, len(before) - tail, len(after) - tail


def _shown(text: str, start: int, end: int) -> str:
    """The region of ``text`` for a confirmation, with what is around it; a long one is cut."""
    first, last = max(0, start - CONTEXT), min(len(text), end + CONTEXT)
    if end - start <= SHOWN:
        shown = excerpt(text[first:last])
    else:
        half = SHOWN // 2
        left_out = end - start - SHOWN
        shown = (
            f"{excerpt(text[first : start + half])} ({left_out} more "
            f"character{'' if left_out == 1 else 's'}) {excerpt(text[end - half : last])}"
        )
    return f"{'…' if first else ''}{shown}{'…' if last < len(text) else ''}"


def _check(edit: TextEdit) -> None:
    """Refuse arguments that make no correction, without repeating them."""
    given = (edit.text is not None, edit.passage is not None, edit.replacement is not None)
    if given not in {(True, False, False), (False, True, True)}:
        message = "Pass either text, or passage and replacement."
        raise Validation(message, hint="An empty replacement removes the passage.")
    if edit.passage == "":
        message = "passage is empty."
        raise Validation(message, hint="Pass the wrong text, copied exactly.")
    limits = {"passage": MAX_PASSAGE, "replacement": MAX_PASSAGE, "text": MAX_TEXT}
    values = {"passage": edit.passage, "replacement": edit.replacement, "text": edit.text}
    for name, limit in limits.items():
        length = len(values[name] or "")
        if length > limit:
            message = f"{name} holds at most {limit} characters; this one has {length}."
            raise Validation(message, hint="Correct the text in smaller steps.")


def _replaced(found: DocumentText, edit: TextEdit) -> str:
    """The whole text after the correction."""
    if edit.text is not None:
        return edit.text
    passage, content = edit.passage or "", found.content
    first = content.find(passage)
    if first < 0:
        message = (
            f"The passage does not occur in the text of version {found.version_id}. "
            "Nothing was changed."
        )
        hint = (
            "Copy it character for character from get_document_content; line breaks count. A "
            "shorter passage may help."
        )
        raise Validation(message, hint=hint)
    second = content.find(passage, first + 1)  # overlapping occurrences count
    if second >= 0:
        message = (
            f"The passage occurs more than once, first at characters {first} and {second}. "
            "Nothing was changed."
        )
        raise Validation(message, hint="Include more of the surrounding text.")
    return f"{content[:first]}{edit.replacement}{content[first + len(passage) :]}"


def _check_storable(old: str, new: str) -> None:
    if new == old:
        message = "The text already reads like that. Nothing was changed."
        raise Validation(message, hint="Read it again with get_document_content.")
    if _UNSTORABLE.search(new):
        message = (
            "The corrected text would hold a NUL character or a lone surrogate, which Paperless "
            "cannot store. Nothing was changed."
        )
        raise Validation(message, hint="Leave it out.")
    size = len(json.dumps({"content": new}, ensure_ascii=False, separators=(",", ":")).encode())
    if size > MAX_BODY:
        message = (
            f"The corrected text is too large for Paperless ({size} bytes of at most {MAX_BODY}). "
            "Nothing was changed."
        )
        raise Validation(message, hint="This text cannot be corrected over the API.")


def _newest(text: DocumentText) -> bool:
    return not text.versions or text.versions[0].id == text.version_id


class TextEditor:
    """Corrects the text of documents for the current user."""

    def __init__(self, reader: DocumentReader, writer: DocumentWriter, *, web_url: str) -> None:
        """Read through ``reader``, write through ``writer``; ``web_url`` is the Paperless UI."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._web_url: str = web_url

    async def correct(  # noqa: PLR0913 -- the text, the correction and how far to go
        self,
        document_id: int,
        state: str,
        edit: TextEdit,
        *,
        dry_run: bool = False,
        offset: int = 0,
        confirm: bool = False,
    ) -> TextCorrected:
        """Replace a passage or the whole text of a version that still has ``state``.

        Without ``confirm`` nothing changes and the refusal shows the change. ``dry_run`` changes
        nothing either and returns the change itself, a part of it from ``offset``.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Conflict: The text is not the one ``state`` was read from. Nothing was changed.
            Validation: The correction is no valid one, or the passage is missing or not unique.
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user may not change the document.
        """
        _check(edit)
        found = await self._reader.get_document_content(document_id, version_id=edit.version_id)
        require_change(found)
        version, old = found.version_id, found.content
        if state != text_state(document_id, version, old):
            message = (
                f"The text of document {document_id} changed since it was read, or text_state "
                "belongs to another version. Nothing was changed."
            )
            hint = (
                "Read it again with get_document_content; an earlier call of yours may already "
                "have corrected it."
            )
            raise Conflict(message, hint=hint)
        asked = _replaced(found, edit)
        # Paperless strips the text of the root version, and only that.
        new = asked.strip() if version == document_id else asked
        _check_storable(old, new)
        effect = self._effect(found, asked, new)
        if dry_run:
            return self._previewed(found, asked, new, effect, offset)
        if not confirm:
            ConfirmationRequired.require(
                effect, scope=(version, state, text_state(document_id, version, new))
            )
        stored = await self._writer.set_document_text(document_id, version, new)
        return await self._result(found, new, stored)

    def _previewed(
        self, found: DocumentText, asked: str, new: str, effect: str, offset: int
    ) -> TextCorrected:
        start, end_old, end_new = changed_region(found.content, asked)
        before, after = found.content[start:end_old], asked[start:end_new]
        end = offset + PART
        return TextCorrected(
            document_id=found.id,
            version_id=found.version_id,
            is_newest=_newest(found),
            dry_run=True,
            chars_before=len(found.content),
            chars_after=len(new),
            text_state=text_state(found.id, found.version_id, found.content),
            change=TextChange(
                position=start,
                offset=offset,
                before=before[offset:end],
                after=after[offset:end],
                total_before=len(before),
                total_after=len(after),
                next_offset=end if end < max(len(before), len(after)) else None,
            ),
            message=f"Nothing was changed. Without dry_run and once confirmed, it would {effect}.",
            web_url=document_link(self._web_url, found.id),
        )

    async def _result(self, found: DocumentText, new: str, stored: str) -> TextCorrected:
        """What the correction did; the text is read again to see that it holds."""
        version, lost = found.version_id, False
        now = found.model_copy(update={"content": stored})
        try:
            now = await self._reader.get_document_content(found.id, version_id=version)
        except PaperlessMcpError:  # the correction is made: an error here would hide that
            lost = True
        parts = [
            f"The text of version {version} was replaced. Reprocessing overwrites the correction."
        ]
        if not _newest(now):
            parts.append("Search does not find it: it is not the newest version.")
        if lost:
            parts.append("The text could not be read again after the change.")
        if {stored, now.content} != {new}:
            parts.append(
                "The stored text is not the one that was sent: read it with get_document_content."
            )
        return TextCorrected(
            document_id=found.id,
            version_id=version,
            is_newest=_newest(now),
            dry_run=False,
            chars_before=len(found.content),
            chars_after=len(now.content),
            text_state=text_state(found.id, version, now.content),
            change=None,
            message=" ".join(parts),
            web_url=document_link(self._web_url, found.id),
        )

    @staticmethod
    def _effect(found: DocumentText, asked: str, new: str) -> str:
        """What a confirmation says; ``asked`` becomes ``new`` when Paperless stores it."""
        old = found.content
        start, end_old, end_new = changed_region(old, asked)
        newest = _newest(found)
        change = (
            f"replace the text of version {found.version_id}{' (the newest)' if newest else ''} of "
            f"document {found.id} {quoted(found.title)}: at character {start}, "
            f"{_shown(old, start, end_old)} becomes {_shown(asked, start, end_new)} "
            f"({len(old)} characters become {len(new)})"
        )
        parts = [change]
        if max(end_old, end_new) - start > SHOWN:
            parts.append("the change is longer than shown here, dry_run=true returns all of it")
        if not new:
            parts.append("the text becomes empty")
        if new != asked:
            parts.append("leading and trailing whitespace is dropped")
        if not newest:
            parts.append("search does not find it, because it is not the newest version")
        return (
            f"{'; '.join(parts)}. Paperless then runs the workflows for updated documents and "
            "keeps the old text only in its audit log, if that is on; reprocessing overwrites the "
            "correction"
        )
