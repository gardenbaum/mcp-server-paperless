"""Listing a directory of uploads: what is named, what never is, and races (decision-50).

A listing grants nothing: every file it names is read through `read_allowed`, whose tests are in
test_upload_source.py. Here it is about what the walk opens and what it tells.
"""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.domain.errors import NotFound, Validation
from mcp_server_paperless.services.upload_listing import (
    MAX_DEPTH,
    MAX_ENTRIES,
    Listing,
    StoppedError,
    given_path,
    list_allowed,
    shown_path,
)
from mcp_server_paperless.services.upload_source import read_allowed

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = [pytest.mark.decision("decision-7"), pytest.mark.decision("decision-50")]


@pytest.fixture
def allowed(tmp_path: Path) -> Path:
    root = tmp_path / "allowed"
    for name in ("b.pdf", "a.pdf", "sub/c.pdf", "sub/deep/d.pdf"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        _ = (root / name).write_bytes(name.encode())
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    folder = tmp_path / "outside"
    folder.mkdir()
    _ = (folder / "secret.pdf").write_bytes(b"secret")
    return folder


def listing(directory: Path | str, root: Path, **given: object) -> Listing:
    return list_allowed(str(directory), [str(root)], **given)  # pyright: ignore[reportArgumentType] -- the hooks


def names(found: Listing, root: Path) -> list[str]:
    return [path.removeprefix(f"{root}/") for path in found.files]


def refused(directory: Path | str, root: Path, **given: object) -> NotFound:
    with pytest.raises(NotFound) as raised:
        _ = listing(directory, root, **given)
    return raised.value


def at(stage: str, action: Callable[[], object]) -> Callable[[str], None]:
    """A hook that acts once, the first time the walk reaches `stage`."""
    left = [action]

    def hook(reached: str) -> None:
        if reached == stage and left:
            _ = left.pop()()

    return hook


def test_the_files_are_sorted_and_subdirectories_are_only_named(allowed: Path) -> None:
    found = listing(allowed, allowed)

    assert found == Listing(
        files=(f"{allowed}/a.pdf", f"{allowed}/b.pdf"),
        directories=(f"{allowed}/sub",),
        deeper=(),
        hidden=0,
    )


def test_a_recursive_walk_reads_every_directory_and_sorts_the_whole(allowed: Path) -> None:
    found = listing(allowed, allowed, recursive=True)

    assert names(found, allowed) == ["a.pdf", "b.pdf", "sub/c.pdf", "sub/deep/d.pdf"]
    assert found.directories == ()


def test_a_directory_below_the_allowed_one_can_be_listed(allowed: Path) -> None:
    assert names(listing(allowed / "sub", allowed), allowed) == ["sub/c.pdf"]


def test_paths_are_reported_as_they_were_given(allowed: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(allowed, target_is_directory=True)

    assert listing(link, allowed).files == (f"{link}/a.pdf", f"{link}/b.pdf")
    assert listing(f"{allowed}/", allowed).files[0] == f"{allowed}/a.pdf"


def test_dot_names_are_counted_and_neither_named_nor_opened(allowed: Path) -> None:
    for name in (".env", f"{chr(0x200C)}.env"):
        _ = (allowed / name).write_bytes(b"secret")
    (allowed / ".git").mkdir()
    _ = (allowed / ".git" / "config").write_bytes(b"secret")
    (allowed / ".latest.pdf").symlink_to(allowed / "a.pdf")

    found = listing(allowed, allowed, recursive=True)

    assert found.hidden == 4
    assert names(found, allowed) == ["a.pdf", "b.pdf", "sub/c.pdf", "sub/deep/d.pdf"]


@pytest.mark.parametrize(
    "tail", ["nope", "a.pdf", ".git", "../outside", "sub/..", "sub/../sub", "escape", "dangling"]
)
def test_every_directory_that_cannot_be_listed_has_one_answer(
    allowed: Path, outside: Path, tail: str
) -> None:
    (allowed / ".git").mkdir()
    (allowed / "escape").symlink_to(outside, target_is_directory=True)
    (allowed / "dangling").symlink_to(outside / "nothing")

    error = refused(f"{allowed}/{tail}", allowed)

    assert error.message == "No directory was found at that path inside the allowed directories."
    assert str(outside) not in error.describe()
    assert error.describe() == refused(outside, allowed).describe()


@pytest.mark.parametrize(("path", "word"), [("in", "absolute"), ("/a\x00b", "NUL")])
def test_a_path_that_is_no_absolute_path_is_refused(path: str, word: str, allowed: Path) -> None:
    with pytest.raises(Validation, match=word):
        _ = listing(path, allowed)


def test_nothing_is_listed_without_an_allowlist_or_on_an_unsafe_platform(allowed: Path) -> None:
    with pytest.raises(Validation, match="not enabled"):
        _ = list_allowed(str(allowed), [])
    with pytest.raises(Validation, match="not supported"):
        _ = listing(allowed, allowed, secure=False)


def test_a_path_that_cannot_be_encoded_is_just_not_found(allowed: Path) -> None:
    _ = refused(f"{allowed}/\ud800", allowed)


# --- links and special files ---------------------------------------------------------------------


def test_links_that_lead_out_are_named_like_files_and_never_followed(
    allowed: Path, outside: Path
) -> None:
    """What they lead to is not told apart; reading each of them gives the one refusal."""
    (allowed / "z1").symlink_to(outside, target_is_directory=True)
    (allowed / "z2").symlink_to(outside / "secret.pdf")
    (allowed / "z3").symlink_to(outside / "nothing")
    (allowed / "z4").symlink_to(allowed / "z4")
    (allowed / "z5").symlink_to(allowed / ".git", target_is_directory=True)
    (allowed / ".git").mkdir()

    found = listing(allowed, allowed, recursive=True)

    assert names(found, allowed)[4:] == ["z1", "z2", "z3", "z4", "z5"]
    refusals: set[str] = set()
    for name in ("z1", "z2", "z3", "z4", "z5"):
        with pytest.raises(NotFound) as raised:
            _ = read_allowed(str(allowed / name), [str(allowed)], max_bytes=100)
        refusals.add(raised.value.describe())
    assert len(refusals) == 1


def test_a_link_to_a_file_inside_is_a_file_and_one_to_a_directory_a_directory(
    allowed: Path,
) -> None:
    (allowed / "x.pdf").symlink_to(allowed / "sub" / "c.pdf")
    (allowed / "y").symlink_to(allowed / "sub" / "deep", target_is_directory=True)

    found = listing(allowed, allowed)

    assert names(found, allowed) == ["a.pdf", "b.pdf", "x.pdf"]
    assert found.directories == (f"{allowed}/sub", f"{allowed}/y")


def test_a_linked_directory_is_walked_unless_it_was_already(allowed: Path, tmp_path: Path) -> None:
    second = tmp_path / "second"
    second.mkdir()
    _ = (second / "e.pdf").write_bytes(b"e")
    (allowed / "again").symlink_to(allowed / "sub", target_is_directory=True)
    (allowed / "more").symlink_to(second, target_is_directory=True)
    (second / "more-again").symlink_to(second, target_is_directory=True)
    (allowed / "sub" / "loop").symlink_to(allowed, target_is_directory=True)

    found = list_allowed(str(allowed), [str(allowed), str(second)], recursive=True)

    # The real directory wins over the link, whatever sorts first.
    assert names(found, allowed) == [
        *("a.pdf", "b.pdf", "more/e.pdf", "sub/c.pdf", "sub/deep/d.pdf")
    ]


def test_special_and_hard_linked_files_are_named_and_refused_when_read(allowed: Path) -> None:
    os.mkfifo(allowed / "pipe")
    os.link(allowed / "a.pdf", allowed / "second.pdf")

    assert names(listing(allowed, allowed), allowed) == ["a.pdf", "b.pdf", "pipe", "second.pdf"]
    for name in ("a.pdf", "pipe", "second.pdf"):
        with pytest.raises(NotFound):
            _ = read_allowed(str(allowed / name), [str(allowed)], max_bytes=100)


# --- names ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Käse 日本語 🧾.pdf", "Käse 日本語 🧾.pdf"),
        ("x\udcff.pdf", "x\\udcff.pdf"),
        ("a\nb.pdf", "a\\nb.pdf"),
        ("del\x7f", "del\\x7f"),
        ("gpj.\u202eexe", "gpj.\\u202eexe"),
        ("tag\U000e0001", "tag\\U000e0001"),
        ("a\\nb.pdf", "a\\\\nb.pdf"),  # never taken for a line break
    ],
)
def test_a_path_is_shown_without_what_hides_and_read_back_as_it_was(name: str, shown: str) -> None:
    assert shown_path(name) == shown
    assert shown_path(name).encode()
    assert given_path(shown) == name


