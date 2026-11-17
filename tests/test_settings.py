import os
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from paperless_mcp.settings import HttpSettings, PaperlessSettings, ServerSettings

if TYPE_CHECKING:
    from pathlib import Path

URL = "http://paperless.internal:8000"
TOKEN = "test-token"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide PAPERLESS_* variables of the developer's shell from every test."""
    for name in os.environ:
        if name.upper().startswith("PAPERLESS_"):
            monkeypatch.delenv(name)


def invalid_fields(
    settings: type[PaperlessSettings | ServerSettings | HttpSettings],
) -> list[tuple[int | str, ...]]:
    with pytest.raises(ValidationError) as caught:
        _ = settings.model_validate({})
    return [error["loc"] for error in caught.value.errors()]


@pytest.fixture
def connection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_URL", URL)
    monkeypatch.setenv("PAPERLESS_TOKEN", TOKEN)


@pytest.mark.usefixtures("connection_env")
def test_connection_is_read_from_paperless_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_PUBLIC_URL", "https://paperless.example.org")

    settings = PaperlessSettings.model_validate({})

    assert str(settings.url) == "http://paperless.internal:8000/"
    assert settings.token.get_secret_value() == TOKEN
    assert str(settings.public_url) == "https://paperless.example.org/"


@pytest.mark.usefixtures("connection_env")
def test_public_url_is_optional() -> None:
    assert PaperlessSettings.model_validate({}).public_url is None


@pytest.mark.usefixtures("connection_env")
def test_token_never_appears_in_repr_or_str() -> None:
    settings = PaperlessSettings.model_validate({})

    assert TOKEN not in repr(settings)
    assert TOKEN not in str(settings)


@pytest.mark.parametrize("missing", ["PAPERLESS_URL", "PAPERLESS_TOKEN"])
@pytest.mark.usefixtures("connection_env")
def test_url_and_token_are_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.delenv(missing)

    assert invalid_fields(PaperlessSettings) == [(missing.removeprefix("PAPERLESS_").lower(),)]


@pytest.mark.usefixtures("connection_env")
def test_empty_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_TOKEN", "")

    assert invalid_fields(PaperlessSettings) == [("token",)]


@pytest.mark.parametrize("variable", ["PAPERLESS_URL", "PAPERLESS_PUBLIC_URL"])
@pytest.mark.parametrize("value", ["paperless.internal", "ftp://paperless.internal"])
@pytest.mark.usefixtures("connection_env")
def test_urls_must_be_http_or_https(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    assert invalid_fields(PaperlessSettings) == [(variable.removeprefix("PAPERLESS_").lower(),)]


@pytest.mark.usefixtures("connection_env")
def test_settings_are_immutable() -> None:
    settings = PaperlessSettings.model_validate({})

    with pytest.raises(ValidationError, match="frozen"):
        settings.url = settings.url


def test_log_level_defaults_to_info() -> None:
    assert ServerSettings.model_validate({}).log_level == "INFO"


def test_server_settings_use_the_paperless_mcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "DEBUG")

    assert ServerSettings.model_validate({}).log_level == "DEBUG"


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_LOG_LEVEL", "VERBOSE")

    assert invalid_fields(ServerSettings) == [("log_level",)]


