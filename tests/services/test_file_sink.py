"""Saving into an allowed directory: where, under which name, and with others interfering."""

import asyncio
import errno
import hashlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, override

import anyio
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.errors import (
    ConfirmationRequired,
    NotFound,
    PaperlessMcpError,
    Validation,
    accepting,
)
from mcp_server_paperless.services import file_sink, transfer
from mcp_server_paperless.services.allowed_paths import MAX_NAME_BYTES
from mcp_server_paperless.services.file_sink import (
    MIB,
    RESERVE,
    Expected,
    FileSink,
    System,
    file_name,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-45")]

DATA = b"x" * 10 + b"y"
NAME = "a.pdf"


class Tree:
    """An allowed directory `in` with `sub`, and `out` beside it."""

    def __init__(self, root: Path) -> None:
        self.allowed: Path = root / "in"
        self.sub: Path = self.allowed / "sub"
        self.out: Path = root / "out"
        self.sub.mkdir(parents=True)
        self.out.mkdir()

    def names(self) -> list[str]:
        return sorted(p.name for p in self.sub.iterdir())


@pytest.fixture
def tree(tmp_path: Path) -> Tree:
    return Tree(tmp_path)


async def chunks(*given_chunks: bytes) -> AsyncGenerator[bytes]:
    for chunk in given_chunks:
        yield chunk


def nothing(_stage: str) -> None:
    """No interference."""


def when(stage: str, act: Callable[[], object]) -> Callable[[str], None]:
    """A seam that acts at `stage`."""

    def at(reached: str) -> None:
        if reached == stage:
            _ = act()

    return at


def sink(tree: Tree, at: Callable[[str], None] = nothing, system: System | None = None) -> FileSink:
    return FileSink([str(tree.allowed)], at=at, system=system or System())


async def save(  # noqa: PLR0913 -- one keyword per thing a test varies
    tree: Tree,
    name: str = NAME,
    *,
    directory: Path | str | None = None,
    data: AsyncGenerator[bytes] | None = None,
    confirm: bool = False,
    at: Callable[[str], None] = nothing,
    system: System | None = None,
    expected: Expected | None = None,
    through: FileSink | None = None,
    source: object = None,
    what: str = "this",
) -> file_sink.Saved:
    async with (through or sink(tree, at, system)).open(str(directory or tree.sub)) as target:
        await target.claim(name, confirm=confirm, what=what, source=source)
        return await target.write(
            data or chunks(DATA[:10], DATA[10:]), expected or Expected(len(DATA), None)
        )


# --- what is saved


async def test_a_new_file_has_the_bytes_and_is_the_owners_only(tree: Tree) -> None:
    saved = await save(tree)

    info = (tree.sub / NAME).stat()
    assert (tree.sub / NAME).read_bytes() == DATA
    assert (stat.S_IMODE(info.st_mode), info.st_nlink) == (0o600, 1)
    assert tree.names() == [NAME]
    assert saved == file_sink.Saved(
        NAME, len(DATA), hashlib.sha256(DATA).hexdigest(), replaced=False
    )


async def test_a_file_is_saved_into_the_allowed_directory_itself(tree: Tree) -> None:
    _ = await save(tree, directory=tree.allowed)

    assert (tree.allowed / NAME).read_bytes() == DATA


async def test_a_large_file_is_written_in_pieces_of_a_mebibyte(tree: Tree) -> None:
    sizes: list[int] = []

    class Counting(System):
        @override
        def write(self, fd: int, data: memoryview) -> int:
            sizes.append(len(data))
            return super().write(fd, data)

    data = os.urandom(2 * MIB + 5)
    saved = await save(
        tree,
        data=chunks(*[data[i : i + 65536] for i in range(0, len(data), 65536)]),
        system=Counting(),
        expected=Expected(len(data), hashlib.sha256(data).hexdigest()),
    )

    assert (tree.sub / NAME).read_bytes() == data
    assert saved.sha256 == hashlib.sha256(data).hexdigest()
    assert sizes == [MIB, MIB, 5]


async def test_an_empty_file_is_saved(tree: Tree) -> None:
    saved = await save(tree, data=chunks(), expected=Expected(0, None))

    assert (saved.size, (tree.sub / NAME).read_bytes()) == (0, b"")


async def test_short_writes_are_continued(tree: Tree) -> None:
    class Short(System):
        @override
        def write(self, fd: int, data: memoryview) -> int:
            return super().write(fd, data[:3])

    _ = await save(tree, system=Short())

    assert (tree.sub / NAME).read_bytes() == DATA


# --- replacing


async def test_an_existing_file_is_replaced_only_with_confirm(tree: Tree) -> None:
    old = tree.sub / NAME
    _ = old.write_bytes(b"old!")
    os.utime(old, (1_700_000_000, 1_700_000_000))

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await save(tree)
    assert raised.value.message == (
        "Nothing was changed. Confirming would replace the file 'a.pdf' (4 bytes, changed "
        f"2023-11-14) in '{tree.sub}' with this."
    )
    assert (old.read_bytes(), tree.names()) == (b"old!", [NAME])

    saved = await save(tree, confirm=True)
    assert (saved.replaced, old.read_bytes(), tree.names()) == (True, DATA, [NAME])


@pytest.mark.parametrize(
    ("folder", "name"),
    [
        ("sub  two", NAME),
        ('it\'s "quoted"', NAME),
        ("sub", "a  b.pdf"),
    ],
)
async def test_a_replacement_that_cannot_be_shown_is_refused_also_with_confirm(
    tree: Tree, folder: str, name: str
) -> None:
    directory = tree.allowed / folder
    directory.mkdir(exist_ok=True)
    _ = (directory / name).write_bytes(b"old")

    for confirm in (False, True):
        with pytest.raises(Validation) as raised:
            _ = await save(tree, name, directory=directory, confirm=confirm)
        assert raised.value.message.startswith("Nothing was changed: the ")
        assert "cannot be shown to the user in full" in raised.value.message
        assert raised.value.hint == "Choose another name with save_as, or another directory."
    assert (directory / name).read_bytes() == b"old"
    _ = await save(tree, "new.pdf", directory=directory)  # a new file needs no confirmation


async def test_a_confirmation_shows_the_whole_name_and_directory_or_is_refused(tree: Tree) -> None:
    directory = tree.allowed / "d"
    while len(str(directory)) < 200:
        directory /= "d" * 20
    directory.mkdir(parents=True)
    name = "n" * 200 + ".pdf"
    _ = (directory / name).write_bytes(b"old")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await save(tree, name, directory=directory)
    assert f"replace the file '{name}' (3 bytes" in raised.value.message
    assert raised.value.message.endswith(f" in '{directory}' with this.")

    deeper = directory / ("e" * 60)
    deeper.mkdir()
    _ = (deeper / NAME).write_bytes(b"old")
    for confirm in (False, True):
        with pytest.raises(Validation, match=r"directory .* it has more than 254 characters"):
            _ = await save(tree, directory=deeper, confirm=confirm)


async def test_a_confirmation_names_the_directory_a_link_leads_to(tree: Tree) -> None:
    (tree.allowed / "short").symlink_to(tree.sub)
    _ = (tree.sub / NAME).write_bytes(b"old")

    with pytest.raises(ConfirmationRequired) as raised:
        _ = await save(tree, directory=tree.allowed / "short", what="the original of document 7")

    assert raised.value.message.endswith(f" in '{tree.sub}' with the original of document 7.")


async def fingerprint(tree: Tree, source: object = 1) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        _ = await save(tree, source=source)
    return raised.value.fingerprint


@pytest.mark.decision("decision-44")
async def test_an_acceptance_covers_the_file_the_directory_and_what_is_saved(tree: Tree) -> None:
    """The text is the same each time; what it is about is not."""
    target = tree.sub / NAME
    _ = target.write_bytes(b"old")
    stamp = target.stat().st_mtime_ns
    accepted = await fingerprint(tree)
    assert await fingerprint(tree) == accepted

    assert await fingerprint(tree, 2) != accepted
    # Another file of the same name, size and day.
    target.unlink()
    (tree.sub / "filler").touch()
    _ = target.write_bytes(b"two")
    os.utime(target, ns=(stamp, stamp))
    other = await fingerprint(tree)
    assert other != accepted
    # The same file, written again in the same size and dated back: only its ctime tells.
    _ = target.write_bytes(b"new")
    os.utime(target, ns=(stamp, stamp))
    written = await fingerprint(tree)
    assert written != other
    other = written
    # The same file, with another date.
    os.utime(target, ns=(stamp, stamp + 1000))
    assert await fingerprint(tree) != other
    # Another directory at the same path, the file moved into it.
    held = await fingerprint(tree)
    _ = tree.sub.rename(tree.allowed / "gone")
    tree.sub.mkdir()
    _ = (tree.allowed / "gone" / NAME).rename(target)
    assert await fingerprint(tree) != held


@pytest.mark.decision("decision-44")
async def test_the_accepted_replacement_runs_and_no_other_one(tree: Tree) -> None:
    target = tree.sub / NAME
    _ = target.write_bytes(b"old")
    accepted = await fingerprint(tree)

    with accepting(accepted), pytest.raises(ConfirmationRequired):
        _ = await save(tree, source=2)
    with accepting(accepted):
        saved = await save(tree, source=1)

    assert (saved.replaced, target.read_bytes()) == (True, DATA)


async def test_the_refusal_comes_before_anything_is_read_or_written(tree: Tree) -> None:
    _ = (tree.sub / NAME).write_bytes(b"old")
    read: list[int] = []

    async def counted() -> AsyncGenerator[bytes]:
        read.append(1)
        yield b""

    with pytest.raises(ConfirmationRequired):
        _ = await save(tree, data=counted())

    assert read == []


async def test_a_file_dated_outside_the_calendar_is_still_described(tree: Tree) -> None:
    _ = (tree.sub / NAME).write_bytes(b"old")

    class Odd(System):
        @override
        def date(self, seconds: float) -> str:
            return super().date(1e18)

    with pytest.raises(ConfirmationRequired, match=r"\(3 bytes\) in"):
        _ = await save(tree, system=Odd())


async def test_confirm_for_a_name_that_is_free_makes_a_new_file(tree: Tree) -> None:
    assert not (await save(tree, confirm=True)).replaced


async def test_a_name_that_differs_only_in_case_is_the_same_file_where_the_system_says_so(
    tree: Tree,
) -> None:
    _ = (tree.sub / "A.PDF").write_bytes(b"old")
    if not (tree.sub / NAME).exists():
        pytest.skip("this file system tells case apart")

    with pytest.raises(ConfirmationRequired):
        _ = await save(tree)


# --- what is never written


@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("kind", ["link", "dangling", "hard", "pipe", "directory"])
async def test_only_a_plain_file_of_its_own_is_replaced(
    tree: Tree, kind: str, *, confirm: bool
) -> None:
    victim, target = tree.out / "victim", tree.sub / NAME
    _ = victim.write_bytes(b"victim")
    match kind:
        case "link":
            target.symlink_to(victim)
        case "dangling":
            target.symlink_to(tree.out / "nowhere")
        case "hard":
            target.hardlink_to(victim)
        case "pipe":
            os.mkfifo(target)
        case _:
            target.mkdir()

    with pytest.raises(Validation) as raised:
        _ = await save(tree, confirm=confirm)

    assert raised.value.message == "'a.pdf' in that directory is not a plain file of its own."
    assert raised.value.hint == "Choose another name with save_as."
    assert victim.read_bytes() == b"victim"
    assert [p.name for p in tree.out.iterdir()] == ["victim"]
    assert tree.names() == [NAME]


def refused_directories(tree: Tree) -> dict[str, str]:
    (tree.allowed / "escape").symlink_to(tree.out)
    (tree.allowed / "loop").symlink_to(tree.allowed / "loop")
    (tree.allowed / ".git").mkdir()
    # HFS+ ignores some format characters: there this name is .git, too.
    unseen = chr(0x200C)
    (tree.allowed / f"{unseen}.git").mkdir(exist_ok=True)
    (tree.allowed / f"a{unseen}b").mkdir()
    (tree.allowed / "file").touch()
    (tree.allowed.parent / "inner").mkdir()
    return {
        "a link that leads out": f"{tree.allowed}/escape",
        "a loop": f"{tree.allowed}/loop",
        "up and down again": f"{tree.allowed}/../in/sub",
        "up inside": f"{tree.sub}/../sub",
        "a dot directory": f"{tree.allowed}/.git",
        "a dot directory behind a character nobody sees": f"{tree.allowed}/{unseen}.git",
        "a character nobody sees": f"{tree.allowed}/a{unseen}b",
        "outside": str(tree.out),
        "a missing one outside": f"{tree.out}/nothing",
        "a missing one inside": f"{tree.allowed}/nothing",
        "the same start of the name": f"{tree.allowed}ner",
        "the parent": str(tree.allowed.parent),
        "a file": f"{tree.allowed}/file",
        "no encoding": f"{tree.allowed}/\ud800",
        "a NUL": f"{tree.allowed}/\0",
        "relative": "in/sub",
        "empty": "",
    }


async def test_every_unusable_directory_gets_the_same_answer(tree: Tree) -> None:
    answers: set[tuple[str, str | None]] = set()
    for directory in refused_directories(tree).values():
        with pytest.raises(NotFound) as raised:
            async with sink(tree).open(directory):
                raise AssertionError
        answers.add((raised.value.message, raised.value.hint))

    assert answers == {
        (
            "No usable directory was found at that path inside the allowed directories.",
            f"Give the absolute path of an existing directory inside one of: {tree.allowed}.",
        )
    }
    assert list(tree.out.iterdir()) == []


async def test_the_answer_names_every_allowed_directory(tree: Tree) -> None:
    both = FileSink([str(tree.allowed), str(tree.out)])

    with pytest.raises(NotFound) as raised:
        async with both.open("/nowhere"):
            raise AssertionError

    assert (raised.value.hint or "").endswith(f"inside one of: {tree.allowed}, {tree.out}.")


async def test_a_link_to_a_directory_inside_is_followed(tree: Tree) -> None:
    (tree.allowed / "short").symlink_to(tree.sub)

    _ = await save(tree, directory=tree.allowed / "short")

    assert tree.names() == [NAME]


async def test_without_an_allowed_directory_nothing_is_saved(tree: Tree) -> None:
    with pytest.raises(Validation) as raised:
        async with FileSink([]).open(str(tree.sub)):
            raise AssertionError

    assert raised.value.message == "Saving files is not enabled."
    assert raised.value.hint == "The operator enables it with PAPERLESS_MCP_DOWNLOAD_DIRS."


async def test_a_platform_without_safe_opening_saves_nothing(tree: Tree) -> None:
    unsafe = FileSink([str(tree.allowed)], secure=False)

    with pytest.raises(Validation) as raised:
        async with unsafe.open(str(tree.sub)):
            raise AssertionError

    assert raised.value.message == "Saving files is not supported on this platform."
    assert raised.value.hint == "Read the file in parts with offset."


# --- names

PDF, TEXT = "application/pdf", "text/plain"


@pytest.mark.parametrize(
    ("save_as", "proposed", "mime_type", "expected"),
    [
        (None, "2026 Müller €.pdf", PDF, "2026 Müller €.pdf"),
        (None, "2026 Scan.PDF", PDF, "2026 Scan.PDF"),
        (None, "2026 Scan.jpeg", "image/jpeg", "2026 Scan.jpeg"),
        (None, "a.txt", "text/plain; charset=utf-8", "a.txt"),
        (None, "a.txt", "TEXT/PLAIN", "a.txt"),
        ("x.pdf", "2026.pdf", PDF, "x.pdf"),
        ("x.PDF", "2026.pdf", PDF, "x.PDF"),
        ("x.tiff", "2026.tif", "image/tiff", "x.tiff"),
        ("日本語.pdf", "2026.PDF", PDF, "日本語.pdf"),
        (" x.pdf ", "2026.pdf", PDF, "x.pdf"),
        # The ending is the type's, whatever the title or the upload was called.
        (None, "2026 x.command", PDF, "2026 x.command.pdf"),
        (None, "2026 x.command", "application/x-sh", "2026 x.command.bin"),
        (None, "run.sh", TEXT, "run.sh.txt"),
        (None, "noext", TEXT, "noext.txt"),
        (None, "x.app", "", "x.app.bin"),
        ("x.bin", "x.app", "application/octet-stream", "x.bin"),
    ],
)
def test_a_file_has_the_proposed_name_or_the_given_one_and_the_ending_of_its_type(
    save_as: str | None, proposed: str, mime_type: str, expected: str
) -> None:
    assert file_name(save_as, proposed, mime_type) == expected


# Every type Paperless 3.5.1 consumes, with the ending it gives the file.
CONSUMED = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/rtf": ".rtf",
    "message/rfc822": ".eml",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow": ".ppsx",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.graphics": ".odg",
    "application/zip": ".zip",
}


