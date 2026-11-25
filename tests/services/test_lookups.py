"""Lookups: words of the search index, and the path a storage path would give a file."""

import pytest
from fakes.accounts import ADMIN, BLIND, READER
from fakes.paperless import FakeDocument, FakePaperless, FakeState, seeded

from mcp_server_paperless.domain.errors import (
    Ambiguous,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.paperless.auth import StaticTokenProvider
from mcp_server_paperless.services.lookups import MAX_PATH_SHOWN, MAX_WORD, Lookups
from mcp_server_paperless.services.taxonomy import TaxonomyResolver

pytestmark = pytest.mark.anyio

PUBLIC, PRIVATE = 1, 2


def lookups(state: FakeState | None = None, user: str = ADMIN[0]) -> Lookups:
    # No write is allowed: a lookup that writes fails its test.
    fake = FakePaperless(state or seeded(), user=user)
    resolver = TaxonomyResolver(fake, StaticTokenProvider("t"), ttl=3600.0)
    return Lookups(fake, fake, resolver)


async def test_words_come_most_frequent_first_and_as_many_as_asked_for() -> None:
    state = seeded()
    state.documents += [
        FakeDocument(id=10, title="x", content="zebra zoo Zoé"),
        FakeDocument(id=11, title="y", content="zoo"),
    ]

    found = await lookups(state).suggest_words("ZÖ", 10)
    one = await lookups(state).suggest_words(" zo ", 1)

    assert (found.words, found.too_long) == (("zoo", "zoe"), 0)
    assert one.words == ("zoo",)


async def test_no_word_is_an_answer() -> None:
    assert (await lookups().suggest_words("zzzq", 10)).words == ()


async def test_a_word_too_long_to_show_is_counted() -> None:
    state = seeded()
    state.documents.append(
        FakeDocument(id=10, title="x", content=f"zz{'z' * MAX_WORD} zz{'a' * (MAX_WORD - 2)}")
    )

    found = await lookups(state).suggest_words("zz", 10)

    assert (found.words, found.too_long) == ((f"zz{'a' * (MAX_WORD - 2)}",), 1)


@pytest.mark.parametrize("term", ["", "  ", "two words", "a\tb"])
async def test_only_the_beginning_of_one_word_is_looked_up(term: str) -> None:
    with pytest.raises(Validation, match="one word"):
        _ = await lookups().suggest_words(term, 10)


async def test_who_may_not_view_documents_gets_no_words() -> None:
    with pytest.raises(PermissionDenied):
        _ = await lookups(user=BLIND[0]).suggest_words("inv", 10)


@pytest.mark.parametrize("given", ["e2e-archive", "E2E-ARCHIVE", 1])
async def test_a_stored_path_is_tried_by_name_or_id(given: str | int) -> None:
    state = seeded()
    state.storage_paths[0].path = "new/{{ title }}"

    found = await lookups(state).test_storage_path(PUBLIC, given, None)

    assert (found.document_id, found.path, found.note) == (
        PUBLIC,
        "new/e2e-public-invoice.pdf",
        None,
    )
    assert found.storage_path is not None
    assert (found.storage_path.id, found.storage_path.name) == (1, "e2e-archive")


async def test_a_new_template_is_tried() -> None:
    found = await lookups().test_storage_path(PUBLIC, None, "a/{{ title }}")

    assert (found.path, found.storage_path) == ("a/e2e-public-invoice.pdf", None)


@pytest.mark.parametrize("template", ["{{ nope }}", "{{ title", "../x", "{{ '' }}"])
async def test_a_template_without_a_path_says_so(template: str) -> None:
    found = await lookups().test_storage_path(PUBLIC, None, template)

    assert found.path is None
    assert found.note is not None
    assert "cannot be rendered" in found.note
    assert "id" in found.note


async def test_a_long_path_is_cut_and_its_length_told() -> None:
    fits = await lookups().test_storage_path(PUBLIC, None, "a" * (MAX_PATH_SHOWN - 4))
    cut = await lookups().test_storage_path(PUBLIC, None, "a" * (MAX_PATH_SHOWN - 3))

    assert (len(fits.path or ""), fits.note) == (MAX_PATH_SHOWN, None)
    assert cut.path == "a" * (MAX_PATH_SHOWN - 3) + ".pd"
    assert str(MAX_PATH_SHOWN + 1) in (cut.note or "")


@pytest.mark.parametrize(("name", "template"), [(None, None), ("e2e-archive", "x")])
async def test_exactly_one_of_path_and_template_is_given(
    name: str | None, template: str | None
) -> None:
    with pytest.raises(Validation, match="exactly one"):
        _ = await lookups().test_storage_path(PUBLIC, name, template)


async def test_a_blank_template_is_refused() -> None:
    with pytest.raises(Validation, match="blank"):
        _ = await lookups().test_storage_path(PUBLIC, None, " ")


async def test_an_unknown_or_hidden_document_or_path_is_named() -> None:
    with pytest.raises(NotFound, match="storage path"):
        _ = await lookups().test_storage_path(PUBLIC, "nope", None)
    for user, document in ((ADMIN[0], 99), (READER[0], PRIVATE)):
        with pytest.raises(NotFound, match=f"document {document}"):
            _ = await lookups(user=user).test_storage_path(document, None, "x")
    with pytest.raises(PermissionDenied):
        _ = await lookups(user=BLIND[0]).test_storage_path(PUBLIC, None, "x")


async def test_a_name_that_matches_two_paths_is_refused() -> None:
    state = seeded()
    path = state.storage_paths[0]
    state.storage_paths += [
        type(path)(id=8, name="Twice", path="x"),
        type(path)(id=9, name="TWICE", path="x"),
    ]

    with pytest.raises(Ambiguous):
        _ = await lookups(state).test_storage_path(PUBLIC, "twice", None)
