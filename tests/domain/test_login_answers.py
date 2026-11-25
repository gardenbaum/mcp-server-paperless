"""What a mail server answered to a login is in no text that is shown (decision-80).

The texts are the tracebacks and log lines Paperless 3.5.1 makes of a failed login: imap_tools
raises `MailboxLoginError` with the answer, imaplib `IMAP4.error` and `IMAP4.abort`, and
`mailbox_login` logs the answer and raises a `MailError` from it.
"""

import base64
import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_server_paperless.domain.urls import (
    ANSWER_NOT_SHOWN,
    REST_NOT_SHOWN,
    lines_without_credentials,
    text_without_credentials,
)

pytestmark = pytest.mark.decision("decision-80")

PASSWORD = "S3cret-Passw0rd!x"
PLAIN = base64.b64encode(f"\0me\0{PASSWORD}".encode()).decode()
XOAUTH2 = base64.b64encode(f"user=me\1auth=Bearer {PASSWORD}\1\1".encode()).decode()
FIRST = (
    "Traceback (most recent call last):\n"
    '  File "/usr/src/paperless/src/paperless_mail/mail.py", line 238, in mailbox_login\n'
    "    mailbox.login(account.username, account.password)\n"
    '  File "/usr/local/lib/python3.14/site-packages/imap_tools/utils.py", line 47, in check\n'
    "    raise exception(command_result=command_result, expected=expected)\n"
)
CAUSE = "\n\nThe above exception was the direct cause of the following exception:\n\n"
LAST = (
    "Traceback (most recent call last):\n"
    '  File "/usr/src/paperless/src/paperless_mail/mail.py", line 284, in apply_mail_action\n'
    "    mailbox_login(M, account)\n"
    '  File "/usr/src/paperless/src/paperless_mail/mail.py", line 248, in mailbox_login\n'
    "    raise MailError(\n"
    '        f"Error while authenticating account {account}",\n'
    "    ) from e\n"
    "paperless_mail.mail.MailError: Error while authenticating account Work\n"
)
LOGIN_ERROR = "imap_tools.errors.MailboxLoginError: "
START = "[2026-11-24 10:00:00,123] [ERROR] [paperless_mail] "
LOGGED = f"{START}Error while authenticating account Work: "


def failed(message: str, kind: str = LOGIN_ERROR) -> str:
    """The traceback of a failed login whose server answered `message`."""
    return FIRST + kind + message + CAUSE + LAST


def hidden(kind: str, message: str, start: str = FIRST) -> str:
    """The same as it is shown: from the answer on, one sentence stands for each line."""
    end = failed(message, kind).rstrip("\n")
    lines = end.count("\n") - start.count("\n") + 1
    return start + kind + "\n".join([ANSWER_NOT_SHOWN] * lines) + "\n"


@pytest.mark.parametrize(
    "kind", [LOGIN_ERROR, "imaplib.IMAP4.error: ", "imaplib.IMAP4.abort: ", "imaplib.error: "]
)
@pytest.mark.parametrize(
    "answer",
    [
        'Response status "OK" expected, but "NO" received. Data: [b\'LOGIN me S3cret\']',
        f"LOGIN command error: BAD [b'{PASSWORD}']",
        "see https://help.example/login or ask admin@mail.example",
    ],
)
def test_the_answer_to_a_login_goes_with_all_behind_it_and_what_is_before_stays(
    kind: str, answer: str
) -> None:
    assert text_without_credentials(failed(answer, kind)) == hidden(kind, answer)


HANDLING = "\n\nDuring handling of the above exception, another exception occurred:\n\n"
HEADER = "Traceback (most recent call last):\n"
FORGED_END = "paperless_mail.mail.MailError: Error while authenticating account Work\n"


