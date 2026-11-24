"""Uploads of a file Paperless already holds: asked before, reported after (decision-47)."""

import base64
from typing import TYPE_CHECKING, override

import pytest
from e2e.helpers import pdf
from fakes.accounts import ADMIN, READER, READER_PERMISSIONS
from fakes.paperless import FakeDocument, FakePaperless, FakeState, FakeTask, seeded

from mcp_server_paperless.domain.documents import UploadMetadata
from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    UpstreamTimeout,
    Validation,
    accepting,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.duplicates import (
    HIDDEN,
    HIDDEN_REJECTED,
    UNCOMPARED,
    VERSION_AFTER,
    VERSIONS_AFTER,
    DuplicateFinder,
)
from mcp_server_paperless.services.tasks import TaskService
from mcp_server_paperless.services.taxonomy import TaxonomyResolver
from mcp_server_paperless.services.uploads import UploadService
from mcp_server_paperless.services.version_uploads import VersionUploadService

if TYPE_CHECKING:
    from pathlib import Path

    from mcp_server_paperless.domain.documents import UploadResult
    from mcp_server_paperless.domain.trash import TrashPage
    from mcp_server_paperless.domain.versions import VersionUploadResult

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-47")]

WEB = "https://paperless.example"
FILE = pdf("the same file")


class Setup:
    """Both upload services as one user; without `writes` any write fails the test."""

    def __init__(
        self,
        user: str = ADMIN[0],
        *,
        state: FakeState | None = None,
        writes: bool = False,
        upload_dirs: tuple[str, ...] = (),
        fake: FakePaperless | None = None,
    ) -> None:
        self.state: FakeState = state or seeded()
        self.fake: FakePaperless = fake or FakePaperless(self.state, user=user, allow_writes=writes)
        finder = DuplicateFinder(self.fake, self.fake, web_url=WEB)
        self.tasks: TaskService = TaskService(self.fake, duplicates=finder)
        taxonomy = TaxonomyResolver(self.fake, StaticTokenProvider("t"), ttl=3600.0)
        self.uploads: UploadService = UploadService(
            self.fake,
            self.fake,
            taxonomy,
            self.tasks,
            finder,
            web_url=WEB,
            max_bytes=10_000,
            upload_dirs=upload_dirs,
        )
        self.versions: VersionUploadService = VersionUploadService(
            self.fake, self.fake, self.tasks, finder, web_url=WEB, max_bytes=10_000
        )

    def document(self, number: int, title: str, content: bytes = FILE, **given: object) -> None:
        values: dict[str, object] = {"owner": 2, "original": content} | given
        self.state.documents.append(FakeDocument(id=number, title=title, **values))  # pyright: ignore[reportArgumentType] -- keyword passthrough

    async def upload(self, *, confirm: bool = False, content: bytes = FILE) -> UploadResult:
        return await self.uploads.from_content(
            base64.b64encode(content).decode(), "x.pdf", UploadMetadata(), confirm=confirm
        )

    async def version(
        self, document: int, *, confirm: bool = False, content: bytes = FILE
    ) -> VersionUploadResult:
        return await self.versions.from_content(
            document, base64.b64encode(content).decode(), "x.pdf", None, confirm=confirm
        )


def writing(setup: Setup) -> Setup:
    return Setup(state=setup.state, writes=True)


async def refusal(setup: Setup, document: int | None = None) -> ConfirmationRequired:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await (setup.upload() if document is None else setup.version(document))
    return raised.value


# --- before sending ------------------------------------------------------------------------------


async def test_a_known_file_is_not_sent_and_the_match_is_named() -> None:
    setup = Setup()  # a write would fail
    setup.document(12, "Invoice")

    error = await refusal(setup)

    assert error.message == (
        "Nothing was changed. Confirming would upload 'x.pdf' although the same file is already "
        "in Paperless: document 12 'Invoice'. Paperless may reject it, if it is configured to."
    )


async def test_the_trash_is_named_and_how_to_restore_from_it() -> None:
    setup = Setup(writes=True)
    setup.document(12, "Invoice")
    await setup.fake.delete_document(12)

    message = (await refusal(setup)).message
    expected = (
        "document 12 'Invoice' in the trash. What is in the trash can be restored in the "
        "Paperless web UI or with restore_from_trash. Paperless may reject it, if it is "
        "configured to."
    )
    assert message.endswith(expected)


async def test_a_version_names_its_target_too() -> None:
    setup = Setup()
    setup.document(12, "Invoice")
    setup.document(13, "Target", b"other")

    message = (await refusal(setup, 13)).message
    expected = (
        "Nothing was changed. Confirming would upload 'x.pdf' as a new version of document 13 "
        "'Target' although the same file is already in Paperless: document 12 'Invoice'."
    )
    assert message.startswith(expected)


