"""One reading of the versions and the trash for several files (decision-50).

What the check of one file has must hold here, too: the originals are looked up before and after
the versions, both steps end in time, and one that does not add up is repeated once.
"""

import asyncio
import hashlib
from typing import TYPE_CHECKING, override

import pytest

from mcp_server_paperless.domain.errors import PermissionDenied, UpstreamTimeout
from mcp_server_paperless.services.duplicates import Bounds, CheckMovedError, DuplicateFinder
from services.test_duplicates import SHA, WEB, Failing, Setup, keys

if TYPE_CHECKING:
    from mcp_server_paperless.domain.documents import DocumentPage, DocumentQuery

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-50")]

OTHER = hashlib.sha256(b"another file").hexdigest()


async def test_one_reading_serves_several_files() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11, original=b"third")
    version = await setup.version(11, b"another file")

    batch = await setup.finder.read_for((SHA, OTHER))
    assert keys((await setup.finder.find(SHA, batch)).matches) == [(10, None, False)]
    assert keys((await setup.finder.find(OTHER, batch)).matches) == [(11, version, False)]

    assert setup.fake.calls.count("list_versions") == 1
    assert setup.fake.trash_reads.count(500) == 1  # read once, then only counted
    assert setup.fake.trash_reads.count(1) == 2


async def test_a_document_merged_after_the_versions_were_read_is_found() -> None:
    setup = Setup()
    merged = setup.document(10)
    batch = await setup.finder.read_for((SHA, OTHER))

    # Neither a version in the snapshot nor an original any more.
    setup.state.documents.remove(merged)

    found = await setup.finder.find(SHA, batch)
    assert keys(found.matches) == [(10, None, False)]
    assert found.count == 1


async def test_a_document_merged_while_the_versions_are_read_is_found() -> None:
    setup = Setup()
    merged = setup.document(10)
    setup.fake.changes["read_trash"] = [lambda: setup.state.documents.remove(merged)]

    batch = await setup.finder.read_for((OTHER, SHA))

    assert keys((await setup.finder.find(SHA, batch)).matches) == [(10, None, False)]


async def test_a_document_added_after_the_reading_is_found() -> None:
    setup = Setup()
    batch = await setup.finder.read_for((SHA,))
    _ = setup.document(10)

    assert keys((await setup.finder.find(SHA, batch)).matches) == [(10, None, False)]


async def test_a_trash_that_changed_is_read_again_once_for_every_file_left() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(12, original=b"another file")
    batch = await setup.finder.read_for((SHA, OTHER))
    await setup.delete(10)

    assert keys((await setup.finder.find(SHA, batch)).matches) == [(10, None, True)]
    assert keys((await setup.finder.find(OTHER, batch)).matches) == [(12, None, False)]
    assert setup.fake.trash_reads.count(500) == 2  # the second file uses the new reading


async def test_a_check_that_keeps_moving_fails_and_names_the_cause() -> None:
    setup = Setup()
    batch = await setup.finder.read_for((SHA,))
    for number in (20, 21, 22):
        _ = setup.document(number, original=bytes([number]))
    await setup.delete(20)
    # The second reading lists one entry fewer than it counts.
    setup.fake.short_trash = True

    with pytest.raises(CheckMovedError):
        _ = await setup.finder.find(SHA, batch)


async def test_a_first_reading_that_moves_is_repeated_once() -> None:
    setup = Setup()
    _ = setup.document(10)
    _ = setup.document(11, original=b"x")
    await setup.delete(11)
    setup.fake.short_trash = True

    def settle() -> None:
        setup.fake.short_trash = False

    setup.fake.changes["read_trash"] = [lambda: None, settle]

    batch = await setup.finder.read_for((SHA,))

    assert keys((await setup.finder.find(SHA, batch)).matches) == [(10, None, False)]


async def test_a_user_who_may_not_list_documents_gets_the_gap() -> None:
    setup = Setup()
    setup.fake.errors["search_documents"] = PermissionDenied("no")

    batch = await setup.finder.read_for((SHA,))

    found = await setup.finder.find(SHA, batch)
    assert "Documents: this user may not list them." in found.not_checked


class Slow(Failing):
    slow: bool = True

    @override
    async def search_documents(
        self, query: DocumentQuery, *, page: int, page_size: int
    ) -> DocumentPage:
        if self.slow:
            await asyncio.sleep(30)
        return await super().search_documents(query, page=page, page_size=page_size)


async def test_both_steps_end_in_time() -> None:
    setup = Setup()
    fake = Slow(setup.state)
    finder = DuplicateFinder(fake, fake, web_url=WEB, bounds=Bounds(seconds=0.05))

    with pytest.raises(UpstreamTimeout, match=r"longer than 0\.05 seconds"):
        _ = await finder.read_for((SHA,))
    fake.slow = False
    batch = await finder.read_for((SHA,))
    fake.slow = True
    with pytest.raises(UpstreamTimeout, match="duplicate check"):
        _ = await finder.find(SHA, batch)
