"""SystemAdmin: configuration, logs and bulk download.

Fakes of "nothing may be written" cases are created without `allow_writes`. Seeded: documents 1
(public) and 2 (private, owned by the admin), users admin 2, reader 3.
"""

import io
import zipfile
from typing import final

import pytest
from fakes.accounts import ADMIN, READER
from fakes.paperless import FakePaperless, FakeState, seeded
from pydantic import SecretStr

from mcp_server_paperless.domain.admin import NotText
from mcp_server_paperless.domain.errors import (
    CONFIRM_HINT,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.urls import ANSWER_NOT_SHOWN, REST_NOT_SHOWN
from mcp_server_paperless.services.admin_system import (
    ARCHIVE_NAME,
    MAX_DOCUMENTS,
    MAX_LOG_CHARS,
    SystemAdmin,
)
from mcp_server_paperless.services.log_records import MAX_LOG_LINES

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.decision("decision-6"),
    pytest.mark.decision("decision-15"),
    pytest.mark.decision("decision-18"),
]
KEY = "test-token-not-a-secret"
MB = 1_000_000


@final
class Setup:
    def __init__(self, user: str = ADMIN[0], *, writes: bool = True, max_bytes: int = MB) -> None:
        self.state: FakeState = seeded()
        self.fake: FakePaperless = FakePaperless(self.state, user=user, allow_writes=writes)
        self.service: SystemAdmin = SystemAdmin(self.fake, self.fake, max_bytes=max_bytes)


async def refusal(call: object) -> str:
    with pytest.raises(ConfirmationRequired) as raised:
        await call  # pyright: ignore[reportGeneralTypeIssues] -- test data built from dicts
    assert raised.value.hint == CONFIRM_HINT
    assert raised.value.message.startswith("Nothing was changed. Confirming would ")
    return raised.value.message


# --- configuration


async def test_the_configuration_shows_what_is_set_and_which_keys_are_saved() -> None:
    setup = Setup()
    setup.state.config = {"app_title": "Archive", "pages": 3}
    setup.state.config_keys = {"llm_api_key"}

    found = await setup.service.get_config()

    assert found.settings == {"app_title": "Archive", "pages": 3}
    assert found.keys_configured == ("llm_api_key",)
    assert found.externally_configured_variables == ("PAPERLESS_OCR_LANGUAGE",)


ENDPOINTS = ("llm_endpoint", "llm_embedding_endpoint", "remote_ocr_endpoint")


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize("name", ENDPOINTS)
@pytest.mark.parametrize(
    ("stored", "shown", "in_part"),
    [
        ("https://u:s3cret@e.x:8443/v1?a=b", "https://e.x:8443/v1?a=b", True),
        ("https://s3cret@e.x", "https://e.x", True),
        ("https://u:s3cret@b@e.x/", "a URL that is not shown", True),
        ("u:s3cret@e.x", "a URL that is not shown", True),
        ("https://u:s3cret%40e.x/v1", "a URL that is not shown", True),
        ("https://e.x/v1", "https://e.x/v1", False),
        ("", "", False),
    ],
)
async def test_a_service_url_is_returned_without_its_credentials(
    name: str, stored: str, shown: str, *, in_part: bool
) -> None:
    setup = Setup()
    setup.state.config = {name: stored, "app_title": "a:b@c"}

    found = await setup.service.get_config()
    same = await setup.service.update_config({name: stored})
    changed = await setup.service.update_config({"pages": 3}, confirm=True)

    for config in (found, same.config, changed.config):
        assert config.settings[name] == shown
        assert config.settings["app_title"] == "a:b@c"  # a name, no URL
        assert config.shown_in_part == ((name,) if in_part else ())
    assert "s3cret" not in "".join(m.model_dump_json() for m in (found, same, changed))
    assert setup.state.config[name] == stored


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize("confirm", [False, True])
@pytest.mark.parametrize("stored", ["https://u:s3cret@e.x/v1", "https://u:s3cret@b@e.x/"])
async def test_a_url_written_back_as_it_was_returned_keeps_its_credentials(
    stored: str, *, confirm: bool
) -> None:
    setup = Setup(writes=False)
    setup.state.config = {"llm_endpoint": stored, "app_title": "A"}
    shown = (await setup.service.get_config()).settings

    outcome = await setup.service.update_config(shown, confirm=confirm)

    assert outcome.changed == ()
    assert outcome.message == (
        "Nothing changed. Kept as stored, with any credentials: llm_endpoint."
    )
    assert setup.state.config["llm_endpoint"] == stored