@pytest.mark.parametrize("mime_type", CONSUMED)
def test_every_kind_of_file_paperless_consumes_keeps_its_ending(mime_type: str) -> None:
    name = f"2026 Rechnung{CONSUMED[mime_type]}"

    assert file_name(None, name, mime_type) == file_name(name, "other", mime_type) == name


@pytest.mark.parametrize(
    "save_as", ["x.md", "x", "conftest.py", "x.pdf.sh", "x.pdf.", "x.command", "x.app"]
)
def test_the_given_name_has_the_ending_of_the_type(save_as: str) -> None:
    for proposed in ("2026.pdf", save_as):  # also if Paperless proposed that very name
        with pytest.raises(Validation) as raised:
            _ = file_name(save_as, proposed, PDF)

        assert (
            raised.value.message == "A file of type application/pdf is saved with the ending .pdf."
        )
        assert raised.value.hint == "Change save_as, or leave it out."


def test_the_endings_of_a_type_that_has_several_are_named_and_of_an_unknown_one_is_bin() -> None:
    with pytest.raises(Validation, match=r"image/jpeg is saved with the ending \.jpg or \.jpeg\."):
        _ = file_name("x.py", "data", "image/jpeg")
    with pytest.raises(Validation) as raised:
        _ = file_name("x.py", "data.py", "text/x-python")

    assert raised.value.message == "A file of type text/x-python is saved with the ending .bin."


