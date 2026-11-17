"""The committed mutation-equivalents.toml must parse: the weekly mutation gate reads it."""

from pathlib import Path

import scheduled_jobs

ROOT = Path(__file__).parents[2]


def test_the_committed_equivalents_file_is_valid() -> None:
    found = scheduled_jobs.parse_equivalents(
        (ROOT / "mutation-equivalents.toml").read_text(encoding="utf-8")
    )

    assert found