@pytest.mark.parametrize("chain", [CAUSE, HANDLING])
@pytest.mark.parametrize(
    "forged",
    [
        "{chain}{secret}",
        "{chain}" + HEADER + "{secret}",
        "{chain}" + HEADER + "  {secret}\n" + FORGED_END,
        "{chain}" + HEADER + "  File {secret}\nValueError: x{chain}" + FORGED_END,
        "{chain}imaplib.IMAP4.error: x{chain}{secret}",
    ],
)
@pytest.mark.parametrize("real_end", [CAUSE + LAST, ""])
def test_an_answer_that_forges_the_end_of_its_error_shows_nothing(
    chain: str, forged: str, real_end: str
) -> None:
    text = FIRST + LOGIN_ERROR + forged.format(chain=chain, secret=PASSWORD) + real_end

    shown = text_without_credentials(text)

    assert PASSWORD not in shown
    assert shown.count("\n") == text.count("\n")
    assert shown.startswith(FIRST + LOGIN_ERROR + ANSWER_NOT_SHOWN)


def test_the_errors_before_and_after_the_login_error_in_its_chain_hold_no_answer_either() -> None:
    """The client chains its own errors, and the logout after a failed login can fail as well."""
    start = HEADER + '  File "/usr/local/lib/python3.14/imaplib.py", line 1190, in _command\n'
    text = (
        f"{start}imaplib.IMAP4.abort: unexpected response: b'{PASSWORD}'{HANDLING}"
        + failed(f"command: LOGIN => {PASSWORD}", "imaplib.IMAP4.abort: ")
        + f"{HANDLING}{HEADER}imap_tools.errors.MailboxLogoutError: Data: [b'{PASSWORD}']\n"
    )

    shown = text_without_credentials(text)

    assert PASSWORD not in shown
    assert shown.startswith(f"{start}imaplib.IMAP4.abort: {ANSWER_NOT_SHOWN}\n")


def test_an_answer_outside_a_login_stays() -> None:
    text = FIRST.replace("mailbox_login", "apply_mail_action") + "imaplib.IMAP4.error: NO such\n"

    assert text_without_credentials(text) == text


def test_an_error_of_a_login_that_is_no_answer_stays() -> None:
    text = failed("[Errno 104] Connection reset by peer", "ConnectionResetError: ")

    assert text_without_credentials(text) == text


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        (f"{LOGGED}NO {PASSWORD}", LOGGED + ANSWER_NOT_SHOWN),
        (f"{LOGGED}NO\n{PASSWORD}", f"{LOGGED}{ANSWER_NOT_SHOWN}\n{ANSWER_NOT_SHOWN}"),
        (
            f"{START}Error while authenticating account a\nb: {PASSWORD}",
            f"{START}Error while authenticating account a\nb: {ANSWER_NOT_SHOWN}",
        ),
        (f"{START}Error while authenticating account Work", None),
        (f"{START}Error while processing mail account Work", None),
        (
            f"{START}Error while retrieving mailbox Work: NO {PASSWORD}",
            f"{START}Error while retrieving mailbox Work: {ANSWER_NOT_SHOWN}",
        ),
        (
            f"Error while authenticating account Work: {PASSWORD}",
            f"Error while authenticating account Work: {ANSWER_NOT_SHOWN}",
        ),
    ],
)
def test_a_line_of_the_log_holds_no_answer(line: str, shown: str | None) -> None:
    assert text_without_credentials(line) == (shown or line)
    if line.startswith(START):  # a line without its date is none of a record
        assert lines_without_credentials(line.split("\n")) == (shown or line).split("\n")


@pytest.mark.parametrize("phrase", ["authenticating account", "retrieving mailbox"])
def test_an_answer_that_starts_with_a_carriage_return_is_hidden(phrase: str) -> None:
    """The log is read with universal newlines and its lines lose what is blank at their end."""
    start = f"{START}Error while {phrase} Work:"

    assert PASSWORD not in "\n".join(lines_without_credentials([start, PASSWORD]))
    assert PASSWORD not in text_without_credentials(f"{start}\n{PASSWORD}")
    assert PASSWORD not in text_without_credentials(f"{start}\r{PASSWORD}")
    assert lines_without_credentials([start]) == [start + ANSWER_NOT_SHOWN]