@pytest.mark.parametrize("save_as", ["a/x.pdf", "../x.pdf", "/x.pdf", "a\\x.pdf"])
def test_the_given_name_is_no_path(save_as: str) -> None:
    with pytest.raises(Validation) as raised:
        _ = file_name(save_as, "2026.pdf", PDF)

    assert raised.value.message == "save_as is a file name, not a path."
    assert raised.value.hint == "Name the directory with save_dir."


@pytest.mark.parametrize("save_as", [".env.pdf", ".pdf", ".mcp-0.pdf"])
def test_a_name_that_starts_with_a_dot_is_refused(save_as: str) -> None:
    with pytest.raises(Validation) as raised:
        _ = file_name(save_as, "2026.pdf", PDF)

    assert raised.value.message == "The file name starts with a dot."
    assert raised.value.hint == "Give another name with save_as."


@pytest.mark.parametrize(
    ("proposed", "expected"),
    [
        (".profile", "profile.txt"),
        ("...a.txt", "a.txt"),
        (". .profile", "profile.txt"),
        (" . .. . x.txt", "x.txt"),
        (". . .", "document.txt"),
        (".txt", "txt.txt"),
        (f"a{chr(0xAD)}b{chr(0x200D)}c{chr(0xFEFF)}d{chr(0x2028)}e.txt", "abcde.txt"),
        (f"{chr(0xFEFF)}.env.txt", "env.txt"),
        (f"{chr(0x200D)}", "document.txt"),
        ("", "document.txt"),
        ("a/b\\c.txt", "a_b_c.txt"),
    ],
)
def test_a_proposed_name_is_cleaned_rather_than_refused(proposed: str, expected: str) -> None:
    """The caller cannot change it; a name of their own is refused instead."""
    assert file_name(None, proposed, TEXT) == expected


