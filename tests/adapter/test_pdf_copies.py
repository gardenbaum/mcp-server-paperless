"""Finding the documents of a request whose tasks Paperless does not list (decision-67)."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import httpx2
import pytest

from adapter.test_instance import UI_SETTINGS
from adapter.test_pdf import DONE_TASK, EARLIER, LATER, OLD, QUEUED, Paperless, row, task_id, writer
from mcp_server_paperless.domain.errors import UpstreamUnavailable
from mcp_server_paperless.domain.pdf import CopiedFiles, FoundCopies, PageSlot
from mcp_server_paperless.paperless.client import RetryPolicy
from mcp_server_paperless.paperless.pdf import PaperlessPdfWriter

if TYPE_CHECKING:
    from adapter.conftest import ClientFactory

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-67")]

type Json = dict[str, object]

START = "2026-11-13T17:25:36.540000Z"
NAMES = ("8_edit_1.pdf", "8_edit_2.pdf")
PARTS = CopiedFiles(request=DONE_TASK, names=NAMES)
AT_ONCE = "another operation of this user ran at the same time"
PENDING = "files of earlier operations of this user may still come"
TASKED = "a task of its own makes a file of one of these names"
UNCOMPARED = "its tasks could not be compared"


def ago(**delta: int) -> str:
    """A time before the request started."""
    return (datetime.fromisoformat(START) - timedelta(**delta)).isoformat()


def made(number: int, name: str, added: str = LATER, owner: int = 2) -> Json:
    return {"id": number, "original_file_name": name, "added": added, "owner": owner}


def other(**fields: object) -> Json:
    """Another queued operation of the user, done a minute before the request."""
    times = {"date_started": ago(minutes=2), "date_done": ago(minutes=1)}
    return {**QUEUED, "id": 2, "task_id": task_id(2), **times, **fields}


class Archive:
    """A Paperless with documents, consumption tasks and queued operations."""

    def __init__(self, *documents: Json) -> None:
        self.documents: list[Json] = list(documents)
        self.tasks: list[Json] = []
        self.bulk: list[Json] = [QUEUED]
        self.next: str | None = None
        self.settings: int = 200
        self.failing: str = ""
        self.queries: list[dict[str, str]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        params = dict(request.url.params)
        assert request.method == "GET"
        if request.url.path == "/api/ui_settings/":
            body = UI_SETTINGS if self.settings == 200 else {"detail": "no"}
            return httpx2.Response(self.settings, json=body)
        kind = params.get("task_type", "documents")
        if kind == self.failing:
            return httpx2.Response(503, json={"detail": "busy"})
        rows, more = (self.tasks, None) if kind == "consume_file" else (self.bulk, self.next)
        if kind == "documents":
            self.queries.append(params)
            rows, more = self._found(params), None
        body = {"count": len(rows), "next": more, "previous": None, "results": rows}
        return httpx2.Response(200, json=body)

    def _found(self, params: dict[str, str]) -> list[Json]:
        start = params.get("original_filename__istartswith", "").lower()
        end = params.get("original_filename__iendswith", "").lower()
        since = datetime.fromisoformat(params["added__gte"])
        return [
            d
            for d in self.documents
            if str(d["original_file_name"]).lower().startswith(start)
            and str(d["original_file_name"]).lower().endswith(end)
            and datetime.fromisoformat(str(d["added"])) >= since
            and str(d["owner"]) == params["owner__id"]
        ]


async def find(
    make_client: ClientFactory, archive: Archive, files: CopiedFiles = PARTS, *, final: bool = False
) -> FoundCopies:
    client = make_client(archive, retry=RetryPolicy(attempts=1))
    return await PaperlessPdfWriter(client).find_copies(files, final=final)


async def test_earlier_operations_are_looked_into_once_every_file_is_there_or_at_the_end(
    make_client: ClientFactory,
) -> None:
    archive = Archive(made(61, NAMES[0]))
    archive.bulk = [QUEUED, other()]

    early = await find(make_client, archive)
    late = await find(make_client, archive, final=True)

    assert (early, len(archive.queries)) == (FoundCopies(ids=(61, None)), 3)
    assert late == FoundCopies(ids=(None, None), doubt=PENDING, passing=True)


async def test_a_listed_task_ends_the_wait_early(make_client: ClientFactory) -> None:
    archive = Archive()
    archive.tasks = [{**row(20, "consume_file", NAMES[0]), "date_created": LATER}]

    found = await find(make_client, archive)

    assert (found.doubt, found.passing) == (TASKED, False)


async def test_the_files_of_a_request_are_found_by_name_owner_and_time(
    make_client: ClientFactory,
) -> None:
    archive = Archive(made(62, NAMES[1]), made(61, NAMES[0]))

    found = await find(make_client, archive)

    assert found == FoundCopies(ids=(61, 62), doubt=None)
    [query] = archive.queries
    assert query == {
        "owner__id": "2",
        "added__gte": "2026-11-13T17:25:36.540000+00:00",
        "original_filename__istartswith": "8_edit_",
        "fields": "id,original_file_name,added",
        "page_size": "100",
    }


async def test_what_is_not_there_yet_or_not_the_users_is_missing_without_a_doubt(
    make_client: ClientFactory,
) -> None:
    archive = Archive(
        made(61, NAMES[0]),
        made(62, NAMES[1], added=EARLIER),  # from before the request
        made(63, NAMES[1], owner=3),
        made(64, "8_edit_2.PDF"),
        made(65, "8_edit_2.pdf.pdf"),
        made(66, "8_edit_x.pdf"),
    )

    assert await find(make_client, archive) == FoundCopies(ids=(61, None), doubt=None)


@pytest.mark.parametrize("name", [NAMES[0], "8_edit_3.pdf"])
async def test_more_documents_than_files_are_a_doubt(make_client: ClientFactory, name: str) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]), made(63, name))

    found = await find(make_client, archive)

    assert found.doubt == "documents 61, 62, 63 have names of these files, of which there are 2"
    assert found.ids == (None, None)


async def test_merged_and_unlocked_documents_are_found_by_their_whole_name(
    make_client: ClientFactory,
) -> None:
    archive = Archive(
        made(61, "8_9_merged.pdf"), made(62, "8_unprotected.pdf"), made(63, "8_edit_1.pdf")
    )

    for number, name in ((61, "8_9_merged.pdf"), (62, "8_unprotected.pdf")):
        files = CopiedFiles(request=DONE_TASK, names=(name,))
        assert await find(make_client, archive, files) == FoundCopies(ids=(number,), doubt=None)
        assert archive.queries[-1]["original_filename__istartswith"] == name


@pytest.mark.parametrize(
    "fields", [{"date_done": None}, {"date_done": START}, {"date_done": LATER}]
)
@pytest.mark.parametrize("operation", ["edit_pdf", "merge", "remove_password", None])
async def test_another_copying_operation_at_the_same_time_or_later_is_a_doubt(
    make_client: ClientFactory, fields: Json, operation: str | None
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.bulk = [QUEUED, other(**fields, input_data={"operation": operation})]

    assert await find(make_client, archive) == FoundCopies(ids=(None, None), doubt=AT_ONCE)


async def test_operations_that_hand_over_nothing_to_confuse_are_no_doubt(
    make_client: ClientFactory,
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.bulk = [
        QUEUED,
        other(input_data={"operation": "set_correspondent"}, date_done=LATER),
        other(owner=3, date_done=LATER),
        other(date_started=None, date_done=None),  # its files come later than these
        other(date_started=ago(hours=3), date_done=ago(minutes=61)),
    ]

    assert (await find(make_client, archive)).doubt is None


async def test_an_earlier_operation_whose_files_may_still_come_is_a_doubt(
    make_client: ClientFactory,
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.bulk = [QUEUED, other()]

    assert (await find(make_client, archive)).doubt == PENDING

    archive.documents.append(made(50, "8_edit_1.pdf", added=ago(seconds=30)))  # the same document
    assert (await find(make_client, archive)).doubt == PENDING
    archive.documents.append(made(51, "7_edit_2.pdf", added=ago(seconds=30)))  # not a first file
    assert (await find(make_client, archive)).doubt == PENDING


@pytest.mark.parametrize("name", ["7_edit_1.pdf", "7_9_merged.pdf", "8_unprotected.pdf"])
async def test_an_earlier_operation_that_made_other_files_is_no_doubt(
    make_client: ClientFactory, name: str
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.documents.append(made(50, name, added=ago(seconds=30)))
    archive.bulk = [QUEUED, other()]

    assert await find(make_client, archive) == FoundCopies(ids=(61, 62), doubt=None)
    assert archive.queries[-1] == {
        "owner__id": "2",
        "added__gte": ago(minutes=2),
        "original_filename__iendswith": ".pdf",
        "fields": "id,original_file_name,added",
        "page_size": "100",
    }

    archive.bulk.append(other(id=3, task_id=task_id(3)))  # one file proves one operation only
    assert (await find(make_client, archive)).doubt == PENDING


# What Paperless queues for itself after metadata changes (documents.tasks.bulk_update_documents).
SYSTEM: Json = {"owner": None, "input_data": {}, "trigger_source": "system"}


@pytest.mark.parametrize("fields", [{}, {"date_done": None}, {"date_done": LATER}])
async def test_what_paperless_queues_for_itself_hands_over_no_document(
    make_client: ClientFactory, fields: Json
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.bulk = [QUEUED, other(**SYSTEM, **fields)]

    assert await find(make_client, archive) == FoundCopies(ids=(61, 62))


@pytest.mark.parametrize(
    "fields",
    [
        {"owner": 2},
        {"trigger_source": "web_ui"},
        {"input_data": {"operation": "edit_pdf"}},
        {"input_data": {"document_count": 1}},
    ],
)
async def test_a_row_that_is_only_almost_of_paperless_itself_stays_a_doubt(
    make_client: ClientFactory, fields: Json
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.bulk = [QUEUED, other(**{**SYSTEM, **fields})]

    assert (await find(make_client, archive)).doubt == PENDING


async def test_an_earlier_new_version_is_known_by_its_task(make_client: ClientFactory) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.bulk = [QUEUED, other()]
    archive.tasks = [
        {**row(20, "consume_file", "8_edited.pdf"), "date_created": ago(seconds=90)},
        {**row(21, "consume_file", "8_edited.pdf"), "date_created": ago(hours=2)},
    ]

    assert (await find(make_client, archive)).doubt is None

    archive.tasks = archive.tasks[1:]  # from before that operation
    assert (await find(make_client, archive)).doubt == PENDING


@pytest.mark.parametrize("name", NAMES)
async def test_a_listed_task_for_one_of_the_names_is_a_doubt(
    make_client: ClientFactory, name: str
) -> None:
    archive = Archive(made(61, NAMES[0]), made(62, NAMES[1]))
    archive.tasks = [{**row(20, "consume_file", name), "date_created": LATER}]

    assert (await find(make_client, archive)).doubt == TASKED

    archive.tasks = [{**row(20, "consume_file", name), "date_created": EARLIER}]
    assert (await find(make_client, archive)).doubt is None


async def test_what_cannot_be_compared_is_a_doubt(make_client: ClientFactory) -> None:
    documents = (made(61, NAMES[0]), made(62, NAMES[1]))
    unknown, gone, unstarted, full, dense = (Archive(*documents) for _ in range(5))
    unknown.settings = 403
    gone.bulk = []
    unstarted.bulk = [{**QUEUED, "date_started": None}]
    full.next = "http://paperless/api/tasks/?page=2"
    full.bulk = [QUEUED, other(date_created=ago(minutes=59), owner=3)]
    dense.next = full.next
    dense.bulk = [QUEUED, other(date_created=ago(minutes=61), owner=3)]

    assert (await find(make_client, unknown)).doubt == "the current user is not known"
    assert (await find(make_client, gone)).doubt == "the times of its queued task are not known"
    assert (await find(make_client, unstarted)).doubt == (
        "the times of its queued task are not known"
    )
    assert (await find(make_client, full)).doubt == UNCOMPARED
    assert (await find(make_client, dense)).doubt is None  # the list reaches back far enough


@pytest.mark.parametrize("failing", ["documents", "consume_file", "bulk_update"])
async def test_a_failed_read_is_raised(make_client: ClientFactory, failing: str) -> None:
    archive = Archive()
    archive.failing = failing

    with pytest.raises(UpstreamUnavailable):
        _ = await find(make_client, archive)


async def test_a_request_for_new_documents_says_how_its_files_are_found(
    make_client: ClientFactory,
) -> None:
    slots = (PageSlot(page=1), PageSlot(page=2, document=1))
    numbers = tuple(range(1000, 1030))
    whole = "_".join(str(n) for n in numbers)[:100]

    edited = await writer(make_client, Paperless(OLD, OLD)).edit_pdf(
        8, slots, output="new_document"
    )
    merged = await writer(make_client, Paperless(OLD, OLD)).merge_documents(
        (5, 9), metadata_document_id=9
    )
    cut = await writer(make_client, Paperless(OLD, OLD)).merge_documents(
        numbers, metadata_document_id=1000
    )
    unlocked = await writer(make_client, Paperless(OLD, OLD)).remove_password(
        8, "x", output="new_document"
    )
    version = await writer(make_client, Paperless(OLD, OLD)).edit_pdf(
        8, slots[:1], output="new_version"
    )

    assert edited.files == PARTS
    assert merged.files == CopiedFiles(
        request=DONE_TASK, names=("5_9_merged.pdf",), included=(5, 9)
    )
    assert cut.files == CopiedFiles(request=DONE_TASK, names=(f"{whole}_merged.pdf",))
    assert unlocked.files == CopiedFiles(request=DONE_TASK, names=("8_unprotected.pdf",))
    assert version.files is None
