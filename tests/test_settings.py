import os
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from mcp_server_paperless.settings import (
    CLAUDE_CALLBACK,
    CLAUDE_COM_CALLBACK,
    HttpSettings,
    OidcSettings,
    PaperlessSettings,
    ServerSettings,
)

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
    settings: type[PaperlessSettings | ServerSettings | HttpSettings | OidcSettings],
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
def test_an_empty_public_url_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clients that build a form from server.json may pass what the user left blank (PLMCP-65)."""
    monkeypatch.setenv("PAPERLESS_PUBLIC_URL", "")

    assert PaperlessSettings.model_validate({}).public_url is None


@pytest.mark.parametrize("name", ["PAPERLESS_URL", "PAPERLESS_TOKEN"])
@pytest.mark.usefixtures("connection_env")
def test_an_empty_required_variable_is_refused(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "")

    assert invalid_fields(PaperlessSettings) == [(name.removeprefix("PAPERLESS_").lower(),)]


EMPTY_LISTS = {"disabled_tools", "upload_dirs", "download_dirs"}


def test_an_empty_token_is_refused_however_the_settings_are_built() -> None:
    with pytest.raises(ValidationError, match="token"):
        _ = PaperlessSettings.model_validate({"url": URL, "token": ""})


@pytest.mark.parametrize("field", sorted(set(ServerSettings.model_fields) - EMPTY_LISTS))
def test_an_empty_server_setting_is_refused_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Unset would offer more than an empty READ_ONLY or TOOLSETS may mean; a new field decides."""
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", "")

    assert invalid_fields(ServerSettings) == [(field,)]