async def test_the_targets_own_file_is_a_match_too() -> None:
    setup = Setup()
    setup.document(13, "Target")

    assert "document 13 'Target'. Paperless" in (await refusal(setup, 13)).message


async def test_at_most_ten_matches_are_named_and_reported() -> None:
    setup = Setup()
    for number in range(10, 22):
        setup.document(number, f"Copy {number}")

    message = (await refusal(setup)).message
    result = await writing(setup).upload(confirm=True)

    assert message.count("'Copy ") == 10
    assert "and 2 more. Paperless" in message
    assert result.duplicates is not None
    assert (len(result.duplicates.before), result.duplicates.before_count) == (10, 12)


async def test_confirm_uploads_anyway_and_the_result_reports_the_matches() -> None:
    setup = Setup(writes=True)
    setup.document(12, "Invoice")

    result = await setup.upload(confirm=True)

    assert result.state == "success"
    check = result.duplicates
    assert check is not None
    assert [m.document_id for m in check.before] == [12]
    assert check.after is not None
    assert [m.document_id for m in check.after] == [12]
    assert check.before_count == 1
    assert check.rejected_for is None


async def test_no_match_uploads_at_once_and_says_what_was_not_checked() -> None:
    setup = Setup(writes=True)

    result = await setup.upload()

    assert result.state == "success"
    assert result.duplicates is not None
    assert (result.duplicates.before, result.duplicates.after) == ((), ())
    assert result.duplicates.not_checked[0] == UNCOMPARED
    assert VERSIONS_AFTER in result.duplicates.not_checked
    assert result.duplicates.not_checked.count(HIDDEN) == 1


async def test_a_confirmed_version_reports_what_this_server_compared() -> None:
    setup = Setup(writes=True)
    setup.document(12, "Invoice")
    setup.document(13, "Target", b"other")

    result = await setup.version(13, confirm=True)

    check = result.duplicates
    assert check is not None
    assert [m.document_id for m in check.before] == [12]
    assert check.after is not None
    assert [m.document_id for m in check.after] == [12]
    assert VERSION_AFTER in check.not_checked


async def test_a_path_with_an_emoji_name_is_checked_too(tmp_path: Path) -> None:
    """A path may hold a joined emoji (PLMCP-76), the name to upload under may not."""
    setup = Setup(writes=True, upload_dirs=(str(tmp_path),))
    setup.document(12, "Invoice")
    name = f"family {chr(0x1F468)}{chr(0x200D)}{chr(0x1F469)}.pdf"
    _ = (tmp_path / name).write_bytes(FILE)

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.uploads.from_path(str(tmp_path / name), "x.pdf", UploadMetadata())
    result = await setup.uploads.from_path(
        str(tmp_path / name), "x.pdf", UploadMetadata(), confirm=True
    )

    assert "upload 'x.pdf' although" in raised.value.message
    assert result.filename == "x.pdf"
    assert [m.document_id for m in result.duplicates.before] == [12]


async def test_an_acceptance_lets_exactly_this_file_pass(tmp_path: Path) -> None:
    """The file under a path may change between the dialog and the second run."""
    setup = Setup(writes=True, upload_dirs=(str(tmp_path),))
    other = pdf("another known file")
    setup.document(12, "Invoice")
    setup.document(12_000, "Invoice", other)
    path = tmp_path / "x.pdf"
    _ = path.write_bytes(FILE)
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await setup.uploads.from_path(str(path), None, UploadMetadata())
    fingerprint = raised.value.fingerprint

    # Same text: the other file is in a document with the same id and title.
    setup.state.documents = [d for d in setup.state.documents if d.id != 12]
    setup.state.documents[-1].id = 12
    _ = path.write_bytes(other)
    with accepting(fingerprint), pytest.raises(ConfirmationRequired) as again:
        _ = await setup.uploads.from_path(str(path), None, UploadMetadata())

    assert again.value.message == raised.value.message
    assert again.value.fingerprint != fingerprint
    assert setup.state.tasks == []
    with accepting(again.value.fingerprint):
        result = await setup.uploads.from_path(str(path), None, UploadMetadata())
    assert result.state == "success"


async def test_an_acceptance_does_not_cover_other_matches_behind_and_more() -> None:
    setup = Setup()
    for number in range(10, 22):
        setup.document(number, f"Copy {number}")
    fingerprint = (await refusal(setup)).fingerprint
    # The same ten are named first (newest first, then by id).
    setup.state.documents = [d for d in setup.state.documents if d.id != 10]
    setup.document(9, "Copy 9")

    with accepting(fingerprint):
        again = await refusal(setup)

    assert again.fingerprint != fingerprint