@pytest.mark.parametrize("save_as", ["", "..", f"a{chr(0x202E)}gnp.pdf", "a\x00.pdf", "a\n.pdf"])
def test_a_name_with_control_or_direction_characters_is_refused(save_as: str) -> None:
    with pytest.raises(Validation):
        _ = file_name(save_as, "2026.pdf", PDF)


def test_a_name_that_cannot_be_encoded_is_refused_and_a_proposed_one_cleaned() -> None:
    lone = chr(0xD800)
    with pytest.raises(Validation) as raised:
        _ = file_name(f"a{lone}.pdf", "2026.pdf", PDF)

    assert raised.value.message == "The file name has characters that cannot be stored."
    assert raised.value.hint == "Give another name with save_as."
    assert file_name(None, f"a{lone}.pdf", PDF) == "a.pdf"


def test_a_given_name_that_is_too_long_is_refused_and_a_proposed_one_shortened() -> None:
    with pytest.raises(Validation, match="longer than 255 bytes"):
        _ = file_name("x" * 252 + ".pdf", "2026.pdf", PDF)

    assert file_name("x" * 251 + ".pdf", "2026.pdf", PDF) == "x" * 251 + ".pdf"
    assert file_name(None, "x" * 251 + ".pdf", PDF) == "x" * 251 + ".pdf"
    assert file_name(None, "x" * 252 + ".pdf", PDF) == "x" * 251 + ".pdf"
    assert file_name(None, "ü" * 200 + ".pdf", PDF) == "ü" * 125 + ".pdf"


@given(st.text(max_size=300), st.sampled_from(sorted(CONSUMED)))
def test_whatever_name_is_given_or_proposed_stays_one_visible_component(
    text: str, mime_type: str
) -> None:
    for save_as, proposed in ((text, "a.pdf"), (None, text)):
        try:
            name = file_name(save_as, proposed, mime_type)
        except Validation:
            continue
        assert "/" not in name
        assert "\0" not in name
        assert not name.startswith(".")
        assert name.lower().endswith((CONSUMED[mime_type], ".jpeg", ".tiff"))
        assert len(name.encode()) <= MAX_NAME_BYTES


async def test_a_name_the_file_system_does_not_take_is_an_error_without_the_path(
    tree: Tree,
) -> None:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, "x" * 300)

    assert str(tree.sub) not in raised.value.describe()


# --- others interfere


def swap_for_link(tree: Tree) -> None:
    _ = tree.sub.rename(tree.allowed / "moved")
    tree.sub.symlink_to(tree.out)


@pytest.mark.parametrize("stage", ["resolved", "written"])
async def test_a_directory_swapped_for_a_link_is_not_followed(tree: Tree, stage: str) -> None:
    with pytest.raises(PaperlessMcpError):
        _ = await save(tree, at=when(stage, lambda: swap_for_link(tree)))

    assert list(tree.out.iterdir()) == []


async def test_a_directory_moved_out_meanwhile_gets_no_file(tree: Tree) -> None:
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, at=when("written", lambda: tree.sub.rename(tree.out / "sub")))

    assert raised.value.message == "The directory or the file changed meanwhile; nothing was saved."
    assert raised.value.hint == "Look at the directory, then try again."
    assert list((tree.out / "sub").iterdir()) == []


async def test_another_directory_in_its_place_gets_no_file(tree: Tree) -> None:
    def swap() -> None:
        _ = tree.sub.rename(tree.out / "sub")
        tree.sub.mkdir()

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(tree, at=when("written", swap))

    assert tree.names() == list((tree.out / "sub").iterdir()) == []


async def test_a_file_that_appears_meanwhile_is_not_replaced(tree: Tree) -> None:
    for confirm in (False, True):
        with pytest.raises(PaperlessMcpError) as raised:
            _ = await save(
                tree,
                confirm=confirm,
                at=when("commit", lambda: (tree.sub / NAME).write_bytes(b"theirs")),
            )
        assert raised.value.message == (
            "A file of that name appeared meanwhile; nothing was replaced."
        )
        assert raised.value.hint == "Look at the directory, then try again."
        assert (tree.sub / NAME).read_bytes() == b"theirs"
        assert tree.names() == [NAME]
        (tree.sub / NAME).unlink()


async def test_a_link_that_appears_meanwhile_is_not_followed(tree: Tree) -> None:
    with pytest.raises(PaperlessMcpError, match="appeared meanwhile"):
        _ = await save(
            tree, at=when("commit", lambda: (tree.sub / NAME).symlink_to(tree.out / "new"))
        )

    assert list(tree.out.iterdir()) == []


@pytest.mark.parametrize("stage", ["created", "written", "commit"])
async def test_a_file_that_changed_since_the_check_is_not_replaced(tree: Tree, stage: str) -> None:
    target = tree.sub / NAME
    _ = target.write_bytes(b"old")

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(
            tree, confirm=True, at=when(stage, lambda: target.write_bytes(b"newer content"))
        )

    assert (target.read_bytes(), tree.names()) == (b"newer content", [NAME])


async def test_a_file_that_is_gone_at_the_confirmed_commit_is_not_made_anew(tree: Tree) -> None:
    target = tree.sub / NAME
    _ = target.write_bytes(b"old")

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(tree, confirm=True, at=when("commit", target.unlink))

    assert tree.names() == []


