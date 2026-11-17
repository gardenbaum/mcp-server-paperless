"""Contract of RawReader against the fake and live Paperless 3.4.2 (decision-25).

Only paths both backends serve: the tag list, a document's file and what is no endpoint. The
live stack is the authority for what Django does to a path without its trailing slash.
"""

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from paperless_mcp.domain.errors import NotFound, PaperlessMcpError

if TYPE_CHECKING:
    from contract.conftest import Backend

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]

GET = "RawReader.get"
BIG = 1_000_000


class Row(BaseModel):
    id: int
    name: str


class Tags(BaseModel):
    count: int
    results: list[Row]


def page_of(body: bytes) -> Tags:
    return Tags.model_validate_json(body)


@pytest.mark.contract(GET)
async def test_a_json_endpoint_comes_back_whole_with_its_content_type(backend: Backend) -> None:
    reply = await backend.admin.raw.get("/api/tags/", {}, max_bytes=BIG)

    assert (reply.status, reply.truncated) == (200, False)
    assert reply.content_type.startswith("application/json")
    page = page_of(reply.body)
    names = {row.name for row in page.results}
    assert {"e2e-finance", "e2e-inbox"} <= names
    assert page.count >= 3


@pytest.mark.contract(GET)
async def test_params_are_sent_as_the_query(backend: Backend) -> None:
    reply = await backend.admin.raw.get("/api/tags/", {"page_size": "1"}, max_bytes=BIG)

    page = page_of(reply.body)
    assert len(page.results) == 1
    assert page.count >= 3


@pytest.mark.contract(GET)
async def test_a_body_over_the_limit_is_reported_and_not_kept(backend: Backend) -> None:
    reply = await backend.admin.raw.get("/api/tags/", {}, max_bytes=20)

    assert (reply.truncated, reply.body) == (True, b"")


@pytest.mark.contract(GET)
async def test_a_file_comes_back_with_its_own_content_type(backend: Backend) -> None:
    reply = await backend.admin.raw.get("/api/documents/1/download/", {}, max_bytes=BIG)

    assert reply.content_type == "application/pdf"
    assert reply.body.startswith(b"%PDF")


@pytest.mark.contract(GET)
async def test_an_unknown_endpoint_is_a_redirect_that_is_not_followed(backend: Backend) -> None:
    with pytest.raises(PaperlessMcpError, match="redirected"):
        _ = await backend.admin.raw.get("/api/no_such_endpoint/", {}, max_bytes=BIG)


@pytest.mark.contract(GET)
async def test_an_unknown_document_is_not_found(backend: Backend) -> None:
    with pytest.raises(NotFound):
        _ = await backend.admin.raw.get("/api/documents/987654321/download/", {}, max_bytes=BIG)


@pytest.mark.contract(GET)
async def test_a_path_without_its_trailing_slash_is_a_redirect_that_is_not_followed(
    backend: Backend,
) -> None:
    with pytest.raises(PaperlessMcpError, match="redirected"):
        _ = await backend.admin.raw.get("/api/tags", {}, max_bytes=BIG)


@pytest.mark.contract(GET)
async def test_the_user_sees_what_paperless_shows_that_user(backend: Backend) -> None:
    reply = await backend.reader.raw.get("/api/tags/", {}, max_bytes=BIG)

    everything = await backend.admin.raw.get("/api/tags/", {}, max_bytes=BIG)
    assert {row.id for row in page_of(reply.body).results} <= {
        row.id for row in page_of(everything.body).results
    }
