"""The README and the pages in docs/ say what the code does (PLMCP-51).

Numbers and names in the documentation are checked against the catalogue and the settings, and
every relative link must lead somewhere, so the landing page cannot go stale unnoticed. The tool
reference itself (docs/tools.md) is generated; tests/tools/test_tools_doc.py keeps it current.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from paperless_mcp.settings import HttpSettings, OidcSettings, PaperlessSettings, ServerSettings
from paperless_mcp.tools._shared import TOOL_TOOLSETS, TOOLSETS

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = ROOT / "docs"
# The pages written for users; the reading guides for contributors are checked for links too.
PAGES = (README, *sorted(DOCS.glob("*.md")))
NAMES = [page.name for page in PAGES]
# The defaults of the server settings, without reading the environment.
DEFAULT_TOOLSETS = ServerSettings.model_construct().toolsets

SIZE = re.compile(r"\b(\d+) tools in (\d+) toolsets\b")
DEFAULT_SIZE = re.compile(r"\b(\d+) tools by default\b")
# A row of the README's toolset overview: | `core` | 16 | ...
TOOLSET_ROW = re.compile(r"^\| `([a-z]+)` \| (\d+) \|", re.MULTILINE)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


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
        if re.match(r"[a-z][a-z0-9+.-]*:", link):
            continue  # an absolute URL (https:, mailto:)
        path, _, fragment = link.partition("#")
        target = (page.parent / path).resolve() if path else page
        missing_heading = fragment and target.suffix == ".md" and fragment not in anchors(target)
        if not target.exists() or missing_heading:
            broken.append(link)

    assert broken == []


def test_the_readme_links_every_page_written_for_users() -> None:
    text = prose(README)
    guides = {"fastmcp-reading-guide.md", "paperless-reading-guide.md"}
    pages = sorted(page.name for page in DOCS.glob("*.md") if page.name not in guides)

    assert pages, "docs/ holds no page for users"
    assert [name for name in pages if f"(docs/{name}" not in text] == []
