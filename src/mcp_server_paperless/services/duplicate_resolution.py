"""Resolving duplicates: one document stays, the others go to the trash or become its versions.

Only documents that Paperless takes for duplicates of the kept one in this very call are touched,
all of them or none (decision-51). Everything Paperless would refuse and this server can see
beforehand is refused first, with every document named. So is keeping a document whose file
Paperless cannot find while a duplicate still has it: duplicates are told by checksums, which
outlive the files (decision-63). Afterwards the state is read again, so a change that failed, or
that could not be followed, names what is gone and what is still there.

Residual race: what is added to a duplicate (a note, a version) between the read and the request
goes with it. A document in the trash can be restored; a merge is final.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.bulk import TrashDocuments
from mcp_server_paperless.domain.duplicate_resolution import (
    MAX_DUPLICATES,
    DuplicateResolution,
    NamedDocument,
    UnresolvedDuplicate,
)
from mcp_server_paperless.domain.duplicates import DuplicateMatch
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    PermissionDenied,
    Validation,
    quoted,
)
from mcp_server_paperless.domain.files import joined
from mcp_server_paperless.services.bulk_documents import DRY_RUN, TO_GO_AHEAD
from mcp_server_paperless.services.document_edits import COUNTED
from mcp_server_paperless.services.documents import document_link, match_of, root_checksum
from mcp_server_paperless.services.duplicates import SHOWN, describe
from mcp_server_paperless.services.file_checks import check_files
from mcp_server_paperless.services.versions import bound, merge_effect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord, DocumentVersion
    from mcp_server_paperless.domain.duplicate_resolution import ResolveAction
    from mcp_server_paperless.domain.ports import BulkWriter, DocumentReader, DocumentWriter
    from mcp_server_paperless.domain.tasks import Unconfirmed
    from mcp_server_paperless.services.file_checks import FileCheck
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

NOT_DUPLICATE: Final = "Paperless does not take it for a duplicate of document {keep}"
IN_TRASH: Final = "it is in the trash already"
UNSEEN: Final = "it does not exist outside the trash as a document this user may see"
MAY_NOT: Final = "this user may not change it"
HAS_VERSIONS: Final = "it has {count} versions, so it cannot be merged"
STILL_THERE: Final = "it was still there when read again"
NO_FILE: Final = "Paperless cannot find its file, and {others} still {have} it"
UNCHECKED: Final = "whether Paperless can find its file could not be checked (the reason: {reason})"
NOT_READ: Final = "what became of it could not be read"
REPORTED: Final = (
    "Paperless reported the change as done; read the documents again before repeating the call."
)
NOT_SEEN: Final = "what became of it could not be seen"
NEITHER: Final = "it was neither a document of its own nor a version when read again"
KEEP_GONE: Final = (
    "WARNING: the kept {keep} was gone when read again (deleted, merged or hidden meanwhile). "
    "Look for it and the duplicates in the trash with list_trash, and restore one if none is left."
)
CHOOSE: Final = (
    "Read the duplicates with get_document(include=['duplicates']) and pass only those outside "
    "the trash."
)
_OWN: Final = (
    "This user needs the permissions to change and delete documents, and has to own every "
    "duplicate{} (or they have no owner)."
)
OWNERS: Final[dict[ResolveAction, str]] = {
    "trash": _OWN.format(""),
    "merge_as_versions": _OWN.format(" and the kept document"),
}
HAS_VERSIONS_HINT: Final = "Use action trash for it, or keep that document instead."
NO_FILE_HINT: Final = (
    "Keep a document whose file is there instead, or restore the missing file from a backup."
)
UNCHECKED_HINT: Final = (
    "A merge is final, so every file is checked before it: try again in a moment, or use action "
    "trash."
)
RESTORABLE: Final = (
    "{it} can be restored until the trash is emptied (by default after 30 days), in the Paperless "
    "web UI or with restore_from_trash (toolset trash)"
)
FINAL: Final = "With action trash {it} could be restored instead"
HIDDEN: Final = "Documents this user cannot see are not compared"


@dataclass(frozen=True, slots=True)
class _Group:
    """The kept document, the duplicates to resolve, those that stay, and what files are there."""

    keep: DocumentRecord
    duplicates: tuple[DocumentRecord, ...]
    others: tuple[DuplicateMatch, ...]
    files: FileCheck

    @property
    def notes(self) -> list[str]:
        return self.files.notes(self.keep.id)


@dataclass(frozen=True, slots=True)
class _After:
    """What was seen after the change; ``kept`` is None if that could not be read."""

    kept: bool | None
    resolved: tuple[NamedDocument, ...]
    not_resolved: tuple[UnresolvedDuplicate, ...]
    versions: tuple[DocumentVersion, ...] | None = None


def _named(document: DocumentRecord) -> NamedDocument:
    return NamedDocument(id=document.id, title=document.title)


def _title(document: DocumentRecord) -> str:
    return f"document {document.id} {quoted(document.title)}"


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _listed(documents: Sequence[UnresolvedDuplicate]) -> str:
    return "; ".join(f"document {d.id} {quoted(d.title)}: {d.why}" for d in documents)


class DuplicateResolver:
    """Resolves duplicates of a document for the current user."""

    def __init__(
        self,
        reader: DocumentReader,
        writer: DocumentWriter,
        bulk: BulkWriter,
        taxonomy: TaxonomyResolver,
        *,
        web_url: str,
    ) -> None:
        """Trash through ``bulk``, merge through ``writer``; ``taxonomy`` forgets the counts."""
        self._reader: DocumentReader = reader
        self._writer: DocumentWriter = writer
        self._bulk: BulkWriter = bulk
        self._taxonomy: TaxonomyResolver = taxonomy
        self._web_url: str = web_url

    async def resolve(
        self,
        keep_id: int,
        duplicate_ids: Sequence[int],
        action: ResolveAction,
        *,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> DuplicateResolution:
        """Keep ``keep_id`` and trash or merge ``duplicate_ids``; nothing changes for a preview.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            Validation: The selection is empty, too large or repeated, a document is no duplicate
                of the kept one outside the trash, it cannot be merged, its file cannot be checked
                before a merge, or Paperless cannot find the file of the kept one while a duplicate
                has it. Nothing was changed.
            NotFound: The kept document does not exist, or the user may not see it.
            PermissionDenied: The user may not change the documents. Nothing was changed.
            PaperlessMcpError: The change failed; the message names what was seen afterwards.
        """
        _check(keep_id, duplicate_ids)
        group = await self._group(keep_id, duplicate_ids, action)
        blocked, hint = _blocked(group, action)
        effect = _effect(group, action)
        if dry_run:
            return self._preview(group, action, effect, blocked)
        if blocked:
            message = f"Nothing was changed: {_listed(blocked)}."
            if any(b.why == MAY_NOT for b in blocked):
                raise PermissionDenied(message, hint=OWNERS[action])
            raise Validation(message, hint=hint)
        if not confirm:
            ConfirmationRequired.require(
                effect,
                scope=(
                    action,
                    [bound(d) for d in (group.keep, *group.duplicates)],
                    [(o.document_id, o.in_trash) for o in group.others],
                ),
            )
        try:
            unconfirmed = await self._send(group, action)
        except PermissionDenied:
            message = (
                "Paperless refused it; it checks every document first, so nothing was changed."
            )
            raise PermissionDenied(message, hint=OWNERS[action]) from None
        except PaperlessMcpError as error:
            seen = _seen(group, action, await self._after(group, action))
            message = f"{error.message} {seen}"
            raise PaperlessMcpError(message, hint=error.hint) from error
        return self._report(group, action, await self._after(group, action), unconfirmed)

    # --- reading ---------------------------------------------------------------------------------

    async def _group(
        self, keep_id: int, duplicate_ids: Sequence[int], action: ResolveAction
    ) -> _Group:
        """The documents, each of them a duplicate of the kept one now.

        Raises:
            NotFound: The kept document does not exist, or the user may not see it.
            Validation: Some ids are no such duplicates; all of them are named.
        """
        keep = await self._reader.get_document(keep_id)
        found = {d.id: d for d in await self._reader.get_duplicates(keep_id)}
        duplicates: list[DocumentRecord] = []
        refused: list[str] = []
        for number in duplicate_ids:
            if number in found and found[number].deleted_at is not None:
                refused.append(f"document {number}: {IN_TRASH}")
                continue
            try:
                document = await self._reader.get_document(number)
            except NotFound:
                refused.append(f"document {number}: {UNSEEN}")
                continue
            if number in found:
                duplicates.append(document)
            else:
                refused.append(f"document {number}: {NOT_DUPLICATE.format(keep=keep_id)}")
        if refused:
            message = f"Nothing was changed: {'; '.join(refused)}."
            raise Validation(message, hint=CHOOSE)
        others = (
            DuplicateMatch(
                document_id=d.id,
                title=d.title,
                in_trash=d.deleted_at is not None,
                deleted_at=d.deleted_at,
                web_url=None if d.deleted_at else document_link(self._web_url, d.id),
            )
            for d in found.values()
            if d.id not in duplicate_ids
        )
        ordered = sorted(others, key=lambda o: (o.in_trash, o.document_id))
        return _Group(
            keep=keep,
            duplicates=tuple(duplicates),
            others=tuple(ordered),
            files=await self._files(keep, duplicates, action),
        )

    async def _files(
        self, keep: DocumentRecord, duplicates: Sequence[DocumentRecord], action: ResolveAction
    ) -> FileCheck:
        """Before the trash the duplicates matter only if the kept document lacks a file."""
        if action == "trash":
            kept = await check_files(self._reader, [keep])
            if not kept.lost(keep.id):
                return kept
        return await check_files(self._reader, [keep, *duplicates])

    async def _after(self, group: _Group, action: ResolveAction) -> _After:
        """What became of the duplicates; never raises, because the change may be made.

        Only what was seen counts: a document that is not found may be hidden, not in the trash.
        """
        documents, keep = group.duplicates, group.keep.id
        kept: bool | None = True
        versions = None
        proven: set[int] = set()
        try:
            if action == "trash":
                found = await self._reader.get_duplicates(keep)
                proven = {d.id for d in found if d.deleted_at is not None}
            else:
                versions = (await self._reader.get_document(keep)).versions
                proven = {v.id for v in versions}
        except NotFound:
            kept = False
        except PaperlessMcpError:
            kept = None
        unseen = NEITHER if kept and action == "merge_as_versions" else NOT_SEEN
        if kept is None:
            unseen = NOT_READ
        left = [d.id for d in documents if d.id not in proven]
        there: dict[int, str] = {}
        if left:
            try:
                there = await self._reader.get_document_titles(left)
            except PaperlessMcpError:
                unseen = NOT_READ
        return _After(
            kept=kept,
            resolved=tuple(_named(d) for d in documents if d.id in proven),
            not_resolved=tuple(
                UnresolvedDuplicate(
                    id=d.id, title=d.title, why=STILL_THERE if d.id in there else unseen
                )
                for d in documents
                if d.id not in proven
            ),
            versions=versions,
        )

    # --- writing ---------------------------------------------------------------------------------

    async def _send(self, group: _Group, action: ResolveAction) -> Unconfirmed | None:
        ids = [d.id for d in group.duplicates]
        try:
            if action == "trash":
                return await self._bulk.bulk_edit_documents(ids, TrashDocuments())
            return await self._writer.merge_as_versions(group.keep.id, ids, None)
        finally:
            for kind in COUNTED:
                self._taxonomy.invalidate(kind)

    # --- answers ---------------------------------------------------------------------------------

    def _preview(
        self,
        group: _Group,
        action: ResolveAction,
        effect: str,
        blocked: tuple[UnresolvedDuplicate, ...],
    ) -> DuplicateResolution:
        message = f"{DRY_RUN} Confirming would {effect}. {TO_GO_AHEAD}"
        if blocked:
            message = f"{DRY_RUN} It would be refused as a whole: {_listed(blocked)}."
            message = " ".join([message, *_sentences(group.notes)])
        return self._resolution(
            group,
            action,
            _After(
                kept=True,
                resolved=() if blocked else tuple(_named(d) for d in group.duplicates),
                not_resolved=blocked,
            ),
            message,
            dry_run=True,
        )

    def _report(
        self, group: _Group, action: ResolveAction, after: _After, unconfirmed: Unconfirmed | None
    ) -> DuplicateResolution:
        message = _seen(group, action, after)
        if unconfirmed is not None:
            message = (
                f"Not confirmed: Paperless queued task {unconfirmed.task_id}, but "
                f"{unconfirmed.reason}. {message} Read the task with get_task and the documents "
                "again before repeating the call."
            )
        elif after.not_resolved:
            message = f"{message} {REPORTED}"
        if action == "trash" and after.resolved:
            message += f" {RESTORABLE.format(it=_it(after.resolved).capitalize())}."
        return self._resolution(
            group,
            action,
            after,
            message,
            task_id=None if unconfirmed is None else unconfirmed.task_id,
        )

    def _resolution(  # noqa: PLR0913 -- what the answers have in common
        self,
        group: _Group,
        action: ResolveAction,
        after: _After,
        message: str,
        *,
        dry_run: bool = False,
        task_id: str | None = None,
    ) -> DuplicateResolution:
        return DuplicateResolution(
            action=action,
            dry_run=dry_run,
            applied=not dry_run,
            kept=_named(group.keep),
            kept_seen=after.kept,
            resolved=after.resolved,
            not_resolved=after.not_resolved,
            other_duplicates=group.others[:SHOWN],
            other_duplicates_count=len(group.others),
            versions=after.versions,
            unconfirmed_task_id=task_id,
            message=message,
            web_url=document_link(self._web_url, group.keep.id),
        )


def _check(keep_id: int, duplicate_ids: Sequence[int]) -> None:
    """The checks that need no read of Paperless."""
    if not 1 <= len(duplicate_ids) <= MAX_DUPLICATES:
        message = f"Name between 1 and {MAX_DUPLICATES} duplicates; got {len(duplicate_ids)}."
        raise Validation(message, hint="Work in several steps.")
    if keep_id in duplicate_ids:
        message = f"Document {keep_id} is the one to keep, so it cannot be among the duplicates."
        raise Validation(message, hint="List only the other documents in duplicate_ids.")
    repeated = next((n for n in duplicate_ids if duplicate_ids.count(n) > 1), None)
    if repeated is not None:
        message = f"Document {repeated} is listed twice."
        raise Validation(message, hint="List each document once.")


def _sentences(notes: Sequence[str]) -> list[str]:
    return [f"{note}." for note in notes]


def _blocked(group: _Group, action: ResolveAction) -> tuple[tuple[UnresolvedDuplicate, ...], str]:
    """What is refused, all of it together, and what would help.

    That is what Paperless would refuse, as far as it can be seen, and what would cost a file.
    """
    merging, keep, files = action == "merge_as_versions", group.keep, group.files
    better = files.outdone_by(keep.id, [d.id for d in group.duplicates])

    def why(document: DocumentRecord) -> tuple[str, str] | None:
        # Trashing the duplicates does not touch the kept document.
        if document.user_can_change is False and (merging or document is not keep):
            return MAY_NOT, OWNERS[action]
        if merging and document is not keep and len(document.versions) > 1:
            return HAS_VERSIONS.format(count=len(document.versions)), HAS_VERSIONS_HINT
        if merging and document.id in files.unchecked:
            reason = files.unchecked[document.id].removesuffix(".")
            return UNCHECKED.format(reason=reason), UNCHECKED_HINT
        if better and document is keep:
            have = "has" if len(better) == 1 else "have"
            return NO_FILE.format(others=joined(better), have=have), NO_FILE_HINT
        return None

    found = [(d, reason) for d in (keep, *group.duplicates) if (reason := why(d))]
    hints = dict.fromkeys(hint for _, (_, hint) in found)
    return tuple(
        UnresolvedDuplicate(id=d.id, title=d.title, why=reason) for d, (reason, _) in found
    ), " ".join(hints)


def _it(documents: Sequence[object]) -> str:
    return "it" if len(documents) == 1 else "they"


def _seen(group: _Group, action: ResolveAction, after: _After) -> str:
    """What was seen after the change, every document named."""
    parts = [KEEP_GONE.format(keep=_title(group.keep))] if after.kept is False else []
    if after.resolved:
        named = ", ".join(f"document {d.id} {quoted(d.title)}" for d in after.resolved)
        where = "In the trash" if action == "trash" else f"Versions of document {group.keep.id}"
        parts.append(f"{where} now: {named}.")
    if after.not_resolved:
        parts.append(f"Not known to be resolved: {_listed(after.not_resolved)}.")
    parts += _sentences(group.notes)
    return " ".join(parts)


def _effect(group: _Group, action: ResolveAction) -> str:
    """What confirming would do, with every document and what is lost with it."""
    keep, duplicates = group.keep, group.duplicates
    names = ", ".join(_title(d) for d in duplicates)
    if action == "trash":
        lead = (
            f"keep {_title(keep)} and move {_counted(len(duplicates), 'duplicate')} to the "
            f"Paperless trash: {names}. "
            f"{RESTORABLE.format(it=_it(duplicates).capitalize())}"
        )
    else:
        lead = f"{merge_effect(keep, duplicates, None)}. {FINAL.format(it=_it(duplicates))}"
    parts = [lead, *group.notes, *(_details(d, group) for d in (keep, *duplicates))]
    if group.others:
        more = len(group.others) - SHOWN
        rest = f" and {more} more" if more > 0 else ""
        listed = ", ".join(describe(o) for o in group.others[:SHOWN])
        stay = (
            f"Another duplicate of document {keep.id} stays"
            if len(group.others) == 1
            else f"Other duplicates of document {keep.id} stay"
        )
        parts.append(f"{stay}: {listed}{rest}")
    return ". ".join([*parts, HIDDEN])


def _details(document: DocumentRecord, group: _Group) -> str:
    """What a document holds, and where its files are not the ones that match."""
    facts = [
        _counted(document.notes_count, "note"),
        _counted(len(document.tags), "tag"),
        _counted(len(document.custom_fields), "custom field"),
        _counted(len(document.versions), "version"),
    ]
    if document.archive_serial_number is not None:
        facts.append(f"archive serial number {document.archive_serial_number}")
    root = root_checksum(document)
    newest = document.versions[0]
    if not newest.is_root and (newest.checksum or "").lower() != root:
        facts.append("its newest file is not the one that was compared")
    if document is not group.keep:
        match match_of(group.keep, root):
            case "same_file":
                pass
            case "archive":
                facts.append(
                    f"not byte-identical to document {group.keep.id}, only an archive file matches"
                )
            case None:
                facts.append("whether it is byte-identical is not known")
    role = "Kept: document" if document is group.keep else "Document"
    return f"{role} {document.id} has {', '.join(facts)}"