async def test_a_file_swapped_for_a_link_before_the_replacement_is_refused(tree: Tree) -> None:
    target, victim = tree.sub / NAME, tree.out / "victim"
    _ = target.write_bytes(b"old")
    _ = victim.write_bytes(b"victim")

    def swap() -> None:
        target.unlink()
        target.symlink_to(victim)

    with pytest.raises(PaperlessMcpError):
        _ = await save(tree, confirm=True, at=when("commit", swap))

    assert victim.read_bytes() == b"victim"


@pytest.mark.parametrize("how", ["gone", "link", "file", "second name"])
async def test_a_temporary_file_that_was_swapped_is_not_committed(tree: Tree, how: str) -> None:
    def swap() -> None:
        [temporary] = [p for p in tree.sub.iterdir() if p.name.startswith(".mcp-")]
        if how == "second name":
            (tree.out / "copy").hardlink_to(temporary)
            return
        temporary.unlink()
        if how == "gone":
            return
        if how == "link":
            temporary.symlink_to(tree.out / "victim")
        else:
            _ = temporary.write_bytes(b"theirs")

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(tree, at=when("written", swap))

    assert NAME not in tree.names()


async def test_a_temporary_file_that_became_a_link_to_itself_is_not_committed(tree: Tree) -> None:
    """The bytes are the right ones, but what would get the name is a link that leads out."""

    def swap() -> None:
        [temporary] = [p for p in tree.sub.iterdir() if p.name.startswith(".mcp-")]
        _ = temporary.rename(tree.out / "moved")
        temporary.symlink_to(tree.out / "moved")

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(tree, at=when("written", swap))

    assert tree.names() == []


async def test_the_link_is_made_to_the_file_itself(tree: Tree) -> None:
    _ = (tree.out / "victim").write_bytes(b"victim")
    (tree.sub / "source").symlink_to(tree.out / "victim")
    fd = os.open(tree.sub, os.O_RDONLY)
    try:
        System().link("source", "target", fd)
    finally:
        os.close(fd)

    assert (tree.sub / "target").is_symlink()
    assert (tree.out / "victim").stat().st_nlink == 1


async def test_the_temporary_file_is_hidden_private_and_random(tree: Tree) -> None:
    seen: list[tuple[str, int]] = []

    def look() -> None:
        seen.extend((p.name, stat.S_IMODE(p.stat().st_mode)) for p in tree.sub.iterdir())

    _ = await save(tree, at=when("created", look))
    _ = await save(tree, "b.pdf", at=when("created", look))

    names = [name for name, _ in seen if name.startswith(".mcp-")]
    assert len(set(names)) == 2
    assert all(len(name) == len(".mcp-") + 16 + len(".part") for name in names)
    assert all(mode == 0o600 for _, mode in seen)


# --- file systems without hard links


def without_links(code: int) -> System:
    class NoLinks(System):
        @override
        def link(self, source: str, target: str, directory: int) -> None:
            raise OSError(code, "no hard links")

    return NoLinks()


# ENOTSUP and EOPNOTSUPP are one number on Linux and two on macOS.
NO_LINKS = sorted({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EMLINK})


@pytest.mark.parametrize("code", NO_LINKS)
async def test_without_hard_links_a_new_file_is_saved_and_never_replaces(
    tree: Tree, code: int
) -> None:
    _ = await save(tree, system=without_links(code))

    assert ((tree.sub / NAME).read_bytes(), tree.names()) == (DATA, [NAME])
    with pytest.raises(ConfirmationRequired):
        _ = await save(tree, system=without_links(code))


async def test_without_hard_links_a_file_that_appears_meanwhile_is_not_replaced(
    tree: Tree,
) -> None:
    with pytest.raises(PaperlessMcpError, match="appeared meanwhile"):
        _ = await save(
            tree,
            system=without_links(errno.ENOTSUP),
            at=when("commit", lambda: (tree.sub / NAME).write_bytes(b"theirs")),
        )

    assert ((tree.sub / NAME).read_bytes(), tree.names()) == (b"theirs", [NAME])


@pytest.mark.parametrize("theirs", [b"theirs", b""])
async def test_without_hard_links_a_swapped_reservation_is_left_alone(
    tree: Tree, theirs: bytes
) -> None:
    target = tree.sub / NAME

    def swap() -> None:
        target.unlink()
        # A filler keeps the freed inode number from being used again at once.
        (tree.sub / "filler").touch()
        _ = target.write_bytes(theirs)

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(tree, system=without_links(errno.ENOTSUP), at=when("reserved", swap))

    assert (target.read_bytes(), tree.names()) == (theirs, [NAME, "filler"])


async def test_without_hard_links_bytes_written_before_the_reservation_is_looked_at_are_kept(
    tree: Tree,
) -> None:
    target = tree.sub / NAME

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(
            tree,
            system=without_links(errno.ENOTSUP),
            at=when("made", lambda: target.write_bytes(b"theirs")),
        )

    assert (target.read_bytes(), tree.names()) == (b"theirs", [NAME])


async def test_without_hard_links_a_written_reservation_is_left_alone(tree: Tree) -> None:
    target = tree.sub / NAME

    with pytest.raises(PaperlessMcpError, match="changed meanwhile"):
        _ = await save(
            tree,
            system=without_links(errno.ENOTSUP),
            at=when("reserved", lambda: target.write_bytes(b"theirs")),
        )

    assert target.read_bytes() == b"theirs"


async def test_without_hard_links_a_file_that_appears_before_the_reservation_is_left_alone(
    tree: Tree,
) -> None:
    class NoLinks(System):
        @override
        def link(self, source: str, target: str, directory: int) -> None:
            _ = (tree.sub / NAME).write_bytes(b"theirs")  # after the link was tried
            raise OSError(errno.ENOTSUP, "no hard links")

    with pytest.raises(PaperlessMcpError, match="appeared meanwhile"):
        _ = await save(tree, system=NoLinks())

    assert ((tree.sub / NAME).read_bytes(), tree.names()) == (b"theirs", [NAME])