def test_the_startup_check_is_bounded_by_five_seconds_by_default() -> None:
    assert ServerSettings.model_validate({}).startup_timeout == 5.0


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def test_the_startup_timeout_must_be_a_positive_number(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_STARTUP_TIMEOUT", value)

    assert invalid_fields(ServerSettings) == [("startup_timeout",)]


def test_the_core_and_ai_toolsets_are_on_by_default() -> None:
    settings = ServerSettings.model_validate({})

    assert (settings.toolsets, settings.read_only, settings.disabled_tools) == (
        ("core", "ai"),
        False,
        (),
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("core", ("core",)),
        ("core, pdf ,bulk", ("core", "pdf", "bulk")),
        ("CORE,core", ("core",)),
        ("all", ("all",)),
        ('["core","pdf"]', ("core", "pdf")),
    ],
)
def test_toolsets_are_a_comma_separated_list_or_all(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_TOOLSETS", value)

    assert ServerSettings.model_validate({}).toolsets == expected


@pytest.mark.parametrize("value", ["", " , ", "all,core"])
def test_toolsets_must_name_something_and_all_stands_alone(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_TOOLSETS", value)

    assert invalid_fields(ServerSettings) == [("toolsets",)]


def test_disabled_tools_are_a_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_DISABLED_TOOLS", "get_task, list_taxonomy,get_task")

    assert ServerSettings.model_validate({}).disabled_tools == ("get_task", "list_taxonomy")


def test_an_empty_disabled_tools_list_disables_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_DISABLED_TOOLS", "")

    assert ServerSettings.model_validate({}).disabled_tools == ()


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("1", True), ("false", False)])
def test_read_only_is_a_boolean(
    monkeypatch: pytest.MonkeyPatch, value: str, *, expected: bool
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_READ_ONLY", value)

    assert ServerSettings.model_validate({}).read_only is expected


def test_read_only_must_be_a_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_READ_ONLY", "maybe")

    assert invalid_fields(ServerSettings) == [("read_only",)]


@pytest.mark.parametrize("variable", ["PAPERLESS_MCP_TOOLSETS", "PAPERLESS_MCP_DISABLED_TOOLS"])
def test_malformed_json_names_both_accepted_formats(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, '["core", ')

    with pytest.raises(ValidationError) as caught:
        _ = ServerSettings.model_validate({})

    message = caught.value.errors()[0]["msg"]
    assert "comma-separated" in message
    assert "JSON array" in message


def test_downloads_up_to_512_kibibytes_are_attached_by_default() -> None:
    assert ServerSettings.model_validate({}).max_download_bytes == 512 * 1024


def test_the_download_limit_has_an_upper_bound_of_32_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", str(32 * 1024 * 1024))
    assert ServerSettings.model_validate({}).max_download_bytes == 32 * 1024 * 1024

    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", str(32 * 1024 * 1024 + 1))
    assert invalid_fields(ServerSettings) == [("max_download_bytes",)]


def test_the_download_limit_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", "4096")

    assert ServerSettings.model_validate({}).max_download_bytes == 4096


@pytest.mark.parametrize("value", ["0", "-1", "big", "1.5"])
def test_the_download_limit_must_be_a_positive_whole_number(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_DOWNLOAD_BYTES", value)

    assert invalid_fields(ServerSettings) == [("max_download_bytes",)]


# The upload settings of PLMCP-18


def test_uploads_up_to_10_mebibytes_are_accepted_by_default() -> None:
    assert ServerSettings.model_validate({}).max_upload_bytes == 10 * 1024 * 1024


def test_the_upload_limit_is_read_from_the_environment_up_to_100_mebibytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_UPLOAD_BYTES", "4096")
    assert ServerSettings.model_validate({}).max_upload_bytes == 4096

    monkeypatch.setenv("PAPERLESS_MCP_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))
    assert ServerSettings.model_validate({}).max_upload_bytes == 100 * 1024 * 1024

    monkeypatch.setenv("PAPERLESS_MCP_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024 + 1))
    assert invalid_fields(ServerSettings) == [("max_upload_bytes",)]


@pytest.mark.parametrize("value", ["0", "-1", "big", "1.5"])
def test_the_upload_limit_must_be_a_positive_whole_number(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_MAX_UPLOAD_BYTES", value)

    assert invalid_fields(ServerSettings) == [("max_upload_bytes",)]


def test_uploading_by_path_is_off_by_default() -> None:
    assert ServerSettings.model_validate({}).upload_dirs == ()


@pytest.mark.parametrize(
    "value",
    ["/data/in,/home/me/scans", '["/data/in", "/home/me/scans"]', " /data/in , /home/me/scans,, "],
)
def test_the_upload_directories_are_a_list_of_absolute_paths(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", value)

    assert ServerSettings.model_validate({}).upload_dirs == ("/data/in", "/home/me/scans")


@pytest.mark.parametrize("value", ["scans", "/data/in,relative/dir", "~/scans", "."])
def test_a_relative_upload_directory_is_refused_because_it_would_depend_on_where_the_server_starts(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", value)

    assert invalid_fields(ServerSettings) == [("upload_dirs",)]


def test_the_filesystem_root_is_refused_as_an_upload_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", "/")

    assert invalid_fields(ServerSettings) == [("upload_dirs",)]


@pytest.mark.parametrize("directory", ["/proc", "/sys", "/dev", "/run", "/proc/self", "/dev/pts"])
def test_a_pseudo_file_system_is_refused_as_an_upload_directory(
    monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", directory)

    assert invalid_fields(ServerSettings) == [("upload_dirs",)]


def test_the_home_directory_itself_is_refused_but_a_folder_in_it_is_fine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", str(tmp_path))
    assert invalid_fields(ServerSettings) == [("upload_dirs",)]

    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", f"{tmp_path}/scans, {tmp_path}/../other")
    assert ServerSettings.model_validate({}).upload_dirs == (
        f"{tmp_path}/scans",
        f"{tmp_path}/../other",
    )


def test_a_symlink_to_the_home_directory_is_refused_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "shortcut").symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PAPERLESS_MCP_UPLOAD_DIRS", str(tmp_path / "shortcut"))

    assert invalid_fields(ServerSettings) == [("upload_dirs",)]


# --- HTTP transport (PLMCP-34) ---------------------------------------------------------------

HTTP_TOKEN = "test-token-not-a-secret"


def test_http_defaults_bind_loopback_without_tokens_and_allow_nothing_unauthenticated() -> None:
    settings = HttpSettings.model_validate({})

    assert (settings.host, settings.port, settings.path) == ("127.0.0.1", 8000, "/mcp")
    assert settings.auth_tokens == ()
    assert settings.allowed_hosts == ()
    assert settings.allow_unauthenticated_loopback is False


def test_http_settings_use_their_own_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_HOST", "0.0.0.0")  # noqa: S104 -- a value under test
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_PORT", "9000")
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_PATH", "/api/mcp")
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_ALLOW_UNAUTHENTICATED_LOOPBACK", "true")

    settings = HttpSettings.model_validate({})

    assert (settings.host, settings.port, settings.path) == ("0.0.0.0", 9000, "/api/mcp")  # noqa: S104 -- a value under test
    assert settings.allow_unauthenticated_loopback is True


@pytest.mark.parametrize(
    ("value", "count"),
    [
        (HTTP_TOKEN, 1),
        (f"{HTTP_TOKEN}, {HTTP_TOKEN}-2 ,{HTTP_TOKEN}", 2),
        (f'["{HTTP_TOKEN}","{HTTP_TOKEN}-2"]', 2),
        ("", 0),
    ],
)
def test_auth_tokens_are_a_comma_separated_list_or_a_json_array(
    monkeypatch: pytest.MonkeyPatch, value: str, count: int
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_AUTH_TOKENS", value)

    tokens = HttpSettings.model_validate({}).auth_tokens

    assert len(tokens) == count
    assert all(HTTP_TOKEN in token.get_secret_value() for token in tokens)


def test_auth_tokens_never_appear_in_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_AUTH_TOKENS", HTTP_TOKEN)

    settings = HttpSettings.model_validate({})

    assert HTTP_TOKEN not in repr(settings)
    assert HTTP_TOKEN not in str(settings)


@pytest.mark.parametrize("value", ["short", "has space in it 1234567", "ünicode-token-1234567890"])
def test_weak_or_header_unsafe_tokens_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_AUTH_TOKENS", value)

    with pytest.raises(ValidationError) as caught:
        _ = HttpSettings.model_validate({})

    messages = [error["msg"] for error in caught.value.errors()]
    assert [error["loc"][0] for error in caught.value.errors()] == ["auth_tokens"]
    assert not any(value in message for message in messages)


@pytest.mark.parametrize("value", ["0", "65536", "http"])
def test_the_port_must_be_a_valid_port(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_PORT", value)

    assert invalid_fields(HttpSettings) == [("port",)]


@pytest.mark.parametrize(
    "value",
    [
        "mcp",
        "/",
        "/a b",
        "/mcp?x=1",
        "/mcp#x",
        "",
        "//mcp",
        "/mcp/",
        "/a//b",
        "/.",
        "/..",
        "/a/./b",
        "/a/../b",
    ],
)
def test_the_path_must_be_an_absolute_path_below_the_root(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_PATH", value)

    assert invalid_fields(HttpSettings) == [("path",)]


def test_allowed_hosts_are_a_lowercase_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_ALLOWED_HOSTS", "MCP.example.com, 10.0.0.5 ,[::1]")

    assert HttpSettings.model_validate({}).allowed_hosts == ("mcp.example.com", "10.0.0.5", "[::1]")


@pytest.mark.parametrize(
    "value", ["*", "*.example.com", "mcp.example.com/x", "https://mcp.example.com", "a b", "h?st"]
)
def test_allowed_hosts_take_plain_host_names_without_wildcards(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_HTTP_ALLOWED_HOSTS", value)

    assert invalid_fields(HttpSettings) == [("allowed_hosts",)]
