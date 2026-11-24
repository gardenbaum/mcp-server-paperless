"""Finding the versions of a rotation whose tasks Paperless does not list (decision-74)."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, override

import httpx2
import pytest

from adapter import test_pdf
from adapter.test_instance import UI_SETTINGS
from adapter.test_pdf import DONE_TASK, EARLIER, LATER, OLD, Paperless, row, task_id, writer
from mcp_server_paperless.domain.errors import PermissionDenied, UpstreamUnavailable
from mcp_server_paperless.domain.pdf import CopiedFiles, FoundCopies, RotationSearch
from mcp_server_paperless.paperless.client import RetryPolicy
from mcp_server_paperless.paperless.pdf import PaperlessPdfWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-74")]

type Json = dict[str, object]

START = "2026-11-13T17:25:36.540000Z"
ADMIN = 2
# The queued task of the request.
QUEUED = {**test_pdf.QUEUED, "input_data": {"operation": "rotate", "document_count": 1}}
AT_ONCE = "another operation of this user ran at the same time"
UNFINISHED = (
    "another rotation of this user started and has no end in the task list (it still runs, or it "
    "was cut off)"
)
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
            wanted = params.get("id__in", "").split(",")
            rows = [
                {"id": n, "versions": v}
                for n, v in self.versions.items()
                if "modified__gte" in params or str(n) in wanted
            ]
            kind = "documents" if "modified__gte" in params else "wanted"
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


async def find(  # noqa: PLR0913 -- what is looked for, and how
    make_client: ClientFactory,
    archive: Archive,
    before: dict[int, int] | None = None,
    *,
    documents: tuple[int, ...] = (),
    search: RotationSearch | None = None,
    final: bool = False,
) -> FoundCopies:
    client = make_client(archive, retry=RetryPolicy(attempts=1))
    pdf = PaperlessPdfWriter(client)
    return await pdf.find_rotations(search or looking(before, documents), final=final)


def looking(
    before: dict[int, int] | None = None, documents: tuple[int, ...] = ()
) -> RotationSearch:
    hidden = before or {8: 8}
    shown = {n: n for n in documents} | hidden
    return RotationSearch(request=DONE_TASK, hidden=tuple(hidden), before=shown)


async def test_the_new_version_is_told_by_place_time_file_name_and_who_added_it(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.document(9)
    archive.add(50, 9, added=ago(hours=3))
    archive.add(61)
    archive.add(62, 9)

    assert await find(make_client, archive, {8: 8, 9: 50}) == FoundCopies(ids=(61, 62))
    listed = {"id__in": "8,9", "fields": "id,versions", "page_size": "100"}
    assert ("/api/documents/", listed) in archive.requests
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
async def test_another_operation_of_the_user_at_the_same_time_or_later_is_a_doubt(
    make_client: ClientFactory, fields: Json
) -> None:
    archive = Archive()
    archive.add(61)
    archive.bulk = [rotation(**fields), QUEUED]

    found = await find(make_client, archive)

    doubt = AT_ONCE if fields["date_done"] else UNFINISHED
    assert found == FoundCopies(ids=(None,), doubt=doubt)
    archive.bulk.append(rotation(id=3, task_id=task_id(3), date_done=None))
    assert (await find(make_client, archive)).doubt == UNFINISHED  # the one that stays is told
    assert (await find(make_client, Archive())).doubt is None


async def test_operations_that_add_no_such_version_are_no_doubt(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.add(61)
    archive.bulk = [
        QUEUED,
        rotation(input_data={"operation": "edit_pdf"}, date_done=LATER),
        rotation(input_data={}, date_done=LATER),  # Paperless's own update
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
    listed = {"modified__gte": ago(minutes=2), "fields": "id,versions", "page_size": "100"}
    assert ("/api/documents/", listed) in archive.requests

    archive.bulk.append(rotation(id=3, task_id=task_id(3)))  # one version settles one only
    assert (await find(make_client, archive, before)).doubt == PENDING
    archive.bulk = [QUEUED, rotation(input_data={"operation": "rotate", "document_count": 2})]
    assert (await find(make_client, archive, before)).doubt == PENDING


@pytest.mark.parametrize("data", [{}, {"document_count": "1"}])
async def test_an_earlier_operation_of_unknown_size_is_not_settled(
    make_client: ClientFactory, data: Json
) -> None:
    archive = Archive()
    archive.add(61)
    archive.bulk = [QUEUED, rotation(input_data={"operation": "rotate", **data})]

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


@pytest.mark.parametrize(
    "failing", ["documents", "metadata", "history", "bulk_update", "consume_file"]
)
async def test_a_failed_read_is_raised(make_client: ClientFactory, failing: str) -> None:
    archive = Archive()
    archive.add(61)
    archive.status[failing] = 503

    with pytest.raises(UpstreamUnavailable):
        _ = await find(make_client, archive)


async def test_a_hidden_document_is_a_doubt_and_hidden_tasks_are_raised(
    make_client: ClientFactory,
) -> None:
    hidden, untold = Archive(), Archive()
    del hidden.versions[8]
    untold.status["bulk_update"] = 403

    assert (await find(make_client, hidden)).doubt == "document 8 cannot be read any more"
    with pytest.raises(PermissionDenied):
        _ = await find(make_client, untold)


EARLIER_STAYS = FoundCopies(ids=(None,), doubt=PENDING, passing=True)


async def test_the_other_documents_of_the_request_settle_no_earlier_rotation(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.document(9)
    archive.bulk = [QUEUED, rotation()]
    archive.add(60)  # of the earlier rotation; ours is still to come
    archive.add(61, 9)  # ours, of a document whose task is listed

    async def mixed(*, final: bool = False, shown: int = 9) -> FoundCopies:
        search = RotationSearch(DONE_TASK, (8,), {8: 8, 9: shown})
        return await find(make_client, archive, search=search, final=final)

    assert await mixed() == EARLIER_STAYS
    assert await mixed(final=True) == EARLIER_STAYS
    archive.add(62, 9, added=START)  # in the moment ours started: it may be ours
    assert await mixed() == EARLIER_STAYS
    archive.document(10)
    archive.add(59, 10, added=ago(seconds=30))  # what the earlier one really made
    assert await mixed() == FoundCopies(ids=(60,))
    _ = archive.versions.pop(10)
    # Newer than what 9 showed, so it may be ours, written by a host whose clock is behind.
    archive.add(58, 9, added=ago(seconds=30))
    assert await mixed() == EARLIER_STAYS
    assert await mixed(shown=58) == FoundCopies(ids=(60,))  # 9 showed it before: it is not ours
    archive.versions[9][0]["added"] = LATER
    assert await mixed(shown=58) == EARLIER_STAYS


async def test_an_upload_under_the_name_of_a_rotation_settles_nothing(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    archive.document(9)
    archive.bulk = [QUEUED, rotation()]
    archive.add(60)
    archive.add(61, 9)
    upload = {**row(20, "consume_file", "9_rotated.pdf"), "date_created": LATER}
    data = {"filename": "9_rotated.pdf", "overrides": {"owner_id": 2, "actor_id": 2}}

    for shown in (data, {"filename": "9_rotated.pdf"}):
        archive.tasks = [{**upload, "input_data": shown}]
        assert await find(make_client, archive) == EARLIER_STAYS
    turned = {**data, "overrides": {"title": "Scan", "owner_id": 2, "actor_id": 2}}
    archive.tasks = [{**upload, "input_data": turned}]
    assert await find(make_client, archive) == FoundCopies(ids=(60,))


class Racing(Archive):
    """After the lists of tasks and operations were read, something more happens once."""

    def __init__(self, *, upload: bool, pending: bool = False) -> None:
        super().__init__()
        self.upload: bool = upload
        self.document(10)
        self.bulk: list[Json] = [QUEUED, rotation()]
        if pending:  # queued before, started only now
            self.bulk.append(rotation(id=9, task_id=task_id(9), date_started=None, date_done=None))
        self.add(60)  # of the earlier rotation
        self.raced: bool = False

    @override
    def _list(self, kind: str, params: dict[str, str]) -> httpx2.Response:
        answer = super()._list(kind, params)
        if kind == "consume_file" and not self.raced:
            self.raced = True
            self.add(70, 10)
            if self.upload:
                task = {**row(20, "consume_file", "10_rotated.pdf"), "date_created": LATER}
                data = {"filename": "10_rotated.pdf", "overrides": {"actor_id": 2}}
                self.tasks: list[Json] = [{**task, "input_data": data}]
            else:
                now = {"date_started": LATER, "date_done": LATER}
                self.bulk = [*self.bulk[:2], rotation(id=9, task_id=task_id(9), **now)]
        return answer


@pytest.mark.parametrize("pending", [False, True])
async def test_a_rotation_that_starts_while_versions_are_counted_settles_nothing(
    make_client: ClientFactory, *, pending: bool
) -> None:
    archive = Racing(upload=False, pending=pending)

    found = await find(make_client, archive)

    assert archive.raced
    assert found == FoundCopies(ids=(None,), doubt=AT_ONCE)


async def test_an_upload_that_comes_while_versions_are_counted_settles_nothing(
    make_client: ClientFactory,
) -> None:
    assert await find(make_client, Racing(upload=True)) == EARLIER_STAYS


async def test_the_window_holds_every_rotation_that_could_have_filled_it(
    make_client: ClientFactory,
) -> None:
    """One that ended over an hour ago, while one that counts ran, could have made what is there."""
    archive = Archive()
    archive.document(9)
    long = {"date_started": ago(minutes=80), "date_done": ago(minutes=50)}
    ended = {"date_started": ago(minutes=79), "date_done": ago(minutes=78)}
    before = {"date_started": ago(minutes=85), "date_done": ago(minutes=80)}
    apart = {"date_started": ago(minutes=99), "date_done": ago(minutes=90)}
    archive.bulk = [
        QUEUED,
        *(rotation(id=n, **t) for n, t in enumerate((long, ended, before, apart))),
    ]
    archive.add(50, 9, added=ago(minutes=77))
    archive.add(51, 9, added=ago(minutes=76))
    archive.add(52, 9, added=ago(minutes=86))
    archive.add(61)

    assert (await find(make_client, archive)).doubt == PENDING
    archive.add(53, 9, added=ago(minutes=84))
    assert await find(make_client, archive) == FoundCopies(ids=(61,))
    asked = [p["modified__gte"] for _, p in archive.requests if "modified__gte" in p]
    assert set(asked) == {ago(minutes=85)}

    last = {"date_created": ago(minutes=84)}
    for kind in ("bulk_update", "consume_file"):
        archive.next = {kind: "http://paperless/api/tasks/?page=2"}
        archive.bulk[1:] = [{**r, "date_created": ago(minutes=84)} for r in archive.bulk[1:]]
        archive.tasks = [{**row(20, "consume_file", "x.pdf"), **last}]
        assert (await find(make_client, archive)).doubt == UNCOMPARED


async def test_settled_reads_are_kept_and_the_history_waits_for_every_version(
    make_client: ClientFactory,
) -> None:
    archive = Archive()
    numbers = tuple(range(100, 120))
    for number in numbers:
        archive.document(number)
    search = looking({n: n for n in numbers})

    def asked(*ends: str) -> list[int]:
        paths = [path.rstrip("/").rsplit("/", 1)[-1] for path, _ in archive.requests]
        archive.requests.clear()
        return [paths.count(end) for end in ends]

    kinds = ("ui_settings", "tasks", "documents", "metadata", "history")
    for number in numbers[:-1]:
        archive.add(number + 1000, number)
    # A first file is no rotation, so its name is not asked for.
    archive.document(99)
    archive.versions[99][0]["added"] = LATER
    archive.versions[100][-1]["added"] = LATER
    search.before = {**search.before, 100: -1}
    assert (await find(make_client, archive, search=search)).ids == (None,) * 20
    assert asked(*kinds) == [1, 2, 1, 19, 0]
    assert (await find(make_client, archive, search=search)).ids == (None,) * 20
    assert asked(*kinds) == [0, 2, 1, 0, 0]
    archive.add(numbers[-1] + 1000, numbers[-1])
    assert None not in (await find(make_client, archive, search=search)).ids
    assert asked(*kinds) == [0, 2, 1, 1, 20]
    assert None not in (await find(make_client, archive, search=search)).ids
    assert asked(*kinds) == [0, 2, 1, 0, 0]


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
