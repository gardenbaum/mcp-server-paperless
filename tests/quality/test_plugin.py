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

from mcp_server_paperless.settings import PaperlessSettings, ServerSettings

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


def test_an_option_claude_leaves_empty_means_the_default_of_the_server() -> None:
    """The server refuses most empty variables, so every optional option has a default."""
    defaults = settings_defaults()
    for key, item in options().items():
        if key.upper() in REQUIRED:
            continue
        default = defaults[key.upper()]
        if isinstance(default, tuple):
            default = ",".join(cast("tuple[str, ...]", default))
        assert item["default"] == ("" if default is None else default), key
        assert item["type"] == ("boolean" if isinstance(default, bool) else "string"), key


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