@pytest.mark.decision("decision-64")
async def test_a_url_written_back_is_not_part_of_another_change() -> None:
    setup = Setup()
    stored = dict.fromkeys(ENDPOINTS, "https://u:s3cret@e.x/v1")
    setup.state.config = {**stored}
    shown = (await setup.service.get_config()).settings

    message = await refusal(setup.service.update_config({**shown, "pages": 3}))
    outcome = await setup.service.update_config({**shown, "pages": 3}, confirm=True)

    assert "endpoint" not in message
    assert outcome.changed == ("pages",)
    assert outcome.message == (
        "Changed 1 setting: pages. Kept as stored, with any credentials: "
        "llm_endpoint, llm_embedding_endpoint, remote_ocr_endpoint."
    )
    assert setup.state.config == {**stored, "pages": 3}


@pytest.mark.decision("decision-64")
async def test_a_stored_url_without_credentials_is_kept_without_a_word_about_them() -> None:
    setup = Setup(writes=False)
    setup.state.config = {"llm_endpoint": "https://e.x"}

    outcome = await setup.service.update_config({"llm_endpoint": "https://e.x"})

    assert outcome.message == "Nothing changed: the configuration already is as asked."


EMPTY_USER = pytest.mark.parametrize("empty", ["@", ":@"])


@pytest.mark.decision("decision-64")
@EMPTY_USER
async def test_an_empty_user_over_the_stored_url_removes_its_credentials(empty: str) -> None:
    setup = Setup()
    setup.state.config = {"llm_endpoint": "https://u:s3cret@e.x:8443/v1?a=b"}
    asked = {"llm_endpoint": f"https://{empty}e.x:8443/v1?a=b"}

    message = await refusal(setup.service.update_config(asked))
    outcome = await setup.service.update_config(asked, confirm=True)

    assert "remove the credentials of llm_endpoint 'https://e.x:8443/v1?a=b' (not" in message
    assert "s3cret" not in message
    assert outcome.changed == ("llm_endpoint",)
    assert outcome.message == (
        "Changed 1 setting: llm_endpoint. Removed the credentials of: llm_endpoint."
    )
    assert outcome.config.shown_in_part == ()
    assert setup.state.config == {"llm_endpoint": "https://e.x:8443/v1?a=b"}


@pytest.mark.decision("decision-64")
@EMPTY_USER
async def test_an_empty_user_over_a_url_without_credentials_changes_nothing(empty: str) -> None:
    setup = Setup(writes=False)
    setup.state.config = {"llm_endpoint": "https://e.x/v1"}

    outcome = await setup.service.update_config({"llm_endpoint": f"https://{empty}e.x/v1"})

    assert outcome.message == "Nothing changed: the configuration already is as asked."


@pytest.mark.decision("decision-64")
@EMPTY_USER
@pytest.mark.parametrize(
    ("stored", "old"),
    [
        (None, "set llm_endpoint to 'https://new.x/' (it follows"),
        ("https://u:s3cret@e.x/", "from 'https://e.x/' with credentials (not shown) to 'https://n"),
        ("https://u:s3cret@b@e.x/", "from a URL that is not shown to 'https://new.x/';"),
        ("https://e.x/", "from 'https://e.x/' to 'https://new.x/';"),
    ],
)
async def test_a_new_url_is_written_without_its_empty_user(
    stored: str | None, old: str, empty: str
) -> None:
    setup = Setup()
    setup.state.config = {"llm_endpoint": stored} if stored else {}
    asked = {"llm_endpoint": f"https://{empty}new.x/"}

    message = await refusal(setup.service.update_config(asked))
    outcome = await setup.service.update_config(asked, confirm=True)

    assert old in message
    assert outcome.message == "Changed 1 setting: llm_endpoint."
    assert setup.state.config == {"llm_endpoint": "https://new.x/"}


