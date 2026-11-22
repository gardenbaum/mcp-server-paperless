"""Fixtures shared by every test level."""

import logging
from typing import TYPE_CHECKING, cast

import e2e_ci
import pytest
from e2e.helpers import BASE_URL, Stack, client, seed, token_for, wait_ready
from fakes.accounts import BLIND, READER

from mcp_server_paperless.observability import QUIET_LOGGERS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.config import Config
    from _pytest.config.argparsing import Parser


def pytest_addoption(parser: Parser) -> None:
    """`--e2e-shard K/N` runs one shard of the live suite; `--write-docs` writes docs/tools.md."""
    parser.addoption(
        "--e2e-shard",
        default=None,
        type=e2e_ci.parse_shard,
        metavar="K/N",
        help="keep only the test modules of shard K out of N (balanced by measured duration)",
    )
    parser.addoption(
        "--write-docs",
        action="store_true",
        default=False,
        help="write docs/tools.md from the tool surface, not only compare (just docs-tools)",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: Config, items: list[pytest.Item]) -> None:
    """Deselect the modules of the other shards, after `-m` has chosen the tests of the run.

    A whole module goes to one shard (its fixtures and the stack's state stay together), and the
    assignment depends only on the module paths, so the shards partition the suite exactly.
    """
    shard = cast("tuple[int, int] | None", config.getoption("--e2e-shard"))
    if shard is None:
        return
    index, count = shard
    module_of = {item: item.path.relative_to(config.rootpath).as_posix() for item in items}
    plan = e2e_ci.assign_shards(sorted(set(module_of.values())), count=count)
    kept = [item for item in items if plan[module_of[item]] == index]
    dropped = [item for item in items if plan[module_of[item]] != index]
    config.hook.pytest_deselected(items=dropped)
    items[:] = kept


@pytest.fixture
def anyio_backend() -> str:
    """Async tests (`pytest.mark.anyio`) run on asyncio, the event loop FastMCP uses."""
    return "asyncio"


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """Undo what `configure_logging` changes in the process-wide logging setup."""
    root = logging.getLogger()
    quiet = [logging.getLogger(name) for name in QUIET_LOGGERS]
    handlers, level, levels = root.handlers[:], root.level, [q.level for q in quiet]
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for logger, saved in zip(quiet, levels, strict=True):
        logger.setLevel(saved)


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    """The ready, seeded Paperless 3.4.2 stack (E2E tests and the live contract backend)."""
    admin_token = wait_ready()
    with client(admin_token) as admin:
        seeded = seed(admin)
        reader_token = token_for(*READER)
        blind_token = token_for(*BLIND)
        with client(reader_token) as reader:
            yield Stack(
                base_url=BASE_URL,
                admin=admin,
                reader=reader,
                seeded=seeded,
                admin_token=admin_token,
                reader_token=reader_token,
                blind_token=blind_token,
            )