async def test_without_hard_links_a_failed_rename_leaves_nothing(tree: Tree) -> None:
    class NoRename(System):
        @override
        def link(self, source: str, target: str, directory: int) -> None:
            raise OSError(errno.ENOTSUP, "no hard links")

        @override
        def replace(self, source: str, target: str, directory: int) -> None:
            raise OSError(errno.EIO, "broken")

    with pytest.raises(PaperlessMcpError):
        _ = await save(tree, system=NoRename())

    assert tree.names() == []


async def test_without_hard_links_a_failed_rename_leaves_what_took_the_name(tree: Tree) -> None:
    class Overtaken(System):
        @override
        def link(self, source: str, target: str, directory: int) -> None:
            raise OSError(errno.ENOTSUP, "no hard links")

        @override
        def replace(self, source: str, target: str, directory: int) -> None:
            _ = (tree.sub / NAME).write_bytes(b"theirs")
            raise OSError(errno.EIO, "broken")

    with pytest.raises(PaperlessMcpError, match="could not be written"):
        _ = await save(tree, system=Overtaken())

    assert ((tree.sub / NAME).read_bytes(), tree.names()) == (b"theirs", [NAME])


async def test_a_link_that_fails_for_another_reason_is_no_reason_to_go_without(tree: Tree) -> None:
    with pytest.raises(PaperlessMcpError, match="could not be written"):
        _ = await save(tree, system=without_links(errno.EIO))

    assert tree.names() == []


# --- failures


FULL = "There is not enough space for the file in that directory; nothing was saved."
FAILURES = {
    errno.ENOSPC: FULL,
    errno.EDQUOT: FULL,
    errno.EFBIG: "The file is too large for the file system of that directory; nothing was saved.",
    errno.EROFS: "The server may not write into that directory; nothing was saved.",
    errno.EACCES: "The server may not write into that directory; nothing was saved.",
    errno.EPERM: "The server may not write into that directory; nothing was saved.",
    errno.EIO: "The file could not be written; nothing was saved.",
}


@pytest.mark.parametrize("step", ["write", "fsync", "replace"])
@pytest.mark.parametrize("code", FAILURES)
async def test_a_failing_disk_is_a_plain_message_and_leaves_nothing(
    tree: Tree, code: int, step: str
) -> None:
    class Failing(System):
        calls: int = 0

        def fail(self, where: str) -> None:
            if where == step:
                raise OSError(code, "secret detail", str(tree.sub))

        @override
        def write(self, fd: int, data: memoryview) -> int:
            self.calls += 1
            if self.calls == 2:
                self.fail("write")
            return super().write(fd, data[:4])

        @override
        def fsync(self, fd: int) -> None:
            self.fail("fsync")
            super().fsync(fd)

        @override
        def replace(self, source: str, target: str, directory: int) -> None:
            self.fail("replace")

    _ = (tree.sub / NAME).write_bytes(b"old")
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, confirm=True, system=Failing())

    assert raised.value.message == FAILURES[code]
    assert raised.value.hint == (
        "Make room or choose another directory; 256 MiB stay free in any case."
        if FAILURES[code] == FULL
        else "Choose another directory, or ask the operator."
    )
    assert (tree.sub / NAME).read_bytes() == b"old"
    assert tree.names() == [NAME]
    assert "secret" not in raised.value.describe()


def test_a_real_limit_on_the_file_size_leaves_nothing(tree: Tree) -> None:
    """EFBIG from the kernel, in a process of its own (the limit cannot be raised again)."""
    code = f"""
import asyncio, resource, signal
from mcp_server_paperless.domain.errors import PaperlessMcpError
from mcp_server_paperless.services.file_sink import Expected, FileSink
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (1000, 1000))
async def data():
    yield b"x" * 5000
async def main():
    async with FileSink([{str(tree.allowed)!r}]).open({str(tree.sub)!r}) as target:
        await target.claim("a.pdf", confirm=False, what="this", source=None)
        try:
            await target.write(data(), Expected(5000, None))
        except PaperlessMcpError as error:
            print(error.message)
asyncio.run(main())
"""
    done = subprocess.run(  # noqa: S603 -- this interpreter and the code above
        [sys.executable, "-c", code], stdin=subprocess.DEVNULL, capture_output=True, check=True
    )

    assert done.stdout.decode().strip() == FAILURES[errno.EFBIG]
    assert tree.names() == []


@pytest.mark.skipif("PAPERLESS_MCP_FULL_DISK" not in os.environ, reason="needs a small tmpfs")
async def test_a_really_full_disk_leaves_nothing() -> None:
    root = Path(tempfile.mkdtemp(dir=os.environ["PAPERLESS_MCP_FULL_DISK"]))

    class Optimist(System):
        @override
        def free(self, directory: int) -> int:
            return 1 << 60

    full = FileSink([str(root)], system=Optimist())
    tree = Tree(root)
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(
            tree,
            data=chunks(*[b"x" * MIB] * 64),
            expected=Expected(64 * MIB, None),
            through=full,
        )

    assert raised.value.message == FULL
    assert tree.names() == []


async def test_too_little_space_is_refused_before_the_file_is_read(tree: Tree) -> None:
    class Small(System):
        space: int = 0

        @override
        def free(self, directory: int) -> int:
            return self.space

    system = Small()
    system.space = len(DATA) + RESERVE
    _ = await save(tree, system=system)
    system.space -= 1
    stages: list[str] = []
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, "b.pdf", system=system, at=stages.append)

    assert raised.value.message == FULL
    assert raised.value.hint == (
        "Make room or choose another directory; 256 MiB stay free in any case."
    )
    assert "created" not in stages


@pytest.mark.parametrize(
    "expected", [Expected(), Expected(zip=True), Expected(1, None)], ids=["none", "zip", "lying"]
)
async def test_the_reserve_is_kept_while_the_file_arrives_whatever_its_size_is_said_to_be(
    tree: Tree, expected: Expected
) -> None:
    """A ZIP archive has no size beforehand, and a size can be wrong."""

    class Shrinking(System):
        space: int = RESERVE + 3 * MIB
        written: int = 0

        @override
        def free(self, directory: int) -> int:
            return self.space - self.written

        @override
        def write(self, fd: int, data: memoryview) -> int:
            self.written += len(data)
            return super().write(fd, data)

    system = Shrinking()
    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, data=chunks(*[b"x" * MIB] * 64), system=system, expected=expected)

    assert raised.value.message == FULL
    assert system.written == 3 * MIB  # never below the reserve
    assert tree.names() == []