UNCLEAR = pytest.mark.parametrize(
    "url",
    ["https://u:s3cret%40e.x/v1", "https://u:s3cret\uff20e.x/v1", "https://u:s3cret\ufe6be.x"],
)


@pytest.mark.decision("decision-66")
@UNCLEAR
@pytest.mark.parametrize("new", ["https://n.x/v1", None])
async def test_a_confirmation_does_not_show_a_url_that_results_do_not(
    url: str, new: str | None
) -> None:
    setup = Setup(writes=False)
    setup.state.config = {"llm_endpoint": url}

    message = await refusal(setup.service.update_config({"llm_endpoint": new}))

    assert "llm_endpoint" in message
    assert "a URL that is not shown" in message
    assert "s3cret" not in message


@pytest.mark.decision("decision-66")
@UNCLEAR
async def test_a_new_url_that_results_do_not_show_is_refused_without_its_text(url: str) -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation, match="the llm_endpoint cannot be shown") as raised:
        _ = await setup.service.update_config({"llm_endpoint": url}, confirm=True)

    assert "s3cret" not in raised.value.message


@pytest.mark.decision("decision-64")
@EMPTY_USER
async def test_a_stored_empty_user_is_removed_by_writing_it_again(empty: str) -> None:
    setup = Setup()
    setup.state.config = {"llm_endpoint": f"https://{empty}e.x"}
    asked = {"llm_endpoint": f"https://{empty}e.x"}

    message = await refusal(setup.service.update_config(asked))
    outcome = await setup.service.update_config(asked, confirm=True)

    assert "remove the credentials of llm_endpoint 'https://e.x'" in message
    assert outcome.changed == ("llm_endpoint",)
    assert setup.state.config == {"llm_endpoint": "https://e.x"}


@pytest.mark.decision("decision-64")
async def test_only_a_service_url_loses_an_empty_user() -> None:
    setup = Setup()

    _ = await setup.service.update_config({"app_title": "https://@e.x", "pages": 3}, confirm=True)

    assert setup.state.config["app_title"] == "https://@e.x"


@pytest.mark.decision("decision-64")
@pytest.mark.parametrize("stored", [None, "https://u:s3cret@e.x/v1", "https://e.x"])
async def test_what_stands_for_an_unshown_url_is_not_written_over_another(
    stored: str | None,
) -> None:
    setup = Setup(writes=False)
    setup.state.config = {"llm_endpoint": stored} if stored else {}

    with pytest.raises(Validation, match="may not show"):
        _ = await setup.service.update_config(
            {"llm_endpoint": "a URL that is not shown"}, confirm=True
        )


async def test_every_configuration_change_needs_confirm_and_shows_old_and_new_values() -> None:
    setup = Setup(writes=False)
    setup.state.config = {"app_title": "Archive"}

    message = await refusal(
        setup.service.update_config({"app_title": "Vault", "pages": 2, "deskew": None})
    )

    assert "change app_title from 'Archive' to 'Vault'" in message
    assert "set pages to 2 (it follows the server's environment now)" in message
    assert "reset deskew" not in message  # it was not set: nothing changes
    assert setup.state.config == {"app_title": "Archive"}


async def test_a_confirmed_change_is_written_and_reported() -> None:
    setup = Setup()
    setup.state.config = {"app_title": "Archive", "pages": 3}

    outcome = await setup.service.update_config({"app_title": "Vault", "pages": None}, confirm=True)

    assert outcome.changed == ("app_title", "pages")
    assert outcome.config.settings == {"app_title": "Vault"}
    assert "Changed 2 settings" in outcome.message
    assert setup.state.config == {"app_title": "Vault"}


async def test_a_reset_names_the_value_it_drops() -> None:
    setup = Setup(writes=False)
    setup.state.config = {"pages": 3}

    assert "reset pages (now 3) to the server's environment" in await refusal(
        setup.service.update_config({"pages": None})
    )


