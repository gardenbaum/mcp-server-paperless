"""The backends the contract suite runs against: FakePaperless and the adapter on live Paperless.

`just check` runs the `fake` backend; `just e2e` runs `live` (marker `e2e`) against the seeded
stack. Both hold the same seed (tests/e2e/helpers.py `seed` and its `SEEDED_OBJECTS`
manifest, mirrored by `fakes.paperless.seeded`), so every contract test makes the same
assertions against both.

Isolation on the live stack, which persists between runs and is shared by every E2E test:
- Never change or delete a seed object, and never assert exact instance-wide counts; rely on the
  seed manifest as lower bounds, or on objects the test created itself.
- A test that writes creates objects with a name unique to the test run (e.g. with a uuid
  suffix) and deletes them again in a fixture finaliser, also when the test fails.

Adding a port: add a field for it to `Ports` and wire it in `fake_ports` and `live_ports`,
implement it in FakePaperless and the adapter, and write its contract tests with
`@pytest.mark.contract("Port.method")` and the `backend` fixture. test_coverage.py fails until
every port is wired and every method has such a test.
"""

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

import httpx2
import pytest
from e2e.helpers import (
    ENCRYPTED_PASSWORD,
    ENCRYPTED_PDF,
    EXPECTED_VERSION,
    consume,
    consume_text,
    in_container,
    lose_file,
    pdf_pages,
    rebuild_search_index,
    results,
)
from fakes.accounts import ADMIN, BLIND, READER
from fakes.mail import FakeMailAccount, FakeProcessedMail
from fakes.paperless import (
    BROKEN_PDF,
    VERSION,
    FakeDocument,
    FakePaperless,
    FakeState,
    FakeTask,
    FakeUser,
    counted_pages,
    seeded,
)
from ratelimit import LimitingTransport, RateLimit, limited_ports

