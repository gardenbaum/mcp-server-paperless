"""Finding the versions of a rotation whose tasks Paperless does not list (decision-74)."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.test_instance import UI_SETTINGS
from adapter.test_pdf import DONE_TASK, EARLIER, LATER, OLD, QUEUED, Paperless, row, task_id, writer
from mcp_server_paperless.domain.errors import NotFound, PermissionDenied, UpstreamUnavailable
from mcp_server_paperless.domain.pdf import CopiedFiles, FoundCopies
from mcp_server_paperless.paperless.client import RetryPolicy
from mcp_server_paperless.paperless.pdf import PaperlessPdfWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-74")]

type Json = dict[str, object]

START = "2026-11-13T17:25:36.540000Z"
ADMIN = 2
AT_ONCE = "another operation of this user ran at the same time"
PENDING = "files of earlier operations of this user may still come"
TASKED = "a task of its own makes a file of one of these names"
UNCOMPARED = "its tasks could not be compared"
NO_HISTORY = "the history, which names who added a version, may not be read"
NOTHING = FoundCopies(ids=(None,))


def ago(**delta: int) -> str:
    """A time before the request started."""
    return (datetime.fromisoformat(START) - timedelta(**delta)).isoformat()


def rotation(**fields: object) -> Json:
    """Another queued rotation of the user, done a minute before the request."""
    times = {"date_started": ago(minutes=2), "date_done": ago(minutes=1)}
    data = {"operation": "rotate", "document_count": 1}
    return {**QUEUED, "id": 2, "task_id": task_id(2), "input_data": data, **times, **fields}


class Archive:
    """A Paperless with documents, their versions and histories, tasks and queued operations."""

    def __init__(self) -> None:
        self.versions: dict[int, list[Json]] = {}
        self.names: dict[int, str] = {}
        self.history: dict[int, list[Json]] = {}
        self.tasks: list[Json] = []
        self.bulk: list[Json] = [QUEUED]
        self.next: dict[str, str] = {}
        self.settings: int = 200
        self.status: dict[str, int] = {}
        self.forbidden: set[int] = set()
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.document(8)

    def document(self, number: int) -> None:
        self.versions[number] = [{"id": number, "added": EARLIER, "is_root": True}]
        self.history[number] = []

    def add(
        self,
        version: int,
        document: int = 8,
        *,
        name: str | None = None,
        added: str = LATER,
        actor: int | None = ADMIN,
    ) -> None:
        """A new version, by default as a rotation of the user makes it."""
        row: Json = {"id": version, "added": added, "is_root": False}
        self.versions[document].insert(0, row)
        self.names[version] = name or f"{document}_rotated.pdf"
        entry: Json = {
            "id": version * 10,
            "timestamp": added,
            "action": "update",
            "changes": {"Version Added": ["None", version]},
            "actor": None if actor is None else {"id": actor, "username": f"user {actor}"},
        }
        noise: list[Json] = [
            {**entry, "id": 1, "changes": {"title": ["a", "b"], "tags": {"type": "m2m"}}},
            {**entry, "id": 2, "changes": {"Version Added": []}},
            {**entry, "id": 3, "changes": {"Merged As Versions": ["None", [version]]}},
        ]
        self.history[document] = [*noise, entry, *self.history[document]]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        path = request.url.path
        assert request.method == "GET"
        self.requests.append((path, params))
        if path == "/api/ui_settings/":
            body = UI_SETTINGS if self.settings == 200 else {"detail": "no"}
            return httpx2.Response(self.settings, json=body)
        parts = path.strip("/").split("/")
        kind = params.get("task_type", parts[-1])
        if kind in self.status:
            return httpx2.Response(self.status[kind], json={"detail": "no"})
        if kind in {"bulk_update", "consume_file", "documents"}:
            return self._list(kind, params)
        number = int(parts[2])
        if number not in self.versions:
            return httpx2.Response(404, json={"detail": "no"})
        if kind in {"metadata", "history"}:
            return self._part(number, params.get("version"))
        return httpx2.Response(200, json={"id": number, "versions": self.versions[number]})

    def _list(self, kind: str, params: dict[str, str]) -> httpx2.Response:
        rows = self.tasks if kind == "consume_file" else self.bulk
        if kind == "documents":
            assert set(params) == {"modified__gte", "fields", "page_size"}
            rows = [{"id": n, "versions": v} for n, v in self.versions.items()]
        more = self.next.get(kind)
        body = {"count": len(rows), "next": more, "previous": None, "results": rows}
        return httpx2.Response(200, json=body)

    def _part(self, number: int, version: str | None) -> httpx2.Response:
        """The metadata of a version, or the history."""
        if version is None:
            if number in self.forbidden:
                return httpx2.Response(403, text="Insufficient permissions")
            return httpx2.Response(200, json=self.history[number])
        if int(version) not in self.names:
            return httpx2.Response(404, json={"detail": "no"})
        return httpx2.Response(
            200,
            json={
                "original_checksum": "c",
                "original_size": 1,
                "original_mime_type": "application/pdf",
                "media_filename": "x.pdf",
                "has_archive_version": False,
                "original_metadata": [],
                "archive_checksum": None,
                "archive_media_filename": None,
                "original_filename": self.names[int(version)],
                "archive_size": None,
                "archive_metadata": None,
                "barcodes": [],
                "lang": "en",
            },
        )


async def find(
    make_client: ClientFactory,
    archive: Archive,
    before: dict[int, int] | None = None,
    *,
    final: bool = False,
) -> FoundCopies:
    client = make_client(archive, retry=RetryPolicy(attempts=1))
    pdf = PaperlessPdfWriter(client)
    return await pdf.find_rotations(DONE_TASK, before or {8: 8}, final=final)


async def test_the_new_version_is_told_by_place_time_file_name_and_who_added_it(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.document(9)
    archive.add(50, 9, added=ago(hours=3))
    archive.add(61)
    archive.add(62, 9)

    assert await find(make_client, archive, {8: 8, 9: 50}) == FoundCopies(ids=(61, 62))
    assert ("/api/documents/8/", {"fields": "versions"}) in archive.requests
    assert ("/api/documents/9/metadata/", {"version": "62"}) in archive.requests


async def test_what_is_not_there_yet_is_missing_without_a_doubt(make_client: ClientFactory) -> None:
    archive = Archive()

    assert await find(make_client, archive, final=True) == NOTHING
    # Nothing is asked of a history before there is something to tell.
    assert not [path for path, _ in archive.requests if path.endswith("/history/")]


@pytest.mark.parametrize("actor", [3, None])
async def test_a_version_of_somebody_else_is_never_taken(
    make_client: ClientFactory, actor: int | None
) -> None:
    archive = Archive()
    archive.add(61, actor=actor)

    assert await find(make_client, archive, final=True) == NOTHING

    archive.add(62)
    archive.add(63, actor=actor)  # theirs is the newest now
    assert await find(make_client, archive) == FoundCopies(ids=(62,))


@pytest.mark.parametrize("name", ["8_edited.pdf", "9_rotated.pdf", "8_rotated.pdf.pdf", "scan.pdf"])
async def test_a_version_with_another_file_name_is_not_the_rotation(
    make_client: ClientFactory, name: str
) -> None:
    archive = Archive()
    archive.add(61, name=name)

    assert await find(make_client, archive, final=True) == NOTHING


async def test_versions_from_before_the_request_are_not_the_rotation(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.add(60)
    archive.add(61, added=ago(seconds=1))  # after the document was read, before the start

    assert await find(make_client, archive, {8: 60}, final=True) == NOTHING
    assert await find(make_client, archive, {8: 61}, final=True) == NOTHING


async def test_a_version_without_a_history_entry_is_not_taken(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.add(61)
    _ = archive.history[8].pop()  # the entry that says it was added

    assert await find(make_client, archive, final=True) == NOTHING


async def test_a_version_that_is_deleted_meanwhile_is_not_taken(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.add(61)
    del archive.names[61]

    assert await find(make_client, archive, final=True) == NOTHING


async def test_two_versions_that_could_be_it_are_a_doubt(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.add(61)
    archive.add(62)

    found = await find(make_client, archive)

    assert found == FoundCopies(
        ids=(None,), doubt="versions 62, 61 of document 8 could both be the rotation"
    )


@pytest.mark.parametrize("status", [400, 403])
async def test_without_the_history_nothing_is_taken(
    make_client: ClientFactory, status: int
) -> None:
    archive = Archive()
    archive.status["history"] = status
    archive.add(61)

    assert await find(make_client, archive) == FoundCopies(ids=(None,), doubt=NO_HISTORY)


@pytest.mark.parametrize(
    "fields", [{"date_done": None}, {"date_done": START}, {"date_done": LATER}]
)
@pytest.mark.parametrize("operation", ["rotate", None])
async def test_another_operation_of_the_user_at_the_same_time_or_later_is_a_doubt(
    make_client: ClientFactory, fields: Json, operation: str | None
) -> None:
    archive = Archive()
    archive.add(61)
    archive.bulk = [rotation(**fields, input_data={"operation": operation}), QUEUED]

    found = await find(make_client, archive)

    assert found == FoundCopies(ids=(None,), doubt=AT_ONCE)
    assert (await find(make_client, Archive())).doubt is None


async def test_operations_that_add_no_such_version_are_no_doubt(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.add(61)
    archive.bulk = [
        QUEUED,
        rotation(input_data={"operation": "edit_pdf"}, date_done=LATER),
        rotation(input_data={"operation": "merge_as_versions"}, date_done=LATER),
        rotation(owner=3, date_done=LATER),
        rotation(owner=None, date_done=LATER),
        rotation(date_started=None, date_done=None),  # its files come later than this
        rotation(date_started=ago(hours=3), date_done=ago(minutes=61)),
    ]

    assert await find(make_client, archive) == FoundCopies(ids=(61,))


async def test_an_earlier_rotation_whose_version_may_still_come_is_a_passing_doubt(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.bulk = [QUEUED, rotation()]

    early = await find(make_client, archive)
    archive.add(61)
    there = await find(make_client, archive)

    assert early == NOTHING  # looked into once every version is there, or at the end
    assert await find(make_client, archive, final=True) == there
    assert there == FoundCopies(ids=(None,), doubt=PENDING, passing=True)


@pytest.mark.parametrize("document", [8, 9])
async def test_an_earlier_rotation_is_settled_by_the_versions_it_made(
    make_client: ClientFactory, document: int
) -> None:
    archive = Archive()
    archive.document(9)
    archive.add(50, document, added=ago(seconds=30))
    archive.add(61)
    archive.bulk = [QUEUED, rotation()]
    before = {8: 50 if document == 8 else 8}

    assert await find(make_client, archive, before) == FoundCopies(ids=(61,))
    assert archive.requests[-1][0] == f"/api/documents/{document}/history/"
    listed = {"modified__gte": ago(minutes=2), "fields": "id,versions", "page_size": "100"}
    assert ("/api/documents/", listed) in archive.requests

    archive.bulk.append(rotation(id=3, task_id=task_id(3)))  # one version settles one only
    assert (await find(make_client, archive, before)).doubt == PENDING
    archive.bulk = [QUEUED, rotation(input_data={"operation": "rotate", "document_count": 2})]
    assert (await find(make_client, archive, before)).doubt == PENDING


@pytest.mark.parametrize("data", [{"operation": "rotate"}, {}])
async def test_an_earlier_operation_of_unknown_size_is_not_settled(
    make_client: ClientFactory, data: Json
) -> None:
    archive = Archive()
    archive.add(61)
    archive.bulk = [QUEUED, rotation(input_data=data)]

    assert (await find(make_client, archive)).doubt == PENDING


async def test_versions_of_others_and_older_ones_settle_nothing(make_client: ClientFactory) -> None:
    archive = Archive()
    for number in (9, 10, 11, 12):
        archive.document(number)
    archive.add(50, 9, added=ago(seconds=30), actor=3)
    archive.add(51, 10, added=ago(minutes=3))
    archive.add(52, 11, added=ago(seconds=30), name="11_edited.pdf")
    archive.add(53, 12, added=ago(seconds=30))
    archive.forbidden.add(12)  # whose history the user may not read
    archive.add(61)
    archive.bulk = [QUEUED, rotation()]

    assert (await find(make_client, archive)).doubt == PENDING


@pytest.mark.parametrize(
    "fields", [{}, {"date_done": LATER}, {"date_created": ago(minutes=59), "date_done": LATER}]
)
async def test_a_listed_task_for_the_file_name_is_a_doubt(
    make_client: ClientFactory, fields: Json
) -> None:
    archive = Archive()
    archive.add(61)
    task = {**row(20, "consume_file", "8_rotated.pdf", owner=None), "date_created": LATER}
    archive.tasks = [{**task, **fields}]

    assert await find(make_client, archive) == FoundCopies(ids=(None,), doubt=TASKED)
    queried = [params for _, params in archive.requests if params.get("task_type")]
    assert queried[-1] == {
        "task_type": "consume_file",
        "ordering": "-date_created",
        "page_size": "100",
    }


async def test_tasks_that_are_done_or_of_other_files_are_no_doubt(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.add(61)
    early = {"date_created": ago(minutes=2), "date_done": ago(minutes=1)}
    archive.tasks = [
        {**row(20, "consume_file", "8_rotated.pdf"), **early},
        {**row(21, "consume_file", "8_rotated.pdf"), "date_created": ago(minutes=61)},
        {**row(22, "consume_file", "9_rotated.pdf"), "date_created": LATER},
    ]

    assert await find(make_client, archive) == FoundCopies(ids=(61,))


@pytest.mark.parametrize("kind", ["bulk_update", "consume_file"])
async def test_lists_that_do_not_reach_back_an_hour_are_a_doubt(
    make_client: ClientFactory, kind: str
) -> None:
    archive = Archive()
    archive.add(61)
    archive.next[kind] = "http://paperless/api/tasks/?page=2"
    oldest = {**row(20, "consume_file", "x.pdf"), "owner": 3}

    for minutes, doubt in ((59, UNCOMPARED), (61, None)):
        last = {"date_created": ago(minutes=minutes)}
        archive.bulk = [QUEUED, rotation(owner=3, **last)]
        archive.tasks = [{**oldest, **last}]
        assert (await find(make_client, archive)).doubt == doubt


async def test_too_many_changed_documents_settle_nothing(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.document(9)
    archive.add(50, 9, added=ago(seconds=30))
    archive.add(61)
    archive.bulk = [QUEUED, rotation()]
    archive.next["documents"] = "http://paperless/api/documents/?page=2"

    assert (await find(make_client, archive)).doubt == UNCOMPARED


async def test_an_unknown_user_or_request_is_a_doubt(make_client: ClientFactory) -> None:
    unknown, gone, unstarted = Archive(), Archive(), Archive()
    unknown.settings = 403
    gone.bulk = []
    unstarted.bulk = [{**QUEUED, "date_started": None}]
    times = "the times of its queued task are not known"

    assert (await find(make_client, unknown)).doubt == "the current user is not known"
    assert (await find(make_client, gone)).doubt == times
    assert (await find(make_client, unstarted)).doubt == times


@pytest.mark.parametrize("failing", ["8", "metadata", "history", "bulk_update", "consume_file"])
async def test_a_failed_read_is_raised(make_client: ClientFactory, failing: str) -> None:
    archive = Archive()
    archive.add(61)
    archive.status[failing] = 503

    with pytest.raises(UpstreamUnavailable):
        _ = await find(make_client, archive)


async def test_a_hidden_document_and_hidden_tasks_are_raised(make_client: ClientFactory) -> None:
    hidden, untold = Archive(), Archive()
    del hidden.versions[8]
    untold.status["bulk_update"] = 403

    with pytest.raises(NotFound):
        _ = await find(make_client, hidden)
    with pytest.raises(PermissionDenied):
        _ = await find(make_client, untold)


async def test_a_rotation_says_which_documents_have_no_task(make_client: ClientFactory) -> None:
    one = [*OLD, row(12, "consume_file", "4_rotated.pdf")]
    both = [*one, row(13, "consume_file", "7_rotated.pdf")]
    refused = Paperless(OLD, OLD, tasks_status=403)

    none = await writer(make_client, Paperless(OLD, OLD)).rotate_documents((4, 7), 90)
    some = await writer(make_client, Paperless(OLD, one)).rotate_documents((4, 7), 90)
    every = await writer(make_client, Paperless(OLD, both)).rotate_documents((4, 7), 90)
    unread = await writer(make_client, refused).rotate_documents((4, 7), 90)

    names = ("4_rotated.pdf", "7_rotated.pdf")
    assert none.files == CopiedFiles(request=DONE_TASK, names=names)
    assert some.files == CopiedFiles(request=DONE_TASK, names=names[1:])
    assert (every.files, unread.files) == (None, None)