async def test_a_change_that_changes_nothing_is_not_sent_or_confirmed() -> None:
    setup = Setup(writes=False)
    setup.state.config = {"app_title": "Archive"}

    outcome = await setup.service.update_config({"app_title": "Archive", "pages": None})

    assert not outcome.changed
    assert outcome.changed == ()
    assert "already" in outcome.message


async def test_a_key_is_written_but_never_shown_or_echoed() -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.update_config(keys={"llm_api_key": SecretStr(KEY)}))

    assert "save the llm_api_key you gave (its value is never shown)" in message
    assert KEY not in message
    writer = Setup()
    outcome = await writer.service.update_config(keys={"llm_api_key": SecretStr(KEY)}, confirm=True)
    assert outcome.config.keys_configured == ("llm_api_key",)
    assert outcome.changed == ("llm_api_key",)
    assert KEY not in outcome.model_dump_json() + outcome.message


async def test_a_key_that_is_blank_masked_or_not_text_is_refused() -> None:
    setup = Setup(writes=False)

    for secret, text in (
        (SecretStr(""), "empty"),
        (SecretStr("***"), "asterisks"),
        (NotText(), "text"),
    ):
        with pytest.raises(Validation) as raised:
            _ = await setup.service.update_config(keys={"llm_api_key": secret}, confirm=True)
        assert text in raised.value.message


async def test_a_name_that_is_not_a_setting_is_refused_with_a_hint() -> None:
    setup = Setup(writes=False)

    with pytest.raises(Validation) as raised:
        _ = await setup.service.update_config({"nope": 1, "app_logo": "x", "llm_api_key": "k"})

    assert "nope" in raised.value.message
    assert "app_logo" in raised.value.message
    assert "llm_api_key" in raised.value.message
    assert raised.value.hint


async def test_nothing_given_is_a_validation_error() -> None:
    with pytest.raises(Validation):
        _ = await Setup(writes=False).service.update_config({})


async def test_changes_that_send_text_elsewhere_say_so() -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.update_config({"llm_endpoint": "https://llm.example/v1"}))

    assert "where Paperless sends document text" in message


async def test_long_lists_of_changes_are_counted_after_the_first_few() -> None:
    setup = Setup(writes=False)
    names = [
        "pages",
        "image_dpi",
        "barcode_dpi",
        "barcode_max_pages",
        "llm_context_size",
        "llm_request_timeout",
        "barcode_upscale",
    ]

    message = await refusal(setup.service.update_config(dict.fromkeys(names, 5)))

    assert message.count(" to 5") == 5
    assert "and 2 more settings" in message


async def test_a_long_value_is_cut_in_the_confirmation() -> None:
    setup = Setup(writes=False)

    message = await refusal(setup.service.update_config({"app_title": "x" * 40}))

    assert "…" not in message
    other = await refusal(setup.service.update_config({"app_title": "y" * 100}))
    assert "…" in other


async def test_paperless_rejecting_a_value_is_a_validation_error() -> None:
    setup = Setup()

    with pytest.raises(Validation):
        _ = await setup.service.update_config({"mode": "bogus"}, confirm=True)


async def test_without_the_permission_the_change_is_denied_with_a_hint() -> None:
    setup = Setup(READER[0])

    with pytest.raises(PermissionDenied) as raised:
        _ = await setup.service.update_config({"pages": 2}, confirm=True)

    assert raised.value.hint


# --- logs

NOTE = (
    "{} line(s) are not shown in full: they hold or may hold credentials of URLs or an answer to "
    "a mail login."
)


async def test_the_logs_are_listed() -> None:
    assert (await Setup().service.list_logs()).logs == ("paperless", "mail", "celery")


async def test_the_last_lines_of_a_log_are_returned_oldest_first() -> None:
    setup = Setup()
    setup.state.logs["mail"] = [
        "[2026-11-24 10:00:00,001] [INFO] [paperless.x] x",
        *(f"line {n}" for n in range(10)),
    ]

    found = await setup.service.get_log("mail", lines=3)

    assert (found.name, found.lines, found.returned_lines) == (
        "mail",
        ("line 7", "line 8", "line 9"),
        3,
    )
    assert not found.truncated


