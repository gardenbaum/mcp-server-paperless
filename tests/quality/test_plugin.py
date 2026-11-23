"""The plugin for Claude and Cursor matches the package and its settings (PLMCP-68).

People who install the plugin get only the folder ``plugin/``; the marketplace files at the
repository root make it installable from GitHub. Nothing here installs or submits anything.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import yaml

from mcp_server_paperless.settings import PaperlessSettings, ServerSettings
from mcp_server_paperless.tools._shared import TOOL_TOOLSETS
from mcp_server_paperless.tools.prompts import (
    CONFIRMATION,
    SPECS,
    annual_review,
    cleanup_taxonomy,
    find_document,
    mentioned_tools,
    triage_inbox,
)

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings

ROOT = Path(__file__).parents[2]
PLUGIN = ROOT / "plugin"
CLAUDE = ".claude-plugin"
CURSOR = ".cursor-plugin"
REQUIRED = {"PAPERLESS_URL", "PAPERLESS_TOKEN"}
# The directory refuses a launcher that is not pinned to an exact version, so no @latest here.
PIN = re.compile(r"mcp-server-paperless==(\d+\.\d+\.\d+)")
# A variable of the README's configuration table: | `PAPERLESS_URL` | ...
README_VARIABLE = re.compile(r"^\| `(PAPERLESS_\w+)` \|", re.MULTILINE)
# A row of the plugin README's option table, its heading first: | Toolsets | ...
OPTION_ROW = re.compile(r"^\| ([^|]+) \| ([^|]+) \|$", re.MULTILINE)
FRONTMATTER = re.compile(r"---\n(.*?)\n---\n(.*)", re.DOTALL)
# What the prompt argument stands for in the skill, where the user names it in the conversation.
PROMPTS = {
    "triage_inbox": triage_inbox(),
    "find_document": find_document(description="what the user described"),
    "annual_review": annual_review(year=1999, purpose="PURPOSE").replace("1999", "YEAR"),
    "cleanup_taxonomy": cleanup_taxonomy(),
}
SKILLS = {name: f"paperless-{name.replace('_', '-')}" for name in SPECS}
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def mapping(value: object) -> dict[str, object]:
    """The value as a string-keyed mapping; fails the test when it is not one."""
    assert isinstance(value, dict)
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def load(path: Path) -> dict[str, object]:
    return mapping(cast("object", json.loads(path.read_text(encoding="utf-8"))))


def manifest(client: str) -> dict[str, object]:
    return load(PLUGIN / client / "plugin.json")


def project() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as file:
        return mapping(cast("object", tomllib.load(file)["project"]))


def server(client: str) -> dict[str, object]:
    servers = mapping(manifest(client)["mcpServers"])
    assert set(servers) == {"paperless"}
    return mapping(servers["paperless"])


def options() -> dict[str, dict[str, object]]:
    found = mapping(manifest(CLAUDE)["userConfig"])
    return {key: mapping(item) for key, item in found.items()}


def option_rows() -> list[tuple[str, str]]:
    """Title and meaning of each row of the plugin README's option table."""
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")
    table = text[text.index("| Option |") :]  # the table of the skills comes before
    rows = [(found[1], found[2]) for found in OPTION_ROW.finditer(table)]
    assert rows[0] == ("Option", "Meaning")
    return rows[1:]


def settings_defaults() -> dict[str, object]:
    """Each variable the stdio server reads with its default; required ones are left out."""
    found: dict[str, object] = {}
    classes: tuple[type[BaseSettings], ...] = (PaperlessSettings, ServerSettings)
    for settings in classes:
        prefix = settings.model_config.get("env_prefix") or ""
        for name, field in settings.model_fields.items():
            if not field.is_required():
                found[f"{prefix}{name}".upper()] = cast("object", field.default)
    return found


@pytest.mark.parametrize("client", [CLAUDE, CURSOR])
def test_the_manifest_describes_the_package(client: str) -> None:
    plugin = manifest(client)

    assert plugin["name"] == project()["name"] == "mcp-server-paperless"
    assert plugin["license"] == project()["license"]
    assert mapping(plugin["author"])["name"] == "gardenbaum"
    assert plugin["repository"] == "https://github.com/gardenbaum/mcp-server-paperless"
    assert plugin["description"]