@pytest.mark.parametrize("field", sorted(EMPTY_LISTS))
def test_an_empty_list_setting_is_an_empty_list(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", "")

    settings = ServerSettings()

    assert (settings.disabled_tools, settings.upload_dirs, settings.download_dirs) == ((), (), ())


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


def test_require_dialog_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ServerSettings.model_validate({}).require_dialog is False
    monkeypatch.setenv("PAPERLESS_MCP_REQUIRE_DIALOG", "true")

    assert ServerSettings.model_validate({}).require_dialog is True


def test_require_dialog_must_be_a_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_REQUIRE_DIALOG", "maybe")

    assert invalid_fields(ServerSettings) == [("require_dialog",)]


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


DIRECTORY_SETTINGS = ["upload_dirs", "download_dirs"]


def directories(field: str) -> object:
    return cast("object", getattr(ServerSettings.model_validate({}), field))


@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_no_directory_is_allowed_by_default(field: str) -> None:
    assert directories(field) == ()


@pytest.mark.parametrize(
    "value",
    ["/data/in,/home/me/scans", '["/data/in", "/home/me/scans"]', " /data/in , /home/me/scans,, "],
)
@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_the_upload_directories_are_a_list_of_absolute_paths(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", value)

    assert directories(field) == ("/data/in", "/home/me/scans")


@pytest.mark.parametrize("value", ["scans", "/data/in,relative/dir", "~/scans", "."])
@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_a_relative_upload_directory_is_refused_because_it_would_depend_on_where_the_server_starts(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", value)

    assert invalid_fields(ServerSettings) == [(field,)]


@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_the_filesystem_root_is_refused_as_an_upload_directory(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", "/")

    assert invalid_fields(ServerSettings) == [(field,)]


@pytest.mark.parametrize("directory", ["/proc", "/sys", "/dev", "/run", "/proc/self", "/dev/pts"])
@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_a_pseudo_file_system_is_refused_as_an_upload_directory(
    monkeypatch: pytest.MonkeyPatch, field: str, directory: str
) -> None:
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", directory)

    assert invalid_fields(ServerSettings) == [(field,)]


@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_the_home_directory_itself_is_refused_but_a_folder_in_it_is_fine(
    monkeypatch: pytest.MonkeyPatch, field: str, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", str(tmp_path))
    assert invalid_fields(ServerSettings) == [(field,)]

    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", f"{tmp_path}/scans, {tmp_path}/../other")
    assert directories(field) == (
        f"{tmp_path}/scans",
        f"{tmp_path}/../other",
    )


@pytest.mark.parametrize("field", DIRECTORY_SETTINGS)
def test_a_symlink_to_the_home_directory_is_refused_too(
    monkeypatch: pytest.MonkeyPatch, field: str, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "shortcut").symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(f"PAPERLESS_MCP_{field.upper()}", str(tmp_path / "shortcut"))

    assert invalid_fields(ServerSettings) == [(field,)]


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


# --- the optional OIDC layer (decision-31) --------------------------------------------------------

OIDC_SECRET = "oidc-client-secret-not-a-secret"
SIGNING_KEY = "signing-key-not-a-secret-not-a-secret"
OIDC_ENV = {
    "PAPERLESS_MCP_OIDC_CONFIG_URL": "https://login.example.com/.well-known/openid-configuration",
    "PAPERLESS_MCP_OIDC_CLIENT_ID": "paperless-mcp",
    "PAPERLESS_MCP_OIDC_CLIENT_SECRET": OIDC_SECRET,
    "PAPERLESS_MCP_OIDC_BASE_URL": "https://mcp.example.com",
    "PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY": SIGNING_KEY,
}


@pytest.fixture
def oidc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in OIDC_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.mark.decision("decision-31")
def test_without_oidc_variables_the_layer_is_off() -> None:
    settings = OidcSettings.model_validate({})

    assert not settings.configured
    assert settings.missing() == []
    assert settings.allowed_redirect_uris == (
        CLAUDE_CALLBACK,
        CLAUDE_COM_CALLBACK,
        "http://localhost:*",
        "http://127.0.0.1:*",
    )
    assert CLAUDE_CALLBACK == "https://claude.ai/api/mcp/auth_callback"
    assert CLAUDE_COM_CALLBACK == "https://claude.com/api/mcp/auth_callback"


@pytest.mark.decision("decision-31")
def test_empty_oidc_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in OIDC_ENV:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_STORAGE_DIR", "")

    assert not OidcSettings.model_validate({}).configured


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
def test_the_oidc_layer_is_read_from_its_own_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "PAPERLESS_MCP_OIDC_ALLOWED_REDIRECT_URIS", f"{CLAUDE_CALLBACK}, https://app.example.com/cb"
    )

    settings = OidcSettings.model_validate({})

    assert settings.configured
    assert settings.missing() == []
    assert str(settings.config_url) == OIDC_ENV["PAPERLESS_MCP_OIDC_CONFIG_URL"]
    assert settings.client_id == "paperless-mcp"
    assert settings.client_secret is not None
    assert settings.client_secret.get_secret_value() == OIDC_SECRET
    assert str(settings.base_url) == "https://mcp.example.com/"
    assert settings.jwt_signing_key is not None
    assert settings.jwt_signing_key.get_secret_value() == SIGNING_KEY
    assert settings.storage_dir == str(tmp_path)
    assert settings.allowed_redirect_uris == (CLAUDE_CALLBACK, "https://app.example.com/cb")


@pytest.mark.decision("decision-31")
@pytest.mark.parametrize("unset", sorted(OIDC_ENV))
def test_missing_lists_the_required_variables_that_are_not_set(
    monkeypatch: pytest.MonkeyPatch, unset: str
) -> None:
    for name, value in OIDC_ENV.items():
        if name != unset:
            monkeypatch.setenv(name, value)

    settings = OidcSettings.model_validate({})

    assert settings.configured
    assert settings.missing() == [unset]


@pytest.mark.decision("decision-31")
def test_an_optional_oidc_variable_alone_still_configures_the_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_ALLOWED_REDIRECT_URIS", CLAUDE_CALLBACK)

    settings = OidcSettings.model_validate({})

    assert settings.configured
    assert settings.missing() == sorted(OIDC_ENV)


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
def test_oidc_secrets_never_appear_in_repr_or_str() -> None:
    settings = OidcSettings.model_validate({})

    for text in (repr(settings), str(settings)):
        assert OIDC_SECRET not in text
        assert SIGNING_KEY not in text


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
@pytest.mark.parametrize(
    "value",
    [
        "http://login.example.com/.well-known/openid-configuration",
        "ftp://login.example.com/x",
        "not a url",
        "https://user:pw@login.example.com/.well-known/openid-configuration",
        "https://user@login.example.com/.well-known/openid-configuration",
    ],
)
def test_the_discovery_url_must_be_https_unless_loopback_and_carries_no_credentials(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_CONFIG_URL", value)

    assert invalid_fields(OidcSettings) == [("config_url",)]


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
def test_plain_http_is_refused_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_BASE_URL", "http://mcp.example.com")

    with pytest.raises(ValidationError) as caught:
        _ = OidcSettings.model_validate({})

    assert "use https (plain http only for a loopback address)" in caught.value.errors()[0]["msg"]


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1:9000/.well-known/openid-configuration",
        "http://localhost/.well-known/openid-configuration",
        "http://[::1]:9000/.well-known/openid-configuration",
    ],
)
def test_a_loopback_issuer_may_use_plain_http(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_CONFIG_URL", value)

    assert str(OidcSettings.model_validate({}).config_url) == value


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
@pytest.mark.parametrize(
    "value",
    [
        "http://mcp.example.com",
        "https://mcp.example.com/mcp",
        "https://mcp.example.com/?x=1",
        "https://mcp.example.com/#x",
        "https://user:pw@mcp.example.com",
        "ftp://mcp.example.com",
    ],
)
def test_the_base_url_is_an_https_origin(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_BASE_URL", value)

    assert invalid_fields(OidcSettings) == [("base_url",)]


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
@pytest.mark.parametrize("value", ["http://localhost:8000", "https://mcp.example.com:8443/"])
def test_a_base_url_may_carry_a_port_and_plain_http_on_loopback(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_BASE_URL", value)

    assert str(OidcSettings.model_validate({}).base_url).rstrip("/") == value.rstrip("/")


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
def test_a_short_signing_key_is_refused_without_echoing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY", "too-short-key")

    with pytest.raises(ValidationError) as caught:
        _ = OidcSettings.model_validate({})

    assert [error["loc"] for error in caught.value.errors()] == [("jwt_signing_key",)]
    assert not any("too-short-key" in error["msg"] for error in caught.value.errors())


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
def test_the_client_id_may_not_be_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_CLIENT_ID", "   ")

    assert invalid_fields(OidcSettings) == [("client_id",)]


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
def test_the_storage_directory_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_STORAGE_DIR", "relative/dir")

    assert invalid_fields(OidcSettings) == [("storage_dir",)]


@pytest.mark.decision("decision-31")
@pytest.mark.usefixtures("oidc_env")
@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://*",
        "http://mcp.example.com/cb",
        "javascript:alert(1)",
        "myapp://callback",
        " , ",
    ],
)
def test_allowed_redirect_uris_are_https_or_loopback_http(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PAPERLESS_MCP_OIDC_ALLOWED_REDIRECT_URIS", value)

    assert invalid_fields(OidcSettings) == [("allowed_redirect_uris",)]
