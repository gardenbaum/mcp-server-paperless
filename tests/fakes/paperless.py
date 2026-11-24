"""FakePaperless: an in-memory Paperless that implements the domain ports (design 9.1).

It behaves like 3.5.1, the reference and oldest supported release (decision-43).

Services and tools are tested against it instead of mocks. The contract suite (tests/contract)
runs the same tests against it and against the adapter on live Paperless, so it cannot drift.

- `FakeState` is the instance: users, documents and taxonomy. `seeded()` mirrors the E2E seed
  (tests/e2e/helpers.py `seed`); tests change the state directly to set up a case.
- `FakePaperless(state, user=...)` is one credential: it sees what that user sees in Paperless
  (owner, object-level view grants, superuser and global permissions).
- Write guard: methods of write ports are decorated with `@write` and raise
  `WriteNotAllowedError` unless the fake was created with `allow_writes=True`, so a read-only
  tool that writes fails its own tests. tests/contract/test_coverage.py checks that every
  method of a `*Writer` port is decorated.

Adding a port method: implement it here with Paperless 3.5.1's behaviour (read the view in the
Paperless source of the tag, not the docs alone), extend the state only as far as it needs, and
write its contract tests.
"""

import contextlib
import functools
import re
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from itertools import dropwhile, takewhile
from pathlib import PurePath
from typing import TYPE_CHECKING, Concatenate, Literal, cast

from pydantic import JsonValue

from fakes import mail, raw, sharing, workflows
from fakes.accounts import (
    ADMIN,
    BLIND,
    BLIND_PERMISSIONS,
    PRIVATE_ASN,
    PRIVATE_CONTENT,
    PUBLIC_ASN,
    PUBLIC_NOTE,
    READER,
    READER_PERMISSIONS,
)
from fakes.admin import (
    apply_settings,
    archive_of,
    check_email,
    check_group_name,
    check_permissions,
    check_username,
)
from fakes.custom_field_query import compile_query
from fakes.document_values import coerce, reject, tag_closure
from fakes.duplicates import duplicates_of, rejected_for, root_checksum, sha256
from fakes.mail import (
    FakeMailAccount,
    FakeMailRule,
    FakeProcessedMail,
    MailData,
)
from fakes.mail import Viewer as MailViewer
from fakes.permissions import access_of, check_principals, set_grants
from fakes.sharing import FakeShareBundle, FakeShareLink, SentEmail, SharingData, Viewer
from fakes.taxonomy_rules import check_colour, check_match, check_name, check_path, with_ids
from fakes.view_filters import ONLY_ONE_SEARCH, combines, searches, view_filters
from fakes.view_rules import check_page_size, check_rules
from fakes.workflows import Actor, WorkflowData
from mcp_server_paperless.domain.admin import (
    CONFIG_SECRETS,
    AppConfigRecord,
    ArchiveFile,
    GroupRecord,
    TaskRow,
    TaskRowPage,
    UserRecord,
    check_secret,
)
from mcp_server_paperless.domain.ai import AiSuggestions, ChatAnswer, ChatSource
from mcp_server_paperless.domain.bulk import (
    FieldsChange,
    ObjectPermissions,
    ReferenceChange,
    TagsChange,
    TrashDocuments,
)
from mcp_server_paperless.domain.documents import (
    CustomFieldValue,
    DocumentFile,
    DocumentHit,
    DocumentPage,
    DocumentPatch,
    DocumentQuery,
    DocumentRecord,
    DocumentText,
    DocumentUpload,
    DocumentVersion,
    FileMetadata,
    HistoryEntry,
    Note,
    SignedIn,
    Suggestions,
)
from mcp_server_paperless.domain.duplicates import DuplicateRecord
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    FeatureUnavailable,
    NotFound,
    PermissionDenied,
    UpstreamUnreachable,
    Validation,
)
from mcp_server_paperless.domain.files import OpenedFile, missing_file, missing_files
from mcp_server_paperless.domain.instance import (
    ComponentStatus,
    CurrentUser,
    InstanceOverview,
    InstanceStatistics,
    SystemStatus,
)
from mcp_server_paperless.domain.pdf import CopiedFiles, FoundCopies, RotationSearch, StartedTasks
from mcp_server_paperless.domain.permissions import (
    GroupPage,
    GroupRow,
    ObjectAccess,
    UserPage,
    UserRow,
)
from mcp_server_paperless.domain.selection import DocumentRow
from mcp_server_paperless.domain.tasks import REJECTED, TaskState, TaskStatus, Unconfirmed
from mcp_server_paperless.domain.taxonomy import SelectOption, TaxonomyDetail, TaxonomyItem
from mcp_server_paperless.domain.trash import (
    TrashedDetail,
    TrashedDocument,
    TrashedNote,
    TrashedRecord,
    TrashLookup,
    TrashPage,
)
from mcp_server_paperless.domain.versions import VersionPage, VersionRow
from mcp_server_paperless.domain.views import FilterRule, SavedView
from mcp_server_paperless.domain.workflows import Workflow
from mcp_server_paperless.paperless.admin import (
    ACK_TASKS,
    ADD_GROUP,
    ADD_USER,
    ARCHIVE_FILES,
    CHANGE_CONFIG,
    CHANGE_GROUP,
    CHANGE_USER,
    DELETE_GROUP,
    DELETE_USER,
    DOWNLOAD,
    READ_CONFIG,
    READ_LOGS,
    RUN_TASKS,
    VIEW_GROUP,
    VIEW_TASKS,
    VIEW_USER,
    denied,
    unseen_group,
    unseen_log,
    unseen_user,
)
from mcp_server_paperless.paperless.admin import beyond_last_page as beyond_tasks
from mcp_server_paperless.paperless.bulk import documents_refused, objects_refused
from mcp_server_paperless.paperless.documents import (
    NO_THUMBNAIL_HINT,
    beyond_last_page,
    unseen_document,
    unseen_note,
    unseen_version,
)
from mcp_server_paperless.paperless.errors import ai_disabled
from mcp_server_paperless.paperless.pdf import (
    AT_ONCE,
    NO_HISTORY,
    NO_TIMES,
    NO_USER,
    PENDING,
    SEVERAL,
    TASKED,
    UNREADABLE,
    edit_failed,
    not_editable,
    wrong_password,
)
from mcp_server_paperless.paperless.tasks import not_found
from mcp_server_paperless.paperless.taxonomy import TAG_DELETE_HINT
from mcp_server_paperless.paperless.trash import beyond_last_page as beyond_trash
from mcp_server_paperless.paperless.trash import refused as trash_refused
from mcp_server_paperless.paperless.views import unseen_view

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Collection, Iterable, Mapping, Sequence
    from types import CoroutineType

    from _typeshed import SupportsRichComparison

    from mcp_server_paperless.domain.admin import (
        ArchiveContent,
        Compression,
        ConfigValues,
        GroupValues,
        RunnableTask,
        TaskType,
        UserValues,
    )
    from mcp_server_paperless.domain.bulk import (
        BulkKind,
        DocumentChange,
        PermissionsChange,
    )
    from mcp_server_paperless.domain.documents import FileVersion
    from mcp_server_paperless.domain.mail import (
        MailAccount,
        MailAccountTestResult,
        MailAccountValues,
        MailRule,
        MailRuleValues,
        ProcessedMailPage,
        ProcessedStatus,
    )
    from mcp_server_paperless.domain.pdf import FileOutput, PageSlot
    from mcp_server_paperless.domain.permissions import AccessChange, PermissionKind
    from mcp_server_paperless.domain.raw import RawReply
    from mcp_server_paperless.domain.selection import ReadField
    from mcp_server_paperless.domain.sharing import FileVersionKind, ShareBundle, ShareLink
    from mcp_server_paperless.domain.taxonomy import (
        MatchingAlgorithm,
        TaxonomyKind,
        TaxonomyValues,
        WritableKind,
    )
    from mcp_server_paperless.domain.versions import VersionUpload
    from mcp_server_paperless.domain.views import ViewValues
    from mcp_server_paperless.domain.workflows import WorkflowValues

# Paperless's max_length of document titles and tag names.
MAX_TITLE = 128
# And of version labels.
MAX_LABEL = 64

VERSION = "3.5.1"
API_VERSION = 10

# The types 3.4.2 consumes without Tika or Gotenberg; `post_document` answers 400 for any other.
CONSUMABLE = frozenset(
    {
        *("application/pdf", "text/plain", "text/csv", "text/markdown"),
        *("image/jpeg", "image/png", "image/gif", "image/tiff", "image/bmp", "image/webp"),
    }
)

# A WebP header, as far as clients sniff it: Paperless renders thumbnails as WebP.
THUMBNAIL = b"RIFF\x1a\x00\x00\x00WEBPVP8L fake"

# What the E2E stack reports: a fresh instance without classifier, sanity check or AI.
HEALTHY = SystemStatus(
    install_type="docker",
    storage_total_bytes=50_000_000_000,
    storage_available_bytes=40_000_000_000,
    database=ComponentStatus(status="OK", error=None),
    redis=ComponentStatus(status="OK", error=None),
    celery=ComponentStatus(status="OK", error=None),
    search_index=ComponentStatus(status="OK", error=None),
    classifier=ComponentStatus(status="WARNING", error="No classifier training tasks found"),
    sanity_check=ComponentStatus(status="WARNING", error="No sanity check tasks found"),
    llm_index=ComponentStatus(status="DISABLED", error=None),
)


@dataclass(frozen=True, kw_only=True)
class FakeUser:
    """A Paperless user.

    Attributes:
        permissions: Global permission codenames without the app label, e.g. `view_document`.
    """

    id: int
    username: str
    is_superuser: bool = False
    is_staff: bool = False
    permissions: frozenset[str] = frozenset()
    first_name: str = ""
    last_name: str = ""
    is_active: bool = True
    groups: frozenset[int] = frozenset()
    email: str = ""
    # Whether the user has a password; the fake keeps no password, so it cannot sign in.
    has_password: bool = False
    date_joined: datetime = datetime(2026, 11, 11, 19, 0, tzinfo=UTC)

    def has(self, permission: str) -> bool:
        """Django's `has_perm`: superusers have every permission."""
        return self.is_superuser or permission in self.permissions

    def may_change_object(self, document: Owned) -> bool:
        """Paperless's `user_can_change`: object permissions only, not the global permission.

        Known gap: object-level change grants are not modelled, so a user who does not own a
        document, is not a superuser and has it shared for viewing cannot change it here.
        (A search hit does model them: `Owned.changeable_by`.)
        """
        return self.is_superuser or document.owner in {None, self.id}

    def may_change(self, document: Owned) -> bool:
        """Whether a PATCH of `document` is allowed: `change_document` and the object."""
        return self.has("change_document") and self.may_change_object(document)


@dataclass(kw_only=True)
class Owned:
    """An object with Paperless object permissions.

    Attributes:
        owner: The owning user's id; None means shared, visible to every user.
        viewers: Ids of users with an object-level view grant.
        editors: Ids of users with an object-level change grant.
    """

    id: int
    owner: int | None = None
    viewers: set[int] = field(default_factory=set[int])
    editors: set[int] = field(default_factory=set[int])
    # Group grants are stored but give nobody access: group membership is not modelled.
    view_groups: set[int] = field(default_factory=set[int])
    change_groups: set[int] = field(default_factory=set[int])

    def changeable_by(self, user: FakeUser) -> bool:
        """Paperless's `has_perms_owner_aware(user, "change_document", obj)`."""
        return user.is_superuser or self.owner in {None, user.id} or user.id in self.editors

    def visible_to(self, user: FakeUser) -> bool:
        """Paperless's `permitted_object_ids` (group grants are not modelled yet)."""
        return user.is_superuser or self.owner in {None, user.id} or user.id in self.viewers


@dataclass(kw_only=True)
class FakeTaxonomy(Owned):
    """A correspondent, document type, storage path or saved view.

    Matching is Paperless's default for a new object made by hand (any word, nothing to match).
    """

    name: str
    matching_algorithm: MatchingAlgorithm = "any"
    match: str = ""
    case_insensitive: bool = True


@dataclass(kw_only=True)
class FakeSavedView(FakeTaxonomy):
    """A saved view: filter rules and how the web UI sorts them."""

    filter_rules: tuple[FilterRule, ...] = ()
    sort_field: str | None = None
    sort_reverse: bool = False
    page_size: int | None = None


@dataclass(kw_only=True)
class FakeTag(FakeTaxonomy):
    """A tag; inbox tags are what the statistics count as the inbox."""

    is_inbox: bool = False
    colour: str = "#a6cee3"
    parent: int | None = None


@dataclass(kw_only=True)
class FakeStoragePath(FakeTaxonomy):
    """A storage path with its template."""

    path: str = "{{ title }}"


