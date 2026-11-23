"""The README and the pages in docs/ say what the code does (PLMCP-51).

Numbers and names in the documentation are checked against the catalogue and the settings, and
every relative link must lead somewhere, so the landing page cannot go stale unnoticed. The tool
reference itself (docs/tools.md) is generated; tests/tools/test_tools_doc.py keeps it current.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mcp_server_paperless.settings import (
    HttpSettings,
    OidcSettings,
    PaperlessSettings,
    ServerSettings,
)
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS, TOOLSETS

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = ROOT / "docs"
PRIVACY = ROOT / "PRIVACY.md"
# The pages written for users; the reading guides for contributors are checked for links too.
PAGES = (README, PRIVACY, ROOT / "plugin" / "README.md", *sorted(DOCS.glob("*.md")))
NAMES = [page.relative_to(ROOT).as_posix() for page in PAGES]
# Every current doc and the Dockerfile; decisions and tasks keep the names of their time.
NAMED = (
    *sorted(ROOT.glob("*.md")),
    ROOT / "plugin" / "README.md",
    *sorted(DOCS.glob("*.md")),
    *sorted((ROOT / "backlog" / "docs").rglob("*.md")),
    ROOT / "Dockerfile",
)
# The defaults of the server settings, without reading the environment.
DEFAULT_TOOLSETS = ServerSettings.model_construct().toolsets

SIZE = re.compile(r"\b(\d+) tools in (\d+) toolsets\b")
DEFAULT_SIZE = re.compile(r"\b(\d+) tools by default\b")
# A row of the README's toolset overview: | `core` | 16 | ...
TOOLSET_ROW = re.compile(r"^\| `([a-z]+)` \| (\d+) \|", re.MULTILINE)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
# PyPI renders the README on its own page, where a relative link leads nowhere, so the README
# links files of the repository by their URL on main; they are checked like relative links.
BLOB = "https://github.com/gardenbaum/mcp-server-paperless/blob/main/"
# The repository and image names before the rename to mcp-server-paperless (PLMCP-59), whatever
# the host or scheme (web, SSH, API, raw, badge, image), also URL-encoded inside a badge's query.
OLD_NAMES = re.compile(
    r"gardenbaum(?:/|%2F)(?:packages/container/(?:package/)?)?paperless-mcp(?!\w)", re.IGNORECASE
)


def prose(page: Path) -> str:
    """The page without its code blocks, where a `#` starts a comment and `](` is no link."""
    return FENCE.sub("", page.read_text(encoding="utf-8"))


def anchor(heading: str) -> str:
    """The anchor GitHub gives a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", r"\1", heading).lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def anchors(page: Path) -> set[str]:
    return {anchor(found[1]) for found in HEADING.finditer(prose(page))}


def test_the_anchor_of_a_heading_is_the_one_github_makes() -> None:
    assert anchor("Run over HTTP") == "run-over-http"
    assert anchor("`core` (on by default)") == "core-on-by-default"
    assert anchor("Optional: sign in (claude.ai)") == "optional-sign-in-claudeai"


@pytest.mark.parametrize("page", PAGES, ids=NAMES)
def test_the_stated_size_of_the_catalogue_is_the_real_one(page: Path) -> None:
    for found in SIZE.finditer(prose(page)):
        assert (int(found[1]), int(found[2])) == (len(TOOL_TOOLSETS), len(TOOLSETS))


def test_the_readme_states_the_size_of_the_catalogue_and_of_the_default_profile() -> None:
    text = prose(README)
    default = [tool for tool, toolset in TOOL_TOOLSETS.items() if toolset in DEFAULT_TOOLSETS]

    assert SIZE.search(text), "the README no longer says how many tools there are"
    stated = {int(found[1]) for found in DEFAULT_SIZE.finditer(text)}
    assert stated, "the README no longer says how many tools are on by default"
    assert stated == {len(default)}


def test_the_readme_overview_has_every_toolset_with_its_number_of_tools() -> None:
    rows = {found[1]: int(found[2]) for found in TOOLSET_ROW.finditer(prose(README))}
    sizes = dict.fromkeys(TOOLSETS, 0)
    for toolset in TOOL_TOOLSETS.values():
        sizes[toolset] += 1

    assert rows == sizes


def test_the_toolsets_page_has_a_section_for_every_toolset() -> None:
    sections = anchors(DOCS / "configuration.md")

    assert [name for name in sorted(TOOLSETS) if name not in sections] == []


@pytest.mark.parametrize(
    "settings", [PaperlessSettings, ServerSettings, HttpSettings, OidcSettings]
)
def test_every_setting_is_documented(settings: type[BaseSettings]) -> None:
    documented = "".join(prose(page) for page in DOCS.glob("*.md"))
    prefix = settings.model_config.get("env_prefix") or ""
    variables = [f"{prefix}{field}".upper() for field in settings.model_fields]

    assert [name for name in variables if f"`{name}`" not in documented] == []