@pytest.mark.decision("decision-66")
async def test_log_lines_hold_no_credentials_of_the_urls_paperless_called() -> None:
    setup = Setup()
    setup.state.logs["paperless"] = [
        "[2026-11-24 10:00:00,123] [INFO] [paperless] Webhook sent to https://user:p w@h.example/x",
        "[2026-11-24 10:00:00,124] [INFO] [paperless.x] Error for url 'https://user:pw@h.example/x'",
        "[2026-11-24 10:00:00,125] [INFO] [paperless.x] mail from a@b.example: https://h.example",
        "[2026-11-24 10:00:00,126] [INFO] [paperless.x] sent to https://user:p",
        "w@h.example/x",
    ]

    found = await setup.service.get_log("paperless", lines=5)

    assert found.lines == (
        "[2026-11-24 10:00:00,123] [INFO] [paperless] Webhook sent to a URL that is not shown",
        "[2026-11-24 10:00:00,124] [INFO] [paperless.x] Error for url 'https://h.example/x'",
        "[2026-11-24 10:00:00,125] [INFO] [paperless.x] mail from a@b.example: https://h.example",
        "[2026-11-24 10:00:00,126] [INFO] [paperless.x] sent to a URL that is not shown",
        "a URL that is not shown",
    )
    assert found.note == NOTE.format(4)


@pytest.mark.decision("decision-80")
async def test_log_lines_hold_no_answer_of_a_mail_server_to_a_login() -> None:
    setup = Setup()
    start = "[2026-11-24 10:00:00,123] [ERROR] [paperless_mail] "
    setup.state.logs["mail"] = [
        f"{start}Error while authenticating account Work: Data: [b'pa55w0rd']",
        f"{start}Error while processing mail account Work",
        '  File "mail.py", line 1, in mailbox_login',
        "imap_tools.errors.MailboxLoginError: Data: [b'pa55w0rd']",
        "",
        "paperless_mail.mail.MailError: Error while authenticating account Work",
        f"{start}Next",
    ]

    found = await setup.service.get_log("mail", lines=4)

    assert found.lines == (
        f"imap_tools.errors.MailboxLoginError: {ANSWER_NOT_SHOWN}",
        ANSWER_NOT_SHOWN,
        ANSWER_NOT_SHOWN,
        f"{start}Next",
    )
    assert found.note == NOTE.format(3)
    whole = await setup.service.get_log("mail", lines=7)
    assert "pa55w0rd" not in whole.model_dump_json()


CUT_SECRET = [
    "[2026-11-24 10:00:00,001] [INFO] [paperless.x] to https://user:se",
    "cr",
    "et@h.example/x",
    "end",
]


@pytest.mark.decision("decision-66")
@pytest.mark.parametrize("before", [0, 2 * MAX_LOG_LINES])
@pytest.mark.parametrize("lines", [1, 2, 3, 4])
async def test_a_window_that_starts_inside_a_record_shows_no_rest_of_a_password(
    lines: int, before: int
) -> None:
    setup = Setup()
    setup.state.logs["paperless"] = [
        "[2026-11-24 09:00:00,001] [INFO] [paperless.x] a"
    ] * before + CUT_SECRET

    found = await setup.service.get_log("paperless", lines=lines)

    hidden = "a URL that is not shown"
    whole = (f"[2026-11-24 10:00:00,001] [INFO] [paperless.x] to {hidden}", hidden, hidden, "end")
    assert found.lines == whole[-lines:]
    assert (found.returned_lines, found.truncated) == (lines, False)
    note = NOTE.format(lines - 1)
    assert found.note == (note if lines > 1 else None)


@pytest.mark.decision("decision-66")
async def test_a_record_longer_than_all_that_is_read_shows_no_rest_of_a_password() -> None:
    setup = Setup()
    setup.state.logs["paperless"] = [*CUT_SECRET[:1], *["p"] * MAX_LOG_LINES, "et@h", "end"]

    found = await setup.service.get_log("paperless", lines=3)

    assert found.lines == (REST_NOT_SHOWN, REST_NOT_SHOWN, REST_NOT_SHOWN)