@pytest.mark.parametrize(
    "text", ["a\\b.pdf", "x\\", "\\x4", "\\u12", "\\u12g4", "\\U0000", "\\Uffffffff", "\\N{DASH}"]
)
def test_an_escape_no_report_holds_is_refused(text: str) -> None:
    with pytest.raises(Validation, match="escape"):
        _ = given_path(text)


def test_files_are_sorted_by_what_they_are_not_by_what_is_shown(allowed: Path) -> None:
    for name in ("a\nb.pdf", "a\\nb.pdf", "a\u202eb.pdf"):
        _ = (allowed / name).write_bytes(b"x")

    found = listing(allowed, allowed)

    assert list(found.files) == sorted(found.files)
    assert len({shown_path(path) for path in found.files}) == 5


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS refuses names that are not UTF-8")
def test_a_name_that_is_no_text_is_listed(allowed: Path) -> None:
    _ = Path(os.fsdecode(os.fsencode(allowed) + b"/x\xff.pdf")).write_bytes(b"x")

    found = listing(allowed, allowed)

    assert [shown_path(path) for path in found.files][2:] == [f"{allowed}/x\\udcff.pdf"]


# --- limits --------------------------------------------------------------------------------------


def test_more_entries_than_the_limit_are_refused_and_exactly_the_limit_is_not(
    allowed: Path,
) -> None:
    _ = (allowed / ".hidden").write_bytes(b"")  # a.pdf, b.pdf, sub, .hidden

    assert len(listing(allowed, allowed, most=4).files) == 2
    with pytest.raises(Validation, match="more than 3 entries") as raised:
        _ = listing(allowed, allowed, most=3)
    assert "Name the files in paths" in raised.value.describe()