async def test_without_any_room_not_even_the_temporary_file_is_made(tree: Tree) -> None:
    class Full(System):
        @override
        def free(self, directory: int) -> int:
            return RESERVE - 1

    stages: list[str] = []
    with pytest.raises(PaperlessMcpError, match="not enough space"):
        _ = await save(tree, "c.zip", system=Full(), at=stages.append, expected=Expected(zip=True))

    assert stages == ["resolved"]


async def test_an_empty_file_needs_only_the_reserve(tree: Tree) -> None:
    class Exact(System):
        @override
        def free(self, directory: int) -> int:
            return RESERVE

    saved = await save(tree, data=chunks(), system=Exact(), expected=Expected(0, None))

    assert saved.size == 0


async def test_a_temporary_file_that_cannot_be_removed_hides_nothing(tree: Tree) -> None:
    class Stuck(System):
        @override
        def unlink(self, name: str, directory: int) -> None:
            raise OSError(errno.EIO, "stuck")

    with pytest.raises(PaperlessMcpError, match="other bytes"):
        _ = await save(tree, system=Stuck(), expected=Expected(1, None))


async def test_the_space_of_the_directory_is_read(tree: Tree) -> None:
    fd = os.open(tree.sub, os.O_RDONLY)
    try:
        found = os.statvfs(tree.sub)
        assert abs(System().free(fd) - found.f_bavail * found.f_frsize) < 1 << 32
    finally:
        os.close(fd)


@pytest.mark.skipif(os.geteuid() == 0, reason="root may write anywhere")
async def test_a_directory_that_may_not_be_written_is_a_plain_message(tree: Tree) -> None:
    tree.sub.chmod(0o500)
    try:
        with pytest.raises(PaperlessMcpError) as raised:
            _ = await save(tree)
    finally:
        tree.sub.chmod(0o700)

    assert raised.value.message == FAILURES[errno.EACCES]
    assert raised.value.hint == "Choose another directory, or ask the operator."


async def test_a_directory_that_cannot_be_synced_is_no_failure(tree: Tree) -> None:
    class NoDirectorySync(System):
        @override
        def fsync(self, fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "not here")
            super().fsync(fd)

    assert (await save(tree, system=NoDirectorySync())).size == len(DATA)


async def test_a_directory_whose_sync_fails_is_said_although_the_file_is_there(tree: Tree) -> None:
    class BrokenSync(System):
        @override
        def fsync(self, fd: int) -> None:
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "broken")
            super().fsync(fd)

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, system=BrokenSync())

    assert raised.value.message == (
        "The file was saved, but the disk reported an error afterwards; it may be lost."
    )
    assert raised.value.hint == "Look at the file before you rely on it."
    assert tree.names() == [NAME]


async def test_a_broken_download_leaves_nothing(tree: Tree) -> None:
    async def broken() -> AsyncGenerator[bytes]:
        yield b"half"
        message = "gone"
        raise PaperlessMcpError(message)

    with pytest.raises(PaperlessMcpError, match="gone"):
        _ = await save(tree, data=broken())

    assert tree.names() == []


@pytest.mark.parametrize(
    "expected",
    [
        Expected(len(DATA) - 1, None),
        Expected(len(DATA) + 1, None),
        Expected(len(DATA), hashlib.sha256(b"other").hexdigest()),
    ],
)
async def test_other_bytes_than_paperless_describes_are_not_saved(
    tree: Tree, expected: Expected
) -> None:
    with pytest.raises(PaperlessMcpError, match="other bytes than it says"):
        _ = await save(tree, expected=expected)

    assert tree.names() == []


@pytest.mark.parametrize("checksum", [None, "0" * 32, hashlib.sha256(DATA).hexdigest()])
async def test_a_checksum_is_compared_if_it_is_a_sha_256(tree: Tree, checksum: str | None) -> None:
    saved = await save(tree, expected=Expected(len(DATA), checksum))

    assert saved.sha256 == hashlib.sha256(DATA).hexdigest()


def zipped() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.txt", b"text")
    return buffer.getvalue()


ZIPPED = zipped()


async def test_an_archive_needs_its_end(tree: Tree) -> None:
    saved = await save(tree, "d.zip", data=chunks(ZIPPED), expected=Expected(zip=True))
    assert saved.size == len(ZIPPED)

    with pytest.raises(PaperlessMcpError, match="complete ZIP archive"):
        _ = await save(tree, "e.zip", data=chunks(ZIPPED[:-30]), expected=Expected(zip=True))

    assert tree.names() == ["d.zip"]


async def test_a_file_over_the_limit_is_given_up_and_leaves_nothing(tree: Tree) -> None:
    small = FileSink([str(tree.allowed)], max_bytes=len(DATA))
    exact = Expected(len(DATA), None)
    assert (await save(tree, expected=exact, through=small)).size == len(DATA)
    assert (await save(tree, confirm=True, expected=Expected(), through=small)).size == len(DATA)
    for data, expected in (
        (chunks(DATA, b"z"), Expected()),
        (chunks(), Expected(len(DATA) + 1)),
    ):
        with pytest.raises(PaperlessMcpError, match="larger than 2 GiB"):
            _ = await save(tree, "b.pdf", data=data, expected=expected, through=small)

    assert tree.names() == [NAME]
    assert FileSink(["/x"]).max_bytes == transfer.MAX_FILE_BYTES == 2 * 1024**3


async def test_a_file_that_takes_too_long_is_given_up_and_leaves_nothing(tree: Tree) -> None:
    async def slow() -> AsyncGenerator[bytes]:
        yield b"half"
        _ = await asyncio.Event().wait()

    hasty = FileSink([str(tree.allowed)], seconds=0.05)
    with pytest.raises(PaperlessMcpError, match="did not send the file within"):
        _ = await save(tree, data=slow(), expected=Expected(), through=hasty)

    assert tree.names() == []
    assert FileSink(["/x"]).seconds == transfer.MAX_SECONDS == 1800


class Stalling(System):
    """A disk whose writes wait for `go`."""

    def __init__(self) -> None:
        self.started: threading.Event = threading.Event()
        self.go: threading.Event = threading.Event()
        self.ended: bool = False

    @override
    def write(self, fd: int, data: memoryview) -> int:
        self.started.set()
        _ = self.go.wait(5)
        written = super().write(fd, data)
        self.ended = True
        return written