@pytest.mark.decision("decision-66")
async def test_the_undated_head_of_a_short_log_is_not_shown_either() -> None:
    setup = Setup()
    setup.state.logs["paperless"] = ["et@h", "end"]

    found = await setup.service.get_log("paperless", lines=50)

    assert found.lines == (REST_NOT_SHOWN, REST_NOT_SHOWN)
    assert found.note == NOTE.format(2)


async def test_a_log_without_credentials_has_no_note() -> None:
    assert (await Setup().service.get_log("paperless", lines=3)).note is None


async def test_a_log_over_the_character_limit_keeps_the_newest_lines() -> None:
    setup = Setup()
    setup.state.logs["mail"] = [
        "[2026-11-24 10:00:00,001] [INFO] [paperless.x] x",
        *(f"{n:04d} " + "x" * 995 for n in range(50)),
    ]

    found = await setup.service.get_log("mail", lines=50)

    assert found.truncated
    assert found.lines[-1].startswith("0049")
    assert sum(len(line) + 1 for line in found.lines) <= MAX_LOG_CHARS
    assert found.returned_lines == len(found.lines) < 50


async def test_a_single_line_over_the_limit_is_cut_from_the_front() -> None:
    setup = Setup()
    setup.state.logs["mail"] = [
        "[2026-11-24 10:00:00,001] [INFO] [paperless.x] " + "a" * (MAX_LOG_CHARS * 2) + "END"
    ]

    found = await setup.service.get_log("mail", lines=5)

    [line] = found.lines
    assert found.truncated
    assert line.startswith("…")
    assert line.endswith("END")
    assert len(line) <= MAX_LOG_CHARS


async def test_an_empty_log_is_an_empty_excerpt() -> None:
    found = await Setup().service.get_log("celery", lines=5)

    assert (found.lines, found.returned_lines, found.truncated) == ((), 0, False)


async def test_a_log_that_does_not_exist_is_not_found_and_lines_are_bounded() -> None:
    setup = Setup()

    with pytest.raises(NotFound):
        _ = await setup.service.get_log("nope", lines=5)
    for bad in (0, MAX_LOG_LINES + 1):
        with pytest.raises(Validation):
            _ = await setup.service.get_log("mail", lines=bad)


async def test_logs_are_for_staff_only() -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await Setup(READER[0]).service.list_logs()

    assert raised.value.hint


# --- bulk download


def names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as opened:
        return opened.namelist()


async def test_documents_come_as_one_archive_with_a_report() -> None:
    setup = Setup()

    delivery = await setup.service.download([1, 2], content="originals")

    assert delivery.data is not None
    assert len(names(delivery.data)) == 2
    report = delivery.report
    assert (report.filename, report.mime_type, report.delivered) == (
        ARCHIVE_NAME,
        "application/zip",
        True,
    )
    assert (report.document_ids, report.content, report.size_bytes) == (
        (1, 2),
        "originals",
        len(delivery.data),
    )
    assert report.note is None


async def test_an_archive_over_the_limit_is_reported_and_not_attached() -> None:
    setup = Setup(max_bytes=10)

    delivery = await setup.service.download([1])

    assert delivery.data is None
    assert (delivery.report.delivered, delivery.report.size_bytes, delivery.report.max_bytes) == (
        False,
        None,
        10,
    )
    assert delivery.report.note is not None
    assert "download_document" in delivery.report.note
    assert "PAPERLESS_MCP_MAX_DOWNLOAD_BYTES" in delivery.report.note


async def test_the_document_list_is_checked() -> None:
    setup = Setup()

    for bad in ([], [1, 1], list(range(1, MAX_DOCUMENTS + 2))):
        with pytest.raises(Validation):
            _ = await setup.service.download(bad)


async def test_hidden_and_unknown_documents_are_errors() -> None:
    with pytest.raises(PermissionDenied) as raised:
        _ = await Setup(READER[0]).service.download([2])
    assert raised.value.hint
    with pytest.raises(Validation):
        _ = await Setup().service.download([999])


async def test_a_name_that_is_not_an_api_key_is_refused() -> None:
    with pytest.raises(Validation) as raised:
        _ = await Setup(writes=False).service.update_config(keys={"app_title": SecretStr("x")})

    assert "not an API key setting" in raised.value.message
    assert "llm_api_key" in (raised.value.hint or "")
