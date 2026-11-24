"""The duplicate check with many trash entries of one date, on PostgreSQL.

Paperless orders the trash by the date documents were created, with no tie-break. PostgreSQL may
give rows with equal dates in another order for every page, so pages overlap and entries are on
none of them. The tests run on a stack of their own (`just e2e-postgres`, marker `postgres_e2e`) and
skip elsewhere: SQLite keeps one order.
"""

import os
from typing import TYPE_CHECKING

import pytest

from e2e.helpers import num, results
from e2e.uploading import Scope, matches, output, text, unique

if TYPE_CHECKING:
    from collections.abc import Iterator

    from e2e.helpers import Stack

POSTGRES = os.environ.get("PAPERLESS_E2E_POSTGRES") == "1"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.postgres_e2e,
    pytest.mark.anyio,
    pytest.mark.decision("decision-47"),
    pytest.mark.skipif(
        not POSTGRES, reason="Not a PostgreSQL stack; `just e2e-postgres` runs this on one."
    ),
]
# More than two pages of 25, on three dates.
ENTRIES = 60
DATES = ("2026-01-01", "2026-01-02", "2026-01-03")


@pytest.fixture
def scope(stack: Stack) -> Iterator[Scope]:
    made = Scope(stack)
    yield made
    made.clean_up()


async def test_a_full_trash_neither_blocks_uploads_nor_hides_a_duplicate(
    stack: Stack, scope: Scope
) -> None:
    contents = [unique() for _ in range(ENTRIES)]
    documents = [scope.document(content) for content in contents]
    for index, document in enumerate(documents):
        _ = stack.admin.patch(
            f"/api/documents/{document}/", json={"created": DATES[index % len(DATES)]}
        ).raise_for_status()
        _ = stack.admin.delete(f"/api/documents/{document}/").raise_for_status()
    listed = results(stack.admin.get("/api/trash/", params={"page_size": 500}))
    assert {num(row["id"]) for row in listed} >= set(documents)

    new = output(await scope.upload(unique()))
    for index in (0, ENTRIES // 2, ENTRIES - 1):
        refused = await scope.upload(contents[index])
        assert f"document {documents[index]} " in text(refused)
        assert "in the trash" in text(refused)
    done = output(await scope.upload(contents[7], confirm=True))

    assert new["state"] == "success"
    assert matches(new["duplicates"], "before") == []
    assert matches(done["duplicates"], "before") == [(documents[7], None, True)]
