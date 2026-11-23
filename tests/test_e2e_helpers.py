"""The wait of the E2E helpers for a rebuilt search index, without a live Paperless (PLMCP-67)."""

import httpx2
import pytest
from e2e import helpers


def api(counts: list[int], seen: list[str]) -> httpx2.Client:
    """A client whose searches report the given counts in turn; the last one repeats."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.params["query"])
        return httpx2.Response(200, json={"count": counts.pop(0) if len(counts) > 1 else counts[0]})

    return httpx2.Client(transport=httpx2.MockTransport(answer), base_url="http://paperless.test")


def test_the_wait_outlasts_an_index_the_web_process_still_sees_empty() -> None:
    seen: list[str] = []
    pauses: list[float] = []

    helpers.wait_for_index_reload(api([0, 0, 2], seen), pause=pauses.append)

    assert seen == [helpers.INDEXED_WORD] * 3
    # A pause before each search: the first one outlasts the reader's poll interval, so an index
    # from before the rebuild cannot answer it.
    assert pauses == [helpers.INDEX_RELOAD] * 3
    assert helpers.INDEX_RELOAD > 0.5


def test_an_index_that_stays_empty_fails_the_test() -> None:
    with pytest.raises(pytest.fail.Exception, match="still empty"):
        helpers.wait_for_index_reload(api([0], []), pause=lambda _: None, timeout=0.0)