from mcp_server_paperless.domain.bulk import ObjectPermissions
from mcp_server_paperless.domain.ports import (  # noqa: TC001 -- Ports' annotations are introspected
    AdminReader,
    AdminWriter,
    AiReader,
    BulkWriter,
    DocumentReader,
    DocumentWriter,
    InstanceReader,
    MailReader,
    MailWriter,
    PdfWriter,
    PermissionReader,
    PermissionWriter,
    RawReader,
    SharingReader,
    SharingWriter,
    SystemReportReader,
    TaskListReader,
    TaskListWriter,
    TaskReader,
    TaxonomyReader,
    TaxonomyWriter,
    TrashReader,
    TrashWriter,
    UiSettingsReader,
    UiSettingsWriter,
    ViewReader,
    ViewWriter,
    WorkflowReader,
    WorkflowWriter,
)
from mcp_server_paperless.paperless.admin import PaperlessAdminReader, PaperlessAdminWriter
from mcp_server_paperless.paperless.ai import PaperlessAiReader
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.paperless.bulk import PaperlessBulkWriter
from mcp_server_paperless.paperless.client import PaperlessClient, RetryPolicy
from mcp_server_paperless.paperless.documents import (
    PaperlessDocumentReader,
    PaperlessDocumentWriter,
)
from mcp_server_paperless.paperless.instance import PaperlessInstanceReader
from mcp_server_paperless.paperless.mail import PaperlessMailReader, PaperlessMailWriter
from mcp_server_paperless.paperless.pdf import PaperlessPdfWriter
from mcp_server_paperless.paperless.permissions import (
    PaperlessPermissionReader,
    PaperlessPermissionWriter,
)
from mcp_server_paperless.paperless.raw import PaperlessRawReader
from mcp_server_paperless.paperless.sharing import PaperlessSharingReader, PaperlessSharingWriter
from mcp_server_paperless.paperless.task_lists import (
    PaperlessTaskListReader,
    PaperlessTaskListWriter,
)
from mcp_server_paperless.paperless.tasks import PaperlessTaskReader
from mcp_server_paperless.paperless.taxonomy import PaperlessTaxonomyReader, PaperlessTaxonomyWriter
from mcp_server_paperless.paperless.trash import PaperlessTrashReader, PaperlessTrashWriter
from mcp_server_paperless.paperless.ui_settings import (
    PaperlessUiSettingsReader,
    PaperlessUiSettingsWriter,
)
from mcp_server_paperless.paperless.views import PaperlessViewReader, PaperlessViewWriter
from mcp_server_paperless.paperless.workflows import (
    PaperlessWorkflowReader,
    PaperlessWorkflowWriter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence

    from e2e.helpers import FilePart, Stack

    from mcp_server_paperless.domain.bulk import BulkKind


# The access token of `Backend.new_token_account`, which nothing may show, and when it expires.
TOKEN = "contract-acc3ss-t0ken"
EXPIRES = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
# Every tag a contract test creates starts with this; the live backend deletes them again.
SCRATCH_TAG = "contract-tag-"
# The same for every other kind of taxonomy object (and tags that are not about tags as such).
SCRATCH_ITEM = "contract-item-"
# The same for saved views, which Paperless cannot filter by name: the cleanup lists them.
SCRATCH_VIEW = "contract-view-"
# The same for workflows. A live workflow acts on documents other tests consume, so the tests make
# them disabled or with a trigger nothing matches.
SCRATCH_WORKFLOW = "contract-workflow-"
SCRATCH_PATHS = (
    "/api/tags/",
    "/api/correspondents/",
    "/api/document_types/",
    "/api/storage_paths/",
    "/api/custom_fields/",
)


@dataclass(frozen=True)
class Ports:
    """One credential's implementation of every port, one field per port."""

    instance: InstanceReader
    taxonomy: TaxonomyReader
    documents: DocumentReader
    tasks: TaskReader
    document_writes: DocumentWriter
    taxonomy_writes: TaxonomyWriter
    bulk_writes: BulkWriter
    ai: AiReader
    views: ViewReader
    view_writes: ViewWriter
    ui_settings: UiSettingsReader
    ui_setting_writes: UiSettingsWriter
    trash: TrashReader
    trash_writes: TrashWriter
    permissions: PermissionReader
    permission_writes: PermissionWriter
    pdf_writes: PdfWriter
    sharing: SharingReader
    sharing_writes: SharingWriter
    mail: MailReader
    mail_writes: MailWriter
    raw: RawReader
    workflows: WorkflowReader
    workflow_writes: WorkflowWriter
    management: AdminReader
    management_writes: AdminWriter
    reports: SystemReportReader
    task_lists: TaskListReader
    task_list_writes: TaskListWriter


def fake_ports(state: FakeState, user: str) -> Ports:
    """The fake implements every port itself; only its writer ports may write."""
    fake = FakePaperless(state, user=user)
    writer = FakePaperless(state, user=user, allow_writes=True)
    return Ports(
        instance=fake,
        taxonomy=fake,
        documents=fake,
        tasks=fake,
        document_writes=writer,
        taxonomy_writes=writer,
        bulk_writes=writer,
        ai=fake,
        views=fake,
        view_writes=writer,
        ui_settings=fake,
        ui_setting_writes=writer,
        trash=fake,
        trash_writes=writer,
        permissions=fake,
        permission_writes=writer,
        pdf_writes=writer,
        sharing=fake,
        sharing_writes=writer,
        mail=fake,
        mail_writes=writer,
        raw=fake,
        workflows=fake,
        workflow_writes=writer,
        management=fake,
        management_writes=writer,
        reports=fake,
        task_lists=fake,
        task_list_writes=writer,
    )


def live_ports(client: PaperlessClient) -> Ports:
    """The adapter, one object per port, all on `client`."""
    return Ports(
        instance=PaperlessInstanceReader(client),
        taxonomy=PaperlessTaxonomyReader(client),
        documents=PaperlessDocumentReader(client),
        tasks=PaperlessTaskReader(client),
        document_writes=PaperlessDocumentWriter(client),
        taxonomy_writes=PaperlessTaxonomyWriter(client),
        bulk_writes=PaperlessBulkWriter(client),
        ai=PaperlessAiReader(client),
        views=PaperlessViewReader(client),
        view_writes=PaperlessViewWriter(client),
        ui_settings=PaperlessUiSettingsReader(client),
        ui_setting_writes=PaperlessUiSettingsWriter(client),
        trash=PaperlessTrashReader(client),
        trash_writes=PaperlessTrashWriter(client),
        permissions=PaperlessPermissionReader(client),
        permission_writes=PaperlessPermissionWriter(client),
        pdf_writes=PaperlessPdfWriter(client),
        sharing=PaperlessSharingReader(client),
        sharing_writes=PaperlessSharingWriter(client),
        mail=PaperlessMailReader(client),
        mail_writes=PaperlessMailWriter(client),
        raw=PaperlessRawReader(client),
        workflows=PaperlessWorkflowReader(client),
        workflow_writes=PaperlessWorkflowWriter(client),
        management=PaperlessAdminReader(client),
        management_writes=PaperlessAdminWriter(client),
        reports=PaperlessInstanceReader(client),
        task_lists=PaperlessTaskListReader(client),
        task_list_writes=PaperlessTaskListWriter(client),
    )


class Consume(Protocol):
    """Runs a consumption task to its end and returns its id."""

    def __call__(self, *, succeed: bool) -> str: ...


class Upload(Protocol):
    """Uploads a file as a new document, waits for its consumption and returns the document id."""

    def __call__(self, title: str, filename: str, content: bytes, mime_type: str) -> int: ...


class Restricted(Protocol):
    """Makes a user with just these global permissions and object grants on admin documents.

    `view` documents are shared with it for viewing only, `change` documents for changing too;
    every other document of the admin stays hidden from it. Grants replace those a document had, so
    a second user on the same document takes it from the first: use it before making the next. The
    live one deletes the user again
    when the test ends. A `superuser` is made without the staff flag. A
    `limited` one is behind `Backend.rate_limit`.
    """

    def __call__(  # noqa: PLR0913 -- one keyword per thing tests vary
        self,
        permissions: frozenset[str],
        *,
        view: Sequence[int] = (),
        change: Sequence[int] = (),
        name: str | None = None,
        superuser: bool = False,
        limited: bool = False,
    ) -> Ports: ...


class Share(Protocol):
    """Makes a document the admin's own and shares it with the restricted user to view only."""

    def __call__(self, document_id: int) -> None: ...


type Processed = tuple[str, str, str, str | None]


class NewProcessed(Protocol):
    """Makes records of processed mail (see `Backend.new_processed`)."""

    def __call__(
        self, rule_id: int, rows: Sequence[Processed], *, owner: int | None = None
    ) -> None: ...


@dataclass(frozen=True)
class Grants:
    """Who owns an object and who may view and change it, as ids (users and groups)."""

    owner: int | None
    permissions: ObjectPermissions


class GrantsOf(Protocol):
    """Reads the owner and the grants of one taxonomy object, bypassing the ports."""

    def __call__(self, kind: BulkKind, item_id: int) -> Grants: ...


@dataclass(frozen=True)
class Backend:
    """One Paperless, seen through the seeded superuser, restricted user and blind user.

    The blind user may sign in but holds no model permission (tests/fakes/accounts.py).
    """

    admin: Ports
    reader: Ports
    blind: Ports
    # Starts a consumption task and waits for its end; `succeed=False` uploads a broken PDF.
    # The live one uploads for real and deletes the documents again when the test ends.
    consume: Consume
    # Uploads a document; the live one deletes it again when the test ends, after consumption.
    upload: Upload
    # A new document of the admin to write to, after its consumption; the live one is deleted when
    # the test ends. Its tags and custom fields are whatever consumption gave it.
    new_document: Callable[[], int]
    # New plain text documents of the admin (live: no OCR, about six times faster than a PDF).
    new_documents: Callable[[int], list[int]]
    # A new PDF document of the admin with this many pages (live: each page has its own text, so
    # OCR takes a few seconds), deleted when the test ends on the live backend.
    new_pdf: Callable[[int], int]
    # A new PDF of the admin that is encrypted with this password and holds one page once unlocked
    # (Paperless consumes it without text and without a page count).
    new_encrypted: Callable[[str], int]
    # Shares an uploaded document view-only with the restricted user (see `Share`).
    share_read_only: Share
    # A user with only the given permissions and grants (see `Restricted`).
    restricted: Restricted
    # The owner and grants of a taxonomy object (the ports do not expose them).
    grants: GrantsOf
    # Whether the document with this id is in the trash (deleted, not yet emptied from it).
    in_trash: Callable[[int], bool]
    # Registers a document a test created through the port itself: the live backend deletes it
    # (and empties its trash entry) when the test ends; the fake needs no cleanup.
    track_document: Callable[[int], None]
    # The Paperless release behind the backend: the fake's, or the one the live stack runs
    # (`just e2e-latest` runs the suite against the latest release instead of the reference).
    expected_version: str
    # Makes the full-text index hold exactly one entry per current document (live: waits for the
    # index-writing tasks, then rebuilds it; the fake has no stale entries). Call it right before
    # a more-like-this search: see `rebuild_search_index` for why the result can be empty without.
    rebuild_search_index: Callable[[], None]
    # A new mail account of the admin that signs in with an OAuth access token, as Paperless takes
    # it from anyone (the web UI makes real ones); the test deletes it.
    new_token_account: Callable[[], int]
    # Records of processed mail of a rule, one per (subject, uid, status, error), processed in this
    # order; the live one writes them in Paperless itself, because only a fetch makes real ones.
    # They belong to the owner, or to nobody, and go with the rule.
    new_processed: NewProcessed
    # Takes a file of a document with one version away from Paperless, or its file names.
    lose_file: Callable[[int, FilePart], None]
    # Writes an error into the mail log, as Paperless itself does.
    log_mail_error: Callable[[str], None]
    # The admin behind something that limits the rate of requests, once `rate_limit` begins.
    limited: Ports
    rate_limit: RateLimit


def fake_consume(state: FakeState) -> Consume:
    def consume_fake(*, succeed: bool) -> str:
        now = datetime.now(UTC)
        [owner] = [user.id for user in state.users if user.username == ADMIN[0]]
        task = FakeTask(
            id=len(state.tasks) + 1,
            task_id=str(uuid.uuid4()),
            task_type="consume_file",
            status="success" if succeed else "failure",
            owner=owner,
            file_name=f"contract-task-{uuid.uuid4().hex}.pdf",
            created=now,
            finished=now,
            wait_time_seconds=0.5,
            duration_seconds=1.0,
            document_ids=(len(state.documents) + 1,) if succeed else (),
            message=None if succeed else "broken.pdf: Error occurred while consuming document",
        )
        state.tasks.append(task)
        return task.task_id

    return consume_fake


def fake_upload(state: FakeState) -> Upload:
    def upload_fake(title: str, filename: str, content: bytes, mime_type: str) -> int:
        number = max((d.id for d in (*state.documents, *state.trash)), default=0) + 1
        state.documents.append(
            FakeDocument(
                id=number,
                title=title,
                mime_type=mime_type,
                file_name=filename,
                original=content,
                archive=content + b"\n%archive" if mime_type != "text/plain" else None,
                page_count=counted_pages(content) or 1 if mime_type == "application/pdf" else None,
            )
        )
        return number

    return upload_fake


def fake_new_document(state: FakeState) -> Callable[[], int]:
    def new_document() -> int:
        [admin] = [user for user in state.users if user.username == ADMIN[0]]
        number = max(d.id for d in (*state.documents, *state.trash)) + 1
        state.documents.append(
            FakeDocument(id=number, title=f"contract-write-{uuid.uuid4().hex}", owner=admin.id)
        )
        return number

    return new_document


def fake_share(state: FakeState) -> Share:
    def share_fake(document_id: int) -> None:
        [document] = [d for d in state.documents if d.id == document_id]
        [admin] = [u for u in state.users if u.username == ADMIN[0]]
        [reader] = [u for u in state.users if u.username == READER[0]]
        document.owner = admin.id
        document.viewers = {reader.id}

    return share_fake


def fake_restricted(state: FakeState, rate_limit: RateLimit) -> Restricted:
    def restricted(  # noqa: PLR0913 -- one keyword per thing tests vary
        permissions: frozenset[str],
        *,
        view: Sequence[int] = (),
        change: Sequence[int] = (),
        name: str | None = None,
        superuser: bool = False,
        limited: bool = False,
    ) -> Ports:
        user = FakeUser(
            id=max(u.id for u in state.users) + 1,
            username=name or f"contract-user-{uuid.uuid4().hex[:12]}",
            permissions=permissions,
            is_superuser=superuser,
        )
        state.users.append(user)
        [admin] = [u for u in state.users if u.username == ADMIN[0]]
        for document in state.documents:
            if document.id in {*view, *change}:
                # The PATCH of the live one replaces every grant the document had.
                document.owner = admin.id
                document.viewers = {user.id}
                document.editors = {user.id} if document.id in change else set()
        ports = fake_ports(state, user.username)
        return limited_ports(ports, rate_limit) if limited else ports

    return restricted


def fake_processed(state: FakeState) -> NewProcessed:
    def new_processed(rule_id: int, rows: Sequence[Processed], *, owner: int | None = None) -> None:
        for subject, uid, status, error in rows:
            number = max((row.id for row in state.processed_mail), default=0) + 1
            state.processed_mail.append(
                FakeProcessedMail(
                    id=number,
                    rule=rule_id,
                    subject=subject,
                    uid=uid,
                    status=status,
                    error=error,
                    owner=owner,
                    processed=EXPIRES + timedelta(seconds=number),
                )
            )

    return new_processed


def live_processed(rule_id: int, rows: Sequence[Processed], *, owner: int | None = None) -> None:
    code = (
        "from paperless_mail.models import ProcessedMail\n"
        "from django.utils.timezone import now\n"
        f"for subject, uid, status, error in {list(rows)!r}:\n"
        f"    ProcessedMail.objects.create(rule_id={rule_id}, owner_id={owner}, folder='INBOX', "
        "subject=subject, uid=uid, status=status, error=error, received=now())\n"
    )
    in_container("python", "manage.py", "shell", "-c", code)


def fake_log_mail_error(state: FakeState) -> Callable[[str], None]:
    def log(message: str) -> None:
        line = f"[2026-11-25 10:00:00,001] [ERROR] [paperless_mail] {message}"
        state.logs["mail"] = [*state.logs["mail"], line]

    return log


def live_log_mail_error(message: str) -> None:
    code = f"import logging; logging.getLogger('paperless_mail').error({message!r})"
    in_container("python", "manage.py", "shell", "-c", code)


def fake_token_account(state: FakeState) -> Callable[[], int]:
    def token_account() -> int:
        [admin] = [user for user in state.users if user.username == ADMIN[0]]
        number = max((row.id for row in state.mail_accounts), default=0) + 1
        state.mail_accounts.append(
            FakeMailAccount(
                id=number,
                name=f"contract-{uuid.uuid4().hex[:12]}",
                imap_server="127.0.0.1",
                imap_port=1,
                username="",
                password=TOKEN,
                account_type="gmail_oauth",
                is_token=True,
                expiration=EXPIRES,
                owner=admin.id,
            )
        )
        return number

    return token_account


def live_token_account(stack: Stack) -> int:
    body = {
        "name": f"contract-{uuid.uuid4().hex[:12]}",
        "imap_server": "127.0.0.1",
        "imap_port": 1,
        "imap_security": 2,
        "username": "unset",
        "password": TOKEN,
        "account_type": 2,
        "is_token": True,
        "expiration": EXPIRES.isoformat(),
    }
    made = stack.admin.post("/api/mail_accounts/", json=body).raise_for_status()
    return int(cast("int", cast("dict[str, object]", made.json())["id"]))


def fake_in_trash(state: FakeState) -> Callable[[int], bool]:
    def in_trash(document_id: int) -> bool:
        return document_id in {d.id for d in state.trash}

    return in_trash


def fake_grants(state: FakeState) -> GrantsOf:
    def grants(kind: BulkKind, item_id: int) -> Grants:
        table = {
            "tag": state.tags,
            "correspondent": state.correspondents,
            "document_type": state.document_types,
            "storage_path": state.storage_paths,
        }
        [found] = [o for o in table[kind] if o.id == item_id]
        return Grants(
            owner=found.owner,
            permissions=ObjectPermissions(
                view_users=tuple(sorted(found.viewers)),
                view_groups=tuple(sorted(found.view_groups)),
                change_users=tuple(sorted(found.editors)),
                change_groups=tuple(sorted(found.change_groups)),
            ),
        )

    return grants


def fake_new_pdf(state: FakeState) -> Callable[[int], int]:
    def new_pdf(pages: int) -> int:
        [admin] = [user for user in state.users if user.username == ADMIN[0]]
        number = max(d.id for d in state.documents) + 1
        state.documents.append(
            FakeDocument(
                id=number,
                title=f"contract-pdf-{uuid.uuid4().hex}",
                owner=admin.id,
                page_count=pages,
            )
        )
        return number

    return new_pdf


def fake_new_encrypted(state: FakeState) -> Callable[[str], int]:
    def new_encrypted(password: str) -> int:
        [admin] = [user for user in state.users if user.username == ADMIN[0]]
        number = max(d.id for d in state.documents) + 1
        state.documents.append(
            FakeDocument(
                id=number,
                title=f"contract-locked-{uuid.uuid4().hex}",
                owner=admin.id,
                page_count=None,
                password=password,
            )
        )
        return number

    return new_encrypted


def fake_new_documents(new_document: Callable[[], int]) -> Callable[[int], list[int]]:
    return lambda count: [new_document() for _ in range(count)]


def fake_lose_file(state: FakeState) -> Callable[[int, FilePart], None]:
    def lose(document_id: int, part: FilePart) -> None:
        if part == "names":
            state.nameless.add(document_id)
        else:
            state.missing.add((document_id, part))

    return lose


def fake_backend(*, ai_enabled: bool = False) -> Backend:
    """The fake's backend; the E2E stack of `just e2e` has AI off, so that is the default."""
    state = seeded()
    state.ai_enabled = ai_enabled
    rate_limit = RateLimit()
    return Backend(
        admin=fake_ports(state, ADMIN[0]),
        reader=fake_ports(state, READER[0]),
        blind=fake_ports(state, BLIND[0]),
        consume=fake_consume(state),
        upload=fake_upload(state),
        new_document=fake_new_document(state),
        new_documents=fake_new_documents(fake_new_document(state)),
        new_pdf=fake_new_pdf(state),
        new_encrypted=fake_new_encrypted(state),
        share_read_only=fake_share(state),
        restricted=fake_restricted(state, rate_limit),
        grants=fake_grants(state),
        in_trash=fake_in_trash(state),
        track_document=lambda _document: None,
        expected_version=VERSION,
        rebuild_search_index=lambda: None,
        new_processed=fake_processed(state),
        new_token_account=fake_token_account(state),
        lose_file=fake_lose_file(state),
        log_mail_error=fake_log_mail_error(state),
        limited=limited_ports(fake_ports(state, ADMIN[0]), rate_limit),
        rate_limit=rate_limit,
    )


def trashed(stack: Stack) -> set[int]:
    """The ids of every document in the live trash, over all its pages."""
    found: set[int] = set()
    page = 1
    while True:
        listed = stack.admin.get("/api/trash/", params={"page": page, "page_size": 100})
        found |= {int(cast("int", row["id"])) for row in results(listed)}
        if not cast("dict[str, object]", listed.json())["next"]:
            return found
        page += 1


def live_in_trash(stack: Stack, document_id: int) -> bool:
    """Whether the live trash holds the document, over all its pages."""
    return document_id in trashed(stack)


def live_grants(stack: Stack) -> GrantsOf:
    """Live `GrantsOf`: the object with `full_perms`, which is the only way to read its grants."""
    paths = {
        "tag": "/api/tags/",
        "correspondent": "/api/correspondents/",
        "document_type": "/api/document_types/",
        "storage_path": "/api/storage_paths/",
    }

    def grants(kind: BulkKind, item_id: int) -> Grants:
        row = cast(
            "dict[str, object]",
            stack.admin.get(f"{paths[kind]}{item_id}/", params={"full_perms": "true"})
            .raise_for_status()
            .json(),
        )
        given = cast("dict[str, dict[str, dict[str, list[int]]]]", row)["permissions"]
        return Grants(
            owner=cast("int | None", row["owner"]),
            permissions=ObjectPermissions(
                view_users=tuple(sorted(given["view"]["users"])),
                view_groups=tuple(sorted(given["view"]["groups"])),
                change_users=tuple(sorted(given["change"]["users"])),
                change_groups=tuple(sorted(given["change"]["groups"])),
            ),
        )

    return grants


def live_pdf(stack: Stack, created: list[int], pages: int) -> int:
    """A PDF with `pages` pages, each with its own text layer (OCR has little to do)."""
    title = f"contract-pdf-{uuid.uuid4().hex}"
    texts = [f"{title} page {n}" for n in range(1, pages + 1)]
    _, document = consume(stack.admin, title, pdf_pages(texts))
    assert document is not None, f"Consuming {title!r} failed"
    created.append(document)
    return document


def live_task(stack: Stack, *, succeed: bool) -> tuple[str, int | None]:
    """A consumption task that ends well or badly, and the document it made (None if it failed).

    A plain text file consumes without OCR; a broken PDF is what makes a task fail.
    """
    title = f"contract-task-{uuid.uuid4().hex}"
    if not succeed:
        return consume(stack.admin, title, BROKEN_PDF)
    return consume(
        stack.admin, title, title.encode(), filename=f"{title}.txt", mime_type="text/plain"
    )


def live_encrypted(stack: Stack, created: list[int], password: str) -> int:
    """The encrypted PDF fixture (Paperless consumes it without text and without page count)."""
    assert password == ENCRYPTED_PASSWORD, "the live fixture is locked with its own password"
    title = f"contract-locked-{uuid.uuid4().hex}"
    _, document = consume(stack.admin, title, ENCRYPTED_PDF)
    assert document is not None, f"Consuming {title!r} failed"
    created.append(document)
    return document


def delete_and_empty_trash(stack: Stack, documents: list[int]) -> None:
    """Delete the documents, then remove their trash entries: a delete only moves them there.

    Ids that no longer exist (a document a merge consumed) are skipped when emptying: Paperless
    answers 400 to an empty request for ids that are not in the trash.
    """
    for document in documents:
        _ = stack.admin.delete(f"/api/documents/{document}/")
    in_trash = sorted(trashed(stack) & set(documents))
    if in_trash:
        emptied = stack.admin.post("/api/trash/", json={"action": "empty", "documents": in_trash})
        _ = emptied.raise_for_status()


def live_restricted(
    stack: Stack, ports_for: Callable[[str, bool], Ports], users: list[int]
) -> Restricted:
    """Live `Restricted`: a temporary user whose id goes on `users` for the finaliser."""

    def restricted(  # noqa: PLR0913 -- one keyword per thing tests vary
        permissions: frozenset[str],
        *,
        view: Sequence[int] = (),
        change: Sequence[int] = (),
        name: str | None = None,
        superuser: bool = False,
        limited: bool = False,
    ) -> Ports:
        name = name or f"contract-user-{uuid.uuid4().hex[:12]}"
        password = f"pw-{uuid.uuid4().hex}"
        body = {
            "username": name,
            "password": password,
            "user_permissions": sorted(permissions),
            "is_superuser": superuser,
        }
        created = stack.admin.post("/api/users/", json=body).raise_for_status()
        user = int(cast("int", cast("dict[str, object]", created.json())["id"]))
        users.append(user)
        owner = cast("dict[str, dict[str, object]]", stack.admin.get("/api/ui_settings/").json())
        for document in {*view, *change}:
            grant = {"users": [user], "groups": []}
            nobody: dict[str, list[int]] = {"users": [], "groups": []}
            shared = {"view": grant, "change": grant if document in change else nobody}
            _ = stack.admin.patch(
                f"/api/documents/{document}/",
                json={"owner": owner["user"]["id"], "set_permissions": shared},
            ).raise_for_status()
        # Basic auth, not /api/token/: that endpoint is throttled.
        with httpx2.Client(
            base_url=stack.base_url,
            auth=(name, password),
            headers={"Accept": "application/json; version=10"},
        ) as signed_in:
            issued = signed_in.post("/api/profile/generate_auth_token/").raise_for_status()
        return ports_for(str(cast("object", issued.json())), limited)

    return restricted


def every_row(stack: Stack, path: str) -> list[dict[str, object]]:
    """Every row of a paged list, over all pages."""
    rows: list[dict[str, object]] = []
    page = 1
    while True:
        response = stack.admin.get(path, params={"page_size": 100, "page": page})
        body = cast("dict[str, object]", response.json())
        rows.extend(results(response))
        if not body.get("next"):
            return rows
        page += 1


def drop_orphaned_workflow_parts(stack: Stack) -> None:
    """Delete the triggers and actions no workflow holds.

    Paperless 3.4.2 keeps them as objects of their own when their workflow is deleted or replaces
    them, and lists them under /api/workflow_triggers/ and /api/workflow_actions/ for good.
    """
    held = every_row(stack, "/api/workflows/")
    for part in ("triggers", "actions"):
        used = {
            cast("dict[str, object]", item)["id"]
            for row in held
            for item in cast("list[object]", row[part])
        }
        for row in every_row(stack, f"/api/workflow_{part}/"):
            if row["id"] not in used:
                _ = stack.admin.delete(f"/api/workflow_{part}/{row['id']}/")


def cleanup(stack: Stack, documents: list[int], users: list[int]) -> None:
    """Delete what a live test made: documents, users and scratch objects (SCRATCH_*)."""
    delete_and_empty_trash(stack, documents)
    for user in users:
        _ = stack.admin.delete(f"/api/users/{user}/")
    views = stack.admin.get("/api/saved_views/", params={"page_size": 100})
    for row in results(views):
        if str(row["name"]).startswith(SCRATCH_VIEW):
            _ = stack.admin.delete(f"/api/saved_views/{row['id']}/")
    workflows = stack.admin.get("/api/workflows/", params={"page_size": 100})
    for row in results(workflows):
        if str(row["name"]).startswith(SCRATCH_WORKFLOW):
            _ = stack.admin.delete(f"/api/workflows/{row['id']}/")
    drop_orphaned_workflow_parts(stack)
    for path in SCRATCH_PATHS:
        for prefix in (SCRATCH_TAG, SCRATCH_ITEM):
            found = stack.admin.get(path, params={"name__istartswith": prefix})
            for row in results(found):
                _ = stack.admin.delete(f"{path}{row['id']}/")


@contextlib.asynccontextmanager
async def live_backend(stack: Stack) -> AsyncGenerator[Backend]:
    """The adapter on the live stack; documents a test uploads are deleted when it ends."""
    created: list[int] = []
    users: list[int] = []

    def consume_live(*, succeed: bool) -> str:
        task_id, document = live_task(stack, succeed=succeed)
        if document is not None:
            created.append(document)
        return task_id

    def upload_live(title: str, filename: str, content: bytes, mime_type: str) -> int:
        _, document = consume(stack.admin, title, content, filename=filename, mime_type=mime_type)
        assert document is not None, f"Consuming {title!r} failed"
        created.append(document)
        return document

    def new_live_document() -> int:
        document = consume_text(stack.admin, f"contract-write-{uuid.uuid4().hex}")
        created.append(document)
        return document

    def new_live_documents(count: int) -> list[int]:
        documents: list[int] = []
        for _ in range(count):
            documents.append(consume_text(stack.admin, f"contract-write-{uuid.uuid4().hex}"))
            created.append(documents[-1])
        return documents

    def user_id(api: httpx2.Client) -> int:
        user = cast("dict[str, dict[str, object]]", api.get("/api/ui_settings/").json())["user"]
        return int(cast("int", user["id"]))

    def share_live(document_id: int) -> None:
        none: dict[str, list[int]] = {"users": [], "groups": []}
        permissions = {"view": {"users": [user_id(stack.reader)], "groups": []}, "change": none}
        _ = stack.admin.patch(
            f"/api/documents/{document_id}/",
            json={"owner": user_id(stack.admin), "set_permissions": permissions},
        ).raise_for_status()

    rate_limit = RateLimit()
    try:
        async with (
            httpx2.AsyncClient(base_url=stack.base_url, timeout=30.0) as http,
            httpx2.AsyncClient(
                base_url=stack.base_url, timeout=30.0, transport=LimitingTransport(rate_limit)
            ) as limited_http,
        ):

            def ports(token: str, limited: bool = False) -> Ports:  # noqa: FBT001, FBT002 -- of a callable
                way = limited_http if limited else http
                credentials = StaticTokenProvider(token)
                return live_ports(
                    PaperlessClient(way, credentials, retry=RetryPolicy(), upload_timeout=60.0)
                )

            yield Backend(
                admin=ports(stack.admin_token),
                reader=ports(stack.reader_token),
                blind=ports(stack.blind_token),
                consume=consume_live,
                upload=upload_live,
                new_document=new_live_document,
                new_documents=new_live_documents,
                new_pdf=lambda pages: live_pdf(stack, created, pages),
                new_encrypted=lambda password: live_encrypted(stack, created, password),
                share_read_only=share_live,
                restricted=live_restricted(stack, ports, users),
                grants=live_grants(stack),
                in_trash=lambda document_id: live_in_trash(stack, document_id),
                track_document=created.append,
                expected_version=EXPECTED_VERSION,
                rebuild_search_index=lambda: rebuild_search_index(stack.admin),
                new_processed=live_processed,
                new_token_account=lambda: live_token_account(stack),
                lose_file=lambda document, part: lose_file(stack.admin, document, part),
                log_mail_error=live_log_mail_error,
                limited=ports(stack.admin_token, limited=True),
                rate_limit=rate_limit,
            )
    finally:
        cleanup(stack, created, users)


@pytest.fixture(params=["fake", pytest.param("live", marks=pytest.mark.e2e)])
async def backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """The backend under contract (the fixture value is created fresh for every test)."""
    if cast("str", request.param) == "fake":
        yield fake_backend()
        return
    async with live_backend(cast("Stack", request.getfixturevalue("stack"))) as live:
        yield live


INDEX_TIMEOUT = 600.0


async def build_llm_index(stack: Stack, live: Backend) -> None:
    """Update the LLM index (the chat answers from it) and wait for the task to end.

    Unverified on a working backend (the CI stack has none): `task_type` is the 3.4.2 name of
    the task, from the OpenAPI schema.
    """
    started = stack.admin.post("/api/tasks/run/", json={"task_type": "llm_index"})
    _ = started.raise_for_status()
    task_id = str(cast("dict[str, object]", started.json())["task_id"])
    deadline = asyncio.get_running_loop().time() + INDEX_TIMEOUT
    while True:
        status = await live.admin.tasks.get_task(task_id)
        if status.is_final:
            break
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail(f"The LLM index update did not end within {INDEX_TIMEOUT:.0f} s.")
        await asyncio.sleep(3)
    if status.state != "success":
        pytest.fail(f"The LLM index update ended {status.state}: {status.message}")


AI_SKIPPED = "AI is disabled on this Paperless; `just e2e-ai` runs these against an AI backend."


@pytest.fixture(params=["fake", pytest.param("live", marks=[pytest.mark.e2e, pytest.mark.ai_e2e])])
async def ai_backend(request: pytest.FixtureRequest) -> AsyncIterator[Backend]:
    """A backend with Paperless AI on: the fake with AI enabled, or a live one that has it.

    The live stack of CI has AI off, so those tests skip there; `just e2e-ai` starts a stack
    with an AI backend the developer provides and runs them.
    """
    if cast("str", request.param) == "fake":
        yield fake_backend(ai_enabled=True)
        return
    stack = cast("Stack", request.getfixturevalue("stack"))
    async with live_backend(stack) as live:
        if (await live.admin.instance.get_overview()).ai_enabled is not True:
            pytest.skip(AI_SKIPPED)
        await build_llm_index(stack, live)
        yield live
