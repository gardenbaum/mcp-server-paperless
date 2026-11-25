"""The ports: what services need from Paperless, split by resource into readers and writers.

Every Paperless port lives here. The adapter (``mcp_server_paperless.paperless``) and the test fake
implement them; the contract suite (tests/contract) needs a contract test per method and checks
that the fake guards every method of a ``*Writer`` port.

Hard rule: every method that changes anything in Paperless lives on a protocol named
``*Writer``, and ``*Reader`` protocols only read. The fake's write guard, and with it the
read-only tests of tools, find writes by that name alone.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from mcp_server_paperless.domain.admin import (
        AppConfigRecord,
        ArchiveContent,
        ArchiveFile,
        Compression,
        ConfigValues,
        GroupRecord,
        GroupValues,
        RunnableTask,
        TaskRowPage,
        TaskType,
        UserRecord,
        UserValues,
    )
    from mcp_server_paperless.domain.ai import AiSuggestions, ChatAnswer
    from mcp_server_paperless.domain.bulk import (
        BulkKind,
        DocumentChange,
        ObjectPermissions,
        PermissionsChange,
    )
    from mcp_server_paperless.domain.documents import (
        DocumentFile,
        DocumentPage,
        DocumentPatch,
        DocumentQuery,
        DocumentRecord,
        DocumentText,
        DocumentUpload,
        DocumentVersion,
        FileMetadata,
        FileVersion,
        HistoryEntry,
        Note,
        SignedIn,
        Suggestions,
    )
    from mcp_server_paperless.domain.duplicates import DuplicateRecord
    from mcp_server_paperless.domain.files import OpenedFile
    from mcp_server_paperless.domain.instance import InstanceOverview
    from mcp_server_paperless.domain.mail import (
        MailAccount,
        MailAccountTestResult,
        MailAccountValues,
        MailRule,
        MailRuleValues,
        ProcessedMail,
        ProcessedMailPage,
        ProcessedOrdering,
        ProcessedStatus,
    )
    from mcp_server_paperless.domain.pdf import (
        CopiedFiles,
        FileOutput,
        FoundCopies,
        PageSlot,
        RotationSearch,
        StartedTasks,
    )
    from mcp_server_paperless.domain.permissions import (
        AccessChange,
        GroupPage,
        ObjectAccess,
        PermissionKind,
        UserPage,
    )
    from mcp_server_paperless.domain.raw import RawReply
    from mcp_server_paperless.domain.selection import DocumentRow, ReadField
    from mcp_server_paperless.domain.sharing import FileVersionKind, ShareBundle, ShareLink
    from mcp_server_paperless.domain.tasks import TaskState, TaskStatus, Unconfirmed
    from mcp_server_paperless.domain.taxonomy import (
        TaxonomyDetail,
        TaxonomyItem,
        TaxonomyKind,
        TaxonomyValues,
        WritableKind,
    )
    from mcp_server_paperless.domain.trash import TrashedRecord, TrashLookup, TrashPage
    from mcp_server_paperless.domain.versions import VersionPage, VersionUpload
    from mcp_server_paperless.domain.views import SavedView, ViewValues
    from mcp_server_paperless.domain.workflows import (
        Workflow,
        WorkflowAction,
        WorkflowRun,
        WorkflowTrigger,
        WorkflowValues,
    )


class InstanceReader(Protocol):
    """Instance-wide facts as the current user sees them."""

    async def get_overview(self) -> InstanceOverview:
        """Versions, statistics, system status, current user and capabilities.

        What the user may not see is None in the result (see ``InstanceOverview``).
        """
        ...


class TaxonomyReader(Protocol):
    """Tags, correspondents, document types, storage paths, custom fields, views, users, groups."""

    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        """Every object of ``kind`` the user may see, over all pages, in Paperless order.

        Raises:
            PermissionDenied: The user lacks the global view permission for ``kind``.
        """
        ...


class DocumentReader(Protocol):
    """Documents the current user may see."""

    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        """One page of the documents matching ``query``, in Paperless order.

        Fetches that page only and never the content. Full-text and more-like results are
        ranked by relevance unless ``query.ordering`` is set, and carry highlights.

        Raises:
            Validation: Paperless rejected the query (e.g. a bad ``custom_field_query``).
            NotFound: ``page`` is beyond the last page.
        """
        ...

    async def select_documents(
        self, query: DocumentQuery, *, limit: int, max_bytes: int
    ) -> DocumentPage:
        """The first ``limit`` documents matching ``query`` in one request, as a search.

        Raises:
            Validation: Paperless rejected the query.
            AnswerTooLarge: The answer is larger than ``max_bytes``.
            AnswerCompressed: The answer was compressed, so its size is not known.
        """
        ...

    async def read_rows(
        self, document_ids: Sequence[int], fields: Sequence[ReadField], *, max_bytes: int
    ) -> tuple[DocumentRow, ...]:
        """Those of the documents the user may see, with ``fields``, in one request.

        ``content`` is no field of a row: it is never read here. Paperless sends the notes also to
        a user without ``view_note``, so whoever asks for them checks that first.

        Raises:
            PermissionDenied: The user lacks the global ``view_document`` permission.
            AnswerTooLarge: The answer is larger than ``max_bytes``.
            AnswerCompressed: The answer was compressed, so its size is not known.
        """
        ...

    async def get_document(self, document_id: int) -> DocumentRecord:
        """One document without its content.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def get_document_content(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentText:
        """The whole OCR text of a version of a document (default: the newest); services slice it.

        Raises:
            NotFound: No such document, the user may not see it, or it lists no such version (a
                deleted one is not listed).
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def get_version_pages(self, document_id: int, version_id: int) -> int | None:
        """How many pages a version of a document has, None where Paperless counted none.

        Raises:
            NotFound: No such document, the user may not see it, or it lists no such version.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def may_delete(self, owners: Sequence[int | None]) -> bool | None:
        """Whether the user may delete documents of these owners; None where that is not known.

        It needs the global ``delete_document`` permission and ownership (or no owner), or a
        superuser. The user's permissions can be read only with ``view_uisettings``.
        """
        ...

    async def get_signed_in(self) -> SignedIn | None:
        """The user the credential belongs to; None without the ``view_uisettings`` permission."""
        ...

    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        """The notes of a document, in Paperless order.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not view notes (a separate permission in Paperless).
        """
        ...

    async def list_document_history(self, document_id: int) -> tuple[HistoryEntry, ...]:
        """The audit log of a document, newest first, without the OCR text.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not view the audit log (it needs ``view_logentry``
                and a document that is unowned or the user's own; a view grant is not enough).
            FeatureUnavailable: The instance has the audit log switched off.
        """
        ...

    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        """The file facts of a document, or of one of its versions (default: the newest).

        A size is None when Paperless cannot find the file (``FileMetadata.lacks``).

        Paperless answers a deleted version, too, while it is in the trash: callers that must not
        show one check the versions ``get_document`` lists.

        Raises:
            NotFound: No such document, or no such version of it.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_suggestions(self, document_id: int) -> Suggestions:
        """The classifier's suggestions (ids) for a document.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        """The thumbnail image of a document (WebP), also in the trash (of its first version).

        Raises:
            NotFound: No such document, or Paperless cannot find the thumbnail.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_file(
        self,
        document_id: int,
        version: FileVersion,
        *,
        max_bytes: int,
        version_id: int | None = None,
    ) -> DocumentFile:
        """The original file or the archive version (the OCR'd PDF/A) of a document.

        ``version_id`` names a version of the document (default: the newest; a deleted one is
        answered, too, see ``get_document_metadata``).

        Reads at most ``max_bytes`` bytes: a larger file comes back ``truncated`` with its type
        and name but no content. Paperless answers the original for ``archive`` when the
        document has no archive version, so callers check ``has_archive_version`` first.

        Raises:
            NotFound: No such document, no such version of it, or Paperless cannot find the file.
            PermissionDenied: The user may not see the document.
        """
        ...

    def open_document_file(
        self, document_id: int, version: FileVersion, *, version_id: int | None = None
    ) -> AbstractAsyncContextManager[OpenedFile]:
        """The same file as ``get_document_file``, handed over chunk by chunk and of any size.

        Raises:
            NotFound: No such document, no such version of it, or Paperless cannot find the file.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        """The titles of those documents the user may see; missing and hidden ones are left out.

        One request for at most as many documents as ``document_ids`` holds distinct ids.
        """
        ...

    async def get_newest_versions(self, document_ids: Collection[int]) -> dict[int, int]:
        """The newest file version of those documents the user may see, by document id.

        The root version has the id of its document. One request, like ``get_document_titles``.
        """
        ...

    async def get_root(self, document_id: int) -> int:
        """The document a version belongs to; a document's own id. Also in the trash.

        Raises:
            NotFound: No such document or version, or the user may not see its document.
        """
        ...

    async def document_exists(self, document_id: int) -> bool:
        """Whether a document or version has this id: also in the trash, also if it is hidden."""
        ...

    async def get_duplicates(self, document_id: int) -> tuple[DuplicateRecord, ...]:
        """The documents Paperless takes for duplicates of this one, in the trash too.

        It compares the original uploads and their archives, not versions, and names only what
        the user may see.

        Raises:
            NotFound: No such document (a version is none), or the user may not see it.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def get_version_checksum(self, document_id: int, version_id: int) -> str | None:
        """The SHA-256 of a version's file, also of a deleted one, whether the file is there or not.

        Raises:
            NotFound: No such document outside the trash, or no such version of it.
            PermissionDenied: The user may not see the document.
        """
        ...

    async def list_versions(
        self, *, page: int, page_size: int, recent: bool = False
    ) -> VersionPage:
        """One page of the documents the user may see with their versions.

        Highest id first, an order that edits do not move; with ``recent`` last changed first.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...


class TaskReader(Protocol):
    """Background tasks (uploads, PDF operations) the current user may see."""

    async def get_task(self, task_id: str) -> TaskStatus:
        """The task Paperless knows under the Celery id ``task_id``.

        Raises:
            NotFound: No task with that id exists that the user may see.
            PermissionDenied: The user lacks the permission to view tasks.
        """
        ...


class DocumentWriter(Protocol):
    """Changes to documents."""

    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        """Change the fields ``patch`` sets and return the document as it is afterwards.

        Paperless replaces the tag and custom field lists as a whole: this port sends what it
        is given, so callers merge first. Nothing else changes.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not change documents, or this one.
            Validation: Paperless rejected a value (unknown id, wrong type, duplicate ASN).
        """
        ...

    async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
        """Replace the whole text of one version and return the text as it is stored.

        Paperless strips the text of the root version, and writes a deleted version, too:
        callers check that the document lists it. There is no conditional write. Not retried.

        Raises:
            NotFound: No such document, the user may not see it, or it never had such a version.
            PermissionDenied: The user may not change documents, or this one.
            Validation: Paperless rejected the text.
        """
        ...

    async def add_note(self, document_id: int, text: str) -> Note:
        """Add a note authored by the user and return it.

        Paperless answers with all notes of the document; the new one is the one with the
        highest id. It accepts empty and whitespace-only text, so callers check that.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user lacks the note permission, may not see the document, or
                may not change it (a view grant is not enough for a note).
            Validation: Paperless could not save the note.
        """
        ...

    async def delete_note(self, document_id: int, note_id: int) -> None:
        """Delete one note of a document for good; notes have no trash.

        Raises:
            NotFound: No such document, or no such note on it.
            PermissionDenied: The user lacks the note permission or may not change the document.
        """
        ...

    async def delete_document(self, document_id: int) -> None:
        """Move a document to the Paperless trash; it can be restored from there.

        Raises:
            NotFound: No such document, or the user may not see it.
            PermissionDenied: The user may not delete documents, or this one (an owner, or any
                user for an unowned document; a change grant is not enough).
        """
        ...

    async def upload_document(self, upload: DocumentUpload) -> str:
        """Hand a file to Paperless for consumption and return the id of its task.

        Paperless checks the form, not the file: it answers at once with the task's UUID, and
        consumption happens later. A clash it only sees then (an archive serial number that is
        taken) fails the task, not this call. Uploading the same content twice creates two
        documents, unless Paperless is configured to reject duplicates, which fails the task. A
        busy Paperless is not retried: a retry could upload twice.

        Raises:
            PermissionDenied: The user may not add documents.
            Validation: Paperless rejected the form (unsupported file type, unknown id, bad
                custom field value).
        """
        ...

    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        """Hand a file to Paperless as a new version of a document and return its task's id.

        Like ``upload_document``: Paperless checks the form and answers with the task's UUID,
        and consumption happens later. The task's document is the new version (not the root).
        The new version becomes the newest one. The same content twice makes two versions. Not
        retried: a retry could add the version twice.

        Raises:
            NotFound: No such document.
            PermissionDenied: The user lacks the global add and change permissions, or may not
                change the document (its owner, a superuser, anyone for an unowned document, or a
                user with a change grant): a document the user may not see is refused, not hidden.
            Validation: Paperless rejected the form (unsupported file type, label too long).
        """
        ...

    async def set_version_label(
        self, document_id: int, version_id: int, label: str | None
    ) -> DocumentVersion:
        """Set the label of a version (the root's too) and return the version; None clears it.

        Raises:
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user lacks the global change permission or may not change the
                document (as for ``upload_document_version``).
            Validation: The label is longer than 64 characters.
        """
        ...

    async def delete_version(self, document_id: int, version_id: int) -> int:
        """Delete a version other than the root and return the id of the newest one left.

        Paperless moves the version to the trash, where it can be restored from (with its
        document id being the version id). Not retried: a repeat would answer not found.

        Raises:
            NotFound: No such document, or it has no such version.
            PermissionDenied: The user lacks the global delete permission, or is not the owner
                (a superuser, or anyone for an unowned document): a change grant is not enough.
            Validation: The version is the root, which only deleting the document removes.
        """
        ...

    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> Unconfirmed | None:
        """Make the source documents versions of the root document.

        The sources stop existing as documents of their own, for good (no trash): their
        metadata, notes and tags are lost, only their files remain, as the root's newest
        versions. ``label`` is only valid with exactly one source. Paperless merges at once. Not
        retried: a repeat would answer 400 for sources that are gone.

        Returns:
            None when the merge is made, or ``Unconfirmed`` when Paperless queued it and the
            task could not be followed to its end.

        Raises:
            PermissionDenied: Unless the user is a superuser: the global change and delete
                permissions, and a change grant on, and ownership of (or no owner for), every
                document are needed; a document the user may not see is refused, not hidden.
            Validation: A document is missing or listed twice, a source has versions of its own
                or is itself a version, or ``label`` comes with several sources.
        """
        ...


class TaxonomyWriter(Protocol):
    """Changes to tags, correspondents and the like."""

    async def create_tag(self, name: str) -> TaxonomyItem:
        """A new tag named ``name`` that matches nothing automatically, owned by the user.

        Raises:
            PermissionDenied: The user may not add tags.
            Validation: The name is blank, too long, or the user already has such a tag.
        """
        ...

    async def create_taxonomy_item(
        self, kind: WritableKind, name: str, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """A new object of ``kind`` named ``name`` with the settings ``values`` sets.

        The user owns it.

        Where the kind has a matching rule it defaults to "none" (never assigned automatically).
        ``values.name`` is ignored; a custom field needs ``data_type``, a storage path ``path``.

        Raises:
            PermissionDenied: The user may not add objects of ``kind``.
            Validation: Paperless rejected a value (blank or duplicate name, bad colour, unknown
                parent, invalid pattern or path template, missing options).
        """
        ...

    async def update_taxonomy_item(
        self, kind: WritableKind, item_id: int, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """Change the settings ``values`` sets and return the object as it is afterwards.

        Select options in ``values.select_options`` replace the old list; options with an id keep
        it. A custom field's options stay when ``values`` leaves them out (the adapter sends
        them along, Paperless refuses a change of a select field without them).

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not change objects of ``kind``, or this one.
            Validation: Paperless rejected a value (a parent that would make a cycle, ...).
        """
        ...

    async def delete_taxonomy_item(self, kind: WritableKind, item_id: int) -> None:
        """Delete the object for good: Paperless has no trash for taxonomy.

        Documents lose what refers to it (tag, correspondent, type, path, or the custom field's
        value); deleting a tag deletes its child tags too, at any depth.

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not delete objects of ``kind``, or this one.
        """
        ...


class BulkWriter(Protocol):
    """Changes to many documents or taxonomy objects in one request.

    Paperless checks every selected object first: a refusal changes none. Documents are then
    changed by a queued task, which can fail half way. Ids that do not exist, appear twice or are
    an empty list are rejected as a whole (callers deduplicate and check before they ask).
    """

    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange, *, stop_on_limit: bool = False
    ) -> Unconfirmed | None:
        """Apply ``change`` to every document of ``document_ids``.

        Tags are removed (with their child tags) and then added (with their parent tags); custom
        fields are set one by one and the others stay. A deletion moves to the trash;
        reprocessing is queued and runs in the background. ``stop_on_limit`` gives the queued task
        up at HTTP 429 instead of waiting that out.

        Returns:
            None when the change is made (Paperless queues it as a task and the writer
            waits for it), or ``Unconfirmed`` when the queued task could not be followed to its
            end: the change was accepted but may not be made yet.

        Raises:
            PermissionDenied: The user may not change one of the documents (nothing is changed),
                or may not delete it: that needs ownership.
            TaskFailed: The queued task failed; part of the change may be made.
            Validation: An id or a referenced object does not exist, or a value is wrong; the
                message names the reason and nothing is changed.
        """
        ...

    async def delete_taxonomy_items(self, kind: BulkKind, item_ids: Sequence[int]) -> None:
        """Delete the objects for good; documents lose what refers to them, tags take children.

        Raises:
            PermissionDenied: The user may not delete one of them (an owner, or any user for an
                unowned object, and the global permission).
            Validation: An id does not exist; nothing is changed.
        """
        ...

    async def set_taxonomy_permissions(
        self, kind: BulkKind, item_ids: Sequence[int], change: PermissionsChange
    ) -> None:
        """Set the owner and/or the grants of the objects.

        Raises:
            PermissionDenied: The user may not change one of them (owner or unowned).
            Validation: An id, user or group does not exist; nothing is changed.
        """
        ...


class AiReader(Protocol):
    """Paperless AI (a language model over the documents); it changes nothing in Paperless."""

    async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
        """The whole answer of the chat to ``question``, about one document or all the user sees.

        The answer streams in Paperless; this returns it complete, cut at a size or time limit
        (``truncated``). Paperless names at most three source documents.

        Raises:
            FeatureUnavailable: Paperless AI is switched off.
            NotFound: ``document_id`` names no document.
            PermissionDenied: The user may not view documents, or not this document.
            UpstreamUnavailable: The language model failed or took too long.
        """
        ...

    async def get_ai_suggestions(self, document_id: int) -> AiSuggestions:
        """What the language model suggests for a document, asked for now (or cached by Paperless).

        Raises:
            NotFound: No such document.
            PermissionDenied: The user may not change the document (Paperless requires it).
            FeatureUnavailable: Paperless AI is switched off.
            UpstreamUnavailable: The language model failed or took too long.
        """
        ...


class ViewReader(Protocol):
    """Saved views the current user may see."""

    async def list_saved_views(self) -> tuple[SavedView, ...]:
        """Every saved view the user may see, over all pages, in Paperless order (by name).

        Raises:
            PermissionDenied: The user lacks the global ``view_savedview`` permission.
        """
        ...

    async def get_saved_view(self, view_id: int) -> SavedView:
        """One saved view.

        Raises:
            NotFound: No such view, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_savedview`` permission.
        """
        ...


class ViewWriter(Protocol):
    """Changes to saved views."""

    async def create_saved_view(self, name: str, values: ViewValues) -> SavedView:
        """A new view named ``name`` with the settings ``values`` sets, owned by the user.

        ``values.name`` is ignored. Paperless needs a rule list; none given means no rules.

        Raises:
            PermissionDenied: The user may not add saved views.
            Validation: Paperless rejected a value (blank or taken name, unknown rule type, a
                rule value over 255 characters, a page size below 1).
        """
        ...

    async def update_saved_view(self, view_id: int, values: ViewValues) -> SavedView:
        """Change the settings ``values`` sets and return the view as it is afterwards.

        Paperless replaces the rule list as a whole and, unlike a create, accepts a name another
        view of the user already has.

        Raises:
            NotFound: No such view, or the user may not see it.
            PermissionDenied: The user may not change saved views, or this one.
            Validation: Paperless rejected a value.
        """
        ...

    async def delete_saved_view(self, view_id: int) -> None:
        """Delete the view for good: Paperless has no trash for saved views.

        Raises:
            NotFound: No such view, or the user may not see it.
            PermissionDenied: The user may not delete saved views, or this one.
        """
        ...


class TrashReader(Protocol):
    """The Paperless trash: deleted documents and versions the current user may see."""

    async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
        """One page of the trash, in Paperless order (by creation date, newest first).

        A version deleted from a document is an entry of its own, but not while its document is
        in the trash too (the document's entry covers it). The user sees the entries of
        documents they own or that have no owner, all of them for a superuser; a view grant is
        not enough. The rows carry the whole text of the documents, so at most ``max_bytes`` are
        read; that counts what a compressed answer unpacks to.

        Raises:
            AnswerCompressed: The answer is compressed in a way that cannot be unpacked in parts.
            AnswerTooLarge: The page is larger than ``max_bytes``.
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        """The first ``entries`` entries in one request, like a page of ``list_trash``.

        Paperless orders the trash by a date only, so pages of it can overlap; one request cannot.
        The rows carry the whole text of the documents, so at most ``max_bytes`` are read.

        Raises:
            AnswerCompressed: The answer is compressed, so its size is not known.
            AnswerTooLarge: The answer is larger than ``max_bytes``.
            PermissionDenied: The user lacks the global ``view_document`` permission.
        """
        ...

    async def find_in_trash(
        self, document_ids: Collection[int], *, entries: int, max_bytes: int
    ) -> dict[int, TrashedRecord]:
        """Those of the documents that are among the first ``entries`` entries, by id.

        Versions are left out. One request, like ``read_trash``, with the same bounds and errors.
        """
        ...

    async def look_up_in_trash(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> TrashLookup:
        """Everything of one entry, a version too, if it is on this page of the trash.

        The text is the entry's own: of a document the first version's, of a version its own. One
        request, bounded like ``read_trash``, with the same errors.

        Raises:
            NotFound: ``page`` is beyond the last page.
        """
        ...

    async def get_trash_permissions(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> ObjectPermissions | None:
        """Who may view and change an entry, if it is on this page; read like ``look_up_in_trash``.

        Raises:
            NotFound: ``page`` is beyond the last page.
        """
        ...


class TrashWriter(Protocol):
    """Changes to the trash."""

    async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
        """Take the entries out of the trash.

        A document comes back with its notes, versions and metadata. A version deleted from a
        document is re-attached to it with its label and upload time, and becomes the newest
        version if it was. Only ids that are in the trash and distinct are accepted, all or none.
        This port always names ids: Paperless reads a request without them as "everything".

        Raises:
            PermissionDenied: The user lacks the global ``delete_document`` permission, or may
                not delete one of the documents (an owner, or any user for an unowned one).
            Validation: An id is not in the trash (or listed twice), or is a version whose
                document is in the trash too: restore the document instead. Nothing is changed.
        """
        ...

    async def empty_trash(self, document_ids: Sequence[int]) -> None:
        """Remove the entries from the trash for good: files, text, notes and metadata are lost.

        Emptying a document also removes its versions that are in the trash, those deleted
        earlier too. Only ids that are in the trash and distinct are accepted, all or none. This
        port always names ids: Paperless reads a request without them as "everything" and an
        empty list as nothing, which is why callers deduplicate and never pass none.

        Raises:
            PermissionDenied: As for ``restore_from_trash``.
            Validation: An id is not in the trash (or listed twice). Nothing is changed.
        """
        ...


class PermissionReader(Protocol):
    """Owners and grants of documents and taxonomy objects, and the users and groups."""

    async def get_permissions(
        self, kind: PermissionKind, object_ids: Sequence[int]
    ) -> list[ObjectAccess]:
        """The owner and the grants of the objects the user may see, in the order of ``object_ids``.

        An id of an object that does not exist, or that the user may not see, is left out;
        callers compare. A change grant is listed with its view grant (Paperless stores both).
        Paperless shows grants of an object to any user who may see it.

        Raises:
            PermissionDenied: The user lacks the global view permission for ``kind``.
        """
        ...

    async def list_users(self, *, name_contains: str | None, page: int, page_size: int) -> UserPage:
        """One page of users in Paperless order (by username), optionally by part of the name.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user lacks the global ``view_user`` permission.
        """
        ...

    async def list_groups(
        self, *, name_contains: str | None, page: int, page_size: int
    ) -> GroupPage:
        """One page of groups in Paperless order (by name), optionally by part of the name.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user lacks the global ``view_group`` permission.
        """
        ...


class PermissionWriter(Protocol):
    """Changes to owners and grants."""

    async def set_permissions(
        self, kind: PermissionKind, object_id: int, change: AccessChange
    ) -> ObjectAccess:
        """Set the owner and/or the whole grants of one object and return how it is now.

        Lists in ``change.permissions`` replace the object's lists, an empty list removes all of
        them; granting change also grants view. The owner changes only if ``change`` sets it.

        Raises:
            NotFound: The object does not exist or the user may not see it.
            PermissionDenied: Only the owner of the object (or anyone, for an object without
                owner) and a superuser may change its permissions.
            Validation: A user or group does not exist; nothing is changed.
        """
        ...


class PdfWriter(Protocol):
    """Server-side PDF operations: Paperless queues them and tasks do the work.

    Every method returns the tasks Paperless started for the request, as far as they can be found
    (``StartedTasks``). Paperless itself answers "OK" without naming them, so the adapter finds
    them by looking at the task list around the request: tasks of this user that are newer than
    the request's mark, of the operation's kind and file, at most as many as the request starts.
    Fewer are returned when the user may not view tasks, when Paperless started none (e.g.
    removing the password of a file that has none), when they did not show up within a short
    bounded time or when reading them failed: the operation was accepted all the same, and none
    of this is raised. Operations are serialised per writer, and requests are never retried (a
    repeat would do the work twice).

    Permissions (probed on 3.4.2): all need the global permission to change documents. Rotating,
    editing, splitting, deleting pages and removing a password also need ownership of every
    document (or no owner; a change grant is not enough). Merging and reprocessing accept a
    change grant. A new document needs the permission to add documents. No method deletes an
    original: the services do that, after they verified what was made (decision-60).
    """

    async def rotate_documents(
        self, document_ids: Sequence[int], degrees: int, *, original_files: bool = False
    ) -> StartedTasks:
        """Rotate every page of the PDFs clockwise by ``degrees`` as a new version of each.

        With ``original_files`` the first file of each document is rotated, not the newest.

        ``degrees`` is a multiple of 90, negative turns counter-clockwise. Each document gets its
        own task; the older version stays.

        Raises:
            PermissionDenied: See the class docstring.
            Validation: An id does not exist (or is hidden), is listed twice or is empty, or the
                degrees are not a multiple of 90.
        """
        ...

    async def merge_documents(
        self,
        document_ids: Sequence[int],
        *,
        metadata_document_id: int | None,
        archive_fallback: bool = False,
        original_files: bool = False,
    ) -> StartedTasks:
        """Merge the PDFs, in this order, into one new document; they stay as they are.

        The new document takes the metadata (title with ' (merged)', tags, ...) of
        ``metadata_document_id`` if given, which must be one of the documents. With
        ``archive_fallback`` the archive PDF stands in for a file that is not a PDF, with
        ``original_files`` the first file of each document is merged, not the newest. A document
        that cannot be merged is left out without an error.

        Raises:
            PermissionDenied: See the class docstring.
            Validation: An id does not exist (or is hidden) or is listed twice.
        """
        ...

    async def edit_pdf(
        self,
        document_id: int,
        pages: Sequence[PageSlot],
        *,
        output: FileOutput,
        version_id: int | None = None,
        keep_metadata: bool = True,
    ) -> StartedTasks:
        """Build files from the pages of one PDF: ``pages`` in order, grouped by their file.

        ``output`` decides where the result goes. ``new_version`` needs a single output file.
        One task per output file. ``version_id`` is the version whose file is read (the root's id
        is its first file); Paperless checks the pages against the document that is sent, so
        without it a newest version with more pages than the first file is refused. New documents
        take the metadata of the document unless ``keep_metadata`` is false.

        Raises:
            PermissionDenied: See the class docstring.
            Validation: A page is beyond the document's last, the document is not a PDF, the
                output files are not numbered 0, 1, 2... without gaps, ``new_version`` has
                several output files, or a rotation is not a multiple of 90.
        """
        ...

    async def remove_password(
        self,
        document_id: int,
        password: str,
        *,
        output: FileOutput,
        version_id: int | None = None,
        keep_metadata: bool = True,
    ) -> StartedTasks:
        """Unlock an encrypted PDF with ``password``; ``output`` decides where the copy goes.

        ``version_id`` and ``keep_metadata`` as for ``edit_pdf``.

        A file without a password makes Paperless start no task.

        Raises:
            PermissionDenied: See the class docstring.
            Validation: The password is wrong, or the document is not a PDF.
        """
        ...

    async def reprocess_documents(
        self, document_ids: Sequence[int], *, remote_ocr: bool
    ) -> StartedTasks:
        """Run OCR and archiving again for every document; one task each.

        Raises:
            PermissionDenied: See the class docstring.
            Validation: An id does not exist or is listed twice.
        """
        ...

    async def find_copies(self, files: CopiedFiles, *, final: bool = False) -> FoundCopies:
        """The documents that have the file names of a request, as far as they exist now.

        For requests whose tasks Paperless does not list (a date among the custom fields that are
        copied). They are the current user's, added since the queued task of the request started,
        and one of each name. A doubt is whatever lets them be another request's: a file too many,
        a listed task for one of the names, another copying operation of the user at the same time
        or later, or an earlier one of the last hour whose files may still come. The last is looked
        into once every file is there; ``final`` asks for it anyway, so that those that are there
        can be used.

        Raises:
            PermissionDenied: The user may not view tasks or documents.
        """
        ...

    async def find_rotations(self, search: RotationSearch, *, final: bool = False) -> FoundCopies:
        """The versions a rotation made of the documents of ``search.hidden``, as they exist now.

        For documents whose tasks Paperless does not list (a date among their custom fields). A
        version is taken only if it is newer than the one shown before, was added since the queued
        task started, has the file name of a rotation of this document, and the history says that
        the current user added it; so what somebody else adds never is. A doubt is whatever lets it
        be another request of the same user's: two such versions, a listed task for the file name
        that was not done before, another rotation at the same time or later, or an earlier one of
        the last hour whose versions are not all there; and a document or history that may not be
        read. History and earlier ones are looked into once every version is there; ``final`` asks
        for it anyway, so that those that are there can be used. ``search`` keeps what does not
        change, so the same one is passed for every look.

        Raises:
            PermissionDenied: The user may not view tasks or documents.
        """
        ...


class SharingReader(Protocol):
    """Share links and share-link bundles the current user may see."""

    async def list_share_links(self) -> tuple[ShareLink, ...]:
        """Every share link the user may see, over all pages, in Paperless order.

        Raises:
            PermissionDenied: The user lacks the global ``view_sharelink`` permission.
            PaperlessMcpError: More links than this server reads at most.
        """
        ...

    async def get_share_link(self, link_id: int) -> ShareLink:
        """One share link.

        Raises:
            NotFound: No such link, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_sharelink`` permission.
        """
        ...

    async def list_share_bundles(self) -> tuple[ShareBundle, ...]:
        """Every share-link bundle the user may see, over all pages, in Paperless order.

        Raises:
            PermissionDenied: The user lacks the global ``view_sharelinkbundle`` permission.
            PaperlessMcpError: More bundles than this server reads at most.
        """
        ...

    async def get_email_enabled(self) -> bool | None:
        """Whether Paperless has a mail server set up (``email_enabled`` of the UI settings).

        None means unknown: the user may not view the UI settings.
        """
        ...

    async def get_share_bundle(self, bundle_id: int) -> ShareBundle:
        """One bundle with its current build status.

        Raises:
            NotFound: No such bundle, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_sharelinkbundle`` permission.
        """
        ...


class SharingWriter(Protocol):
    """Creating and deleting share links and bundles, and sending documents by e-mail."""

    async def create_share_link(
        self, document_id: int, *, expiration: datetime | None, file_version: FileVersionKind
    ) -> ShareLink:
        """Publish the file of a document under a new link that ``expiration`` ends (None: never).

        Raises:
            Validation: No such document, or a rejected value.
            PermissionDenied: The user may not add share links, or may not see the document.
        """
        ...

    async def delete_share_link(self, link_id: int) -> None:
        """Delete a share link; its URL stops working.

        Raises:
            NotFound: No such link, or the user may not see it.
            PermissionDenied: The user may not delete share links, or this one.
        """
        ...

    async def create_share_bundle(
        self,
        document_ids: Sequence[int],
        *,
        expiration_days: int | None,
        file_version: FileVersionKind,
    ) -> ShareBundle:
        """Create a bundle of documents; Paperless builds the archive after this returns.

        The result is usually still ``pending``. ``expiration_days`` counts from now (None: never).

        Raises:
            Validation: No documents, a duplicate or unknown document, one the user may not
                share, or a bad value.
            PermissionDenied: The user may not add bundles.
        """
        ...

    async def rebuild_share_bundle(self, bundle_id: int) -> ShareBundle:
        """Build the archive of a bundle again (for a failed build, or after documents changed).

        The result is ``pending``; the URL stays the same and then serves the documents' current
        files.

        Raises:
            NotFound: No such bundle, or the user may not see it.
            Validation: Paperless refused the rebuild, with its reason.
            PermissionDenied: The user may not change this bundle.
        """
        ...

    async def delete_share_bundle(self, bundle_id: int) -> None:
        """Delete a bundle with its archive; its URL stops working.

        Raises:
            NotFound: No such bundle, or the user may not see it.
            PermissionDenied: The user may not delete bundles, or this one.
        """
        ...

    async def email_documents(
        self,
        document_ids: Sequence[int],
        *,
        addresses: Sequence[str],
        subject: str,
        message: str,
        use_archive_version: bool,
    ) -> None:
        """E-mail the files of the documents as attachments through Paperless's mail server.

        Raises:
            Validation: No documents, an unknown or duplicate document, an invalid address or an
                empty subject or message.
            DeliveryUnknown: Paperless failed while sending, or did not answer (it sends
                synchronously and without a mail server answers 500). Whether anyone received
                the message is unknown; the request is never repeated.
            PermissionDenied: The user may not see a document.
        """
        ...


class MailReader(Protocol):
    """Mail accounts, rules and processed mail the current user may see.

    No method returns a password, token or any mask of one: a ``MailAccount`` has no such field.
    """

    async def list_mail_accounts(self) -> tuple[MailAccount, ...]:
        """Every mail account the user may see, over all pages, in Paperless order (by id).

        Raises:
            PermissionDenied: The user lacks the global ``view_mailaccount`` permission.
            PaperlessMcpError: More accounts than this server reads at most.
        """
        ...

    async def get_mail_account(self, account_id: int) -> MailAccount:
        """One mail account.

        Raises:
            NotFound: No such account, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_mailaccount`` permission.
        """
        ...

    async def list_mail_rules(self) -> tuple[MailRule, ...]:
        """Every mail rule the user may see, over all pages, in Paperless order (by ``order``).

        Raises:
            PermissionDenied: The user lacks the global ``view_mailrule`` permission.
            PaperlessMcpError: More rules than this server reads at most.
        """
        ...

    async def get_mail_rule(self, rule_id: int) -> MailRule:
        """One mail rule.

        Raises:
            NotFound: No such rule, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_mailrule`` permission.
        """
        ...

    async def list_processed_mail(
        self,
        *,
        rule_id: int | None,
        status: ProcessedStatus | None,
        ordering: ProcessedOrdering,
        page: int,
        page_size: int,
    ) -> ProcessedMailPage:
        """One page of the processed mail the user may see.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user lacks the global ``view_processedmail`` permission.
        """
        ...

    async def get_processed_mail(self, mail_id: int) -> ProcessedMail:
        """One mail Paperless handled.

        Raises:
            NotFound: No such record, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_processedmail`` permission.
        """
        ...


class MailWriter(Protocol):
    """Changes to mail accounts and rules, and the two actions that contact the mail server."""

    async def create_mail_account(self, values: MailAccountValues) -> MailAccount:
        """Create an account from ``values`` (name, server, port, username and password are needed).

        Raises:
            Validation: A missing or rejected value.
            PermissionDenied: The user may not add mail accounts.
        """
        ...

    async def update_mail_account(self, account_id: int, values: MailAccountValues) -> MailAccount:
        """Change the fields that are set in ``values``; the password stays unless it is set.

        Raises:
            NotFound: No such account, or the user may not see it.
            Validation: A rejected value, or a password for an account that signs in with an OAuth
                access token (read right before the change; nothing is sent).
            PermissionDenied: The user may not change this account.
        """
        ...

    async def delete_mail_account(self, account_id: int) -> None:
        """Delete an account; Paperless deletes its rules and their processed mail with it.

        Raises:
            NotFound: No such account, or the user may not see it.
            PermissionDenied: The user may not delete this account.
        """
        ...

    async def test_mail_account(self, account_id: int) -> MailAccountTestResult:
        """Connect to the account's mail server and log in with its saved password.

        A failure to connect or to log in is a result with ``success`` false, not an error.
        Nothing is changed on the mail server or in Paperless.

        Raises:
            NotFound: No such account, or the user may not see it.
            PermissionDenied: The user may not change this account (Paperless asks for it).
        """
        ...

    async def process_mail_account(self, account_id: int) -> None:
        """Queue a fetch of the account's mail now, instead of at the next scheduled run.

        Paperless works in the background: documents appear and the rule actions run on the
        mail server after this returns. The request is never repeated.

        Raises:
            NotFound: No such account, or the user may not see it.
            PermissionDenied: The user may not change this account.
        """
        ...

    async def create_mail_rule(self, values: MailRuleValues) -> MailRule:
        """Create a rule from ``values`` (name and account are needed).

        Raises:
            Validation: A missing or rejected value, or an account or taxonomy id that does
                not exist.
            PermissionDenied: The user may not add mail rules.
        """
        ...

    async def update_mail_rule(self, rule_id: int, values: MailRuleValues) -> MailRule:
        """Change the fields that are set in ``values``.

        Raises:
            NotFound: No such rule, or the user may not see it.
            Validation: A rejected value.
            PermissionDenied: The user may not change this rule.
        """
        ...

    async def delete_mail_rule(self, rule_id: int) -> None:
        """Delete a rule; Paperless deletes its processed mail with it.

        Raises:
            NotFound: No such rule, or the user may not see it.
            PermissionDenied: The user may not delete this rule.
        """
        ...


class RawReader(Protocol):
    """Read-only GET of any path under ``/api/`` of Paperless (the raw toolset, decision-25)."""

    async def get(self, path: str, params: Mapping[str, str], *, max_bytes: int) -> RawReply:
        """GET ``path`` (already checked by the caller) and read at most ``max_bytes`` of the body.

        The request goes to the configured Paperless only, with the credential of the current
        user; redirects are not followed. A body longer than ``max_bytes`` is not kept
        (``truncated``).

        Raises:
            Validation: The path is not one the adapter may request, or Paperless rejects the
                request.
            NotFound: No such endpoint or object, or not visible to the user.
            PermissionDenied: The user may not read it.
            PaperlessMcpError: Paperless redirected the request, or answered with an error.
        """
        ...


class WorkflowReader(Protocol):
    """Workflows, with their triggers and actions, as the current user sees them."""

    async def list_workflows(
        self, *, enabled: bool | None = None, trigger_type: int | None = None
    ) -> tuple[Workflow, ...]:
        """Every workflow, over all pages, in Paperless order (by order, then id).

        ``enabled`` and ``trigger_type`` limit it to workflows that are (not) enabled and to those
        with a trigger of that type.

        Raises:
            PermissionDenied: The user lacks the global ``view_workflow`` permission.
            AnswerTooLarge: There are more than can be read.
        """
        ...

    async def get_workflow(self, workflow_id: int) -> Workflow:
        """One workflow.

        Raises:
            NotFound: No such workflow.
            PermissionDenied: The user lacks the global ``view_workflow`` permission.
        """
        ...

    async def get_trigger(self, trigger_id: int) -> WorkflowTrigger:
        """One trigger, also one that no workflow holds.

        Raises:
            NotFound: No such trigger.
            PermissionDenied: The user lacks the global ``view_workflowtrigger`` permission.
        """
        ...

    async def get_action(self, action_id: int) -> WorkflowAction:
        """One action, also one that no workflow holds.

        Raises:
            NotFound: No such action.
            PermissionDenied: The user lacks the global ``view_workflowaction`` permission.
        """
        ...


class WorkflowWriter(Protocol):
    """Changes to workflows."""

    async def create_workflow(self, name: str, values: WorkflowValues) -> Workflow:
        """A new workflow named ``name`` with the settings ``values`` sets.

        ``values.name`` is ignored. Paperless needs both lists; none given means empty ones.

        Raises:
            PermissionDenied: The user may not add workflows.
            Validation: Paperless rejected a value (a blank or taken name, an unknown type, a
                trigger or action that does not fit its type).
        """
        ...

    async def update_workflow(self, workflow_id: int, values: WorkflowValues) -> Workflow:
        """Change the settings ``values`` sets and return the workflow as it is afterwards.

        A list that is given replaces the old one as a whole; triggers and actions are sent
        without their ids, so each is stored as a new object.

        Raises:
            NotFound: No such workflow.
            PermissionDenied: The user may not change workflows.
            Validation: Paperless rejected a value.
        """
        ...

    async def delete_workflow(self, workflow_id: int) -> None:
        """Delete the workflow for good: Paperless has no trash for workflows.

        Raises:
            NotFound: No such workflow.
            PermissionDenied: The user may not delete workflows.
        """
        ...

    async def run_workflow(
        self, workflow_id: int, document_ids: Sequence[int], *, filter_documents: bool
    ) -> WorkflowRun:
        """Run a workflow with a manual trigger on the documents, one after the other.

        With ``filter_documents`` the documents that match none of its manual triggers are skipped;
        Paperless does not tell which. A failure on one document does not stop the others. What its
        e-mails, webhooks and AI actions come to is not told either: they fail in the log only.

        Raises:
            PermissionDenied: The user may not view workflows or change documents, or does not own
                (or may not change) one of the documents. Nothing was run.
            Validation: No such workflow, it is not enabled or has no manual trigger, or a document
                does not exist. Nothing was run.
            TaskFailed: The queued task failed without naming the documents; part may have run.
        """
        ...


class AdminReader(Protocol):
    """Users and groups by id, the application configuration, logs, tasks and bulk download.

    Listing users and groups is ``PermissionReader``'s job; the admin tools resolve names with it.
    """

    async def get_user(self, user_id: int) -> UserRecord:
        """One user, never with a password.

        Raises:
            NotFound: No such user, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_user`` permission.
        """
        ...

    async def get_current_user_id(self) -> int:
        """The id of the user the credential belongs to, from one light request that reads only it.

        Raises:
            PermissionDenied: The user lacks the ``view_uisettings`` permission.
            PaperlessMcpError: Paperless did not answer (any upstream error).
        """
        ...

    async def get_group(self, group_id: int) -> GroupRecord:
        """One group with the permission codenames it grants.

        Raises:
            NotFound: No such group, or the user may not see it.
            PermissionDenied: The user lacks the global ``view_group`` permission.
        """
        ...

    async def get_config(self) -> AppConfigRecord:
        """The application configuration: settings with a value, and which API keys are saved.

        Raises:
            PermissionDenied: The user lacks the ``view_applicationconfiguration`` permission.
            PaperlessMcpError: Paperless has no configuration row.
        """
        ...

    async def list_logs(self) -> tuple[str, ...]:
        """The names of the log files this instance has.

        Raises:
            PermissionDenied: The user is not a staff user.
        """
        ...

    async def get_log(self, name: str, *, limit: int) -> tuple[str, ...]:
        """The last ``limit`` lines of a log, oldest first.

        Raises:
            NotFound: No log of that name.
            PermissionDenied: The user is not a staff user.
        """
        ...

    async def list_tasks(
        self,
        *,
        state: TaskState | None,
        task_type: TaskType | None,
        acknowledged: bool | None,
        page: int,
        page_size: int,
    ) -> TaskRowPage:
        """One page of tasks, newest first, optionally filtered.

        Raises:
            NotFound: ``page`` is beyond the last page.
            PermissionDenied: The user lacks the ``view_paperlesstask`` permission.
        """
        ...

    async def download_documents(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        max_bytes: int,
    ) -> ArchiveFile:
        """The files of the documents as one ZIP archive; at most ``max_bytes`` bytes are read.

        Over the limit the archive is ``truncated`` and has no content. Changes nothing.

        Raises:
            NotFound: Paperless cannot find a file of some documents; it names them.
            PermissionDenied: A document is hidden from the user (the whole request is refused).
            Validation: A document does not exist; no archive is made.
        """
        ...

    def open_archive(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        wait_seconds: float,
    ) -> AbstractAsyncContextManager[OpenedFile]:
        """The same archive as ``download_documents``, handed over chunk by chunk, of any size.

        Paperless builds the whole archive before its first byte; ``wait_seconds`` is the longest
        wait for that byte, and for each later one.

        Raises:
            NotFound: Paperless cannot find a file of some documents; it names them.
            PermissionDenied: A document is hidden from the user (the whole request is refused).
            Validation: A document does not exist; no archive is made.
        """
        ...


class AdminWriter(Protocol):
    """Changes to users, groups, the application configuration and tasks."""

    async def create_user(self, values: UserValues) -> UserRecord:
        """Create a user from the fields set in ``values`` and return it.

        Raises:
            Validation: The username is taken or invalid, a group or permission does not exist,
                or the password is made of asterisks only (nothing is sent).
            PermissionDenied: The user may not add users (or make superusers or staff).
        """
        ...

    async def update_user(self, user_id: int, values: UserValues) -> UserRecord:
        """Write the fields set in ``values`` and return the user.

        Raises:
            NotFound: No such user.
            Validation: As for ``create_user``.
            PermissionDenied: The user may not change users (or this one, or these flags).
        """
        ...

    async def delete_user(self, user_id: int) -> None:
        """Delete a user for good; what it owned loses its owner. Not retried.

        Raises:
            NotFound: No such user.
            PermissionDenied: The user may not delete users.
        """
        ...

    async def create_group(self, values: GroupValues) -> GroupRecord:
        """Create a group from ``values`` and return it.

        Raises:
            Validation: The name is taken or a permission does not exist.
            PermissionDenied: The user may not add groups.
        """
        ...

    async def update_group(self, group_id: int, values: GroupValues) -> GroupRecord:
        """Write the fields set in ``values`` and return the group.

        Raises:
            NotFound: No such group.
            Validation: As for ``create_group``.
            PermissionDenied: The user may not change groups.
        """
        ...

    async def delete_group(self, group_id: int) -> None:
        """Delete a group for good; its members lose what it granted. Not retried.

        Raises:
            NotFound: No such group.
            PermissionDenied: The user may not delete groups.
        """
        ...

    async def update_config(self, values: ConfigValues) -> AppConfigRecord:
        """Write the settings and keys in ``values`` and return the configuration.

        A setting of ``None`` goes back to the server's environment default.

        Raises:
            Validation: Paperless rejects a value, or a key is made of asterisks only.
            PermissionDenied: The user may not view or change the configuration.
        """
        ...

    async def acknowledge_tasks(self, task_ids: Sequence[int]) -> int:
        """Mark tasks (database ids) as seen and return how many were marked.

        Raises:
            Validation: A task does not exist or is listed twice; nothing is marked.
            PermissionDenied: The user lacks the ``change_paperlesstask`` permission.
        """
        ...

    async def run_task(self, task_type: RunnableTask) -> str:
        """Start a background task by hand and return its Celery task id. Never retried.

        Raises:
            Validation: Paperless refuses this task type.
            PermissionDenied: Only a superuser may start tasks.
        """
        ...
