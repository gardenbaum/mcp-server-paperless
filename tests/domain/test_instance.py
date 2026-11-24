"""The comparison with the minimum release (decision-57)."""

import pytest

from mcp_server_paperless.domain.instance import MINIMUM_RELEASE, update_notice

pytestmark = pytest.mark.decision("decision-57")
MAJOR, MINOR, PATCH = MINIMUM_RELEASE


@pytest.mark.parametrize(
    "version",
    [
        f"{MAJOR}.{MINOR}.{PATCH - 1}",
        f"{MAJOR}.{MINOR - 1}.99",
        f"{MAJOR - 1}.99.99",
        "3.4.2",
        "0.0.0",
    ],
)
def test_an_older_release_is_named_with_the_minimum_and_what_to_do(version: str) -> None:
    assert update_notice(version) == (
        f"{version} is older than the minimum release {MAJOR}.{MINOR}.{PATCH}: Paperless must be "
        "updated; until then some tools fail or answer differently"
    )


@pytest.mark.parametrize(
    "version",
    [
        f"{MAJOR}.{MINOR}.{PATCH}",
        f"{MAJOR}.{MINOR}.{PATCH + 1}",
        f"{MAJOR}.{MINOR}.10",
        f"{MAJOR}.10.0",
        "10.0.0",
    ],
)
def test_the_minimum_and_newer_releases_give_no_notice(version: str) -> None:
    assert update_notice(version) is None


@pytest.mark.parametrize(
    "version",
    [
        *("", "unknown", "dev", "3", "3.4", "3.4.x", "v3.4.2", "3.4.2-beta", "3.4.2.1"),
        *("3.4.2\n", " 3.4.2", "-1.0.0", "\u0663.\u0664.\u0662"),
        pytest.param("3.4." + "9" * 5000, id="too many digits"),
    ],
)
def test_a_version_that_is_no_plain_release_gives_no_notice(version: str) -> None:
    assert update_notice(version) is None
