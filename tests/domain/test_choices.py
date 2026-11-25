"""Questions about ambiguous names, the user's picks and the written mark (decision-109)."""

import asyncio
import dataclasses

import pytest

from mcp_server_paperless.domain.choices import (
    Choice,
    Question,
    contradicts,
    note_write,
    picked,
    picking,
)
from mcp_server_paperless.domain.errors import Ambiguous

pytestmark = pytest.mark.decision("decision-109")

A, B = Choice("1", "'Tax' (id 1)"), Choice("2", "'tax' (id 2)")
MORE = dataclasses.replace(B, detail="3 documents")
QUESTION = Question("tags", "TAX", (A, B))


@pytest.mark.parametrize(
    "other",
    [
        dataclasses.replace(QUESTION, what="correspondents"),
        dataclasses.replace(QUESTION, word="tax"),
        dataclasses.replace(QUESTION, role="parent"),
        dataclasses.replace(QUESTION, about="7"),
        dataclasses.replace(QUESTION, choices=(A,)),
        dataclasses.replace(QUESTION, choices=(B, A)),
        dataclasses.replace(QUESTION, choices=(A, B, Choice("3", "'TAX' (id 3)"))),
        dataclasses.replace(QUESTION, choices=(A, Choice("2", "'TAX' (id 2)"))),
        dataclasses.replace(QUESTION, choices=(A, Choice("3", B.title))),
        dataclasses.replace(QUESTION, choices=(A, dataclasses.replace(B, owner=3))),
    ],
)
def test_another_word_kind_or_match_is_another_question(other: Question) -> None:
    assert other.fingerprint != QUESTION.fingerprint


def test_a_detail_that_changes_is_no_other_question() -> None:
    """A document is filed while the user is asked: its count says nothing about the match."""
    assert dataclasses.replace(QUESTION, choices=(A, MORE)).fingerprint == QUESTION.fingerprint
    assert MORE.shown == "'tax' (id 2), 3 documents"
    assert B.shown == "'tax' (id 2)"


@pytest.mark.parametrize("change", [{"role": "parent"}, {"about": "7"}, {"what": "x"}])
def test_another_role_or_thing_is_another_topic(change: dict[str, str]) -> None:
    assert dataclasses.replace(QUESTION, **change).topic != QUESTION.topic


def test_words_that_look_alike_when_cut_are_two_questions() -> None:
    one, two = (Question("tags", "x" * 200 + end, (A, B)) for end in "ab")

    assert one.fingerprint != two.fingerprint


def test_the_fingerprint_is_stable_and_takes_any_text() -> None:
    assert dataclasses.replace(QUESTION).fingerprint == QUESTION.fingerprint
    assert len(Question("tags", "\ud800", (A, B)).fingerprint) == 64


def test_a_pick_answers_exactly_its_question_and_only_in_the_block() -> None:
    other = dataclasses.replace(QUESTION, what="correspondents")

    assert picked(QUESTION) is None
    with picking([QUESTION.picking("2")]):
        assert picked(QUESTION) == "2"
        assert picked(other) is None
        with picking([]):
            assert picked(QUESTION) is None
        assert picked(QUESTION) == "2"
    assert picked(QUESTION) is None


def test_a_pick_contradicts_what_is_left_without_it() -> None:
    def left(*choices: Choice, what: str = "tags") -> str | None:
        return contradicts(Question(what, "TAX", choices))

    assert left(A) is None
    with picking([QUESTION.picking("2")]):
        assert left(A) == "2"
        assert left(B) is None
        assert left(A, what="correspondents") is None
    with picking([QUESTION.picking("2"), QUESTION.picking("1")]):  # the last one counts
        assert left(A) is None
        assert left(B) == "1"
    with picking([Question("tags", "x", (A,)).picking("2")]):
        assert left(A) is None


def test_an_error_carries_its_question_and_says_what_it_always_said() -> None:
    error = Ambiguous("Two match.", hint="Pass the id.", question=QUESTION)

    assert error.question is QUESTION
    assert error.describe() == "Two match. Hint: Pass the id."
    assert Ambiguous("Two match.").question is None


def test_a_write_is_noted_for_its_run_only() -> None:
    note_write()  # outside a run: nothing to note
    with picking([]) as first:
        assert not first.written
        note_write()
    with picking([]) as second:
        assert first.written
        assert not second.written


async def _write() -> None:
    await asyncio.sleep(0)
    note_write()


@pytest.mark.anyio
async def test_a_write_in_a_task_of_the_run_counts() -> None:
    with picking([]) as run:
        async with asyncio.TaskGroup() as group:
            _ = group.create_task(_write())

    assert run.written