@pytest.mark.parametrize("client", [CLAUDE, CURSOR])
def test_the_server_is_the_released_package_pinned_to_the_plugin_version(client: str) -> None:
    entry = server(client)
    args = cast("list[str]", entry["args"])

    assert set(entry) == {"command", "args", "env"}
    assert entry["command"] == "uvx"
    assert len(args) == 1
    pin = PIN.fullmatch(args[0])
    assert pin
    assert pin[1] == manifest(client)["version"]


def test_both_manifests_have_the_same_version_and_description() -> None:
    for key in ("version", "description", "keywords", "homepage"):
        assert manifest(CLAUDE)[key] == manifest(CURSOR)[key], key


def test_claude_asks_for_each_variable_and_passes_it_on() -> None:
    """One option per variable, so no value has to be written into a file."""
    environment = mapping(server(CLAUDE)["env"])

    assert environment == {key.upper(): f"${{user_config.{key}}}" for key in options()}


def test_claude_asks_for_the_settings_of_the_readme_table() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert {key.upper() for key in options()} == set(README_VARIABLE.findall(readme))


def test_claude_requires_the_url_and_the_token_and_masks_only_the_token() -> None:
    asked = options()

    assert {key.upper() for key, item in asked.items() if item.get("required")} == REQUIRED
    assert {key for key, item in asked.items() if item.get("sensitive")} == {"paperless_token"}
    assert all("default" not in asked[name.lower()] for name in REQUIRED)


def test_an_option_that_was_never_set_passes_the_default_of_the_server() -> None:
    """A value that was set and then cleared is passed on empty; see the next test."""
    defaults = settings_defaults()
    for key, item in options().items():
        if key.upper() in REQUIRED:
            continue
        default = defaults[key.upper()]
        if isinstance(default, tuple):
            default = ",".join(cast("tuple[str, ...]", default))
        assert item["default"] == ("" if default is None else default), key
        assert item["type"] == ("boolean" if isinstance(default, bool) else "string"), key


def test_an_option_the_server_refuses_empty_says_how_to_restore_the_default() -> None:
    """The server takes an empty value only where its default is empty."""
    refused = [item for item in options().values() if item["type"] == "string"]
    refused = [item for item in refused if item.get("default")]

    assert [item["title"] for item in refused] == ["Toolsets"]
    for item in refused:
        hint = f"must not be empty: type {item['default']} for the default"
        assert hint in str(item["description"])
    row = "must not be empty: type `core,ai` to restore the default"
    assert row in dict(option_rows())["Toolsets"]


def test_the_option_table_names_the_options_and_their_defaults() -> None:
    rows = option_rows()
    expected: list[tuple[str, str]] = []
    for item in options().values():
        default = item.get("default")
        stated = "(default: off)" if default is False else f"(default: `{default}`"
        if item.get("required"):
            stated = "(required)"
        elif default == "":
            stated = "(default: none)"
        expected.append((str(item["title"]), stated))

    assert [title for title, _ in rows] == [title for title, _ in expected]
    for (title, meaning), (_, stated) in zip(rows, expected, strict=True):
        assert stated in meaning, title


def test_cursor_declares_the_required_variables_and_passes_them_on() -> None:
    variables = mapping(manifest(CURSOR)["variables"])
    declared = mapping(variables["properties"])

    assert variables["type"] == "object"
    assert set(declared) == set(cast("list[str]", variables["required"])) == REQUIRED
    assert mapping(server(CURSOR)["env"]) == {name: f"${{{name}}}" for name in declared}
    assert all("default" not in mapping(item) for item in declared.values())


@pytest.mark.parametrize("client", [CLAUDE, CURSOR])
def test_the_marketplace_lists_the_plugin_folder(client: str) -> None:
    marketplace = load(ROOT / client / "marketplace.json")
    entries = [mapping(item) for item in cast("list[object]", marketplace["plugins"])]

    assert marketplace["name"] == "gardenbaum"
    assert marketplace["owner"] == {"name": "gardenbaum"}
    # The version stays in plugin.json alone: one place to raise per client.
    assert entries == [
        {
            "name": manifest(client)["name"],
            "source": {CLAUDE: "./plugin", CURSOR: "plugin"}[client],
            "description": manifest(client)["description"],
        }
    ]


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk, which a PNG must start with."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    assert data[-8:-4] == b"IEND"  # complete: the directory holds a truncated image
    return int.from_bytes(data[16:20]), int.from_bytes(data[20:24])