@pytest.mark.parametrize("kind", [LOGIN_ERROR, "imaplib.IMAP4.abort: "])
def test_an_error_whose_answer_starts_on_the_next_line_is_hidden(kind: str) -> None:
    record = f"{START}Error while processing mail account Work"
    lines = [record, *FIRST.rstrip().split("\n"), kind.rstrip(), PASSWORD, *CAUSE.split("\n")]

    shown = lines_without_credentials(lines)

    assert PASSWORD not in "\n".join(shown)
    assert len(shown) == len(lines)


def test_a_url_before_the_error_does_not_hide_where_the_answer_starts() -> None:
    text = f'  File "https://h/mail.py"\n{FIRST}{LOGIN_ERROR}me@mail.example {PASSWORD}'

    assert PASSWORD not in text_without_credentials(text)


@pytest.mark.parametrize("first", ["logged", "error"])
def test_the_first_of_two_answers_counts(first: str) -> None:
    logged, error = f"{LOGGED}{PASSWORD}1\n", f"{FIRST}{LOGIN_ERROR}{PASSWORD}2\n"
    text = logged + error if first == "logged" else error + logged

    assert PASSWORD not in text_without_credentials(text)


def test_an_indented_error_is_one_too() -> None:
    text = f"{FIRST}  {LOGIN_ERROR}{PASSWORD}"

    assert text_without_credentials(text) == f"{FIRST}  {LOGIN_ERROR}{ANSWER_NOT_SHOWN}"


def test_the_lines_of_a_record_keep_their_number_and_the_next_record_stays() -> None:
    record = f"{START}Error while processing mail account Work\n{failed(f'NO\n{PASSWORD}')}"
    following = f"{START}Next: all is well"
    lines = [*record.split("\n"), following]

    shown = lines_without_credentials(lines)

    assert len(shown) == len(lines)
    assert PASSWORD not in "\n".join(shown)
    assert shown[-1] == following


@pytest.mark.parametrize(
    "head",
    [
        [f"imaplib.IMAP4.error: {PASSWORD}", *CAUSE.split("\n"), *LAST.split("\n")],
        [PASSWORD],
        [PASSWORD, *CAUSE.split("\n"), *LAST.split("\n")],
        [HEADER.strip(), PASSWORD],
        ["", f"a\n{PASSWORD}", *CAUSE.split("\n"), PASSWORD],
    ],
)
@pytest.mark.parametrize("rest", [[f"{START}Next"], []])
def test_the_rest_of_a_record_whose_start_is_missing_is_not_shown(
    head: list[str], rest: list[str]
) -> None:
    """The start can tell of a login, and an answer can look like anything."""
    shown = lines_without_credentials([*head, *rest])

    assert shown == [
        *("\n".join([REST_NOT_SHOWN] * (line.count("\n") + 1)) for line in head),
        *rest,
    ]


ANSWERS = st.text(max_size=40)


@given(
    ANSWERS,
    ANSWERS,
    st.sampled_from([PASSWORD, PLAIN, XOAUTH2]),
    st.sampled_from([LOGIN_ERROR, "imaplib.IMAP4.error: ", "imaplib.IMAP4.abort: "]),
)
def test_a_planted_secret_is_in_no_shown_text(
    before: str, after: str, secret: str, kind: str
) -> None:
    answer = before + secret + after

    assert secret not in text_without_credentials(failed(answer, kind))
    assert secret not in text_without_credentials(LOGGED + answer)
    record = f"{START}Error while processing mail account Work\n{failed(answer, kind)}"
    lines = record.split("\n")
    for cut in range(len(lines)):
        assert secret not in "\n".join(lines_without_credentials(lines[cut:]))
    assert secret not in "\n".join(lines_without_credentials([record, f"{START}Next"]))


@pytest.mark.parametrize(
    "part",
    ["imaplib.x: ", "\nimaplib.x: ", "\n", " ", ", in mailbox_login\n", CAUSE, "] Error while"],
)
def test_a_hostile_text_is_read_in_linear_time(part: str) -> None:
    started = time.process_time()

    _ = text_without_credentials(FIRST + part * 60_000)
    _ = text_without_credentials(f"{START}Error while authenticating account " + part * 60_000)

    assert time.process_time() - started < 10  # quadratic would take minutes