@dataclass(frozen=True, kw_only=True)
class FakeCustomField:
    """A custom field; Paperless does not give custom fields an owner."""

    id: int
    name: str
    data_type: str
    select_options: tuple[SelectOption, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FakeGroup:
    """A user group and the permission codenames it grants to its members."""

    id: int
    name: str
    permissions: frozenset[str] = frozenset()


SEED_DAY = date(2026, 11, 11)


NO_SUGGESTIONS = Suggestions(
    correspondents=(), tags=(), document_types=(), storage_paths=(), dates=()
)


NO_AI_SUGGESTIONS = AiSuggestions(
    title=None,
    correspondents=(),
    new_correspondents=(),
    tags=(),
    new_tags=(),
    document_types=(),
    new_document_types=(),
    storage_paths=(),
    new_storage_paths=(),
    dates=(),
)
# What Paperless's chat answers when it finds no document text to answer from.
CHAT_NO_CONTENT = "Sorry, I couldn't find any content to answer your question."


# Bytes of a chunk of an opened file; small, so that the files of the tests have several.
CHUNK = 5


def counted_pages(content: bytes) -> int:
    """The pages of a PDF whose objects are not packed, as the tests and PDFium write them."""
    return len(re.findall(rb"/Type\s*/Page\b", content))


@dataclass(frozen=True)
class FakeFile:
    """The file of one version of a document, and the text Paperless read from it."""

    original: bytes
    archive: bytes | None
    content: str
    mime_type: str
    page_count: int | None = None
    # The name it was uploaded under; None: the document's.
    file_name: str | None = None


@dataclass(kw_only=True)
class FakeDocument(Owned):
    """A document.

    Attributes:
        custom_fields: The value of each custom field the document has, by field id, as
            Paperless stores it (a select field stores the option id).
    """

    title: str
    content: str = ""
    created: date = SEED_DAY
    added: datetime = datetime(2026, 11, 11, 19, 47, tzinfo=UTC)
    modified: datetime = datetime(2026, 11, 11, 19, 59, tzinfo=UTC)
    archive_serial_number: int | None = None
    tags: list[int] = field(default_factory=list[int])
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    custom_fields: dict[int, JsonValue] = field(default_factory=dict[int, JsonValue])
    mime_type: str = "application/pdf"
    page_count: int | None = 1
    notes: list[Note] = field(default_factory=list[Note])
    # Newest first, as Paperless sends it; a "create" entry holds the OCR text as `content`.
    history: list[HistoryEntry] = field(default_factory=list[HistoryEntry])
    versions: list[DocumentVersion] = field(default_factory=list[DocumentVersion])
    # The file of every version by version id, filled in when the document gets a second version
    # (the root's file is then kept here); `original`, `archive` and `content` are the newest's.
    version_files: dict[int, FakeFile] = field(default_factory=dict[int, FakeFile])
    suggestions: Suggestions = field(default_factory=lambda: NO_SUGGESTIONS)
    ai_suggestions: AiSuggestions = field(default_factory=lambda: NO_AI_SUGGESTIONS)
    # The name of the uploaded file; the seed's files are named after the title.
    file_name: str | None = None
    # By default every document has a file of its own.
    original: bytes = b""
    # None: Paperless made no archive version (e.g. a plain text document).
    archive: bytes | None = b""
    thumbnail: bytes = THUMBNAIL
    # Set while the document is in the trash (state.trash): when it was deleted.
    deleted_at: datetime | None = None
    # Set on the trash entry of a deleted version: the document it was deleted from.
    root_id: int | None = None
    # Set on such an entry when the version went to the trash together with its document, so
    # that restoring the document brings it back; an earlier deletion stays an entry of its own.
    with_root: bool = False
    # An encrypted PDF: Paperless consumed it without text and with no page count; removing the
    # password gives a file with `unlocked_pages` pages.
    password: str | None = None
    unlocked_pages: int = 1

    def __post_init__(self) -> None:
        """Name the file after the title unless given; a rename keeps the file's name.

        A document made without versions gets its root version, as every Paperless document has.
        """
        self.file_name = self.file_name or f"{self.title}.pdf"
        self.original = self.original or b"%%PDF-1.4 fake original %d" % self.id
        if self.archive is not None:
            self.archive = self.archive or b"%PDF-1.4 fake archive, longer than the original " + (
                b"%d" % self.id
            )
        if not self.versions:
            self.versions = [_root_version(self.id, self.original)]

    @property
    def original_filename(self) -> str:
        """The uploaded file's name; the seed's files are named after the title."""
        return self.file_name or f"{self.title}.pdf"

    def download_filename(self, *, archive: bool) -> str:
        """The name Paperless proposes for a download: the created date, then the title.

        The archive version is always a PDF; the original keeps the upload's extension.
        """
        extension = ".pdf" if archive else PurePath(self.original_filename).suffix
        return f"{self.created.isoformat()} {self.title}{extension}"

    def shown_file(self) -> FakeFile:
        """The file the document shows (its newest version's)."""
        return FakeFile(self.original, self.archive, self.content, self.mime_type, self.page_count)


@dataclass
class FakeCopying:
    """A queued operation that made files, and what the names of new documents start with."""

    request: str
    owner: int | None
    started: datetime
    family: str
    count: int = 1

    @property
    def operation(self) -> str:
        """The operation, which its file names tell."""
        kinds = {
            "_merged.pdf": "merge",
            "_unprotected.pdf": "remove_password",
            "_rotated.pdf": "rotate",
        }
        return next((v for k, v in kinds.items() if self.family.endswith(k)), "edit_pdf")


@dataclass(kw_only=True)
class FakeTask:
    """A background task, as `/api/tasks/` reports it.

    Not owner-filtered: whether Paperless hides another owner's task from a user who may view
    tasks is not verified on 3.4.2 (the seed has no such user).

    Attributes:
        task_id: The Celery UUID clients look tasks up by.
        message: The error message of a failed task (its traceback is not modelled).
        document_ids: The task's related documents.
        duplicate_of: What Paperless rejected the file for (`FakeState.delete_duplicates`).
    """

    id: int
    task_id: str
    task_type: str = "consume_file"
    status: TaskState = "pending"
    created: datetime = datetime(2026, 11, 11, 19, 47, tzinfo=UTC)
    finished: datetime | None = None
    message: str | None = None
    document_ids: tuple[int, ...] = ()
    file_name: str = ""
    duplicate_of: int | None = None
    trigger_source: str = "api_upload"
    acknowledged: bool = False
    started: datetime | None = None
    duration_seconds: float | None = None


@dataclass(kw_only=True)
class FakeState:
    """The data of one fake Paperless instance, shared by every `FakePaperless` on it."""

    users: list[FakeUser] = field(default_factory=list[FakeUser])
    documents: list[FakeDocument] = field(default_factory=list[FakeDocument])
    # The files Paperless cannot find, by version id, and the versions without file names.
    missing: set[tuple[int, Literal["original", "archive", "thumbnail"]]] = field(
        default_factory=set[tuple[int, Literal["original", "archive", "thumbnail"]]]
    )
    nameless: set[int] = field(default_factory=set[int])
    tags: list[FakeTag] = field(default_factory=list[FakeTag])
    correspondents: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    document_types: list[FakeTaxonomy] = field(default_factory=list[FakeTaxonomy])
    storage_paths: list[FakeStoragePath] = field(default_factory=list[FakeStoragePath])
    custom_fields: list[FakeCustomField] = field(default_factory=list[FakeCustomField])
    # PAPERLESS_CONSUMER_DELETE_DUPLICATES: a file any document or version has fails its task.
    delete_duplicates: bool = False
    saved_views: list[FakeSavedView] = field(default_factory=list[FakeSavedView])
    groups: list[FakeGroup] = field(default_factory=list[FakeGroup])
    share_links: list[FakeShareLink] = field(default_factory=list[FakeShareLink])
    share_bundles: list[FakeShareBundle] = field(default_factory=list[FakeShareBundle])
    # The e-mails sent, oldest first. Like the E2E stack, the fake has no mail server until a test
    # sets `email_enabled`; a valid message then fails like 3.4.2 does without one.
    outbox: list[SentEmail] = field(default_factory=list[SentEmail])
    email_enabled: bool = False
    mail_accounts: list[FakeMailAccount] = field(default_factory=list[FakeMailAccount])
    mail_rules: list[FakeMailRule] = field(default_factory=list[FakeMailRule])
    processed_mail: list[FakeProcessedMail] = field(default_factory=list[FakeProcessedMail])
    # The imaginary mail servers (see fakes/mail.py): working logins as (server, port, username,
    # password), hosts that answer, hosts that accept a connection and never answer, and the ids
    # of the accounts a fetch was queued for, in order.
    mail_logins: set[tuple[str, int, str, str]] = field(
        default_factory=set[tuple[str, int, str, str]]
    )
    mail_hosts: set[str] = field(default_factory=set[str])
    mail_silent_hosts: set[str] = field(default_factory=set[str])
    mail_fetches: list[int] = field(default_factory=list[int])
    workflows: list[Workflow] = field(default_factory=list[Workflow])
    # The last id given to a workflow's trigger or action (a one-item list, shared by reference).
    workflow_object_id: list[int] = field(default_factory=lambda: [0])
    tasks: list[FakeTask] = field(default_factory=list[FakeTask])
    copying: list[FakeCopying] = field(default_factory=list[FakeCopying])
    # Deleted documents, newest last; reads no longer see them, the Paperless trash does.
    trash: list[FakeDocument] = field(default_factory=list[FakeDocument])
    # Ids that restoring or emptying silently leaves in the trash (Paperless logs the error of an
    # emptied entry and answers OK).
    stuck: set[int] = field(default_factory=set[int])
    # The ids of the documents a bulk reprocess queued, in order (the fake does no OCR).
    reprocessed: list[int] = field(default_factory=list[int])
    # An opened file breaks off before its chunk of this number (None: never).
    break_after: int | None = None
    # The id of the last note created: note ids are global and never reused.
    last_note_id: int = 1
    system_status: SystemStatus = HEALTHY
    ai_enabled: bool = False
    audit_log_enabled: bool = True
    # The application configuration (fakes/admin.py): the settings with a value, the API keys
    # that hold one (their values are not kept), and the environment variables Paperless lists.
    config: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    config_keys: set[str] = field(default_factory=set[str])
    config_external: tuple[str, ...] = ("PAPERLESS_OCR_LANGUAGE",)
    # The log files and their lines, oldest first.
    logs: dict[str, list[str]] = field(
        default_factory=lambda: {
            "paperless": ["[2026-11-11 19:47:00,001] [INFO] [paperless.management] started"],
            "mail": [],
            "celery": [],
        }
    )


# The seeded custom fields in creation order (tests/e2e/helpers.py `seed`), `select` comes last.
CUSTOM_FIELD_TYPES = (
    *("string", "url", "date", "boolean", "integer", "float", "monetary"),
    *("documentlink", "longtext"),
)
# The global permission Paperless requires to list each kind (PaperlessObjectPermissions).
VIEW_PERMISSION: dict[TaxonomyKind, str] = {
    "tag": "view_tag",
    "correspondent": "view_correspondent",
    "document_type": "view_documenttype",
    "storage_path": "view_storagepath",
    "custom_field": "view_customfield",
    "saved_view": "view_savedview",
    "user": "view_user",
    "group": "view_group",
}


# The seeded public document's custom field values by data type (tests/e2e/helpers.py `seed`).
SEED_VALUES: dict[str, JsonValue] = {
    "string": "hello",
    "url": "https://example.com/e2e",
    "date": "2026-01-15",
    "boolean": True,
    "integer": 42,
    "float": 3.25,
    "monetary": "EUR12.50",
    "documentlink": [2],
    "longtext": "line one\nline two",
    "select": "opt-alpha",
}


def _created(document_id: int, title: str, content: str) -> HistoryEntry:
    """The audit log entry Paperless writes when it consumes a document (it holds the text)."""
    return HistoryEntry(
        id=document_id * 10,
        timestamp=datetime(2026, 11, 11, 19, 47, tzinfo=UTC),
        action="create",
        actor="admin",
        changes={"title": ["None", title], "content": ["None", content]},
    )


def _root_version(document_id: int, original: bytes) -> DocumentVersion:
    return DocumentVersion(
        id=document_id,
        added=datetime(2026, 11, 11, 19, 47, tzinfo=UTC),
        version_label=None,
        is_root=True,
        checksum=sha256(original),
    )


def seeded() -> FakeState:
    """A fresh state with the objects and users of the E2E seed."""
    admin = FakeUser(id=2, username=ADMIN[0], is_superuser=True, is_staff=True)
    reader = FakeUser(id=3, username=READER[0], permissions=frozenset(READER_PERMISSIONS))
    blind = FakeUser(id=4, username=BLIND[0], permissions=frozenset(BLIND_PERMISSIONS))
    fields = [
        FakeCustomField(id=number, name=f"e2e-{data_type}", data_type=data_type)
        for number, data_type in enumerate(CUSTOM_FIELD_TYPES, start=1)
    ]
    fields.append(
        FakeCustomField(
            id=len(fields) + 1,
            name="e2e-select",
            data_type="select",
            select_options=tuple(
                SelectOption(id=f"opt-{label}", label=label) for label in ("alpha", "beta")
            ),
        )
    )
    return FakeState(
        users=[admin, reader, blind],
        tags=[
            FakeTag(id=1, name="e2e-finance"),
            FakeTag(id=2, name="e2e-invoices", parent=1),
            FakeTag(id=3, name="e2e-private", owner=admin.id),
            FakeTag(id=4, name="e2e-inbox", is_inbox=True),
        ],
        correspondents=[FakeTaxonomy(id=1, name="e2e-acme")],
        document_types=[FakeTaxonomy(id=1, name="e2e-invoice")],
        storage_paths=[
            FakeStoragePath(id=1, name="e2e-archive", path="e2e/{{ created_year }}/{{ title }}")
        ],
        custom_fields=fields,
        saved_views=[
            FakeSavedView(
                id=1,
                name="e2e-view",
                owner=admin.id,
                filter_rules=(FilterRule(rule_type=6, value="1"),),  # 6: has tag e2e-finance
            )
        ],
        groups=[FakeGroup(id=1, name="e2e-group")],
        documents=[
            FakeDocument(
                id=1,
                title="e2e-public-invoice",
                content="E2E public invoice ACME 2026",
                tags=[1, 2, 4],
                correspondent=1,
                document_type=1,
                storage_path=1,
                archive_serial_number=PUBLIC_ASN,
                custom_fields={f.id: SEED_VALUES[f.data_type] for f in fields},
                notes=[
                    Note(
                        id=1,
                        note=PUBLIC_NOTE,
                        created=datetime(2026, 11, 11, 20, 0, tzinfo=UTC),
                        user="admin",
                    )
                ],
                history=[
                    HistoryEntry(
                        id=15,
                        timestamp=datetime(2026, 11, 11, 20, 0, tzinfo=UTC),
                        action="update",
                        actor=None,
                        changes={"Note Added": ["None", 1]},
                    ),
                    _created(1, "e2e-public-invoice", "E2E public invoice ACME 2026"),
                ],
            ),
            FakeDocument(
                id=2,
                title="e2e-private-note",
                content=PRIVATE_CONTENT,
                owner=admin.id,
                archive_serial_number=PRIVATE_ASN,
                tags=[3],
                custom_fields={fields[7].id: [1]},  # the mirror of the public document's link
                history=[_created(2, "e2e-private-note", PRIVATE_CONTENT)],
            ),
        ],
    )


def _label(obj: FakeDocument | FakeTaxonomy) -> str:
    """The title of a document, else the name."""
    return obj.title if isinstance(obj, FakeDocument) else obj.name


class WriteNotAllowedError(AssertionError):
    """A test wrote to the fake without opting in with `allow_writes=True`."""


_WRITES: set[object] = set()


def write[F: FakePaperless, **P, R](
    method: Callable[Concatenate[F, P], CoroutineType[object, object, R]],
) -> Callable[Concatenate[F, P], CoroutineType[object, object, R]]:
    """Mark a fake method as a write: it fails unless the fake allows writes."""

    @functools.wraps(method)
    async def guarded(self: F, /, *args: P.args, **kwargs: P.kwargs) -> R:
        if not self.allow_writes:
            message = (
                f"{method.__qualname__} writes to Paperless; create the fake with "
                "allow_writes=True in tests that mean to write."
            )
            raise WriteNotAllowedError(message)
        return await method(self, *args, **kwargs)

    _WRITES.add(guarded)
    return guarded


def is_write(method: object) -> bool:
    """Whether `method` carries the write guard."""
    return method in _WRITES


class FakePaperless:
    """The ports as one Paperless user sees them, on an in-memory instance."""

    def __init__(self, state: FakeState, *, user: str, allow_writes: bool = False) -> None:
        """Act as the user named `user` of `state`.

        Raises:
            LookupError: `state` has no such user (a mistake in the test setup).
        """
        self.state: FakeState = state
        found = [candidate for candidate in state.users if candidate.username == user]
        if not found:
            message = f"the fake has no user {user!r}"
            raise LookupError(message)
        self.user: FakeUser = found[0]
        self.allow_writes: bool = allow_writes

    async def get_overview(self) -> InstanceOverview:
        """`InstanceReader.get_overview` (StatisticsView, SystemStatusView, UiSettingsView)."""
        user = self.user
        may_view_status = user.is_staff or user.has("view_system_monitoring")
        may_view_settings = user.has("view_uisettings")
        return InstanceOverview(
            paperless_version=VERSION,
            api_version=API_VERSION,
            statistics=self._statistics(),
            system_status=self.state.system_status if may_view_status else None,
            current_user=(
                CurrentUser(id=user.id, username=user.username, is_superuser=user.is_superuser)
                if may_view_settings
                else None
            ),
            ai_enabled=self.state.ai_enabled if may_view_settings else None,
        )

    def _statistics(self) -> InstanceStatistics:
        everything = self.user.has("view_global_statistics")

        def visible[O: Owned](objects: list[O]) -> list[O]:
            return [o for o in objects if everything or o.visible_to(self.user)]

        documents = visible(self.state.documents)
        inbox = {tag.id for tag in visible(self.state.tags) if tag.is_inbox}
        return InstanceStatistics(
            documents=len(documents),
            inbox_documents=(
                sum(1 for document in documents if inbox & set(document.tags)) if inbox else None
            ),
            tags=len(visible(self.state.tags)),
            correspondents=len(visible(self.state.correspondents)),
            document_types=len(visible(self.state.document_types)),
            storage_paths=len(visible(self.state.storage_paths)),
        )

    async def list_taxonomy(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        """`TaxonomyReader.list_taxonomy` (the taxonomy, user and group viewsets).

        Paperless orders by name and counts only the documents the user may see.
        """
        if not self.user.has(VIEW_PERMISSION[kind]):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        rows = self._rows(kind)
        return sorted(rows, key=lambda row: (row.name.lower(), row.id))

    def _rows(self, kind: TaxonomyKind) -> list[TaxonomyItem]:
        state, user = self.state, self.user
        documents = [d for d in state.documents if d.visible_to(user)]

        def count(uses: Callable[[FakeDocument], bool]) -> int:
            return sum(1 for document in documents if uses(document))

        def tags() -> list[TaxonomyItem]:
            return [
                TaxonomyItem(
                    id=t.id,
                    name=t.name,
                    document_count=count(lambda d, t=t: t.id in d.tags),
                    colour=t.colour,
                    parent=t.parent,
                )
                for t in state.tags
                if t.visible_to(user)
            ]

        def owned(
            objects: Sequence[FakeTaxonomy], link: Callable[[FakeDocument], int | None]
        ) -> Callable[[], list[TaxonomyItem]]:
            return lambda: [
                TaxonomyItem(
                    id=o.id, name=o.name, document_count=count(lambda d, o=o: link(d) == o.id)
                )
                for o in objects
                if o.visible_to(user)
            ]

        def custom_fields() -> list[TaxonomyItem]:
            return [
                TaxonomyItem(
                    id=f.id,
                    name=f.name,
                    document_count=count(lambda d, f=f: f.id in d.custom_fields),
                    data_type=f.data_type,
                    select_options=f.select_options if f.data_type == "select" else None,
                )
                for f in state.custom_fields
            ]

        def names(rows: list[tuple[int, str]]) -> Callable[[], list[TaxonomyItem]]:
            return lambda: [TaxonomyItem(id=i, name=n, document_count=None) for i, n in rows]

        views = [(v.id, v.name) for v in state.saved_views if v.visible_to(user)]
        kinds: dict[TaxonomyKind, Callable[[], list[TaxonomyItem]]] = {
            "tag": tags,
            "correspondent": owned(state.correspondents, lambda d: d.correspondent),
            "document_type": owned(state.document_types, lambda d: d.document_type),
            "storage_path": owned(state.storage_paths, lambda d: d.storage_path),
            "custom_field": custom_fields,
            "saved_view": names(views),
            "user": names([(u.id, u.username) for u in state.users]),
            "group": names([(g.id, g.name) for g in state.groups]),
        }
        return kinds[kind]()

    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        """`DocumentReader.search_documents` (UnifiedSearchViewSet, DocumentFilterSet).

        Full text matches whole words of the title and content, all terms required (Tantivy
        also searches notes, names and fields and supports its query syntax); more-like finds
        visible documents sharing a word of the content. Without full text Paperless orders
        newest first.

        Raises:
            Validation: Both `query` and `more_like`, or a bad `custom_field_query`.
            NotFound: `page` is beyond the last page.
            PermissionDenied: The user lacks the global `view_document` permission (HTTP 403).
        """
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not combines(searches(query)):
            message = f"Paperless rejected the request: {ONLY_ONE_SEARCH}"
            raise Validation(message)
        documents = [d for d in self.state.documents if d.visible_to(self.user)]
        matches: list[_Match] = [
            *_filters(query, self.state),
            *view_filters(query, self.state),
            *self._duplicate_filters(query),
        ]
        if query.custom_field_query is not None:
            test = compile_query(query.custom_field_query, self.state.custom_fields)
            matches.append(lambda d: test(d.custom_fields))
        terms = _words(query.query or "") - TANTIVY_OPERATORS
        if query.query is not None:
            matches.append(lambda d: terms <= _words(f"{d.title} {d.content}"))
        if query.text is not None:
            matches.append(lambda d: _words(query.text or "") <= _words(f"{d.title} {d.content}"))
        if query.title_search is not None:
            matches.append(lambda d: _words(query.title_search or "") <= _words(d.title))
        if query.more_like is not None:
            like = [d for d in documents if d.id == query.more_like]
            if not like:
                raise unseen_document(query.more_like)
            words = _words(like[0].content)
            matches.append(lambda d: d.id != query.more_like and bool(words & _words(d.content)))
        found = [d for d in documents if all(match(d) for match in matches)]
        ranked = query.query or query.more_like or query.text or query.title_search
        found = _ordered(found, query.ordering, ranked=ranked)
        start = (page - 1) * page_size
        if page > 1 and start >= len(found):
            raise beyond_last_page(page)
        hits = tuple(
            _hit(d, self.user, _highlight(d.content, terms) if query.query is not None else None)
            for d in found[start : start + page_size]
        )
        return DocumentPage(
            count=len(found),
            next_page=page + 1 if start + page_size < len(found) else None,
            hits=hits,
        )

    async def select_documents(
        self, query: DocumentQuery, *, limit: int, max_bytes: int
    ) -> DocumentPage:
        """`DocumentReader.select_documents`: one large page of a search, of a limited size."""
        page = await self.search_documents(query, page=1, page_size=limit)
        _bounded(page.model_dump_json(), max_bytes)
        return page

    async def read_rows(
        self, document_ids: Sequence[int], fields: Sequence[ReadField], *, max_bytes: int
    ) -> tuple[DocumentRow, ...]:
        """`DocumentReader.read_rows` (the list view filtered by `id__in`, `fields` named)."""
        seen = await self.get_document_titles(document_ids)
        rows = tuple(
            DocumentRow.model_validate(
                {"id": d.id}
                | {name: value for name, value in self._row_values(d).items() if name in fields}
            )
            for d in self.state.documents
            if d.id in seen
        )
        _bounded("".join(row.model_dump_json() for row in rows), max_bytes)
        return rows

    def _row_values(self, document: FakeDocument) -> dict[str, object]:
        record = _record(document, self.user)
        return {
            **record.model_dump(exclude={"notes_count", "versions"}),
            "notes": tuple(document.notes),
            "version_count": len(document.versions),
        }

    async def get_task(self, task_id: str) -> TaskStatus:
        """`TaskReader.get_task` (TaskViewSet filtered by `task_id`).

        Raises:
            NotFound: No visible task has that id (Paperless answers an empty list).
            PermissionDenied: The user lacks the global `view_paperlesstask` permission.
        """
        if not self.user.has("view_paperlesstask"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        for task in self.state.tasks:
            if task.task_id == task_id:
                return TaskStatus(
                    task_id=task.task_id,
                    task_type=task.task_type,
                    state=task.status,
                    message=task.message if task.duplicate_of is None else REJECTED,
                    document_ids=task.document_ids,
                    duplicate_of=task.duplicate_of,
                    created=task.created,
                    finished=task.finished,
                )
        raise not_found(task_id)

    def _duplicates(self, document: FakeDocument) -> list[FakeDocument]:
        """What `duplicate_documents` and `has_duplicates` compare with: the trash included."""
        everything = (*self.state.documents, *self.state.trash)
        return duplicates_of(document, [d for d in everything if d.visible_to(self.user)])

    def _duplicate_filters(self, query: DocumentQuery) -> list[_Match]:
        """`checksum__iexact` and `has_duplicates`."""
        checksum = (query.checksum or "").lower()
        checks: list[tuple[object, _Match]] = [
            (query.checksum, lambda d: root_checksum(d) == checksum),
            (
                query.has_duplicates,
                lambda d: bool(self._duplicates(d)) == query.has_duplicates,
            ),
        ]
        return [match for value, match in checks if value is not None]

    async def get_root(self, document_id: int) -> int:
        """`DocumentReader.get_root` (the `root` action: 404 if missing, 403 if hidden)."""
        for document in (*self.state.documents, *self.state.trash):
            if document_id in {document.id, *(v.id for v in document.versions)}:
                break
        else:
            raise unseen_document(document_id)
        if not (self.user.has("view_document") and document.visible_to(self.user)):
            raise unseen_document(document_id)
        return document.root_id or document.id

    async def document_exists(self, document_id: int) -> bool:
        """`DocumentReader.document_exists` (the `root` action: 404 only for a missing one)."""
        return any(
            document_id in {d.id, *(v.id for v in d.versions)}
            for d in (*self.state.documents, *self.state.trash)
        )

    async def get_duplicates(self, document_id: int) -> tuple[DuplicateRecord, ...]:
        """`DocumentReader.get_duplicates` (the detail view, `duplicate_documents`)."""
        return tuple(
            DuplicateRecord(id=d.id, title=d.title, deleted_at=d.deleted_at)
            for d in self._duplicates(self._seen(document_id))
        )

    async def get_version_checksum(self, document_id: int, version_id: int) -> str | None:
        """`DocumentReader.get_version_checksum` (the `metadata` action)."""
        return sha256(self._file_of(self._sub(document_id), version_id).original)

    async def list_versions(
        self, *, page: int, page_size: int, recent: bool = False
    ) -> VersionPage:
        """`DocumentReader.list_versions` (the list view, ordered by `-id` or `-modified,-id`).

        Raises:
            NotFound: `page` is beyond the last page.
            PermissionDenied: The user lacks the global `view_document` permission.
        """
        if not self.user.has("view_document"):
            raise self._denied()
        seen = sorted(
            (d for d in self.state.documents if d.visible_to(self.user)),
            key=lambda d: (d.modified if recent else datetime.min.replace(tzinfo=UTC), d.id),
            reverse=True,
        )
        try:
            shown, following = self._page(tuple(seen), page, page_size)
        except NotFound:
            raise beyond_last_page(page) from None
        return VersionPage(
            count=len(seen),
            next_page=following,
            rows=tuple(
                VersionRow(id=d.id, title=d.title, versions=tuple(d.versions)) for d in shown
            ),
        )

    def _document(self, document_id: int) -> FakeDocument:
        """The document if it exists (`NotFound` otherwise); callers check visibility."""
        found = [d for d in self.state.documents if d.id == document_id]
        if not found:
            raise unseen_document(document_id)
        return found[0]

    def _seen(self, document_id: int) -> FakeDocument:
        """The document for the detail view: a hidden document looks like a missing one (404)."""
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        document = self._document(document_id)
        if not document.visible_to(self.user):
            raise unseen_document(document_id)
        return document

    def _sub(self, document_id: int, *permissions: str, trashed: bool = False) -> FakeDocument:
        """The document for a sub-resource; with ``trashed`` also one in the trash.

        The global permission is checked first (403), then existence (404); a hidden document
        is 403, which differs from the detail view.
        """
        for needed in ("view_document", *permissions):
            if not self.user.has(needed):
                message = "The Paperless user may not perform this action."
                raise PermissionDenied(message)
        deleted = [d for d in self.state.trash if d.id == document_id and trashed]
        document = deleted[0] if deleted else self._document(document_id)
        roots = [d for d in self.state.documents if d.id == document.root_id]
        if not (roots[0] if roots else document).visible_to(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return document

    async def get_document(self, document_id: int) -> DocumentRecord:
        """`DocumentReader.get_document` (DocumentViewSet.retrieve, `fields=` without content)."""
        return _record(self._seen(document_id), self.user)

    @write
    async def update_document(self, document_id: int, patch: DocumentPatch) -> DocumentRecord:
        """`DocumentWriter.update_document` (DocumentViewSet.partial_update).

        Order in Paperless 3.4.2: the global `change_document` permission (403), then the
        document among those the user may see (404), then object permission (403: an owner or
        a superuser may change it, a viewer may not). Lists are replaced as a whole.

        Raises:
            Validation: Paperless's 400 for a value, with its message.
        """
        if not self.user.has("change_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        document = self._seen(document_id)
        if not self.user.may_change(document):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        changes = self._changes(document, patch)
        if "custom_fields" in changes:
            self._mirror_links(document, cast("dict[int, JsonValue]", changes["custom_fields"]))
        document.__dict__.update(changes)
        document.modified = datetime.now(UTC)
        return _record(document, self.user, report_change=False)  # an update's answer omits it

    @write
    async def set_document_text(self, document_id: int, version_id: int, content: str) -> str:
        """`DocumentWriter.set_document_text` (DocumentViewSet.partial_update with `version`).

        Order: the global `change_document` permission (403), the document among those the user
        may see (404), the object (403), the version (404; one in the trash is written, too).
        The text of the root passes the serializer, which rejects NUL characters and lone
        surrogates and strips. (The adapter cannot send a surrogate: its body is UTF-8.)
        """
        if not self.user.has("change_document"):
            raise self._denied()
        document = self._seen(document_id)
        if not document.changeable_by(self.user):
            raise self._denied()
        listed = [v.id for v in document.versions]
        stubs = [d for d in self.state.trash if d.id == version_id and d.root_id == document_id]
        if version_id not in listed and not stubs:
            raise unseen_version(document_id, version_id)
        if version_id == document_id:
            if "\x00" in content:
                reject("content", "Null characters are not allowed.")
            surrogates = [c for c in content if "\ud800" <= c <= "\udfff"]
            if surrogates:
                points = ", ".join(f"U+{ord(c):X}" for c in surrogates)
                reject("content", f"Surrogate characters are not allowed: {points}.")
            content = content.strip()
        for stub in stubs:
            stub.content = content
        if version_id in document.version_files:
            file = document.version_files[version_id]
            document.version_files[version_id] = replace(file, content=content)
        if version_id == listed[0]:
            document.content = content
        document.modified = datetime.now(UTC)
        return content

    @write
    async def upload_document(self, upload: DocumentUpload) -> str:
        """`DocumentWriter.upload_document` (PostDocumentView): the form is checked, then a task.

        Consumption is immediate here. Probed on 3.4.2: a file type it cannot consume, an
        unknown id and a custom field value of the wrong type are 400s; a taken archive serial
        number only fails the task; the same content twice makes two documents; the new
        document is owned by the uploader, gets the parents of its tags and the inbox tags.

        Raises:
            PermissionDenied: The user lacks the global `add_document` permission.
            Validation: Paperless's 400 for the form, with its message.
        """
        if not self.user.has("add_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if upload.mime_type not in CONSUMABLE:
            reject("document", f"File type {upload.mime_type} not supported")
        state = self.state
        for name, known in (
            ("correspondent", state.correspondents),
            ("document_type", state.document_types),
            ("storage_path", state.storage_paths),
        ):
            value = cast("int | None", getattr(upload, name))
            if value is not None and value not in {o.id for o in known}:
                reject(name, f'Invalid pk "{value}" - object does not exist.')
        tags = {t.id: t for t in state.tags}
        for tag in upload.tags:
            if tag not in tags:
                reject("tags", f'Invalid pk "{tag}" - object does not exist.')
        values = self._values(upload)
        now = datetime.now(UTC)
        task = FakeTask(
            id=len(state.tasks) + 1, task_id=str(uuid.uuid4()), created=now, finished=now
        )
        state.tasks.append(task)
        if self._rejected(upload.content, task):
            return task.task_id
        taken = upload.archive_serial_number is not None and any(
            d.archive_serial_number == upload.archive_serial_number for d in state.documents
        )
        if taken:
            task.status = "failure"
            task.message = (
                f"{upload.filename}: Not consuming {upload.filename}: "
                f"Given ASN {upload.archive_serial_number} already exists!"
            )
            return task.task_id
        number = self._next_id()
        title = upload.title or PurePath(upload.filename).stem
        text = (
            upload.content.decode(errors="replace") if upload.mime_type.startswith("text/") else ""
        )
        inbox = [t.id for t in state.tags if t.is_inbox]
        state.documents.append(
            FakeDocument(
                id=number,
                title=title,
                content=text,
                created=upload.created or now.date(),
                added=now,
                modified=now,
                owner=self.user.id,
                archive_serial_number=upload.archive_serial_number,
                tags=tag_closure([], [*upload.tags, *inbox], tags),
                correspondent=upload.correspondent,
                document_type=upload.document_type,
                storage_path=upload.storage_path,
                custom_fields=values,
                mime_type=upload.mime_type,
                page_count=counted_pages(upload.content) or 1,
                file_name=upload.filename,
                original=upload.content,
                archive=upload.content + b"\n%archive"
                if upload.mime_type == "application/pdf"
                else None,
                history=[_created(number, title, text)],
                versions=[_root_version(number, upload.content)],
            )
        )
        task.status = "success"
        task.document_ids = (number,)
        return task.task_id

    def _rejected(self, content: bytes, task: FakeTask) -> bool:
        """Fail the task if duplicates are rejected and any user's document has the file."""
        if not self.state.delete_duplicates:
            return False
        task.duplicate_of = rejected_for(content, self.state.documents, self.state.trash)
        if task.duplicate_of is None:
            return False
        task.status = "failure"
        return True

    def _values(self, upload: DocumentUpload) -> dict[int, JsonValue]:
        """The custom field values of an upload, checked like the form serializer does."""
        fields = {f.id: f for f in self.state.custom_fields}
        documents = {d.id for d in self.state.documents}
        stored: dict[int, JsonValue] = {}
        for item in upload.custom_fields:
            if item.field not in fields:
                reject("custom_fields", f'Invalid pk "{item.field}" - object does not exist.')
            try:
                stored[item.field] = coerce(fields[item.field], item.value, documents)
            except ValueError as error:
                reject("custom_fields", str(error))
        return stored

    def _changes(self, document: FakeDocument, patch: DocumentPatch) -> dict[str, object]:
        """The attributes `patch` sets, validated like the serializer; nothing is applied."""
        state = self.state
        exists: dict[str, set[int]] = {
            "correspondent": {o.id for o in state.correspondents},
            "document_type": {o.id for o in state.document_types},
            "storage_path": {o.id for o in state.storage_paths},
            "owner": {u.id for u in state.users},
        }
        changes: dict[str, object] = {}
        for name in patch.model_fields_set - {"tags", "custom_fields"}:
            value = cast("object", getattr(patch, name))
            if name in exists and value is not None and value not in exists[name]:
                reject(name, f'Invalid pk "{value}" - object does not exist.')
            changes[name] = value
        self._check_title_and_number(document, changes)
        if patch.tags is not None:
            known = {t.id: t for t in state.tags}
            for tag in patch.tags:
                if tag not in known:
                    reject("tags", f'Invalid pk "{tag}" - object does not exist.')
            changes["tags"] = tag_closure(document.tags, list(patch.tags), known)
        if patch.custom_fields is not None:
            changes["custom_fields"] = self._custom_fields(patch)
        return changes

    def _check_title_and_number(self, document: FakeDocument, changes: dict[str, object]) -> None:
        title = changes.get("title")
        if isinstance(title, str) and len(title) > MAX_TITLE:
            reject("title", f"Ensure this field has no more than {MAX_TITLE} characters.")
        number = changes.get("archive_serial_number")
        if isinstance(number, int):
            if number < 0:
                reject("archive_serial_number", "Ensure this value is greater than or equal to 0.")
            taken = (
                d.archive_serial_number == number and d is not document
                for d in self.state.documents
            )
            if any(taken):
                message = "document with this archive serial number already exists."
                reject("archive_serial_number", message)
            if any(d.archive_serial_number == number for d in self.state.trash):
                message = "Document with this Archive Serial Number already exists in the trash."
                reject("archive_serial_number", message)

    def _mirror_links(self, document: FakeDocument, new: dict[int, JsonValue]) -> None:
        """Keep document links symmetrical like Paperless: link and unlink the other side.

        Every target of the list that is sent gets the link back, also one that already was in the
        list and lost it (a bulk edit to a shorter list leaves it one-sided).

        Every document in the lists that are sent, stored ones included, needs to be changeable
        (403 otherwise, also for a document the user cannot see; `validate_documentlink_targets`);
        the other side keeps an empty list when unlinked.
        """
        link_fields = [f.id for f in self.state.custom_fields if f.data_type == "documentlink"]

        def targets(fields: dict[int, JsonValue], number: int) -> set[int]:
            return set(cast("list[int]", fields.get(number) or []))

        by_id = {d.id: d for d in self.state.documents}
        sent = {t for n in link_fields for t in targets(new, n)} - {document.id}
        if not all(by_id[target].changeable_by(self.user) for target in sent):
            message = "Insufficient permissions."
            raise PermissionDenied(message)
        for number in link_fields:
            for target in targets(new, number) - {document.id}:
                other = by_id[target].custom_fields
                if document.id not in targets(other, number):
                    other[number] = [*cast("list[int]", other.get(number) or []), document.id]
            for target in targets(document.custom_fields, number) - targets(new, number):
                if target in by_id:
                    other = by_id[target].custom_fields
                    if document.id in targets(other, number):
                        other[number] = [
                            n for n in cast("list[int]", other[number]) if n != document.id
                        ]

    def _custom_fields(self, patch: DocumentPatch) -> dict[int, JsonValue]:
        """The new custom field values; a field named twice keeps its last value."""
        fields = {f.id: f for f in self.state.custom_fields}
        documents = {d.id for d in self.state.documents}
        stored: dict[int, JsonValue] = {}
        for index, item in enumerate(patch.custom_fields or ()):
            if item.field not in fields:
                where = f"custom_fields.{index}.field"
                reject(where, f'Invalid pk "{item.field}" - object does not exist.')
            try:
                stored[item.field] = coerce(fields[item.field], item.value, documents)
            except ValueError as error:
                where = f"custom_fields.{index}.non_field_errors"
                reject(where, str(error))
        return stored

    @write
    async def create_tag(self, name: str) -> TaxonomyItem:
        """`TaxonomyWriter.create_tag` (TagViewSet.create): owned by the creator.

        Raises:
            PermissionDenied: The user lacks the global `add_tag` permission.
            Validation: A blank or too long name, or the creator has a tag of that name.
        """
        if not self.user.has("add_tag"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not name.strip():
            reject("name", "This field may not be blank.")
        if len(name) > MAX_TITLE:
            reject("name", f"Ensure this field has no more than {MAX_TITLE} characters.")
        if any(t.name == name and t.owner == self.user.id for t in self.state.tags):
            reject("error", "Object violates owner / name unique constraint")
        tag = FakeTag(id=max((t.id for t in self.state.tags), default=0) + 1, name=name)
        tag.owner = self.user.id
        self.state.tags.append(tag)
        return TaxonomyItem(id=tag.id, name=tag.name, document_count=0, colour=tag.colour)

    @write
    async def add_note(self, document_id: int, text: str) -> Note:
        """`DocumentWriter.add_note` (the `notes` action, POST).

        Order in Paperless 3.4.2: the global `view_document`, `add_note` and `change_document`
        permissions (403; live: a change grant on the document does not replace the last),
        the document (404), the object view (403 for a hidden document) and the object change
        permission (403: a viewer may not add notes). Empty and blank text is rejected (400
        "This field may not be blank.", checked first); the text is stored without the
        whitespace around it.
        """
        if not text.strip():
            reject("note", "This field may not be blank.")
        document = self._sub(document_id, "add_note", "change_document")
        if not document.changeable_by(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        state = self.state
        newest = max((n.id for d in state.documents for n in d.notes), default=0)
        state.last_note_id = max(state.last_note_id, newest) + 1
        note = Note(
            id=state.last_note_id,
            note=text.strip(),
            created=datetime.now(UTC),
            user=self.user.username,
        )
        document.notes.append(note)
        return note

    @write
    async def delete_note(self, document_id: int, note_id: int) -> None:
        """`DocumentWriter.delete_note` (the `notes` action, DELETE ?id=).

        Same order as `add_note` with `delete_note` as the global permission; then the note
        must be one of this document (404).
        """
        try:
            document = self._sub(document_id, "delete_note", "change_document")
        except NotFound:
            raise unseen_note(document_id, note_id) from None  # the same 404 for both
        if not document.changeable_by(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if note_id not in {n.id for n in document.notes}:
            raise unseen_note(document_id, note_id)
        document.notes = [n for n in document.notes if n.id != note_id]

    @write
    async def delete_document(self, document_id: int) -> None:
        """`DocumentWriter.delete_document` (DocumentViewSet.destroy: soft delete to the trash).

        Order in Paperless 3.4.2: the global `delete_document` permission (403, `view_document`
        is not needed), the document among those the user may see (404), then the object: only
        an owner, a superuser or anyone for an unowned document may delete it (403: a view or
        change grant is not enough).
        """
        if not self.user.has("delete_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        document = self._document(document_id)
        if not document.visible_to(self.user):
            raise unseen_document(document_id)
        if not self.user.may_change_object(document):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        self._to_trash(document)

    def _to_trash(self, document: FakeDocument) -> None:
        """Move a document to the trash, stamped with the time; its versions go there too.

        Like Paperless, which soft-deletes every version row: they are entries of their own
        (restoring one on its own is refused while the document is in the trash).
        """
        now = datetime.now(UTC)
        self.state.documents.remove(document)
        document.deleted_at = now
        self.state.trash.append(document)
        for version in document.versions:
            if not version.is_root:
                entry = self._version_entry(document, version, now)
                entry.with_root = True
                self.state.trash.append(entry)

    @staticmethod
    def _version_entry(
        document: FakeDocument, version: DocumentVersion, now: datetime
    ) -> FakeDocument:
        """The trash entry of a version: the document's title, the version's id and file."""
        file = document.version_files[version.id]
        return FakeDocument(
            id=version.id,
            title=document.title,
            owner=document.owner,
            created=document.created,
            versions=[version],
            deleted_at=now,
            root_id=document.id,
            original=file.original,
            archive=file.archive,
            content=file.content,
            mime_type=file.mime_type,
            page_count=file.page_count,
        )

    async def list_trash(self, *, page: int, page_size: int, max_bytes: int) -> TrashPage:
        """`TrashReader.list_trash` (TrashView.get), ordered like the document list.

        Paperless sorts by `-created`, a date, with no tie-breaker: on 3.4.2 equal dates came
        back by id, newest first, which is what this does (an order callers must not rely on).

        Needs the global `view_document` permission (403). A user sees the entries of documents
        they own or that have no owner (a view grant is not enough), a superuser all of them. A
        deleted version is an entry of its own, unless its document is in the trash too. The rows
        carry the text.

        Raises:
            AnswerTooLarge: The texts of the page are larger than `max_bytes`.
            NotFound: `page` is beyond the last page.
            PermissionDenied: See above.
        """
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        covered = {d.id for d in self.state.trash}
        seen = [
            d
            for d in self.state.trash
            if d.root_id not in covered
            and (self.user.is_superuser or self._root_owner(d) in {None, self.user.id})
        ]
        seen.sort(key=lambda d: (d.created, d.id), reverse=True)
        start = (page - 1) * page_size
        if page > 1 and start >= len(seen):
            raise beyond_trash(page)
        rows = seen[start : start + page_size]
        if sum(len(d.content.encode()) for d in rows) > max_bytes:
            message = f"Paperless sent more than {max_bytes} bytes for GET /api/trash/."
            raise AnswerTooLarge(message)
        return TrashPage(
            count=len(seen),
            next_page=page + 1 if start + page_size < len(seen) else None,
            entries=tuple(
                TrashedDocument(
                    id=d.id,
                    title=d.title,
                    deleted_at=d.deleted_at or datetime.min.replace(tzinfo=UTC),
                    version_of=d.root_id,
                    created=d.created,
                    mime_type=self._first_file(d).mime_type,
                    document_type=d.document_type,
                    owner=d.owner,
                    has_archive=self._first_file(d).archive is not None,
                    checksum=root_checksum(d) if d.root_id is None else None,
                )
                for d in rows
            ),
        )

    async def read_trash(self, *, entries: int, max_bytes: int) -> TrashPage:
        """`TrashReader.read_trash`: one page of `list_trash`."""
        return await self.list_trash(page=1, page_size=entries, max_bytes=max_bytes)

    async def find_in_trash(
        self, document_ids: Collection[int], *, entries: int, max_bytes: int
    ) -> dict[int, TrashedRecord]:
        """`TrashReader.find_in_trash`: the same page as `read_trash`, the rows as documents."""
        page = await self.read_trash(entries=entries, max_bytes=max_bytes)
        listed = {e.id for e in page.entries if e.version_of is None} & set(document_ids)
        return {
            d.id: TrashedRecord(
                id=d.id,
                title=d.title,
                created=d.created,
                added=d.added,
                modified=d.modified,
                archive_serial_number=d.archive_serial_number,
                owner=d.owner,
                correspondent=d.correspondent,
                document_type=d.document_type,
                storage_path=d.storage_path,
                tags=tuple(d.tags),
                original_file_name=d.original_filename,
                mime_type=d.mime_type,
                checksum=root_checksum(d),
            )
            for d in self.state.trash
            if d.id in listed
        }

    def _root_owner(self, entry: FakeDocument) -> int | None:
        """The owner who counts for an entry: of a version, its document's."""
        roots = [d for d in self.state.documents if d.id == entry.root_id]
        return roots[0].owner if roots else entry.owner

    @staticmethod
    def _first_file(entry: FakeDocument) -> FakeFile:
        """The file of an entry in the trash: of a document the first version's."""
        shown = FakeFile(entry.original, entry.archive, entry.content, entry.mime_type)
        return entry.version_files.get(entry.id, shown)

    async def look_up_in_trash(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> TrashLookup:
        """`TrashReader.look_up_in_trash`: a page of `list_trash`, one row of it whole.

        Notes, custom field values and later versions went to the trash with the document, so
        Paperless does not tell them. A version lists what is left of its document.
        """
        listed = await self.list_trash(page=page, page_size=page_size, max_bytes=max_bytes)
        shown = {e.id for e in listed.entries} & {document_id}
        entry = next((d for d in self.state.trash if d.id in shown), None)
        return TrashLookup(
            entry=None if entry is None else self._trash_row(entry), next_page=listed.next_page
        )

    async def get_trash_permissions(
        self, document_id: int, *, page: int, page_size: int, max_bytes: int
    ) -> ObjectPermissions | None:
        """`TrashReader.get_trash_permissions`: the same row with `full_perms`."""
        found = await self.look_up_in_trash(
            document_id, page=page, page_size=page_size, max_bytes=max_bytes
        )
        if found.entry is None:
            return None
        [entry] = [d for d in self.state.trash if d.id == document_id]
        return ObjectPermissions(
            view_users=tuple(sorted(entry.viewers)),
            view_groups=tuple(sorted(entry.view_groups)),
            change_users=tuple(sorted(entry.editors)),
            change_groups=tuple(sorted(entry.change_groups)),
        )

    def _trash_row(self, entry: FakeDocument) -> TrashedDetail:
        file = self._first_file(entry)
        roots = [d for d in self.state.documents if d.id == entry.root_id]
        versions = roots[0].versions if roots else [v for v in entry.versions if v.is_root]
        return TrashedDetail.model_validate(
            {
                **_record(entry, self.user).model_dump(),
                "mime_type": file.mime_type,
                "custom_fields": (),
                "notes_count": 0,
                "versions": versions,
                "deleted_at": entry.deleted_at or datetime.min.replace(tzinfo=UTC),
                "version_of": entry.root_id,
                "created_date": entry.created,
                "archived_file_name": None
                if file.archive is None
                else PurePath(entry.original_filename).with_suffix(".pdf").name,
                "is_shared_by_requester": entry.owner == self.user.id and bool(entry.viewers),
                "notes": tuple[TrashedNote, ...](),
                "duplicate_documents": (),
                "content": file.content,
            }
        )

    @write
    async def restore_from_trash(self, document_ids: Sequence[int]) -> None:
        """`TrashWriter.restore_from_trash` (TrashView.post, action `restore`).

        Order in Paperless 3.4.2: the global `delete_document` permission (403), every id in the
        trash and none twice (400), the ownership of every document (403), then a version whose
        document is in the trash too (400). A document comes back with the versions that were
        deleted with it (one deleted earlier stays an entry of its own); a version is re-attached
        to its document, which shows the newest one.
        """
        entries = self._selected(document_ids)
        covered = {d.id for d in self.state.trash}
        if any(d.root_id in covered for d in entries):
            reject("documents", "Restore the root document instead of one of its versions.")
        for entry in entries:
            if entry.id in self.state.stuck:
                continue
            self.state.trash.remove(entry)
            entry.deleted_at = None
            if entry.root_id is None:
                self.state.trash[:] = [
                    d for d in self.state.trash if not (d.root_id == entry.id and d.with_root)
                ]
                self.state.documents.append(entry)
            else:
                self._reattach(entry)

    @write
    async def empty_trash(self, document_ids: Sequence[int]) -> None:
        """`TrashWriter.empty_trash` (TrashView.post, action `empty`): a hard delete.

        The same checks as `restore_from_trash` (except the one for versions). A document's
        deleted versions go with it, those deleted earlier too (probed on 3.4.2).
        """
        entries = [d for d in self._selected(document_ids) if d.id not in self.state.stuck]
        gone = {d.id for d in entries}
        self.state.trash[:] = [
            d for d in self.state.trash if d not in entries and d.root_id not in gone
        ]

    def _selected(self, document_ids: Sequence[int]) -> list[FakeDocument]:
        """The trash entries to act on, after the checks both actions share."""
        if not self.user.has("delete_document"):
            raise trash_refused()
        if not document_ids:
            reject("documents", "Name at least one trash entry.")
        wanted = list(document_ids)
        entries = [d for d in self.state.trash if d.id in wanted]
        if len(entries) != len(wanted):
            reject("documents", "Some documents in the list have not yet been deleted.")
        if not all(self.user.may_change_object(d) for d in entries):
            raise trash_refused()
        return entries

    def _reattach(self, stub: FakeDocument) -> None:
        """Make the trash entry of a version a version of its document again."""
        [root] = [d for d in self.state.documents if d.id == stub.root_id]
        version = stub.versions[0]
        if not root.version_files:
            root.version_files[root.id] = root.shown_file()
        root.version_files[version.id] = stub.shown_file()
        root.versions = sorted(
            [*root.versions, version], key=lambda v: (v.added, v.id), reverse=True
        )
        self._show(root, root.version_files[root.versions[0].id])

    def _next_id(self) -> int:
        """The next free id: versions share the id space of documents (trash included)."""
        state = self.state
        used = {d.id for d in (*state.documents, *state.trash)}
        used |= {v.id for d in (*state.documents, *state.trash) for v in d.versions}
        return max(used, default=0) + 1

    def _denied(self) -> PermissionDenied:
        return PermissionDenied("The Paperless user may not perform this action.")

    def _versioned(self, document_id: int, *global_permissions: str) -> FakeDocument:
        """The document for a change of its versions, in Paperless 3.4.2's order of checks.

        The global permissions the HTTP method needs (403), the document, which only has to exist
        (404: a hidden one is a 403 below, not a 404), then `has_perms_owner_aware`: the owner,
        a superuser, anyone for an unowned document, or a user with a change grant (403).
        """
        for needed in global_permissions:
            if not self.user.has(needed):
                raise self._denied()
        document = self._document(document_id)
        if not document.changeable_by(self.user):
            raise self._denied()
        return document

    @staticmethod
    def _show(document: FakeDocument, file: FakeFile) -> None:
        """Make `file` the file and text the document shows (its newest version's)."""
        document.original, document.archive, document.content = (
            file.original,
            file.archive,
            file.content,
        )
        document.page_count = file.page_count
        document.modified = datetime.now(UTC)

    @staticmethod
    def _push(
        document: FakeDocument, version_id: int, file: FakeFile, label: str | None, now: datetime
    ) -> None:
        """Add `file` as the newest version `version_id`; the root's file is kept on the first."""
        if not document.version_files:
            document.version_files[document.id] = document.shown_file()
        document.version_files[version_id] = file
        document.versions.insert(
            0,
            DocumentVersion(
                id=version_id,
                added=now,
                version_label=label,
                is_root=False,
                checksum=sha256(file.original),
            ),
        )
        FakePaperless._show(document, file)

    @write
    async def upload_document_version(self, document_id: int, upload: VersionUpload) -> str:
        """`DocumentWriter.upload_document_version` (the `update_version` action).

        Like `upload_document`, consumption is immediate: the task names the new version, which
        becomes the newest. The document keeps its metadata, its MIME type and its first file
        name. Probed on 3.4.2: a file type it cannot consume and a label over 64 characters are
        400s; a blank label is stored as none; the same content again is a second version.

        Deferred (not modelled): `update_version` on a version id (Paperless resolves it to the
        root), and a restore from the trash re-attaching a trashed version (live: it comes back as
        a version of its root; here the trash entry stays a stub).

        Order: the global `add_document` permission (403, the HTTP method's), the form (400), the
        document (404), then `change_document` and the object (403, see `_versioned`).

        Raises:
            PermissionDenied: See above.
            Validation: Paperless's 400 for the form, with its message.
        """
        if not self.user.has("add_document"):
            raise self._denied()
        if upload.mime_type not in CONSUMABLE:
            reject("document", f"File type {upload.mime_type} not supported")
        if upload.label is not None and len(upload.label) > MAX_LABEL:
            reject("version_label", f"Ensure this field has no more than {MAX_LABEL} characters.")
        document = self._versioned(document_id, "change_document")
        now = datetime.now(UTC)
        text = (
            upload.content.decode(errors="replace") if upload.mime_type.startswith("text/") else ""
        )
        archive = upload.content + b"\n%archive" if upload.mime_type == "application/pdf" else None
        task = FakeTask(
            id=len(self.state.tasks) + 1, task_id=str(uuid.uuid4()), created=now, finished=now
        )
        self.state.tasks.append(task)
        if self._rejected(upload.content, task):
            return task.task_id
        version_id = self._next_id()
        # The fake reads no PDF: one whose pages cannot be counted is taken to have as many.
        pages = counted_pages(upload.content) or document.page_count
        file = FakeFile(upload.content, archive, text, upload.mime_type, pages, upload.filename)
        self._push(document, version_id, file, upload.label or None, now)
        self._log_version(document, version_id, now)
        task.status, task.document_ids = "success", (version_id,)
        return task.task_id

    @write
    async def set_version_label(
        self, document_id: int, version_id: int, label: str | None
    ) -> DocumentVersion:
        """`DocumentWriter.set_version_label` (PATCH `versions/{id}/`); a blank label is none.

        Order: the global `change_document` permission (403), the label (400), the document (404),
        the object (403, see `_versioned`), the version (404).

        Raises:
            NotFound: The document has no such version (404 alike for both).
            PermissionDenied: See above.
            Validation: The label is longer than 64 characters.
        """
        if not self.user.has("change_document"):
            raise self._denied()
        if label is not None and len(label) > MAX_LABEL:
            reject("version_label", f"Ensure this field has no more than {MAX_LABEL} characters.")
        document = self._versioned(document_id)
        found = [v for v in document.versions if v.id == version_id]
        if not found:
            raise unseen_version(document_id, version_id)
        changed = found[0].model_copy(update={"version_label": label or None})
        document.versions = [changed if v.id == version_id else v for v in document.versions]
        return changed

    @write
    async def delete_version(self, document_id: int, version_id: int) -> int:
        """`DocumentWriter.delete_version` (DELETE `versions/{id}/`): a soft delete.

        The version row goes to the trash under its own id, and the document shows the newest
        version that is left (its file and text). Deleting needs the delete permission, not the
        change permission: the global `delete_document` (403), the document (404), then the
        owner, a superuser or anyone for an unowned document (403: a change grant is not enough),
        then the version (404) and not the root (400).

        Raises:
            NotFound: The document has no such version.
            PermissionDenied: See above.
            Validation: The version is the root.
        """
        if not self.user.has("delete_document"):
            raise self._denied()
        document = self._document(document_id)
        if not self.user.may_change_object(document):
            raise self._denied()
        found = [v for v in document.versions if v.id == version_id]
        if not found:
            raise unseen_version(document_id, version_id)
        if found[0].is_root:
            reject(
                "version", "Cannot delete the root/original version. Delete the document instead."
            )
        document.versions = [v for v in document.versions if v.id != version_id]
        self.state.trash.append(self._version_entry(document, found[0], datetime.now(UTC)))
        del document.version_files[version_id]
        current = document.versions[0]
        self._show(document, document.version_files[current.id])
        return current.id

    @write
    async def merge_as_versions(
        self, root_id: int, source_ids: Sequence[int], label: str | None
    ) -> Unconfirmed | None:
        """`DocumentWriter.merge_as_versions`: the sources vanish, their files become versions.

        Seen through the port it merges at once (the adapter waits for the task Paperless
        queues); probed on 3.4.2: the sources are removed for good (no trash) and
        keep nothing but their file; the last source becomes the newest version; a source with
        versions (also one in the trash, 3.5.1), a version as source, a duplicate or an unknown id
        and a label with several sources are 400s. The first archive serial number of the sources
        goes to a root without one, the others are released. The form is checked first (it does
        not look at who may see what: a document the user may not see is a 403 below, not a
        missing one), then, for anyone but a superuser: the global `change_document` and a change
        grant or ownership of every document, ownership (or no owner) of every document, and the
        global `delete_document` (403), because the sources stop existing.

        Raises:
            PermissionDenied: See above.
            Validation: Paperless's 400, with its message.
        """
        state = self.state
        wanted = [root_id, *source_ids]
        seen = {d.id: d for d in state.documents}
        child_versions = {v.id for d in state.documents for v in d.versions if not v.is_root}
        if len(set(wanted)) != len(wanted) or not set(wanted) <= (seen.keys() | child_versions):
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        if child_versions & set(wanted):
            reject("non_field_errors", "Only top-level documents can be merged as versions.")
        if label is not None and len(source_ids) != 1:
            reject(
                "non_field_errors",
                "version_label can only be used when merging one source document.",
            )
        deleted = {d.root_id for d in state.trash}  # a version in the trash counts, too
        if any(len(seen[n].versions) > 1 or n in deleted for n in source_ids):
            reject(
                "non_field_errors",
                "Documents with existing versions cannot be merged into another document.",
            )
        owned = all(seen[n].owner in {None, self.user.id} for n in wanted)
        changeable = all(seen[n].changeable_by(self.user) for n in wanted)
        if not self.user.is_superuser and not (
            self.user.has("change_document")
            and changeable
            and owned
            and self.user.has("delete_document")
        ):
            raise self._denied()
        root = seen[root_id]
        for number in source_ids:
            source = seen[number]
            file = source.shown_file()
            self._push(root, number, file, label, datetime.now(UTC))
            state.documents.remove(source)
            if root.archive_serial_number is None:
                root.archive_serial_number = source.archive_serial_number

    # --- PdfWriter ---------------------------------------------------------------------------

    def _pdf_documents(self, document_ids: Sequence[int]) -> list[FakeDocument]:
        """The documents of a PDF request, or Paperless's 400 for unknown or repeated ids."""
        known = {d.id: d for d in self.state.documents}
        if len(set(document_ids)) != len(document_ids) or not set(document_ids) <= known.keys():
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        return [known[n] for n in document_ids]

    def _pdf_source(
        self, document_id: int, version_id: int | None
    ) -> tuple[FakeDocument, FakeFile, int | None]:
        """The document of a request for one, the file it reads, and the page count of its row.

        A root's row is its first file: without a version the newest file is read, but Paperless
        holds the pages of the request against the first one's count (3.5.1).
        """
        sent = document_id if version_id is None else version_id
        versions = {v.id: d for d in self.state.documents for v in d.versions}
        if sent not in versions:
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        document = versions[sent]
        file = self._file_of(document, version_id)
        return document, file, self._file_of(document, sent).page_count

    def _first_or_newest(self, document: FakeDocument, *, first: bool) -> FakeFile:
        return self._file_of(document, document.id if first else None)

    def _pdf_allowed(self, documents: list[FakeDocument], *, adds: bool, owner_only: bool) -> None:
        """Paperless's permission checks of a PDF request (403), as probed on 3.4.2.

        The global `change_document` and, on every document, a change grant or ownership
        (`owner_only`: ownership alone, a grant is not enough, as for rotating and editing);
        `add_document` when the result is a new document. Superusers may do all.
        """
        user = self.user
        if user.is_superuser:
            return
        owns = all(d.owner in {None, user.id} for d in documents)
        allowed = user.has("change_document") and (
            owns if owner_only else all(d.changeable_by(user) for d in documents)
        )
        if allowed and adds:
            allowed = user.has("add_document")
        if not allowed:
            raise self._denied()

    def _seen_tasks(
        self,
        task_ids: tuple[str, ...],
        expected: int | None = None,
        sources: tuple[int | None, ...] = (),
        outputs: tuple[int, ...] = (),
        included: tuple[int, ...] | None = None,
    ) -> StartedTasks:
        """The tasks the adapter can find: none for a user who may not view tasks.

        ``sources`` is the document of each task where the file name tells it (rotating,
        editing as a new version, unlocking).
        """
        count = len(task_ids) if expected is None else expected
        if self.user.has("view_paperlesstask"):
            return StartedTasks(
                ids=task_ids, expected=count, sources=sources, outputs=outputs, included=included
            )
        return StartedTasks(ids=(), expected=count, problem="forbidden")

    def _unlisted(self, files: CopiedFiles) -> StartedTasks:
        """A request for which no task is listed, and how its files are found."""
        started = self._seen_tasks((), len(files.names))
        return started if started.problem else started.model_copy(update={"files": files})

    def _hidden_by_date(self, document: FakeDocument | None) -> bool:
        """Whether Paperless cannot record the task of an operation that copies its metadata.

        3.5.1 fails to store a task whose metadata holds a date among the custom fields; the work
        is done all the same.
        """
        dates = {f.id for f in self.state.custom_fields if f.data_type == "date"}
        return document is not None and any(
            n in dates and value is not None for n, value in document.custom_fields.items()
        )

    def _pdf_task(self, task_type: str, produced: tuple[int, ...]) -> str:
        """Record a finished task (the fake works at once) and return its id."""
        now = datetime.now(UTC)
        task = FakeTask(
            id=len(self.state.tasks) + 1,
            task_id=str(uuid.uuid4()),
            task_type=task_type,
            status="success",
            created=now,
            finished=now,
            document_ids=produced,
        )
        self.state.tasks.append(task)
        return task.task_id

    def _new_version(
        self,
        document: FakeDocument,
        file: FakeFile,
        *,
        pages: int | None,
        marker: bytes,
        name: str | None = None,
    ) -> int:
        """Add ``file``, edited, as the newest version of ``document``; returns the version id."""
        version_id = self._next_id()
        archive = None if file.archive is None else file.archive + marker
        original = file.original + marker
        made = FakeFile(original, archive, file.content, "application/pdf", pages, name)
        now = datetime.now(UTC)
        self._push(document, version_id, made, None, now)
        self._log_version(document, version_id, now)
        return version_id

    def _log_version(self, document: FakeDocument, version_id: int, now: datetime) -> None:
        """The entry Paperless writes for an added version, by the user who asked for it."""
        entry = HistoryEntry(
            id=version_id * 10,
            timestamp=now,
            action="update",
            actor=self.user.username,
            changes={"Version Added": ["None", version_id]},
        )
        document.history.insert(0, entry)

    def _copying(self, names: Sequence[str], included: tuple[int, ...] | None) -> CopiedFiles:
        """Record an operation that makes new documents, and how its files are found."""
        family = names[0].partition("_edit_")
        made = FakeCopying(str(uuid.uuid4()), self.user.id, datetime.now(UTC), "".join(family[:2]))
        self.state.copying.append(made)
        return CopiedFiles(request=made.request, names=tuple(names), included=included)

    @write  # a read, but of the writer port: there is nothing to find before a write
    async def find_copies(self, files: CopiedFiles, *, final: bool = False) -> FoundCopies:
        """`PdfWriter.find_copies`: the user's documents of these names since the request.

        The fake works at once, so of the earlier operations only one for the same files can be
        in doubt; what it made itself says nothing, because it may have had more files.
        """
        if not self.user.has("view_paperlesstask"):
            raise self._denied()
        nothing = (None,) * len(files.names)
        if not self.user.has("view_uisettings"):
            return FoundCopies(ids=nothing, doubt=NO_USER)
        copying = {c.request: c for c in self.state.copying}
        own = copying.get(files.request)
        if own is None:
            return FoundCopies(ids=nothing, doubt=NO_TIMES)
        found = {
            d.original_filename: d.id
            for d in self.state.documents
            if d.owner == self.user.id
            and d.added >= own.started
            and d.original_filename in files.names
        }
        mine = [
            c
            for c in copying.values()
            if c.owner == own.owner and c is not own and c.operation != "rotate"
        ]
        if any(c.started >= own.started for c in mine):
            return FoundCopies(ids=nothing, doubt=AT_ONCE)
        if any(
            t.task_type == "consume_file"
            and t.created >= own.started
            and t.file_name.startswith(own.family)
            for t in self.state.tasks
        ):
            return FoundCopies(ids=nothing, doubt=TASKED)
        hour = own.started - timedelta(hours=1)
        whole = final or len(found) == len(files.names)
        if whole and any(c.family == own.family and c.started >= hour for c in mine):
            return FoundCopies(ids=nothing, doubt=PENDING, passing=True)
        return FoundCopies(ids=tuple(found.get(name) for name in files.names))

    @write  # a read, but of the writer port
    async def find_rotations(self, search: RotationSearch, *, final: bool = False) -> FoundCopies:
        """`PdfWriter.find_rotations`: the versions with the name of a rotation, by this user.

        The fake works at once; an earlier rotation is unsettled where its versions are not there
        (a test deleted them, or recorded an operation that made none).
        """
        if not self.user.has("view_paperlesstask"):
            raise self._denied()
        nothing = (None,) * len(search.hidden)
        own = next((c for c in self.state.copying if c.request == search.request), None)
        if not self.user.has("view_uisettings") or own is None:
            user_known = self.user.has("view_uisettings")
            return FoundCopies(ids=nothing, doubt=NO_TIMES if user_known else NO_USER)
        ids: list[int | None] = []
        doubt: str | None = None
        for number in search.hidden:
            found = self._rotation_of(number, search.before[number], own) if doubt is None else None
            doubt = found if isinstance(found, str) else doubt
            ids.append(found if isinstance(found, int) else None)
        doubt = doubt or self._rotated_else(search, own, whole=final or None not in ids)
        if doubt is not None:
            return FoundCopies(ids=nothing, doubt=doubt, passing=doubt == PENDING)
        return FoundCopies(ids=tuple(ids))

    def _rotation_of(self, number: int, newest: int, own: FakeCopying) -> int | str | None:
        """The version a rotation made of the document, None if there is none, or a doubt."""
        seen = (d for d in self.state.documents if d.id == number and d.visible_to(self.user))
        document = next(seen, None)
        if document is None:
            return UNREADABLE.format(document=number)
        newer = takewhile(lambda v: v.id != newest, document.versions)
        named = self._turned_versions(document, newer, own.started)
        if named and not (self.state.audit_log_enabled and self.user.has("view_logentry")):
            return NO_HISTORY
        mine = self._added(document, named)
        if len(mine) > 1:
            return SEVERAL.format(versions=", ".join(map(str, mine)), document=number)
        return mine[0] if mine else None

    def _rotated_else(self, search: RotationSearch, own: FakeCopying, *, whole: bool) -> str | None:
        """Why the versions may be another request's of this user."""
        names = {f"{number}_rotated.pdf" for number in search.hidden}
        if any(t.file_name in names and t.created >= own.started for t in self.state.tasks):
            return TASKED
        others = [
            c
            for c in self.state.copying
            if c is not own and c.owner == self.user.id and c.operation == "rotate"
        ]
        if any(c.started >= own.started for c in others):
            return AT_ONCE
        earlier = [c for c in others if c.started >= own.started - timedelta(hours=1)]
        if earlier and whole:
            since = min(c.started for c in earlier)
            made = [
                version
                for document in self.state.documents
                if document.visible_to(self.user)
                for version in self._added(
                    document,
                    self._turned_versions(
                        document,
                        self._not_own(document, search, own),
                        since,
                    ),
                )
            ]
            if len(made) < sum(c.count for c in earlier):
                return PENDING
        return None

    def _not_own(
        self, document: FakeDocument, search: RotationSearch, own: FakeCopying
    ) -> list[DocumentVersion]:
        """The versions that cannot be of the request of the search."""
        if document.id not in search.before:
            return document.versions
        shown = search.before[document.id]
        rest = dropwhile(lambda v: v.id != shown, document.versions)
        return [v for v in rest if v.added < own.started]

    def _turned_versions(
        self, document: FakeDocument, versions: Iterable[DocumentVersion], since: datetime
    ) -> list[int]:
        """Those added since then that have the file name of a rotation."""
        name = f"{document.id}_rotated.pdf"
        late = [v.id for v in versions if not v.is_root and v.added >= since]
        return [n for n in late if self._file_of(document, n).file_name == name]

    def _added(self, document: FakeDocument, versions: Sequence[int]) -> list[int]:
        """Those of the versions that the user added, as the history says."""
        by = {
            str(added[-1]): entry.actor
            for entry in document.history
            if isinstance(added := entry.changes.get("Version Added"), list)
        }
        return [n for n in versions if by.get(str(n)) == self.user.username]

    def _new_document(
        self,
        source: FakeDocument | None,
        title: str,
        pages: int | None,
        content: str = "",
        *,
        file_name: str,
    ) -> FakeDocument:
        """A new PDF document with the metadata of ``source``, owned by the user.

        Notes, history and the archive serial number are never copied.
        """
        number = self._next_id()
        original = f"%PDF-1.4 fake {title} {number}".encode()
        made = FakeDocument(
            id=number,
            title=title,
            owner=self.user.id,
            added=datetime.now(UTC),
            file_name=file_name,
            page_count=pages,
            original=original,
            archive=original + b"\n%archive",
            versions=[_root_version(number, original)],
            content=content,
            tags=[tag.id for tag in self.state.tags if tag.is_inbox],
        )
        if source is not None:
            made.created = source.created
            made.tags = list(dict.fromkeys([*source.tags, *made.tags]))
            made.correspondent = source.correspondent
            made.document_type = source.document_type
            made.storage_path = source.storage_path
            made.custom_fields = dict(source.custom_fields)
            made.viewers, made.editors = set(source.viewers), set(source.editors)
            made.view_groups = set(source.view_groups)
            made.change_groups = set(source.change_groups)
        self.state.documents.append(made)
        return made

    async def may_delete(self, owners: Sequence[int | None]) -> bool | None:
        """`DocumentReader.may_delete` (UiSettingsView: needs `view_uisettings`)."""
        user = self.user
        if not user.has("view_uisettings"):
            return None
        owns = all(owner in {None, user.id} for owner in owners)
        return user.is_superuser or (owns and user.has("delete_document"))

    async def get_signed_in(self) -> SignedIn | None:
        """`DocumentReader.get_signed_in` (UiSettingsView: needs `view_uisettings`)."""
        if not self.user.has("view_uisettings"):
            return None
        return SignedIn(id=self.user.id, is_superuser=self.user.is_superuser)

    @write
    async def rotate_documents(
        self, document_ids: Sequence[int], degrees: int, *, original_files: bool = False
    ) -> StartedTasks:
        """`PdfWriter.rotate_documents`: every document gets a new, rotated version.

        Order (probed on 3.4.2): the global `change_document` (403), the form (400: a multiple
        of 90 for the degrees, known and unrepeated ids), a change grant on every document (403).
        The document keeps its id; the older version stays.
        """
        if not self.user.has("change_document"):
            raise self._denied()
        if degrees % 90:
            reject("degrees", "degrees must be a multiple of 90")
        documents = self._pdf_documents(document_ids)
        self._pdf_allowed(documents, adds=False, owner_only=True)
        marker = f"\n%rotated {degrees}".encode()
        request = self._copying(("_rotated.pdf",), None).request
        self.state.copying[-1].count = len(documents)
        tasks: list[str] = []
        for document in documents:
            file = self._first_or_newest(document, first=original_files)
            version = self._new_version(
                document,
                file,
                pages=file.page_count,
                marker=marker,
                name=f"{document.id}_rotated.pdf",
            )
            tasks.append(self._pdf_task("consume_file", (version,)))
        shown = [n for n, d in enumerate(documents) if not self._hidden_by_date(d)]
        started = self._seen_tasks(
            tuple(tasks[n] for n in shown),
            len(documents),
            sources=tuple(documents[n].id for n in shown),
        )
        listed = started.sources
        names = tuple(f"{d.id}_rotated.pdf" for d in documents if d.id not in listed)
        if started.problem or None in listed or not names:
            return started
        return started.model_copy(update={"files": CopiedFiles(request=request, names=names)})

    @write
    async def merge_documents(
        self,
        document_ids: Sequence[int],
        *,
        metadata_document_id: int | None,
        archive_fallback: bool = False,
        original_files: bool = False,
    ) -> StartedTasks:
        """`PdfWriter.merge_documents`: one new document with all the pages, in this order.

        Order (probed on 3.4.2): the global `change_document` (403), the ids (400), then the
        permissions (403: `add_document`, a change grant is enough; a metadata document outside the
        list is accepted). The new document is titled like the metadata document with " (merged)",
        else after the ids. What cannot be merged (an encrypted PDF, a file that is no PDF without
        the archive that stands in for it) is left out without a word; with nothing left no task
        starts.
        """
        if not self.user.has("change_document"):
            raise self._denied()
        documents = self._pdf_documents(document_ids)
        self._pdf_allowed(documents, adds=True, owner_only=False)
        files = {d.id: self._first_or_newest(d, first=original_files) for d in documents}

        def mergeable(document: FakeDocument) -> bool:
            file = files[document.id]
            if file.mime_type.endswith("pdf"):
                return document.password is None or file.page_count is not None
            return archive_fallback and file.archive is not None

        taken = [d for d in documents if mergeable(d)]
        if not taken:
            return self._unlisted(self._copying(("_merged.pdf",), None))
        source = next((d for d in documents if d.id == metadata_document_id), None)
        title = (
            f"{source.title} (merged)"
            if source is not None
            else f"{'_'.join(str(d.id) for d in taken)}_merged"
        )
        # An image has no page count, its archive PDF one page.
        pages = sum(files[d.id].page_count or 1 for d in taken)
        joined = "_".join(str(d.id) for d in taken)
        name = f"{joined[:100]}_merged.pdf"
        included = tuple(d.id for d in taken) if len(joined) <= 100 else None
        copied = self._copying((name,), included)
        made = self._new_document(source, title, pages, taken[0].content, file_name=name)
        task = self._pdf_task("consume_file", (made.id,))
        if self._hidden_by_date(source):
            return self._unlisted(copied)
        return self._seen_tasks((task,), included=tuple(d.id for d in taken))

    @write
    async def edit_pdf(
        self,
        document_id: int,
        pages: Sequence[PageSlot],
        *,
        output: FileOutput,
        version_id: int | None = None,
        keep_metadata: bool = True,
    ) -> StartedTasks:
        """`PdfWriter.edit_pdf`: the pages, grouped into output files, as new documents or version.

        Order (probed on 3.4.2): the global `change_document` (403), the form (400: a non-empty
        page list, rotations in multiples of 90, output files numbered from 0 without gaps, a
        single one as a new version, pages inside the row that was sent, a PDF), then the object
        (403). A page the row allows and the file read lacks fails in the task.
        """
        if not self.user.has("change_document"):
            raise self._denied()
        document, file, count = self._pdf_source(document_id, version_id)
        files = _check_edit(file, count, pages)
        if output == "new_version" and len(files) > 1:
            reject("non_field_errors", "update_document only allowed with a single output document")
        self._pdf_allowed([document], adds=output != "new_version", owner_only=True)
        if file.page_count is not None and any(s.page > file.page_count for s in pages):
            raise edit_failed(str(uuid.uuid4()))
        if output == "new_version":
            version = self._new_version(document, file, pages=len(pages), marker=b"\n%edited")
            return self._seen_tasks(
                (self._pdf_task("consume_file", (version,)),), sources=(document.id,)
            )
        names = [f"{document.id}_edit_{n + 1}.pdf" for n in files]
        copied = self._copying(names, None)
        tasks = tuple(
            self._pdf_task(
                "consume_file",
                (
                    self._new_document(
                        document if keep_metadata else None,
                        document.title if keep_metadata else name.removesuffix(".pdf"),
                        sum(1 for s in pages if s.document == n),
                        file.content,
                        file_name=name,
                    ).id,
                ),
            )
            for n, name in zip(files, names, strict=True)
        )
        if keep_metadata and self._hidden_by_date(document):
            return self._unlisted(copied)
        return self._seen_tasks(
            tasks, len(files), sources=(document.id,) * len(files), outputs=tuple(files)
        )

    @write
    async def remove_password(
        self,
        document_id: int,
        password: str,
        *,
        output: FileOutput,
        version_id: int | None = None,
        keep_metadata: bool = True,
    ) -> StartedTasks:
        """`PdfWriter.remove_password`: a decrypted copy, as a new document or version.

        Probed on 3.4.2: a wrong password is a plain-text 400; a file without a password is
        accepted and starts no task; a file that is not a PDF is the same plain-text 400.
        Permissions as for an edit.
        """
        if not self.user.has("change_document"):
            raise self._denied()
        document, file, _ = self._pdf_source(document_id, version_id)
        self._pdf_allowed([document], adds=output != "new_version", owner_only=True)
        if not file.mime_type.endswith("pdf"):
            raise wrong_password()
        # The root's file is the locked one; every version made from it is not.
        wanted = document.versions[0].id if version_id is None else version_id
        if document.password is None or wanted != document.id:
            if output == "new_version":
                return self._seen_tasks((), 1)
            return self._unlisted(self._copying((f"{document.id}_unprotected.pdf",), None))
        if document.password != password:
            raise wrong_password()
        pages = document.unlocked_pages
        if output == "new_version":
            version = self._new_version(document, file, pages=pages, marker=b"\n%unlocked")
            return self._seen_tasks(
                (self._pdf_task("consume_file", (version,)),), sources=(document.id,)
            )
        name = f"{document.id}_unprotected.pdf"
        files = self._copying((name,), None)
        made = self._new_document(
            document if keep_metadata else None,
            document.title if keep_metadata else name.removesuffix(".pdf"),
            pages,
            file.content,
            file_name=name,
        )
        task = self._pdf_task("consume_file", (made.id,))
        if keep_metadata and self._hidden_by_date(document):
            return self._unlisted(files)
        return self._seen_tasks((task,), sources=(document.id,))

    @write
    async def reprocess_documents(
        self, document_ids: Sequence[int], *, remote_ocr: bool
    ) -> StartedTasks:
        """`PdfWriter.reprocess_documents`: one task per document; the fake does no OCR.

        Probed on 3.4.2: the global `change_document` (403), the ids (400), a change grant (403).
        The tasks name no document.
        """
        _ = remote_ocr
        if not self.user.has("change_document"):
            raise self._denied()
        documents = self._pdf_documents(document_ids)
        self._pdf_allowed(documents, adds=False, owner_only=False)
        self.state.reprocessed.extend(d.id for d in documents)
        return self._seen_tasks(
            tuple(self._pdf_task("reprocess_document", ()) for _ in documents),
            sources=tuple(None for _ in documents),
        )

    def _owned(self, kind: WritableKind) -> list[FakeTaxonomy]:
        """The objects of a kind with owners (everything but custom fields)."""
        state = self.state
        return cast(
            "list[FakeTaxonomy]",
            {
                "tag": state.tags,
                "correspondent": state.correspondents,
                "document_type": state.document_types,
                "storage_path": state.storage_paths,
            }[kind],
        )

    def _allowed(self, action: str, kind: WritableKind) -> None:
        """The global permission of an action on a kind: `add_tag`, `change_documenttype`, ..."""
        model = kind.replace("_", "")
        if not self.user.has(f"{action}_{model}"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)

    def _target(self, kind: WritableKind, item_id: int) -> FakeTaxonomy | FakeCustomField:
        """The object among those the user may see (`NotFound` otherwise), then may change."""
        if kind == "custom_field":
            found = [f for f in self.state.custom_fields if f.id == item_id]
        else:
            found = [o for o in self._owned(kind) if o.id == item_id and o.visible_to(self.user)]
        if not found:
            message = "The Paperless object does not exist or is not visible to this user."
            raise NotFound(message)
        if not isinstance(found[0], FakeCustomField) and not self.user.may_change_object(found[0]):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return found[0]

    def _detail(self, kind: WritableKind, item_id: int) -> TaxonomyDetail:
        """The object as a write answers it: the list row plus the matching rule."""
        [row] = [r for r in self._rows(kind) if r.id == item_id]
        extras: dict[str, object] = {}
        if kind != "custom_field":
            [obj] = [o for o in self._owned(kind) if o.id == item_id]
            extras = {
                "matching_algorithm": obj.matching_algorithm,
                "match": obj.match,
                "case_insensitive": obj.case_insensitive,
            }
            if isinstance(obj, FakeTag):
                extras["is_inbox_tag"] = obj.is_inbox
            if isinstance(obj, FakeStoragePath):
                extras["path"] = obj.path
        return TaxonomyDetail.model_validate({**row.model_dump(), **extras})

    def _apply(self, obj: FakeTaxonomy, values: TaxonomyValues, *, creating: bool) -> None:
        """Set what `values` sets on `obj`, validated like the serializers (3.4.2)."""
        sent = values.model_fields_set
        if {"matching_algorithm", "match"} & sent:
            algorithm = values.matching_algorithm or obj.matching_algorithm
            check_match(algorithm, obj.match if values.match is None else values.match)
        if values.matching_algorithm is not None:
            obj.matching_algorithm = values.matching_algorithm
        if values.match is not None:
            obj.match = values.match
        if values.case_insensitive is not None:
            obj.case_insensitive = values.case_insensitive
        if isinstance(obj, FakeTag):
            self._apply_tag(obj, values)
        if isinstance(obj, FakeStoragePath):
            if values.path is None and creating:
                reject("path", "This field is required.")
            if values.path is not None:
                check_path(values.path)
                obj.path = values.path

    def _apply_tag(self, tag: FakeTag, values: TaxonomyValues) -> None:
        if values.colour is not None:
            check_colour(values.colour)
            tag.colour = values.colour
        if values.is_inbox_tag is not None:
            tag.is_inbox = values.is_inbox_tag
        if "parent" not in values.model_fields_set:
            return
        parent = values.parent
        if parent is not None:
            if parent == tag.id:
                reject("parent", "Cannot set itself as parent.")
            if parent not in {t.id for t in self.state.tags}:
                reject("parent", f'Invalid pk "{parent}" - object does not exist.')
            if parent in self._tag_tree(tag.id) - {tag.id}:
                reject("parent", "Cannot set parent to a descendant.")
        tag.parent = parent

    def _tag_tree(self, root: int) -> set[int]:
        """The tag and every tag below it."""
        found = {root}
        grew = True
        while grew:
            below = {t.id for t in self.state.tags if t.parent in found}
            grew = not below <= found
            found |= below
        return found

    @write
    async def create_taxonomy_item(
        self, kind: WritableKind, name: str, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """`TaxonomyWriter.create_taxonomy_item` (the viewsets' create): owned by the creator.

        Raises:
            PermissionDenied: The user lacks the global add permission of the kind.
            Validation: Paperless's 400 for a value, with its message.
        """
        self._allowed("add", kind)
        name = check_name(name)
        if kind == "custom_field":
            return self._create_field(name, values)
        objects = self._owned(kind)
        if any(o.name == name and o.owner == self.user.id for o in objects):
            reject("error", "Object violates owner / name unique constraint")
        number = max((o.id for o in objects), default=0) + 1
        made = {"tag": FakeTag, "storage_path": FakeStoragePath}.get(kind, FakeTaxonomy)
        obj = made(id=number, name=name, owner=self.user.id, matching_algorithm="none")
        self._apply(obj, values, creating=True)
        objects.append(obj)
        return self._detail(kind, number)

    def _create_field(self, name: str, values: TaxonomyValues) -> TaxonomyDetail:
        if values.data_type is None:
            reject("data_type", "This field is required.")
        if any(f.name == name for f in self.state.custom_fields):
            reject("name", "custom field with this name already exists.")
        options = self._options(values.data_type, values)
        number = max((f.id for f in self.state.custom_fields), default=0) + 1
        self.state.custom_fields.append(
            FakeCustomField(
                id=number, name=name, data_type=values.data_type, select_options=options
            )
        )
        return self._detail("custom_field", number)

    @staticmethod
    def _options(data_type: str, values: TaxonomyValues) -> tuple[SelectOption, ...]:
        """The options a select field stores; a select field needs at least one."""
        if data_type != "select":
            return ()
        if not values.select_options:
            reject("error", "extra_data.select_options must be a valid list")
        return with_ids(values.select_options)

    @write
    async def update_taxonomy_item(
        self, kind: WritableKind, item_id: int, values: TaxonomyValues
    ) -> TaxonomyDetail:
        """`TaxonomyWriter.update_taxonomy_item` (the taxonomy viewsets' partial_update).

        Order in Paperless 3.4.2: the global change permission (403), the object among those
        the user may see (404), the object permission (403), then the values (400). A select
        field PATCHed without options is a 400 in Paperless; the adapter sends them along, so
        the fake keeps them when `values` leaves them out.

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not change objects of the kind, or this one.
            Validation: Paperless's 400 for a value, with its message.
        """
        self._allowed("change", kind)
        target = self._target(kind, item_id)
        if isinstance(target, FakeCustomField):
            self._update_field(target, values)
            return self._detail(kind, item_id)
        name = target.name
        if values.name is not None:
            name = check_name(values.name)
            clash = (o for o in self._owned(kind) if o.name == name and o.owner == target.owner)
            if any(o is not target for o in clash):
                reject("non_field_errors", "The fields name, owner must make a unique set.")
        self._apply(target, values, creating=False)
        target.name = name
        return self._detail(kind, item_id)

    def _update_field(self, field: FakeCustomField, values: TaxonomyValues) -> None:
        name = field.name if values.name is None else check_name(values.name)
        if any(f.name == name and f.id != field.id for f in self.state.custom_fields):
            reject("name", "custom field with this name already exists.")
        data_type = values.data_type or field.data_type
        options = field.select_options
        if values.select_options is not None or data_type != field.data_type:
            options = self._options(data_type, values)
        fields = self.state.custom_fields
        fields[fields.index(field)] = replace(
            field, name=name, data_type=data_type, select_options=options
        )
        if field.data_type == "select" and data_type == "select":
            # Paperless does this asynchronously (Celery task process_cf_select_update after the
            # save); the fake does it at once.
            kept = {o.id for o in options}
            for document in self.state.documents:
                held = document.custom_fields.get(field.id)
                if held is not None and held not in kept:
                    document.custom_fields[field.id] = None

    @write
    async def delete_taxonomy_item(self, kind: WritableKind, item_id: int) -> None:
        """`TaxonomyWriter.delete_taxonomy_item` (the taxonomy viewsets' destroy).

        Documents lose what refers to the object; a tag takes its descendants with it.

        Raises:
            NotFound: No such object, or the user may not see it.
            PermissionDenied: The user may not delete objects of the kind, or this one.
        """
        self._allowed("delete", kind)
        _ = self._target(kind, item_id)
        if kind == "tag" and self._takes_tags_of_others(item_id):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message, hint=TAG_DELETE_HINT)
        self._remove(kind, item_id)

    def _takes_tags_of_others(self, tag_id: int) -> bool:
        """Whether deleting the tag is refused (3.5): a tag below it is not the user's to delete.

        TagViewSet.perform_destroy of 3.5 refuses unless the user may delete the tag and every
        descendant (owner or unowned, or a superuser).
        """
        user = self.user
        if user.is_superuser:
            return False
        tree = self._tag_tree(tag_id)
        return any(t.owner not in {None, user.id} for t in self.state.tags if t.id in tree)

    def _remove(self, kind: WritableKind, item_id: int) -> None:
        """Delete the object (if it still exists): documents lose it, a tag takes its children."""
        state = self.state
        if kind == "custom_field":
            state.custom_fields[:] = [f for f in state.custom_fields if f.id != item_id]
            for document in state.documents:
                _ = document.custom_fields.pop(item_id, None)
            return
        if kind == "tag":
            gone = self._tag_tree(item_id)
            state.tags[:] = [t for t in state.tags if t.id not in gone]
            for document in state.documents:
                document.tags = [t for t in document.tags if t not in gone]
            return
        self._owned(kind)[:] = [o for o in self._owned(kind) if o.id != item_id]
        for document in state.documents:
            if kind == "correspondent" and document.correspondent == item_id:
                document.correspondent = None
            if kind == "document_type" and document.document_type == item_id:
                document.document_type = None
            if kind == "storage_path" and document.storage_path == item_id:
                document.storage_path = None

    # --- BulkWriter

    @write
    async def bulk_edit_documents(
        self, document_ids: Sequence[int], change: DocumentChange, *, stop_on_limit: bool = False
    ) -> Unconfirmed | None:
        """`BulkWriter.bulk_edit_documents` (BulkEditView).

        Order in Paperless 3.4.2: the request is validated as a whole (400: unknown or repeated
        documents, unknown tags, references or custom fields, wrong values), then permissions
        (403: the global `change_document`, change permission on every document, hidden ones
        included; a deletion also needs ownership and `delete_document`), then everything is
        applied. An empty selection is accepted.
        """
        _ = stop_on_limit  # the fake has no rate limit
        state = self.state
        known = {d.id: d for d in state.documents}
        if len(set(document_ids)) != len(document_ids) or not set(document_ids) <= known.keys():
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        documents = [known[number] for number in document_ids]
        apply = self._plan(change)
        user = self.user
        if not user.is_superuser:
            allowed = user.has("change_document") and all(d.changeable_by(user) for d in documents)
            if allowed and isinstance(change, TrashDocuments):
                owns = all(d.owner in {None, user.id} for d in documents)
                allowed = owns and user.has("delete_document")
            if not allowed:
                raise documents_refused()
        apply(documents)

    def _plan(self, change: DocumentChange) -> Callable[[list[FakeDocument]], None]:
        """Validate ``change`` like the serializer and return what applies it to documents."""
        state = self.state
        if isinstance(change, TagsChange):
            known = {t.id for t in state.tags}
            for where, wanted in (("add_tags", change.add), ("remove_tags", change.remove)):
                if not set(wanted) <= known:
                    message = (
                        f"Paperless rejected the request: Some tags in {where} "
                        "don't exist or were specified twice."
                    )
                    raise Validation(message)
            return lambda docs: self._tags(docs, change)
        if isinstance(change, ReferenceChange):
            table = {
                "correspondent": state.correspondents,
                "document_type": state.document_types,
                "storage_path": state.storage_paths,
            }[change.field]
            if change.value is not None and change.value not in {o.id for o in table}:
                label = change.field.replace("_", " ").capitalize()
                message = f"Paperless rejected the request: {label} does not exist"
                raise Validation(message)
            return lambda docs: self._reference(docs, change)
        if isinstance(change, FieldsChange):
            return self._plan_fields(change)
        if isinstance(change, TrashDocuments):
            return self._trash
        return lambda docs: state.reprocessed.extend(d.id for d in docs)

    @staticmethod
    def _reference(documents: list[FakeDocument], change: ReferenceChange) -> None:
        for document in documents:
            document.__dict__[change.field] = change.value

    def _tags(self, documents: list[FakeDocument], change: TagsChange) -> None:
        """Remove (with the descendants), then add (with the ancestors)."""
        tags = {t.id: t for t in self.state.tags}
        remove = {n for tag in change.remove for n in self._tag_tree(tag)}
        add: list[int] = []
        for tag in change.add:
            step: int | None = tag
            while step is not None and step not in add:
                add.append(step)
                step = tags[step].parent
        for document in documents:
            kept = [t for t in document.tags if t not in remove]
            document.tags = kept + [t for t in add if t not in kept]

    def _checked_values(
        self, change: FieldsChange, fields: dict[int, FakeCustomField]
    ) -> dict[int, JsonValue]:
        """The values in stored form, after the serializer's checks (400)."""
        documents = {d.id for d in self.state.documents}
        for where, wanted in (
            ("add_custom_fields", change.values),
            ("remove_custom_fields", change.remove),
        ):
            if not set(wanted) <= fields.keys():
                message = (
                    f"Paperless rejected the request: Some custom fields in {where} "
                    "don't exist or were specified twice."
                )
                raise Validation(message)
        stored: dict[int, JsonValue] = {}
        for number, value in change.values.items():
            try:
                stored[number] = coerce(fields[number], value, documents)
            except ValueError as error:
                reject(f"add_custom_fields.{number}.non_field_errors", str(error))

        return stored

    def _plan_fields(self, change: FieldsChange) -> Callable[[list[FakeDocument]], None]:
        fields = {f.id: f for f in self.state.custom_fields}
        stored = self._checked_values(change, fields)
        links = {n for n, f in fields.items() if f.data_type == "documentlink"}
        user = self.user
        if not user.is_superuser:
            # The serializer checks the linked documents too: change permission on all of them.
            by_id = {d.id: d for d in self.state.documents}
            for number in links & stored.keys():
                if any(not by_id[t].changeable_by(user) for t in _ids(stored[number])):
                    raise documents_refused()

        return lambda selected: self._set_fields(selected, stored, links, change.remove)

    def _set_fields(
        self,
        selected: list[FakeDocument],
        stored: dict[int, JsonValue],
        links: set[int],
        removed: tuple[int, ...],
    ) -> None:
        for document in selected:
            for number, value in stored.items():
                if number in links and document.id in _ids(value):
                    continue  # Paperless skips the field of a document that links itself
                document.custom_fields[number] = value
                if number in links:
                    self._link_back(document, number, _ids(value))
            for number in removed:
                old = document.custom_fields.pop(number, None)
                if number in links:
                    self._unlink_back(document, number, _ids(old))

    def _link_back(self, document: FakeDocument, number: int, targets: list[int]) -> None:
        """Add the reverse link on the targets; 3.4.2 never takes one from an old target."""
        by_id = {d.id: d for d in self.state.documents}
        for target in targets:
            other = by_id[target].custom_fields
            back = _ids(other.get(number))
            if document.id not in back:
                other[number] = [*back, document.id]

    def _unlink_back(self, document: FakeDocument, number: int, targets: list[int]) -> None:
        """Take the reverse link from the targets of a removed field."""
        by_id = {d.id: d for d in self.state.documents}
        for target in targets:
            if target in by_id and number in by_id[target].custom_fields:
                other = by_id[target].custom_fields
                other[number] = [n for n in _ids(other[number]) if n != document.id]

    def _trash(self, documents: list[FakeDocument]) -> None:
        for document in documents:
            self._to_trash(document)

    def _bulk_items(
        self, kind: BulkKind, item_ids: Sequence[int], action: str
    ) -> list[FakeTaxonomy]:
        """The selected objects, after validating the selection (400) and the permission (403)."""
        rows = {o.id: o for o in self._owned(kind)}
        if not item_ids:
            message = "Paperless rejected the request: objects must not be empty"
            raise Validation(message)
        if len(set(item_ids)) != len(item_ids) or not set(item_ids) <= rows.keys():
            message = (
                "Paperless rejected the request: "
                "Some ids in objects don't exist or were specified twice."
            )
            raise Validation(message)
        selected = [rows[number] for number in item_ids]
        user = self.user
        if not user.is_superuser:
            model = kind.replace("_", "")
            owns = all(o.owner in {None, user.id} for o in selected)
            if not (user.has(f"{action}_{model}") and owns):
                raise objects_refused()
        return selected

    @write
    async def delete_taxonomy_items(self, kind: BulkKind, item_ids: Sequence[int]) -> None:
        """`BulkWriter.delete_taxonomy_items` (BulkEditObjectsView, operation delete).

        400 for an empty, unknown or repeated selection; 403 without the global `delete_*`
        permission or for an object another user owns (hidden ones included).
        """
        _ = self._bulk_items(kind, item_ids, "delete")
        if kind == "tag" and any(self._takes_tags_of_others(number) for number in item_ids):
            raise objects_refused()
        for number in item_ids:
            self._remove(kind, number)

    @write
    async def set_taxonomy_permissions(
        self, kind: BulkKind, item_ids: Sequence[int], change: PermissionsChange
    ) -> None:
        """`BulkWriter.set_taxonomy_permissions` (BulkEditObjectsView, set_permissions).

        Same checks as a deletion with `change_*`, and 400 for an unknown or repeated user or
        group. Without ``merge`` the grants replace all existing ones. An owner is set when the
        request carries one (`null` removes it), except that ``merge`` only sets it on objects
        without an owner and never removes one.
        """
        state = self.state
        given = change.permissions
        if given is not None:
            lists = {
                "users": [*given.view_users, *given.change_users],
                "groups": [*given.view_groups, *given.change_groups],
            }
            known = {"users": {u.id for u in state.users}, "groups": {g.id for g in state.groups}}
            for name, wanted in lists.items():
                if not set(wanted) <= known[name]:
                    message = (
                        f"Paperless rejected the request: Some {name} in "
                        "don't exist or were specified twice."
                    )
                    raise Validation(message)
        if change.owner is not None and change.owner not in {u.id for u in state.users}:
            reject("owner", f'Invalid pk "{change.owner}" - object does not exist.')
        for item in self._bulk_items(kind, item_ids, "change"):
            if "owner" in change.model_fields_set and (
                not change.merge or (change.owner is not None and item.owner is None)
            ):
                item.owner = change.owner
            if given is not None:
                self._grant(item, given, merge=change.merge)

    @staticmethod
    def _grant(item: Owned, given: ObjectPermissions, *, merge: bool) -> None:
        """Replace the grants of ``item`` with ``given``, or add ``given`` to them.

        Granting change also grants view (3.4.2 `set_permissions_for_objects`).
        """
        views = set(given.view_users) | set(given.change_users)
        changes = set(given.change_users)
        view_groups = set(given.view_groups) | set(given.change_groups)
        change_groups = set(given.change_groups)
        if merge:
            views, changes = views | item.viewers, changes | item.editors
            view_groups, change_groups = (
                view_groups | item.view_groups,
                change_groups | item.change_groups,
            )
        item.viewers, item.editors = views, changes
        item.view_groups, item.change_groups = view_groups, change_groups

    # --- PermissionReader and PermissionWriter

    def _permission_objects(self, kind: PermissionKind) -> list[FakeDocument] | list[FakeTaxonomy]:
        state = self.state
        return cast(
            "list[FakeDocument] | list[FakeTaxonomy]",
            {
                "document": state.documents,
                "tag": state.tags,
                "correspondent": state.correspondents,
                "document_type": state.document_types,
                "storage_path": state.storage_paths,
            }[kind],
        )

    async def get_permissions(
        self, kind: PermissionKind, object_ids: Sequence[int]
    ) -> list[ObjectAccess]:
        """`PermissionReader.get_permissions` (the list views with `full_perms=true`).

        A hidden object is not listed; the list needs the global view permission (403).
        """
        if not self.user.has(f"view_{kind.replace('_', '')}"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        seen = {o.id: o for o in self._permission_objects(kind) if o.visible_to(self.user)}
        return [
            access_of(seen[number], _label(seen[number])) for number in object_ids if number in seen
        ]

    async def list_users(self, *, name_contains: str | None, page: int, page_size: int) -> UserPage:
        """`PermissionReader.list_users` (UserViewSet list): by username, 403 without view_user."""
        self._may_list("user")
        needle = (name_contains or "").casefold()
        users = sorted(
            (u for u in self.state.users if needle in u.username.casefold()),
            key=lambda u: (u.username.casefold(), u.id),
        )
        rows = tuple(
            UserRow(
                id=u.id,
                username=u.username,
                name=f"{u.first_name} {u.last_name}".strip() or None,
                is_active=u.is_active,
                is_superuser=u.is_superuser,
                group_ids=tuple(sorted(u.groups)),
            )
            for u in users
        )
        shown, more = self._page(rows, page, page_size)
        return UserPage(count=len(rows), next_page=more, rows=shown)

    async def list_groups(
        self, *, name_contains: str | None, page: int, page_size: int
    ) -> GroupPage:
        """`PermissionReader.list_groups` (GroupViewSet list): by name, 403 without view_group."""
        self._may_list("group")
        needle = (name_contains or "").casefold()
        groups = sorted(
            (g for g in self.state.groups if needle in g.name.casefold()),
            key=lambda g: (g.name.casefold(), g.id),
        )
        rows = tuple(GroupRow(id=g.id, name=g.name) for g in groups)
        shown, more = self._page(rows, page, page_size)
        return GroupPage(count=len(rows), next_page=more, rows=shown)

    def _may_list(self, kind: TaxonomyKind) -> None:
        if not self.user.has(VIEW_PERMISSION[kind]):
            message = "You do not have permission to perform this action."
            raise PermissionDenied(message)

    @staticmethod
    def _page[R](
        rows: tuple[R, ...], page: int, page_size: int
    ) -> tuple[tuple[R, ...], int | None]:
        """One page of ``rows`` and the next page number; a page past the end is a 404."""
        first = (page - 1) * page_size
        if page > 1 and first >= len(rows):
            message = "Invalid page."
            raise NotFound(message)
        more = page + 1 if first + page_size < len(rows) else None
        return rows[first : first + page_size], more

    @write
    async def set_permissions(
        self, kind: PermissionKind, object_id: int, change: AccessChange
    ) -> ObjectAccess:
        """`PermissionWriter.set_permissions` (a PATCH with `owner` and `set_permissions`).

        Order: the global change permission (403), the object among those the user may see (404),
        the values (400: unknown users, groups or owner, and a name another object of the new
        owner already has, as the serializer validates them), then ownership (403: a change
        grant is not enough).
        """
        if not self.user.has(f"change_{kind.replace('_', '')}"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        found = [o for o in self._permission_objects(kind) if o.id == object_id]
        if not found or not found[0].visible_to(self.user):
            message = "The Paperless object does not exist or is not visible to this user."
            raise NotFound(message)
        target = found[0]
        state = self.state
        given = change.permissions
        if given is not None:
            check_principals(given, state.users, state.groups)
        if "owner" in change.model_fields_set:
            if change.owner not in {None, *(u.id for u in state.users)}:
                reject("owner", f'Invalid pk "{change.owner}" - object does not exist.')
            clash = (
                o
                for o in self._permission_objects(kind)
                if o is not target and o.owner == change.owner and _label(o) == _label(target)
            )
            if kind != "document" and any(clash):
                reject("non_field_errors", "The fields name, owner must make a unique set.")
        if not (self.user.is_superuser or target.owner in {None, self.user.id}):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if "owner" in change.model_fields_set:
            target.owner = change.owner
        if given is not None:
            set_grants(target, given)
        return access_of(target, _label(target))

    # --- ViewReader and ViewWriter

    async def list_saved_views(self) -> tuple[SavedView, ...]:
        """`ViewReader.list_saved_views` (SavedViewViewSet list): visible views, by name.

        Raises:
            PermissionDenied: The user lacks the global `view_savedview` permission.
        """
        self._view_allowed("view")
        views = [v for v in self.state.saved_views if v.visible_to(self.user)]
        return tuple(self._saved_view(v) for v in sorted(views, key=lambda v: (v.name, v.id)))

    async def get_saved_view(self, view_id: int) -> SavedView:
        """`ViewReader.get_saved_view` (SavedViewViewSet retrieve).

        Raises:
            NotFound: No such view, or the user may not see it.
            PermissionDenied: The user lacks the global `view_savedview` permission.
        """
        self._view_allowed("view")
        return self._saved_view(self._visible_view(view_id))

    @write
    async def create_saved_view(self, name: str, values: ViewValues) -> SavedView:
        """`ViewWriter.create_saved_view` (SavedViewViewSet create): owned by the creator.

        Raises:
            PermissionDenied: The user lacks the global `add_savedview` permission.
            Validation: Paperless's 400 for a value, with its message.
        """
        self._view_allowed("add")
        name = check_name(name)
        check_rules(values.filter_rules or ())
        check_page_size(values.page_size)
        views = self.state.saved_views
        if any(v.name == name and v.owner == self.user.id for v in views):
            reject("error", "Object violates owner / name unique constraint")
        view = FakeSavedView(
            id=max((v.id for v in views), default=0) + 1,
            name=name,
            owner=self.user.id,
            filter_rules=values.filter_rules or (),
            sort_field=values.sort_field,
            sort_reverse=bool(values.sort_reverse),
            page_size=values.page_size,
        )
        views.append(view)
        return self._saved_view(view)

    @write
    async def update_saved_view(self, view_id: int, values: ViewValues) -> SavedView:
        """`ViewWriter.update_saved_view` (SavedViewViewSet partial update).

        Unlike a create, an update accepts a name another view of the user already has.

        Raises:
            NotFound: No such view, or the user may not see it.
            PermissionDenied: The user may not change saved views, or this one.
            Validation: Paperless's 400 for a value, with its message.
        """
        self._view_allowed("change")
        view = self._visible_view(view_id)
        if not self.user.may_change_object(view):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        sent = values.model_fields_set
        name = check_name(values.name) if values.name is not None else view.name
        check_rules(values.filter_rules or ())
        check_page_size(values.page_size)
        view.name = name
        if values.filter_rules is not None:
            view.filter_rules = values.filter_rules
        if "sort_field" in sent:
            view.sort_field = values.sort_field
        if values.sort_reverse is not None:
            view.sort_reverse = values.sort_reverse
        if "page_size" in sent:
            view.page_size = values.page_size
        return self._saved_view(view)

    @write
    async def delete_saved_view(self, view_id: int) -> None:
        """`ViewWriter.delete_saved_view` (SavedViewViewSet destroy).

        Raises:
            NotFound: No such view, or the user may not see it.
            PermissionDenied: The user may not delete saved views, or this one.
        """
        self._view_allowed("delete")
        view = self._visible_view(view_id)
        if not self.user.may_change_object(view):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        self.state.saved_views.remove(view)

    def _view_allowed(self, action: str) -> None:
        """The global permission of an action on saved views: `add_savedview`, ..."""
        if not self.user.has(f"{action}_savedview"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)

    def _visible_view(self, view_id: int) -> FakeSavedView:
        found = [v for v in self.state.saved_views if v.id == view_id and v.visible_to(self.user)]
        if not found:
            raise unseen_view(view_id)
        return found[0]

    def _saved_view(self, view: FakeSavedView) -> SavedView:
        return SavedView(
            id=view.id,
            name=view.name,
            sort_field=view.sort_field,
            sort_reverse=view.sort_reverse,
            page_size=view.page_size,
            owner=view.owner,
            user_can_change=self.user.may_change_object(view),
            filter_rules=view.filter_rules,
        )

    # --- SharingReader and SharingWriter (the logic is in fakes/sharing.py)

    def _sharing(self) -> SharingData:
        state = self.state
        return SharingData(
            state.share_links, state.share_bundles, state.outbox, state.email_enabled
        )

    def _viewer(self) -> Viewer:
        """The user and the documents as the sharing functions need them."""
        user, documents = self.user, [*self.state.documents, *self.state.trash]
        return Viewer(
            id=user.id,
            is_superuser=user.is_superuser,
            has=user.has,
            titles={d.id: d.title for d in documents},
            visible=frozenset(d.id for d in self.state.documents if d.visible_to(user)),
        )

    async def list_share_links(self) -> tuple[ShareLink, ...]:
        """`SharingReader.list_share_links`."""
        return sharing.list_links(self._sharing(), self._viewer())

    async def get_share_link(self, link_id: int) -> ShareLink:
        """`SharingReader.get_share_link`."""
        return sharing.get_link(self._sharing(), self._viewer(), link_id)

    async def list_share_bundles(self) -> tuple[ShareBundle, ...]:
        """`SharingReader.list_share_bundles`."""
        return sharing.list_bundles(self._sharing(), self._viewer())

    async def get_email_enabled(self) -> bool | None:
        """`SharingReader.get_email_enabled` (UiSettingsView; 403 without `view_uisettings`)."""
        return self.state.email_enabled if self.user.has("view_uisettings") else None

    async def get_share_bundle(self, bundle_id: int) -> ShareBundle:
        """`SharingReader.get_share_bundle`."""
        return sharing.get_bundle(self._sharing(), self._viewer(), bundle_id)

    @write
    async def create_share_link(
        self, document_id: int, *, expiration: datetime | None, file_version: FileVersionKind
    ) -> ShareLink:
        """`SharingWriter.create_share_link`."""
        return sharing.create_link(
            self._sharing(), self._viewer(), document_id, expiration, file_version
        )

    @write
    async def delete_share_link(self, link_id: int) -> None:
        """`SharingWriter.delete_share_link`."""
        sharing.delete_link(self._sharing(), self._viewer(), link_id)

    @write
    async def create_share_bundle(
        self,
        document_ids: Sequence[int],
        *,
        expiration_days: int | None,
        file_version: FileVersionKind,
    ) -> ShareBundle:
        """`SharingWriter.create_share_bundle`."""
        return sharing.create_bundle(
            self._sharing(), self._viewer(), document_ids, expiration_days, file_version
        )

    @write
    async def rebuild_share_bundle(self, bundle_id: int) -> ShareBundle:
        """`SharingWriter.rebuild_share_bundle`."""
        return sharing.rebuild_bundle(self._sharing(), self._viewer(), bundle_id)

    @write
    async def delete_share_bundle(self, bundle_id: int) -> None:
        """`SharingWriter.delete_share_bundle`."""
        sharing.delete_bundle(self._sharing(), self._viewer(), bundle_id)

    @write
    async def email_documents(
        self,
        document_ids: Sequence[int],
        *,
        addresses: Sequence[str],
        subject: str,
        message: str,
        use_archive_version: bool,
    ) -> None:
        """`SharingWriter.email_documents`."""
        sharing.email(
            self._sharing(),
            self._viewer(),
            document_ids,
            addresses,
            subject=subject,
            message=message,
            use_archive_version=use_archive_version,
        )

    # --- MailReader and MailWriter (the logic is in fakes/mail.py)

    def _mail(self) -> MailData:
        state = self.state
        return MailData(
            state.mail_accounts,
            state.mail_rules,
            state.processed_mail,
            state.mail_logins,
            state.mail_hosts,
            state.mail_silent_hosts,
            state.mail_fetches,
        )

    def _mail_viewer(self) -> MailViewer:
        state, user = self.state, self.user
        return MailViewer(
            id=user.id,
            is_superuser=user.is_superuser,
            has=user.has,
            users=frozenset(u.id for u in state.users),
            tags=frozenset(t.id for t in state.tags),
            correspondents=frozenset(c.id for c in state.correspondents),
            document_types=frozenset(d.id for d in state.document_types),
        )

    async def list_mail_accounts(self) -> tuple[MailAccount, ...]:
        """`MailReader.list_mail_accounts`."""
        return mail.list_accounts(self._mail(), self._mail_viewer())

    async def get_mail_account(self, account_id: int) -> MailAccount:
        """`MailReader.get_mail_account`."""
        return mail.get_account(self._mail(), self._mail_viewer(), account_id)

    async def list_mail_rules(self) -> tuple[MailRule, ...]:
        """`MailReader.list_mail_rules`."""
        return mail.list_rules(self._mail(), self._mail_viewer())

    async def get_mail_rule(self, rule_id: int) -> MailRule:
        """`MailReader.get_mail_rule`."""
        return mail.get_rule(self._mail(), self._mail_viewer(), rule_id)

    async def list_processed_mail(
        self, *, rule_id: int | None, status: ProcessedStatus | None, page: int, page_size: int
    ) -> ProcessedMailPage:
        """`MailReader.list_processed_mail`."""
        return mail.list_processed(
            self._mail(),
            self._mail_viewer(),
            rule_id=rule_id,
            status=status,
            page=page,
            page_size=page_size,
        )

    @write
    async def create_mail_account(self, values: MailAccountValues) -> MailAccount:
        """`MailWriter.create_mail_account`."""
        return mail.create_account(self._mail(), self._mail_viewer(), values)

    @write
    async def update_mail_account(self, account_id: int, values: MailAccountValues) -> MailAccount:
        """`MailWriter.update_mail_account`."""
        return mail.update_account(self._mail(), self._mail_viewer(), account_id, values)

    @write
    async def delete_mail_account(self, account_id: int) -> None:
        """`MailWriter.delete_mail_account`."""
        mail.delete_account(self._mail(), self._mail_viewer(), account_id)

    @write
    async def test_mail_account(self, account_id: int) -> MailAccountTestResult:
        """`MailWriter.test_mail_account`."""
        return mail.test_account(self._mail(), self._mail_viewer(), account_id)

    @write
    async def process_mail_account(self, account_id: int) -> None:
        """`MailWriter.process_mail_account`."""
        mail.process_account(self._mail(), self._mail_viewer(), account_id)

    @write
    async def create_mail_rule(self, values: MailRuleValues) -> MailRule:
        """`MailWriter.create_mail_rule`."""
        return mail.create_rule(self._mail(), self._mail_viewer(), values)

    @write
    async def update_mail_rule(self, rule_id: int, values: MailRuleValues) -> MailRule:
        """`MailWriter.update_mail_rule`."""
        return mail.update_rule(self._mail(), self._mail_viewer(), rule_id, values)

    @write
    async def delete_mail_rule(self, rule_id: int) -> None:
        """`MailWriter.delete_mail_rule`."""
        mail.delete_rule(self._mail(), self._mail_viewer(), rule_id)

    # --- WorkflowReader and WorkflowWriter (the logic is in fakes/workflows.py)

    def _workflows(self) -> tuple[WorkflowData, Actor]:
        state = self.state
        user = self.user
        data = WorkflowData(state.workflows, state.workflow_object_id)
        return data, Actor(
            has=user.has, known=lambda kind: frozenset(r.id for r in self._rows(kind))
        )

    async def list_workflows(self) -> tuple[Workflow, ...]:
        """`WorkflowReader.list_workflows`."""
        data, actor = self._workflows()
        return workflows.list_workflows(data, actor)

    async def get_workflow(self, workflow_id: int) -> Workflow:
        """`WorkflowReader.get_workflow`."""
        data, actor = self._workflows()
        return workflows.get_workflow(data, actor, workflow_id)

    @write
    async def create_workflow(self, name: str, values: WorkflowValues) -> Workflow:
        """`WorkflowWriter.create_workflow`."""
        data, actor = self._workflows()
        return workflows.create_workflow(data, actor, name, values)

    @write
    async def update_workflow(self, workflow_id: int, values: WorkflowValues) -> Workflow:
        """`WorkflowWriter.update_workflow`."""
        data, actor = self._workflows()
        return workflows.update_workflow(data, actor, workflow_id, values)

    @write
    async def delete_workflow(self, workflow_id: int) -> None:
        """`WorkflowWriter.delete_workflow`."""
        data, actor = self._workflows()
        workflows.delete_workflow(data, actor, workflow_id)

    # --- AdminReader and AdminWriter (the input rules are in fakes/admin.py)

    def _need(self, permission: str, action: tuple[str, str]) -> None:
        if not self.user.has(permission):
            raise denied(action)

    def _staff(self) -> None:
        if not (self.user.is_active and (self.user.is_staff or self.user.is_superuser)):
            raise denied(READ_LOGS)

    def _find_user(self, user_id: int) -> FakeUser:
        found = [u for u in self.state.users if u.id == user_id]
        if not found:
            raise unseen_user(user_id)
        return found[0]

    def _find_group(self, group_id: int) -> FakeGroup:
        found = [g for g in self.state.groups if g.id == group_id]
        if not found:
            raise unseen_group(group_id)
        return found[0]

    def _user_record(self, user: FakeUser) -> UserRecord:
        inherited = (
            set[str]()
            if user.is_superuser
            else {p for g in self.state.groups if g.id in user.groups for p in g.permissions}
        )
        return UserRecord(
            id=user.id,
            username=user.username,
            email=user.email or None,
            first_name=user.first_name,
            last_name=user.last_name,
            is_active=user.is_active,
            is_staff=user.is_staff,
            is_superuser=user.is_superuser,
            group_ids=tuple(sorted(user.groups)),
            permissions=tuple(sorted(user.permissions)),
            inherited_permissions=tuple(sorted(inherited)),
            date_joined=user.date_joined,
            mfa_enabled=False,
        )

    async def get_user(self, user_id: int) -> UserRecord:
        """`AdminReader.get_user` (UserViewSet retrieve)."""
        self._need("view_user", VIEW_USER)
        return self._user_record(self._find_user(user_id))

    async def get_current_user_id(self) -> int:
        """`AdminReader.get_current_user_id` (UiSettingsView: needs `view_uisettings`)."""
        if not self.user.has("view_uisettings"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        return self.user.id

    async def get_group(self, group_id: int) -> GroupRecord:
        """`AdminReader.get_group` (GroupViewSet retrieve)."""
        self._need("view_group", VIEW_GROUP)
        group = self._find_group(group_id)
        return GroupRecord(
            id=group.id, name=group.name, permissions=tuple(sorted(group.permissions))
        )

    def _config_record(self) -> AppConfigRecord:
        state = self.state
        return AppConfigRecord(
            id=1,
            externally_configured_variables=state.config_external,
            settings=dict(state.config),
            keys_configured=tuple(n for n in CONFIG_SECRETS if n in state.config_keys),
        )

    async def get_config(self) -> AppConfigRecord:
        """`AdminReader.get_config`: needs `view_applicationconfiguration` (403)."""
        self._need("view_applicationconfiguration", READ_CONFIG)
        return self._config_record()

    async def list_logs(self) -> tuple[str, ...]:
        """`AdminReader.list_logs` (LogViewSet list: staff only)."""
        self._staff()
        return tuple(self.state.logs)

    async def get_log(self, name: str, *, limit: int) -> tuple[str, ...]:
        """`AdminReader.get_log` (LogViewSet retrieve: the last `limit` lines)."""
        self._staff()
        if name not in self.state.logs:
            raise unseen_log(name)
        return tuple(self.state.logs[name][-limit:])

    async def list_tasks(
        self,
        *,
        state: TaskState | None,
        task_type: TaskType | None,
        acknowledged: bool | None,
        page: int,
        page_size: int,
    ) -> TaskRowPage:
        """`AdminReader.list_tasks` (TasksViewSet list, newest first)."""
        self._need("view_paperlesstask", VIEW_TASKS)
        tasks = sorted(self.state.tasks, key=lambda t: (t.created, t.id), reverse=True)
        rows = tuple(
            TaskRow(
                id=t.id,
                task_id=t.task_id,
                task_type=t.task_type,
                trigger_source=t.trigger_source,
                state=t.status,
                created=t.created,
                started=t.started,
                finished=t.finished,
                duration_seconds=t.duration_seconds,
                document_ids=t.document_ids,
                acknowledged=t.acknowledged,
                message=t.message if t.duplicate_of is None else REJECTED,
            )
            for t in tasks
            if (state is None or t.status == state)
            and (task_type is None or t.task_type == task_type)
            and (acknowledged is None or t.acknowledged == acknowledged)
        )
        try:
            shown, more = self._page(rows, page, page_size)
        except NotFound:
            raise beyond_tasks(page) from None
        return TaskRowPage(count=len(rows), next_page=more, rows=shown)

    async def download_documents(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        max_bytes: int,
    ) -> ArchiveFile:
        """`AdminReader.download_documents` (BulkDownloadView): one stored archive.

        The order is Paperless's (newest created first) whatever the order of the ids; documents
        created on the same day come as the database has them, which on PostgreSQL an edit
        changes (probed on 3.5.1): here that is the order of `state.documents`.
        """
        del compression
        self._need("view_document", DOWNLOAD)
        known = {d.id: d for d in self.state.documents}
        if len(set(document_ids)) != len(document_ids) or not set(document_ids) <= known.keys():
            reject("documents", "Some documents in documents don't exist or were specified twice.")
        chosen = [known[number] for number in document_ids]
        if not all(d.visible_to(self.user) for d in chosen):
            raise denied(DOWNLOAD)
        facts = {d: await FakePaperless.get_document_metadata(self, d) for d in document_ids}
        lost = missing_files(facts, ARCHIVE_FILES[content], len(facts), choice=True)
        if lost is not None:
            raise lost
        files: list[tuple[str, bytes]] = []
        stored = [d for d in self.state.documents if d in chosen]
        for doc in sorted(stored, key=lambda d: d.created, reverse=True):
            archived = doc.archive is not None
            name = (
                doc.download_filename(archive=False) if follow_formatting else doc.original_filename
            )
            if content in {"originals", "both"} or not archived:
                files.append((name, doc.original))
            if content in {"archive", "both"} and doc.archive is not None:
                prefix = "archive/" if content == "both" else ""
                files.append((f"{prefix}{PurePath(name).stem}.pdf", doc.archive))
        archive = archive_of(files)
        if len(archive) > max_bytes:
            return ArchiveFile(content=b"", truncated=True)
        return ArchiveFile(content=archive, truncated=False)

    @contextlib.asynccontextmanager
    async def open_archive(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent,
        compression: Compression,
        follow_formatting: bool,
        wait_seconds: float,
    ) -> AsyncGenerator[OpenedFile]:
        """`AdminReader.open_archive`: the archive of `download_documents` in chunks."""
        del wait_seconds
        file = await self.download_documents(
            document_ids,
            content=content,
            compression=compression,
            follow_formatting=follow_formatting,
            max_bytes=sys.maxsize,
        )
        yield OpenedFile("application/zip", "documents.zip", self._chunks(file.content))

    @write
    async def create_user(self, values: UserValues) -> UserRecord:
        """`AdminWriter.create_user` (UserViewSet create)."""
        self._need("add_user", ADD_USER)
        if not self.user.is_superuser and (values.is_superuser or values.is_staff):
            raise denied(ADD_USER)
        user = self._apply_user(None, values)
        self.state.users.append(user)
        return self._user_record(user)

    def _apply_user(  # noqa: C901 -- one rule per field
        self, current: FakeUser | None, values: UserValues
    ) -> FakeUser:
        state, sent = self.state, values.model_fields_set
        others = {u.username for u in state.users if current is None or u.id != current.id}
        changes: dict[str, object] = {}
        if "username" in sent or current is None:
            check_username(values.username or "", others)
            changes["username"] = values.username
        if "email" in sent:
            check_email(values.email or "")
            changes["email"] = values.email or ""
        for name in ("first_name", "last_name", "is_active", "is_staff", "is_superuser"):
            if name in sent and getattr(values, name) is not None:
                changes[name] = getattr(values, name)
        if values.group_ids is not None:
            known = {g.id for g in state.groups}
            for number in values.group_ids:
                if number not in known:
                    reject("groups", f'Invalid pk "{number}" - object does not exist.')
            changes["groups"] = frozenset(values.group_ids)
        if values.permissions is not None:
            check_permissions(values.permissions)
            changes["permissions"] = frozenset(values.permissions)
        if values.password is not None:
            check_secret(values.password.get_secret_value(), "password")
            changes["has_password"] = True
        if current is None:
            number = max((u.id for u in state.users), default=1) + 1
            return FakeUser(id=number, **changes)  # pyright: ignore[reportArgumentType] -- fields come from a dict of checked changes
        updated = replace(current, **changes)
        state.users[state.users.index(current)] = updated
        return updated

    @write
    async def update_user(self, user_id: int, values: UserValues) -> UserRecord:
        """`AdminWriter.update_user` (UserViewSet partial_update)."""
        self._need("change_user", CHANGE_USER)
        target = self._find_user(user_id)
        if not self.user.is_superuser:
            flags = (values.is_superuser, values.is_staff)
            if target.is_superuser or (
                flags != (None, None)
                and (
                    values.is_superuser not in {None, target.is_superuser}
                    or values.is_staff not in {None, target.is_staff}
                )
            ):
                raise denied(CHANGE_USER)
        return self._user_record(self._apply_user(target, values))

    def _forget(self, *, user: int | None = None, group: int | None = None) -> None:
        """Take a deleted user or group out of owners and grants, like Paperless."""
        state = self.state
        owned: list[Owned] = [
            *state.documents,
            *state.trash,
            *state.tags,
            *state.correspondents,
            *state.document_types,
            *state.storage_paths,
            *state.saved_views,
        ]
        for obj in owned:
            if user is not None:
                if obj.owner == user:
                    obj.owner = None
                obj.viewers.discard(user)
                obj.editors.discard(user)
            if group is not None:
                obj.view_groups.discard(group)
                obj.change_groups.discard(group)

    @write
    async def delete_user(self, user_id: int) -> None:
        """`AdminWriter.delete_user` (UserViewSet destroy): what the user owned loses its owner."""
        self._need("delete_user", DELETE_USER)
        target = self._find_user(user_id)
        self.state.users.remove(target)
        self._forget(user=user_id)
        for row in self.state.copying:
            row.owner = None if row.owner == user_id else row.owner

    @write
    async def create_group(self, values: GroupValues) -> GroupRecord:
        """`AdminWriter.create_group` (GroupViewSet create)."""
        self._need("add_group", ADD_GROUP)
        check_group_name(values.name or "", {g.name for g in self.state.groups})
        check_permissions(values.permissions or ())
        number = max((g.id for g in self.state.groups), default=0) + 1
        group = FakeGroup(
            id=number, name=values.name or "", permissions=frozenset(values.permissions or ())
        )
        self.state.groups.append(group)
        return GroupRecord(
            id=group.id, name=group.name, permissions=tuple(sorted(group.permissions))
        )

    @write
    async def update_group(self, group_id: int, values: GroupValues) -> GroupRecord:
        """`AdminWriter.update_group` (GroupViewSet partial_update)."""
        self._need("change_group", CHANGE_GROUP)
        target = self._find_group(group_id)
        name, permissions = target.name, target.permissions
        if "name" in values.model_fields_set:
            check_group_name(
                values.name or "", {g.name for g in self.state.groups if g is not target}
            )
            name = values.name or ""
        if values.permissions is not None:
            check_permissions(values.permissions)
            permissions = frozenset(values.permissions)
        updated = replace(target, name=name, permissions=permissions)
        self.state.groups[self.state.groups.index(target)] = updated
        return GroupRecord(id=updated.id, name=name, permissions=tuple(sorted(permissions)))

    @write
    async def delete_group(self, group_id: int) -> None:
        """`AdminWriter.delete_group` (GroupViewSet destroy): members and grants lose it."""
        self._need("delete_group", DELETE_GROUP)
        target = self._find_group(group_id)
        self.state.groups.remove(target)
        state = self.state
        for index, user in enumerate(state.users):
            if group_id in user.groups:
                state.users[index] = replace(user, groups=user.groups - {group_id})
        self._forget(group=group_id)

    @write
    async def update_config(self, values: ConfigValues) -> AppConfigRecord:
        """`AdminWriter.update_config`: a PATCH needs the change permission."""
        self._need("change_applicationconfiguration", CHANGE_CONFIG)
        state = self.state
        settings = apply_settings(state.config, values.settings)
        for secret in values.secrets.values():
            check_secret(secret.get_secret_value(), "API key")
        state.config = settings
        for name, secret in values.secrets.items():
            if name in CONFIG_SECRETS and secret.get_secret_value():
                state.config_keys.add(name)
        for name, setting in values.settings.items():
            if name in CONFIG_SECRETS and setting is None:
                state.config_keys.discard(name)
        return self._config_record()

    @write
    async def acknowledge_tasks(self, task_ids: Sequence[int]) -> int:
        """`AdminWriter.acknowledge_tasks` (TasksViewSet acknowledge)."""
        self._need("change_paperlesstask", ACK_TASKS)
        known = {t.id: t for t in self.state.tasks}
        if len(set(task_ids)) != len(task_ids) or not set(task_ids) <= known.keys():
            reject("tasks", "Some tasks in tasks don't exist or were specified twice.")
        for number in task_ids:
            known[number].acknowledged = True
        return len(task_ids)

    @write
    async def run_task(self, task_type: RunnableTask) -> str:
        """`AdminWriter.run_task` (TasksViewSet run): superusers only, three task types."""
        if not self.user.is_superuser:
            raise denied(RUN_TASKS)
        if task_type not in {"train_classifier", "sanity_check", "llm_index"}:
            reject("task_type", f'"{task_type}" cannot be manually triggered')
        state = self.state
        task = FakeTask(
            id=max((t.id for t in state.tasks), default=0) + 1,
            task_id=str(uuid.uuid4()),
            task_type=task_type,
            trigger_source="manual",
            created=datetime.now(UTC),
        )
        state.tasks.append(task)
        return task.task_id

    async def get_document_content(
        self, document_id: int, *, version_id: int | None = None
    ) -> DocumentText:
        """`DocumentReader.get_document_content` (retrieve with `fields`); listed versions only."""
        document = self._seen(document_id)
        newest = document.versions[0].id
        wanted = newest if version_id is None else version_id
        if wanted not in [v.id for v in document.versions]:
            raise unseen_version(document_id, wanted)
        return DocumentText(
            id=document_id,
            title=document.title,
            version_id=wanted,
            versions=tuple(document.versions),
            user_can_change=document.changeable_by(self.user),
            content=(
                document.content if wanted == newest else document.version_files[wanted].content
            ),
        )

    async def get_version_pages(self, document_id: int, version_id: int) -> int | None:
        """`DocumentReader.get_version_pages` (retrieve with `version`); listed versions only."""
        document = self._seen(document_id)
        if version_id not in [v.id for v in document.versions]:
            raise unseen_version(document_id, version_id)
        return self._file_of(document, version_id).page_count

    async def list_document_notes(self, document_id: int) -> tuple[Note, ...]:
        """`DocumentReader.list_document_notes` (the `notes` action: `view_note` is required)."""
        return tuple(self._sub(document_id, "view_note").notes)

    async def list_document_history(self, document_id: int) -> tuple[HistoryEntry, ...]:
        """`DocumentReader.list_document_history` (the `history` action).

        Order in Paperless 3.4.2: `view_document` (viewset), audit log enabled (400), the
        document exists (404, even without `view_logentry`), then `view_logentry` and ownership:
        an object-level view grant is not enough, the document must be unowned, the user's own,
        or the user a superuser (403). The create entry's `content` (OCR text) is dropped.
        """
        user = self.user
        if not user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not self.state.audit_log_enabled:
            message = "The Paperless audit log is disabled on this instance."
            raise FeatureUnavailable(message, hint="Leave history out of include.")
        document = self._document(document_id)
        owns = document.owner in {None, user.id} or user.is_superuser
        if not (user.has("view_logentry") and owns):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        entries = document.history
        return tuple(
            entry.model_copy(
                update={
                    "changes": {k: v for k, v in entry.changes.items() if k != "content"},
                }
            )
            for entry in entries
        )

    def _file_of(self, document: FakeDocument, version_id: int | None) -> FakeFile:
        """The file of a version; like Paperless it finds a deleted one, too (`include_deleted`)."""
        wanted = document.versions[0].id if version_id is None else version_id
        if wanted in document.version_files:
            return document.version_files[wanted]
        stubs = [d for d in self.state.trash if d.id == wanted and d.root_id == document.id]
        if not stubs and wanted not in {v.id for v in document.versions}:
            raise unseen_version(document.id, wanted)
        shown = stubs[0] if stubs else document
        return shown.shown_file()

    async def get_document_metadata(
        self, document_id: int, *, version_id: int | None = None
    ) -> FileMetadata:
        """`DocumentReader.get_document_metadata` (the `metadata` action)."""
        document = self._sub(document_id)
        file = self._file_of(document, version_id)
        number = document.versions[0].id if version_id is None else version_id
        lost, named = self.state.missing, number not in self.state.nameless
        # Without a name Paperless takes the default one, {id:07}.{type}: the file is found.
        original = (number, "original") not in lost
        archive = file.archive if (number, "archive") not in lost else None
        return FileMetadata(
            original_filename=(file.file_name or document.original_filename) if named else None,
            original_mime_type=file.mime_type,
            original_size=len(file.original) if original else None,
            original_checksum=sha256(file.original),
            media_filename=f"e2e/2026/{document.original_filename}" if named else None,
            has_archive_version=file.archive is not None,
            archive_size=None if archive is None else len(archive),
            archive_checksum=None if file.archive is None else sha256(file.archive),
            archive_media_filename=(
                None if file.archive is None else f"e2e/2026/{document.original_filename}"
            ),
        )

    async def get_document_thumbnail(self, document_id: int) -> DocumentFile:
        """`DocumentReader.get_document_thumbnail` (the `thumb` action: WebP, numbered name).

        It finds documents and versions in the trash, too (`include_deleted`); what is missing of
        them cannot be told from what does not exist, because their metadata is a 404.
        """
        document = self._sub(document_id, trashed=True)
        trashed = any(d is document for d in self.state.trash)
        shown = document.id if trashed else document.versions[0].id
        if (shown, "thumbnail") in self.state.missing:
            if trashed:
                raise unseen_document(document_id)
            message = f"Paperless cannot find the thumbnail of document {document_id}."
            raise NotFound(message, hint=NO_THUMBNAIL_HINT)
        return DocumentFile(
            content=document.thumbnail,
            mime_type="image/webp",
            filename=f"{document.id:07d}.webp",
        )

    async def get_document_file(
        self,
        document_id: int,
        version: FileVersion,
        *,
        max_bytes: int,
        version_id: int | None = None,
    ) -> DocumentFile:
        """`DocumentReader.get_document_file` (the `download` action).

        Without an archive version `archive` answers the original, as Paperless does. It finds
        documents and versions in the trash, too; of a document there the first version's file.
        """
        document = self._sub(document_id, trashed=True)
        if version_id is None and any(d is document for d in self.state.trash):
            file = self._first_file(document)
            sent = "archive" if version == "archive" and file.archive is not None else "original"
            if (document.id, sent) in self.state.missing:
                raise unseen_document(document_id)
        else:
            file = self._file_of(document, version_id)
            facts = await FakePaperless.get_document_metadata(
                self, document_id, version_id=version_id
            )
            if facts.lacks(version):
                raise missing_file(document_id, version, facts, version_id)
        archive = version == "archive" and file.archive is not None
        mime_type = file.mime_type
        if mime_type.startswith("text/"):
            mime_type += "; charset=utf-8"
        content = file.archive if archive and file.archive else file.original
        return DocumentFile(
            content=b"" if len(content) > max_bytes else content,
            mime_type="application/pdf" if archive else mime_type,
            filename=document.download_filename(archive=archive),
            truncated=len(content) > max_bytes,
        )

    @contextlib.asynccontextmanager
    async def open_document_file(
        self, document_id: int, version: FileVersion, *, version_id: int | None = None
    ) -> AsyncGenerator[OpenedFile]:
        """`DocumentReader.open_document_file`: the file of `get_document_file` in chunks."""
        file = await self.get_document_file(
            document_id, version, max_bytes=sys.maxsize, version_id=version_id
        )
        yield OpenedFile(file.mime_type, file.filename, self._chunks(file.content))

    async def _chunks(self, content: bytes) -> AsyncGenerator[bytes]:
        for number, start in enumerate(range(0, len(content), CHUNK)):
            if number == self.state.break_after:
                message = "Paperless could not be reached."
                raise UpstreamUnreachable(message)
            yield content[start : start + CHUNK]

    async def get_document_titles(self, document_ids: Collection[int]) -> dict[int, str]:
        """`DocumentReader.get_document_titles` (the list view filtered by `id__in`)."""
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        wanted = set(document_ids)
        return {
            d.id: d.title
            for d in self.state.documents
            if d.id in wanted and d.visible_to(self.user)
        }

    async def get_newest_versions(self, document_ids: Collection[int]) -> dict[int, int]:
        """`DocumentReader.get_newest_versions` (the list view filtered by `id__in`)."""
        wanted = await self.get_document_titles(document_ids)
        return {
            d.id: d.versions[0].id if d.versions else d.id
            for d in self.state.documents
            if d.id in wanted
        }

    async def get_document_suggestions(self, document_id: int) -> Suggestions:
        """`DocumentReader.get_document_suggestions` (the `suggestions` action)."""
        return self._sub(document_id).suggestions

    async def ask(self, question: str, *, document_id: int | None) -> ChatAnswer:
        """`AiReader.ask` (ChatStreamingView), answering from the documents that match.

        Paperless checks the global `view_document` permission, then that AI is on, then the
        document (unknown: 400, hidden: 403). The language model is replaced by a lookup: the
        documents (at most three) that share a word with the question are the sources, and the
        answer lists their titles; none match, Paperless's no-content sentence.
        """
        if not self.user.has("view_document"):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not self.state.ai_enabled:
            raise ai_disabled()
        if document_id is None:
            candidates = [d for d in self.state.documents if d.visible_to(self.user)]
        else:
            candidates = [self._sub(document_id)]
        words = _words(question)
        found = [d for d in candidates if words & _words(f"{d.title} {d.content}")][:3]
        if not found:
            return ChatAnswer(answer=CHAT_NO_CONTENT, truncated=False, sources=())
        titles = ", ".join(d.title for d in found)
        return ChatAnswer(
            answer=f"The documents that match are: {titles}.",
            truncated=False,
            sources=tuple(ChatSource(id=d.id, title=d.title) for d in found),
        )

    async def get(self, path: str, params: Mapping[str, str], *, max_bytes: int) -> RawReply:
        """`RawReader.get`, for the few paths `fakes/raw.py` serves; the rest is a 404."""
        match = re.fullmatch(r"/api/documents/(\d+)/download/?", path)
        document = self._sub(int(match[1])) if match else None
        tags = [(t.id, t.name) for t in self.state.tags if t.visible_to(self.user)]
        file = (document.original, document.mime_type) if document else None
        seen = [c for c in self.state.copying if c.owner in {None, self.user.id}]
        rows: list[dict[str, object]] = [
            {
                "task_id": c.request,
                "task_type": "bulk_update",
                "owner": c.owner,
                "input_data": {"operation": c.operation, "document_count": c.count},
            }
            for c in seen
        ]
        return raw.answer(path, params, tags, file, rows, max_bytes=max_bytes)

    async def get_ai_suggestions(self, document_id: int) -> AiSuggestions:
        """`AiReader.get_ai_suggestions` (the `ai_suggestions` action).

        Paperless needs `change_document` on the document (403) before it looks at AI (400):
        the owner, anyone for an unowned document, or a user with a change grant.
        """
        document = self._sub(document_id)
        if not document.changeable_by(self.user):
            message = "The Paperless user may not perform this action."
            raise PermissionDenied(message)
        if not self.state.ai_enabled:
            raise ai_disabled()
        return document.ai_suggestions


type _Match = Callable[[FakeDocument], bool]


def _check_edit(file: FakeFile, count: int | None, pages: Sequence[PageSlot]) -> list[int]:
    """Paperless's 400s for the pages of an edit; returns the numbers of the output files.

    ``count`` is the page count of the row that was sent; without one no page is checked.
    """
    if not pages:
        reject("operations", "This list may not be empty.")
    for slot in pages:
        if slot.rotate % 90:
            reject("operations", "rotate must be a multiple of 90")
    files = sorted({slot.document for slot in pages})
    if files != list(range(len(files))):
        reject("non_field_errors", "doc index is out of bounds")
    if file.page_count is None or not file.mime_type.endswith("pdf"):
        raise not_editable()
    for slot in pages:
        if slot.page < 1 or (count is not None and slot.page > count):
            reject(
                "non_field_errors",
                f"Page {slot.page} is out of bounds for document with {count} pages.",
            )
    return files


def _bounded(answer: str, max_bytes: int) -> None:
    if len(answer.encode()) > max_bytes:
        message = f"Paperless sent more than {max_bytes} bytes for GET /api/documents/."
        raise AnswerTooLarge(message)


def _filters(query: DocumentQuery, state: FakeState) -> list[_Match]:
    """The tests of the structured filters `query` sets."""
    inbox = {tag.id for tag in state.tags if tag.is_inbox}
    checks: list[tuple[object, _Match]] = [
        (query.title_contains, lambda d: (query.title_contains or "").lower() in d.title.lower()),
        (query.correspondent, lambda d: d.correspondent == query.correspondent),
        (query.document_type, lambda d: d.document_type == query.document_type),
        (query.storage_path, lambda d: d.storage_path == query.storage_path),
        (query.tags_all, lambda d: set(query.tags_all) <= set(d.tags)),
        (query.tags_any, lambda d: bool(set(query.tags_any) & set(d.tags))),
        (query.tags_none, lambda d: not set(query.tags_none) & set(d.tags)),
        (
            query.created_from,
            lambda d: query.created_from is None or d.created >= query.created_from,
        ),
        (query.created_to, lambda d: query.created_to is None or d.created <= query.created_to),
        (
            query.added_from,
            lambda d: query.added_from is None or d.added.date() >= query.added_from,
        ),
        (query.added_to, lambda d: query.added_to is None or d.added.date() <= query.added_to),
        (query.owner, lambda d: d.owner == query.owner),
        (query.inbox, lambda d: bool(inbox & set(d.tags)) == query.inbox),
        (query.ids, lambda d: d.id in query.ids),
    ]
    return [match for value, match in checks if value not in {None, ()}]


# Words Tantivy reads as operators, not as terms.
TANTIVY_OPERATORS = frozenset({"and", "or", "not"})


def _words(text: str) -> set[str]:
    return {word.lower() for word in cast("list[str]", re.findall(r"\w+", text))}


def _highlight(content: str, terms: set[str]) -> str | None:
    marked = re.sub(r"\w+", lambda m: f"<b>{m[0]}</b>" if m[0].lower() in terms else m[0], content)
    return marked if marked != content else None


def _ordered(
    documents: list[FakeDocument], ordering: str | None, *, ranked: object
) -> list[FakeDocument]:
    if ordering is None:
        return (
            documents
            if ranked
            else sorted(documents, key=lambda d: (d.created, d.id), reverse=True)
        )
    name = ordering.removeprefix("-")

    def key(document: FakeDocument) -> tuple[bool, SupportsRichComparison, int]:
        # The native value, so numbers sort as numbers (ASN 2 before 10); None sorts last.
        value = cast("SupportsRichComparison | None", getattr(document, name))
        return (value is None, 0 if value is None else value, document.id)

    return sorted(documents, key=key, reverse=ordering.startswith("-"))


def _record(
    document: FakeDocument, user: FakeUser, *, report_change: bool = True
) -> DocumentRecord:
    return DocumentRecord(
        id=document.id,
        title=document.title,
        created=document.created,
        added=document.added,
        modified=document.modified,
        archive_serial_number=document.archive_serial_number,
        owner=document.owner,
        correspondent=document.correspondent,
        document_type=document.document_type,
        storage_path=document.storage_path,
        tags=tuple(document.tags),
        custom_fields=tuple(
            CustomFieldValue(field=number, value=value)
            for number, value in document.custom_fields.items()
        ),
        original_file_name=document.original_filename,
        mime_type=document.mime_type,
        page_count=document.page_count,
        notes_count=len(document.notes),
        versions=tuple(document.versions),
        user_can_change=document.changeable_by(user) if report_change else None,
    )


def _ids(value: JsonValue) -> list[int]:
    """The document ids of a document-link value (nothing for any other value)."""
    return [n for n in value if isinstance(n, int)] if isinstance(value, list) else []


def _hit(document: FakeDocument, user: FakeUser, highlights: str | None) -> DocumentHit:
    return DocumentHit(
        id=document.id,
        title=document.title,
        created=document.created,
        added=document.added,
        correspondent=document.correspondent,
        document_type=document.document_type,
        storage_path=document.storage_path,
        tags=tuple(document.tags),
        custom_fields=tuple(
            CustomFieldValue(field=number, value=value)
            for number, value in document.custom_fields.items()
        ),
        highlights=highlights,
        user_can_change=document.changeable_by(user),
    )