def test_the_limit_counts_the_whole_walk(allowed: Path) -> None:
    # a, b, sub; c, deep; d
    assert len(listing(allowed, allowed, recursive=True, most=6).files) == 4
    with pytest.raises(Validation, match="more than 5 entries"):
        _ = listing(allowed, allowed, recursive=True, most=5)
    assert MAX_ENTRIES == 10_000


def test_directories_below_the_depth_limit_are_named_and_not_read(allowed: Path) -> None:
    deep = allowed / "a" / "b" / "c"
    deep.mkdir(parents=True)
    _ = (deep / "x.pdf").write_bytes(b"x")
    (allowed / "a" / "link").symlink_to(deep, target_is_directory=True)

    found = listing(allowed, allowed, recursive=True, depth=2)

    assert found.deeper == (f"{allowed}/a/b/c",)
    assert "a/link/x.pdf" in names(found, allowed)  # two levels down
    shallow = listing(allowed, allowed, recursive=True, depth=1)
    assert shallow.deeper == (f"{allowed}/a/b", f"{allowed}/a/link", f"{allowed}/sub/deep")
    assert listing(allowed, allowed, recursive=True).deeper == ()
    assert MAX_DEPTH == 32


def test_a_chain_of_many_levels_ends_at_the_limit(allowed: Path) -> None:
    deepest = allowed
    for _ in range(100):
        deepest /= "d"
        deepest.mkdir()

    found = listing(allowed, allowed, recursive=True)

    assert found.deeper == (str(allowed / Path(*["d"] * 33)),)


def test_a_walk_stops_when_it_is_told_to(allowed: Path) -> None:
    (allowed / "link").symlink_to(allowed / "nothing")
    asked = 0

    def stop() -> bool:
        nonlocal asked
        asked += 1
        return asked == 6

    with pytest.raises(StoppedError):
        _ = listing(allowed, allowed, recursive=True, stop=stop)
    assert asked == 6


# --- races ---------------------------------------------------------------------------------------


def test_the_directory_swapped_for_a_link_after_it_was_approved_is_not_followed(
    allowed: Path, outside: Path
) -> None:
    def swap() -> None:
        _ = (allowed / "sub").rename(allowed / "old")
        (allowed / "sub").symlink_to(outside, target_is_directory=True)

    error = refused(allowed / "sub", allowed, at=at("resolved", swap))

    assert "secret" not in error.describe()


def test_a_subdirectory_swapped_for_a_link_before_it_is_opened_is_not_followed(
    allowed: Path, outside: Path
) -> None:
    def swap() -> None:
        _ = (allowed / "sub").rename(allowed / "zold")
        (allowed / "sub").symlink_to(outside, target_is_directory=True)

    found = listing(allowed, allowed, recursive=True, at=at("listed", swap))

    assert not any("secret" in path for path in found.files)
    assert "sub" in names(found, allowed)  # named as a file, which read_allowed refuses


def test_a_link_swapped_after_it_was_resolved_is_not_followed(allowed: Path, outside: Path) -> None:
    (allowed / "zz").symlink_to(allowed / "sub" / "deep", target_is_directory=True)

    def swap() -> None:
        _ = (allowed / "sub" / "deep").rename(allowed / "sub" / "old")
        (allowed / "sub" / "deep").symlink_to(outside, target_is_directory=True)

    found = listing(allowed, allowed, at=at("followed", swap))

    assert "zz" in names(found, allowed)
    assert not any("secret" in path for path in found.files)


def test_a_file_removed_after_the_names_were_read_is_still_named(allowed: Path) -> None:
    found = listing(allowed, allowed, at=at("listed", (allowed / "a.pdf").unlink))

    assert names(found, allowed) == ["a.pdf", "b.pdf"]


def test_a_subdirectory_that_vanished_is_named(allowed: Path) -> None:
    def vanish() -> None:
        _ = (allowed / "sub").rename(allowed.parent / "gone")

    for stage in ("listed", "looked"):  # before it was looked at, and before it was opened
        found = listing(allowed, allowed, recursive=True, at=at(stage, vanish))
        assert names(found, allowed) == ["a.pdf", "b.pdf", "sub"]
        _ = (allowed.parent / "gone").rename(allowed / "sub")


def test_no_descriptor_is_left_open(allowed: Path) -> None:
    def opened() -> int:
        return len(os.listdir("/dev/fd"))  # noqa: PTH208 -- counted, not walked

    _ = listing(allowed, allowed, recursive=True)
    before = opened()
    _ = listing(allowed, allowed, recursive=True)
    with pytest.raises(Validation):
        _ = listing(allowed, allowed, recursive=True, most=1)

    assert opened() == before