@pytest.mark.parametrize("how", ["asyncio", "anyio"])
async def test_a_cancelled_save_waits_for_its_write_and_leaves_nothing(
    tree: Tree, how: str
) -> None:
    disk = Stalling()
    if how == "asyncio":
        task = asyncio.create_task(save(tree, system=disk))
        _ = await asyncio.to_thread(disk.started.wait, 5)
        _ = task.cancel()
        await asyncio.sleep(0.05)
        _ = task.cancel()  # also a second time
        await asyncio.sleep(0.05)
        assert not task.done()
        disk.go.set()
        with pytest.raises(asyncio.CancelledError):
            _ = await task
    else:
        scope = anyio.CancelScope()
        async with anyio.create_task_group() as group:
            _ = group.start_soon(save, tree)  # an undisturbed one beside it
            with scope:

                async def stop() -> None:
                    _ = await asyncio.to_thread(disk.started.wait, 5)
                    scope.cancel()
                    await asyncio.sleep(0.05)
                    disk.go.set()

                _ = group.start_soon(stop)
                _ = await save(tree, "b.pdf", system=disk)
        assert scope.cancelled_caught

    assert disk.ended
    assert tree.names() == ([] if how == "asyncio" else [NAME])


@pytest.mark.parametrize("commits", [True, False])
async def test_a_file_whose_closing_fails_leaves_no_descriptor_and_no_temporary_file(
    tree: Tree, *, commits: bool
) -> None:
    """NFS tells of a full disk as late as that; what is committed was synced before."""
    closed: list[int] = []

    class Late(System):
        @override
        def close(self, fd: int) -> None:
            super().close(fd)
            closed.append(fd)
            raise OSError(errno.ENOSPC, "late")

    expected = Expected(len(DATA) if commits else 1, None)
    if commits:
        assert (await save(tree, system=Late(), expected=expected)).size == len(DATA)
    else:
        with pytest.raises(PaperlessMcpError, match="other bytes"):
            _ = await save(tree, system=Late(), expected=expected)

    assert len(closed) == 2  # the file and the directory
    assert tree.names() == ([NAME] if commits else [])


async def test_without_hard_links_a_reservation_whose_closing_fails_is_still_used(
    tree: Tree,
) -> None:
    class Late(System):
        @override
        def link(self, source: str, target: str, directory: int) -> None:
            raise OSError(errno.ENOTSUP, "no hard links")

        @override
        def close(self, fd: int) -> None:
            super().close(fd)
            raise OSError(errno.EDQUOT, "late")

    assert (await save(tree, system=Late())).size == len(DATA)


async def test_a_second_name_that_cannot_be_removed_is_said_after_a_retry_and_the_sync(
    tree: Tree,
) -> None:
    done: list[str] = []

    class Stuck(System):
        @override
        def unlink(self, name: str, directory: int) -> None:
            done.append("unlink")
            raise OSError(errno.EIO, "stuck")

        @override
        def fsync(self, fd: int) -> None:
            done.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            super().fsync(fd)

    with pytest.raises(PaperlessMcpError) as raised:
        _ = await save(tree, system=Stuck())

    [temporary, name] = tree.names()
    assert (name, (tree.sub / NAME).read_bytes()) == (NAME, DATA)
    assert done == ["file", "unlink", "unlink", "directory"]  # and it is not tried once more
    assert raised.value.message == (
        f"The file was saved, but its temporary name {temporary} could not be removed."
    )
    assert raised.value.hint == (
        "Remove that name in the directory; until then the file cannot be replaced."
    )


@pytest.mark.parametrize("failures", [1, 0])
async def test_a_second_name_that_is_removed_at_last_is_no_failure(
    tree: Tree, failures: int
) -> None:
    """By the retry, or by another process (then the name is not found)."""

    class Late(System):
        calls: int = 0

        @override
        def unlink(self, name: str, directory: int) -> None:
            self.calls += 1
            if self.calls <= failures:
                raise OSError(errno.EIO, "busy")
            super().unlink(name, directory)
            if not failures:
                raise FileNotFoundError(errno.ENOENT, "gone")

    saved = await save(tree, system=Late())

    assert (saved.filename, tree.names()) == (NAME, [NAME])
    assert (tree.sub / NAME).stat().st_nlink == 1


async def test_no_descriptor_stays_open(tree: Tree) -> None:
    def open_ones() -> int:
        return len(os.listdir("/dev/fd"))  # noqa: PTH208 -- not a directory to walk

    _ = await save(tree, "warm.pdf")  # threads and caches
    before = open_ones()
    _ = await save(tree)
    with pytest.raises(ConfirmationRequired):
        _ = await save(tree)
    with pytest.raises(NotFound):
        _ = await save(tree, directory=tree.out)
    with pytest.raises(PaperlessMcpError):
        _ = await save(tree, "b.pdf", at=when("written", lambda: swap_for_link(tree)))

    assert open_ones() == before


def test_a_killed_process_leaves_only_its_hidden_file(tree: Tree) -> None:
    code = f"""
import asyncio, os, signal
from mcp_server_paperless.services.file_sink import Expected, FileSink
async def data():
    yield b"half"
    os.kill(os.getpid(), signal.SIGKILL)
async def main():
    async with FileSink([{str(tree.allowed)!r}]).open({str(tree.sub)!r}) as target:
        await target.claim("a.pdf", confirm=False, what="this", source=None)
        await target.write(data(), Expected())
asyncio.run(main())
"""
    done = subprocess.run(  # noqa: S603 -- this interpreter and the code above
        [sys.executable, "-c", code], stdin=subprocess.DEVNULL, check=False
    )

    assert done.returncode == -9
    [left] = tree.names()
    assert left.startswith(".mcp-")


async def test_every_change_is_made_through_the_held_directory(tree: Tree) -> None:
    """Never by a path: a path could lead elsewhere by the time it is used."""
    seen: list[tuple[str, ...]] = []

    class Watching(System):
        @override
        def link(self, source: str, target: str, directory: int) -> None:
            seen.append((source, target))
            super().link(source, target, directory)

        @override
        def replace(self, source: str, target: str, directory: int) -> None:
            seen.append((source, target))
            super().replace(source, target, directory)

        @override
        def unlink(self, name: str, directory: int) -> None:
            seen.append((name,))
            super().unlink(name, directory)

    _ = await save(tree, system=Watching())
    _ = await save(tree, confirm=True, system=Watching())

    assert len(seen) == 3  # link and unlink, then replace
    assert all("/" not in name for names in seen for name in names)