class Down(FakePaperless):
    """Does not answer when the trash is read."""

    @override
    async def list_trash(self, *, page: int, page_size: int) -> TrashPage:
        message = f"Paperless did not answer page {page} of {page_size}."
        raise UpstreamTimeout(message)


@pytest.mark.parametrize("confirm", [False, True])
async def test_nothing_is_sent_when_the_check_cannot_run(*, confirm: bool) -> None:
    state = seeded()
    setup = Setup(state=state, fake=Down(state, user=ADMIN[0]))

    with pytest.raises(UpstreamTimeout):
        _ = await setup.upload(confirm=confirm)
    with pytest.raises(UpstreamTimeout):
        _ = await setup.version(1, confirm=confirm)


async def test_a_bad_file_is_refused_before_anything_is_read() -> None:
    state = seeded()
    setup = Setup(state=state, fake=Down(state, user=ADMIN[0]))

    with pytest.raises(Validation, match="not a type"):
        _ = await setup.upload(content=bytes(40))


# --- rejected by Paperless -----------------------------------------------------------------------


async def test_a_rejected_upload_names_the_existing_document_and_no_new_one() -> None:
    setup = Setup(writes=True)
    setup.state.delete_duplicates = True
    setup.document(12, "Invoice")

    result = await setup.upload(confirm=True)
    report = await setup.tasks.get(result.task_id, duplicates=True)

    assert (result.state, result.document_id, result.web_url) == ("failure", None, None)
    assert result.message == (
        "Paperless rejected the file: it already holds the same file, document 12 'Invoice'."
    )
    assert result.duplicates is not None
    assert result.duplicates.rejected_for is not None
    assert result.duplicates.rejected_for.document_id == 12
    assert result.duplicates.after is None
    assert (report.document_ids, report.message) == ((), result.message)
    assert report.duplicates is not None
    assert report.duplicates.rejected_for == result.duplicates.rejected_for


async def test_a_rejected_version_names_no_version() -> None:
    setup = Setup(writes=True)
    setup.state.delete_duplicates = True
    setup.document(13, "Target")

    result = await setup.version(13, confirm=True)

    assert (result.state, result.version_id) == ("failure", None)
    assert result.duplicates is not None
    assert result.duplicates.rejected_for is not None
    assert result.duplicates.rejected_for.document_id == 13


async def test_a_rejection_for_a_hidden_document_never_names_it() -> None:
    state = seeded()
    state.delete_duplicates = True
    state.documents.append(FakeDocument(id=4711, title="Secret", owner=2, original=FILE))
    for user in state.users:
        if user.username == READER[0]:
            extra = {"add_document", "view_paperlesstask"}
            state.users[state.users.index(user)] = type(user)(
                id=user.id,
                username=user.username,
                permissions=frozenset({*READER_PERMISSIONS, *extra}),
            )
    setup = Setup(READER[0], state=state, writes=True)

    result = await setup.upload()  # no match the reader may see: no question
    report = await setup.tasks.get(result.task_id, duplicates=True)

    for text in (result.model_dump_json(), report.model_dump_json()):
        assert "4711" not in text
        assert "Secret" not in text
    assert (result.state, result.message) == ("failure", HIDDEN_REJECTED)


# --- get_task ------------------------------------------------------------------------------------


async def test_a_task_that_was_not_waited_for_reports_the_duplicates_later() -> None:
    setup = Setup(writes=True)
    setup.document(12, "Invoice")
    result = await setup.upload(confirm=True)

    report = await setup.tasks.get(result.task_id, duplicates=True)
    plain = await setup.tasks.get(result.task_id)

    assert report.duplicates is not None
    assert report.duplicates.after is not None
    assert [m.document_id for m in report.duplicates.after] == [12]
    assert plain.duplicates is None


@pytest.mark.parametrize(
    "task",
    [
        FakeTask(id=1, task_id="t", status="started"),
        FakeTask(id=1, task_id="t", status="failure", message="broken"),
        FakeTask(id=1, task_id="t", status="success"),
        FakeTask(id=1, task_id="t", status="success", document_ids=(1, 2)),
        FakeTask(id=1, task_id="t", status="success", task_type="bulk_update", document_ids=(1,)),
    ],
)
async def test_other_tasks_have_no_duplicates(task: FakeTask) -> None:
    setup = Setup()
    setup.state.tasks = [task]

    report = await setup.tasks.get("t", duplicates=True)

    assert report.duplicates is None
    assert report.message == task.message


async def test_without_a_finder_a_rejection_still_names_nothing() -> None:
    setup = Setup()
    setup.state.tasks = [FakeTask(id=1, task_id="t", status="failure", duplicate_of=2)]

    report = await TaskService(setup.fake).get("t", duplicates=True)

    assert report.duplicates is None
    assert report.message == "Paperless rejected the file: it already holds the same file."
    assert "2" not in report.model_dump_json(include={"message", "document_ids"})