@pytest.mark.parametrize("page", PAGES, ids=NAMES)
def test_every_relative_link_leads_to_a_file_and_a_heading(page: Path) -> None:
    broken: list[str] = []
    for found in LINK.finditer(prose(page)):
        link = found[1]
        in_repository = link.startswith(BLOB)
        if not in_repository and re.match(r"[a-z][a-z0-9+.-]*:", link):
            continue  # an absolute URL (https:, mailto:)
        path, _, fragment = link.removeprefix(BLOB).partition("#")
        base = ROOT if in_repository else page.parent
        target = (base / path).resolve() if path else page
        missing_heading = fragment and target.suffix == ".md" and fragment not in anchors(target)
        if not target.exists() or missing_heading:
            broken.append(link)

    assert broken == []


def test_the_readme_links_every_page_written_for_users() -> None:
    text = prose(README)
    guides = {"fastmcp-reading-guide.md", "paperless-reading-guide.md"}
    pages = sorted(page.name for page in DOCS.glob("*.md") if page.name not in guides)

    assert pages, "docs/ holds no page for users"
    assert [name for name in pages if f"({BLOB}docs/{name}" not in text] == []


def test_the_readme_has_a_privacy_section_that_links_the_policy() -> None:
    # Anthropic asks local connectors for a "Privacy Policy" section in the README (PLMCP-71).
    _, heading, rest = prose(README).partition("\n## Privacy policy\n")

    assert heading
    assert f"({BLOB}PRIVACY.md)" in rest.partition("\n## ")[0]


@pytest.mark.parametrize(
    "name", ["configuration.md", "http.md", "claude-ai-connector.md", "container.md"]
)
def test_the_pages_on_running_the_server_link_the_privacy_policy(name: str) -> None:
    assert "../PRIVACY.md" in {
        found[1].partition("#")[0] for found in LINK.finditer(prose(DOCS / name))
    }


def test_the_privacy_policy_covers_what_the_directory_asks_for() -> None:
    # Collection, use and storage, third-party sharing, retention and contact.
    assert {
        "what-the-server-reads-and-writes",
        "what-the-server-stores",
        "what-the-server-logs",
        "where-your-data-goes",
        "how-long-data-is-kept",
        "contact",
    } <= anchors(PRIVACY)


@pytest.mark.parametrize(
    "name",
    [
        "https://github.com/gardenbaum/paperless-mcp",
        "https://github.com/gardenbaum/paperless-mcp/actions",
        "ghcr.io/gardenbaum/paperless-mcp:0.2.0",
        "https://img.shields.io/github/v/release/gardenbaum/paperless-mcp?logo=github",
        "https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Fgardenbaum%2Fpaperless-mcp",
        "https://raw.githubusercontent.com/gardenbaum/paperless-mcp/main/README.md",
        "git@github.com:gardenbaum/paperless-mcp.git",
        "ssh://git@github.com/gardenbaum/paperless-mcp.git",
        "https://api.github.com/repos/gardenbaum/paperless-mcp/pulls",
        "gh api -X PUT repos/gardenbaum/paperless-mcp/pulls/{n}/merge",
        "https://github.com/users/gardenbaum/packages/container/package/paperless-mcp",
        "https://api.github.com/users/gardenbaum/packages/container/paperless-mcp",
        "gh pr list -R Gardenbaum/Paperless-MCP",
    ],
)
def test_the_guard_finds_the_old_name_in_every_form(name: str) -> None:
    assert OLD_NAMES.search(name)


@pytest.mark.parametrize(
    "name",
    [
        "https://github.com/gardenbaum/mcp-server-paperless",
        "git@github.com:gardenbaum/mcp-server-paperless.git",
        "ghcr.io/gardenbaum/mcp-server-paperless:0.3.0",
        "https://pypi.org/project/paperless-mcp/",  # an unrelated project the docs warn about
        "https://github.com/baruchiro/paperless-mcp",
        "`paperless-mcp` and `paperless-mcp-http`",  # the old command names still work
        "backlog/docs/doc-1 - paperless-mcp-design.md",
        "/var/lib/paperless-mcp",
    ],
)
def test_the_guard_leaves_other_projects_and_the_old_command_names_alone(name: str) -> None:
    assert not OLD_NAMES.search(name)


def test_the_guard_covers_the_design_the_workflow_guide_and_the_dockerfile() -> None:
    names = [path.name for path in NAMED]

    assert {"README.md", "PRIVACY.md", "CLAUDE.md", "AGENTS.md", "http.md", "Dockerfile"} <= set(
        names
    )
    assert [name for name in names if name.startswith(("doc-1 ", "doc-2 "))] == [
        "doc-1 - paperless-mcp-design.md",
        "doc-2 - AI-contributor-workflow.md",
    ]


@pytest.mark.decision("decision-37")
@pytest.mark.parametrize("page", NAMED, ids=[page.relative_to(ROOT).as_posix() for page in NAMED])
def test_no_page_uses_the_old_repository_name(page: Path) -> None:
    lines = page.read_text(encoding="utf-8").splitlines()
    stale = [line for line in lines if OLD_NAMES.search(line)]

    assert stale == []


def test_the_readme_links_files_by_url_so_they_work_on_the_pypi_page() -> None:
    links = [found[1] for found in LINK.finditer(prose(README))]

    assert [link for link in links if not re.match(r"[a-z][a-z0-9+.-]*:|#", link)] == []