def test_both_clients_show_the_icon_in_the_size_the_directory_takes() -> None:
    icon = PLUGIN / "assets" / "icon.png"
    width, height = png_size(icon)

    assert manifest(CLAUDE)["icon"] == "./assets/icon.png"
    assert manifest(CURSOR)["logo"] == "assets/icon.png"
    assert width == height
    assert 512 <= width <= 2048
    assert icon.stat().st_size < 2 * 1024 * 1024


def test_the_repository_keeps_the_icon_for_other_listings() -> None:
    """Cline takes 400 x 400; Docker and Glama link one by its raw URL."""
    svg = (ROOT / "assets" / "icon.svg").read_text(encoding="utf-8")

    assert png_size(ROOT / "assets" / "icon-400.png") == (400, 400)
    assert 'viewBox="0 0 550 550"' in svg


def test_the_folder_is_complete_by_itself() -> None:
    """Only regular files, the directory's README length, and the project's licence."""
    entries = {path.relative_to(PLUGIN).as_posix(): path for path in PLUGIN.rglob("*")}
    files = {name for name, path in entries.items() if path.is_file()}
    prose = FENCE.sub("", (PLUGIN / "README.md").read_text(encoding="utf-8"))

    assert files == {
        ".claude-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        "LICENSE",
        "README.md",
        "assets/icon.png",
        *(f"skills/{skill}/SKILL.md" for skill in SKILLS.values()),
    }
    assert not [name for name, path in entries.items() if path.is_symlink()]
    assert (PLUGIN / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert len(prose.split()) >= 40


@pytest.mark.parametrize("page", ["README.md", "plugin/README.md"])
def test_the_page_names_the_commands_that_install_the_plugin(page: str) -> None:
    text = (ROOT / page).read_text(encoding="utf-8")

    assert (
        "/plugin install mcp-server-paperless --marketplace gardenbaum/mcp-server-paperless" in text
    )
    assert "claude plugin marketplace add gardenbaum/mcp-server-paperless" in text
    assert "claude plugin install mcp-server-paperless@gardenbaum" in text
    # `plugin update` alone compares with the marketplace as last fetched.
    assert (
        "claude plugin marketplace update gardenbaum\n"
        "claude plugin update mcp-server-paperless@gardenbaum\n"
    ) in text
    assert "/reload-plugins" in text
    assert "claude mcp remove paperless" in text


def skill(name: str) -> tuple[dict[str, object], str]:
    """Frontmatter and body of the skill for a prompt."""
    text = (PLUGIN / "skills" / SKILLS[name] / "SKILL.md").read_text(encoding="utf-8")
    found = FRONTMATTER.fullmatch(text)
    assert found
    return mapping(cast("object", yaml.safe_load(found[1]))), found[2]


def one_line(text: str) -> str:
    return " ".join(text.split())


def test_every_prompt_has_a_skill() -> None:
    assert set(PROMPTS) == set(SPECS)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_the_frontmatter_follows_the_agent_skills_specification(name: str) -> None:
    """Both clients read it; no `allowed-tools`, which would pre-approve writing tools."""
    frontmatter, _ = skill(name)
    description = frontmatter["description"]

    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == SKILLS[name]
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", SKILLS[name])
    assert isinstance(description, str)
    assert 0 < len(description) <= 1024
    assert "paperless" in description.lower()
    assert "Use when" in description


@pytest.mark.parametrize("name", sorted(SPECS))
def test_the_skill_says_what_the_prompt_says(name: str) -> None:
    """The steps and the confirmation rule are the prompt's, so the two cannot drift apart."""
    _, body = skill(name)

    assert one_line(PROMPTS[name]) in one_line(body)
    assert one_line(CONFIRMATION) in one_line(body)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_the_skill_names_only_tools_of_the_default_profile(name: str) -> None:
    """A skill that needed another toolset would have to say so; none does."""
    _, body = skill(name)
    default = ServerSettings.model_construct().toolsets

    assert mentioned_tools(body) == SPECS[name].tools
    assert {TOOL_TOOLSETS[tool] for tool in mentioned_tools(body)} <= set(default)


@pytest.mark.parametrize("name", sorted(SPECS))
def test_the_skill_stops_when_the_server_is_not_there(name: str) -> None:
    """Chat on claude.ai loads the skills but starts no local server."""
    _, body = skill(name)

    assert "do not work around it" in one_line(body)


def test_the_plugin_readme_lists_the_skills() -> None:
    text = (PLUGIN / "README.md").read_text(encoding="utf-8")

    assert [name for name in SKILLS.values() if f"`{name}`" not in text] == []


def renovate_rule() -> dict[str, object]:
    """The one Renovate rule that follows the releases of this package."""
    managers = cast("list[object]", load(ROOT / "renovate.json")["customManagers"])
    rules = [mapping(item) for item in managers]
    found = [rule for rule in rules if rule.get("depNameTemplate") == "mcp-server-paperless"]
    assert len(found) == 1
    return found[0]


def settings_in(value: object) -> list[tuple[str, object]]:
    """Every key with its value, at every level of the configuration."""
    found: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, item in mapping(cast("object", value)).items():
            found += [(key, item), *settings_in(item)]
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            found += settings_in(item)
    return found


def test_nothing_in_the_renovate_configuration_switches_the_rule_off() -> None:
    config = load(ROOT / "renovate.json")
    found = settings_in(config)

    assert "custom.regex" in cast("list[str]", config["enabledManagers"])
    assert ("enabled", False) not in found
    assert not {"ignoreDeps", "ignorePaths", "includePaths"} & {key for key, _ in found}


def test_renovate_never_merges_a_release_by_itself_and_takes_only_plain_versions() -> None:
    """The bot's own configuration automerges; the last matching package rule wins."""
    config = load(ROOT / "renovate.json")
    last = mapping(cast("list[object]", config["packageRules"])[-1])

    assert last.pop("description")
    assert last == {
        "matchPackageNames": ["mcp-server-paperless"],
        "automerge": False,
        # The manifests' patterns do not match 1.2.3.post1, so Renovate could not write it.
        "allowedVersions": r"/^\d+\.\d+\.\d+$/",
    }
    assert ("automerge", True) not in settings_in(config)


def test_renovate_follows_the_releases_on_pypi() -> None:
    rule = renovate_rule()

    # Exact: no template can change what is written, nothing else can filter the rule.
    assert set(rule) == {
        "customType",
        "description",
        "managerFilePatterns",
        "matchStrings",
        "depNameTemplate",
        "datasourceTemplate",
        "versioningTemplate",
    }
    assert rule["customType"] == "regex"
    assert rule["datasourceTemplate"] == "pypi"
    assert rule["versioningTemplate"] == "pep440"
    patterns = cast("list[str]", rule["managerFilePatterns"])
    files = [path.relative_to(ROOT).as_posix() for path in PLUGIN.rglob("*") if path.is_file()]
    files += [".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json", "server.json"]
    matched = {file for file in files for item in patterns if re.search(item.strip("/"), file)}
    assert matched == {"plugin/.claude-plugin/plugin.json", "plugin/.cursor-plugin/plugin.json"}


@pytest.mark.parametrize("client", [CLAUDE, CURSOR])
def test_renovate_raises_the_version_and_the_pin_of_a_manifest_together(client: str) -> None:
    """Its pull request keeps the version and the pin equal, which the tests above require."""
    text = (PLUGIN / client / "plugin.json").read_text(encoding="utf-8")
    current = str(manifest(client)["version"])
    raised = text
    matches = 0
    for pattern in cast("list[str]", renovate_rule()["matchStrings"]):
        # Renovate's regex dialect writes a named group without Python's P.
        for found in re.finditer(pattern.replace("(?<", "(?P<"), text):
            assert found["currentValue"] == current
            matches += 1
            raised = raised.replace(found[0], found[0].replace(current, "98.76.54"))

    assert matches == 2
    assert current not in raised
    after = mapping(cast("object", json.loads(raised)))
    entry = mapping(mapping(after["mcpServers"])["paperless"])
    assert after["version"] == "98.76.54"
    assert entry["args"] == ["mcp-server-paperless==98.76.54"]
