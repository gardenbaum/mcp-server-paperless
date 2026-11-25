"""MailExplanations: why a mail was not imported, from what Paperless has stored (PLMCP-81.4).

Every check in its three results, records, tasks and logs, what could not be read, and the bounds.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, final, override

import pytest
from fakes.accounts import ADMIN
from fakes.mail import FakeMailAccount, FakeMailRule, FakeProcessedMail
from fakes.paperless import FakePaperless, FakeState, FakeTask, FakeUser, seeded

from mcp_server_paperless.domain.errors import (
    AuthenticationFailed,
    NotFound,
    PermissionDenied,
    UpstreamTimeout,
    UpstreamUnavailable,
    Validation,
)
from mcp_server_paperless.domain.mail import MailFacts
from mcp_server_paperless.services.mail_explanation import (
    CLOSEST,
    LOG_CHARS,
    MAX_ANSWER,
    MAX_LOGS,
    MAX_PROCESSED,
    NO_DRY_RUN,
    MailExplanations,
)

if TYPE_CHECKING:
    from mcp_server_paperless.domain.errors import PaperlessMcpError
    from mcp_server_paperless.domain.mail import MailExplanation, MailRule

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-90")]

NOW: Final = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
LOG: Final = "[2026-11-25 10:00:0{},123] [DEBUG] [paperless_mail] {}"


@final
class Setup:
    def __init__(
        self,
        user: str = ADMIN[0],
        *,
        permissions: set[str] | None = None,
        make: type[FakePaperless] = FakePaperless,
    ) -> None:
        self.state: FakeState = seeded()
        if permissions is not None:
            self.state.users.append(
                FakeUser(id=77, username=user, permissions=frozenset(permissions))
            )
        self.state.mail_accounts.append(
            FakeMailAccount(
                id=1, name="Work", imap_server="h", imap_port=1, username="u", password="p"
            )
        )
        self.fake: FakePaperless = make(self.state, user=user)
        self.now: float = 0.0
        self.service: MailExplanations = MailExplanations(
            self.fake, self.fake, clock=lambda: self.now, now=lambda: NOW
        )

    def rule(self, **fields: object) -> None:
        number = len(self.state.mail_rules) + 1
        values: dict[str, object] = {"id": number, "name": f"R{number}", "account": 1}
        self.state.mail_rules.append(FakeMailRule(**(values | fields)))  # pyright: ignore[reportArgumentType] -- test data

    def record(self, subject: str, **fields: object) -> None:
        number = len(self.state.processed_mail) + 1
        values: dict[str, object] = {"id": number, "rule": 1, "subject": subject}
        self.state.processed_mail.append(FakeProcessedMail(**(values | fields)))  # pyright: ignore[reportArgumentType] -- test data

    async def explain(self, rule_id: int | None = None, **facts: object) -> MailExplanation:
        return await self.service.explain(1, MailFacts.model_validate(facts), rule_id=rule_id)

    async def check(self, what: str, **facts: object) -> tuple[str, str]:
        [rule] = (await self.explain(**facts)).rules
        [check] = [check for check in rule.checks if check.what == what]
        return check.result, check.why


# --- the answer as a whole


async def test_the_answer_says_first_that_it_is_no_dry_run_and_last_what_comes_closest() -> None:
    setup = Setup()
    setup.rule()

    found = await setup.explain()

    assert found.message.startswith(NO_DRY_RUN)
    assert found.closest_way == CLOSEST
    assert "A check that says no most likely keeps the mail out; ok is no promise." in found.message
    assert "Rules of other users that you may not see" in found.message


async def test_no_rule_and_no_enabled_rule_are_said() -> None:
    setup = Setup()
    assert "You see no rule of this account" in (await setup.explain()).message
    setup.rule(enabled=False)
    found = await setup.explain()

    assert "None of these rules is enabled" in found.message
    [rule] = found.rules
    assert (rule.result, [c.what for c in rule.checks]) == ("no", ["enabled"])


async def test_rules_that_may_not_be_read_are_not_called_none() -> None:
    setup = Setup("mailman", permissions={"view_mailaccount"})

    found = await setup.explain()

    assert "The rules could not be read" in found.message
    assert "see no rule" not in found.message
    assert any(gap.startswith("the rules: ") for gap in found.not_checked)
    assert not found.complete


async def test_a_rule_is_chosen_and_has_to_be_one_of_the_account() -> None:
    setup = Setup()
    setup.rule()
    setup.rule()
    setup.rule(account=2)

    assert [r.rule_id for r in (await setup.explain()).rules] == [1, 2]
    assert [r.rule_id for r in (await setup.explain(rule_id=2)).rules] == [2]
    with pytest.raises(Validation) as raised:
        _ = await setup.explain(rule_id=3)
    assert raised.value.describe() == (
        "Account 1 has no rule 3 that you may see. Hint: list_mail_rules shows the rules of the "
        "account."
    )


async def test_an_account_that_is_hidden_is_an_error() -> None:
    with pytest.raises(NotFound):
        _ = await Setup().service.explain(9, MailFacts(), rule_id=None)
    with pytest.raises(PermissionDenied):
        _ = await Setup("e2e-blind").explain()


async def test_an_expired_token_and_a_missing_username_are_said() -> None:
    setup = Setup()
    [account] = setup.state.mail_accounts
    assert (await setup.explain()).account == ()
    account.username, account.is_token = "", True
    account.expiration = NOW - timedelta(seconds=1)

    expired, unnamed = (await setup.explain()).account
    account.expiration = NOW + timedelta(seconds=1)

    assert "The access token has expired" in expired
    assert "has no username" in unnamed
    assert len((await setup.explain()).account) == 1


# --- the checks


@pytest.mark.parametrize(
    ("folder", "result"), [("INBOX", "ok"), ("inbox", "ok"), ("Bills", "no"), (None, "unknown")]
)
async def test_the_folder(folder: str | None, result: str) -> None:
    setup = Setup()
    setup.rule()

    assert (await setup.check("folder", folder=folder))[0] == result


@pytest.mark.parametrize(
    ("days", "result"),
    [
        (0, "ok"),
        (8, "ok"),
        (9, "unknown"),
        (10, "unknown"),
        (11, "unknown"),
        (12, "no"),
        (99, "no"),
    ],
)
async def test_the_age_is_unsure_for_a_day_at_the_edge(days: int, result: str) -> None:
    setup = Setup()
    setup.rule(maximum_age=10)

    assert (await setup.check("age", received=NOW - timedelta(days=days)))[0] == result


async def test_the_age_without_a_limit_a_date_or_a_time_zone() -> None:
    setup = Setup()
    setup.rule(maximum_age=10)

    assert (await setup.check("age"))[0] == "unknown"
    naive = (NOW - timedelta(days=99)).replace(tzinfo=None)
    assert (await setup.check("age", received=naive))[0] == "no"
    setup.state.mail_rules[0].maximum_age = 0
    assert (await setup.check("age"))[0] == "ok"


@pytest.mark.parametrize(
    ("what", "field"), [("sender", "from"), ("recipient", "to"), ("subject", "subject")]
)
async def test_the_filters_are_contained_without_case(what: str, field: str) -> None:
    setup = Setup()
    setup.rule()
    [rule] = (await setup.explain()).rules
    assert what not in {check.what for check in rule.checks}
    setattr(setup.state.mail_rules[0], f"filter_{field}", "ACME")

    assert (await setup.check(what, **{what: "x acme y"}))[0] == "ok"
    result, why = await setup.check(what, **{what: "other"})
    assert result == "no"
    assert "most likely" in why
    assert (await setup.check(what))[0] == "unknown"


async def test_the_body_is_never_known() -> None:
    setup = Setup()
    setup.rule(filter_body="x")

    assert (await setup.check("body"))[0] == "unknown"


@pytest.mark.parametrize(
    ("include", "exclude", "names", "result"),
    [
        (None, None, ["a.pdf"], "ok"),
        (None, None, [], "no"),
        (None, None, None, "unknown"),
        ("*.pdf", None, ["A.PDF"], "ok"),
        ("*.PDF", None, ["a.pdf"], "ok"),
        ("*.pdf,*.txt", None, ["a.txt"], "ok"),
        ("*.pdf, *.txt", None, ["a.txt"], "no"),  # Paperless does not strip the pattern
        ("pdf", None, ["a.pdf"], "no"),  # the whole name
        (",", None, ["a.pdf"], "no"),
        (None, "*.txt", ["a.txt"], "no"),
        (None, "*.txt", ["a.txt", "b.pdf"], "ok"),
        ("*.pdf", "b*", ["b.pdf"], "no"),
    ],
)
async def test_the_attachment_names_as_paperless_compares_them(
    include: str | None, exclude: str | None, names: list[str] | None, result: str
) -> None:
    setup = Setup()
    setup.rule(
        filter_attachment_filename_include=include, filter_attachment_filename_exclude=exclude
    )

    found, why = await setup.check("attachments", attachment_names=names)

    assert found == result
    if result == "ok":
        assert "is not known" in why


async def test_a_rule_that_imports_the_mail_itself_needs_no_attachment() -> None:
    setup = Setup()
    setup.rule(consumption_scope="eml_and_attachments")

    assert (await setup.check("attachments", attachment_names=[]))[0] == "ok"


async def test_inline_attachments_are_only_unsure_where_the_rule_leaves_them_out() -> None:
    setup = Setup()
    setup.rule()
    assert "inline" in (await setup.check("attachments", attachment_names=["a.pdf"]))[1]
    setup.state.mail_rules[0].attachment_type = "all_files"
    assert "inline" not in (await setup.check("attachments", attachment_names=["a.pdf"]))[1]


@pytest.mark.parametrize(
    ("action", "parameter", "field"),
    [
        ("mark_read", None, "is_read"),
        ("flag", None, "is_flagged"),
        ("tag", "Apple:red", "is_flagged"),
    ],
)
async def test_a_marked_mail_is_left_out(action: str, parameter: str | None, field: str) -> None:
    setup = Setup()
    setup.rule(action=action, action_parameter=parameter)

    assert (await setup.check("mark", **{field: True}))[0] == "no"
    assert (await setup.check("mark", **{field: False}))[0] == "ok"
    assert (await setup.check("mark"))[0] == "unknown"


async def test_a_keyword_is_not_known_and_other_actions_leave_nothing_out() -> None:
    setup = Setup()
    setup.rule(action="tag", action_parameter="done")
    assert (await setup.check("mark"))[0] == "unknown"
    setup.state.mail_rules[0].action = "delete"
    [rule] = (await setup.explain()).rules
    assert "mark" not in {check.what for check in rule.checks}


async def test_earlier_rules_of_the_run_are_named() -> None:
    setup = Setup()
    setup.rule(order=1, stop_processing=True)
    setup.rule(order=2, folder="Other")
    setup.rule(order=2)
    setup.rule(order=0, enabled=False, stop_processing=True)

    found = {rule.rule_id: rule for rule in (await setup.explain()).rules}

    def earlier(rule_id: int) -> list[str]:
        return [c.why for c in found[rule_id].checks if c.what == "earlier rules"]

    assert earlier(1) == []
    assert earlier(2) == ["rule 'R1' (id 1) ends the run once any document was queued in it"]
    stops, takes = earlier(3)
    assert "ends the run" in stops
    assert takes == (
        "rule 'R1' (id 1) reads the same folder first; what it queues is left out in that run"
    )


async def test_the_result_of_a_rule_is_no_before_unknown_before_ok() -> None:
    setup = Setup()
    setup.rule(action="delete", maximum_age=0)

    async def result(**facts: object) -> str:
        return (await setup.explain(**facts)).rules[0].result

    assert await result(folder="INBOX", attachment_names=["a.pdf"]) == "ok"
    assert await result(folder="INBOX") == "unknown"
    assert await result(folder="Other") == "no"


# --- records


async def test_records_with_the_subject_are_shown_with_their_status() -> None:
    setup = Setup()
    setup.rule()
    setup.rule()
    setup.record(" Your INVOICE ", status="FAILED", error="a\nb")
    setup.record("Other")
    setup.record("your invoice", rule=2)
    setup.record("your invoice", rule=9)

    found = await setup.explain(subject="Your invoice")

    assert [(r.id, r.rule_id, r.status, r.error) for r in found.processed] == [
        (1, 1, "FAILED", "a b"),
        (3, 2, "SUCCESS", None),
    ]
    assert not any("processed mail" in gap for gap in found.not_checked)


async def test_a_long_subject_is_compared_as_paperless_cuts_it() -> None:
    setup = Setup()
    setup.rule()
    setup.record("s" * 256)

    assert len((await setup.explain(subject="s" * 300)).processed) == 1


async def test_without_a_subject_no_record_is_looked_for_and_that_is_said() -> None:
    setup = Setup()
    setup.rule()
    setup.record("")

    found = await setup.explain()

    assert found.processed == ()
    assert "processed mail: pass the subject of the mail" in found.not_checked


async def test_records_are_bounded_and_the_rest_is_said() -> None:
    setup = Setup()
    setup.rule()
    for _ in range(1001):
        setup.record("Hi")

    found = await setup.explain(subject="Hi")

    assert len(found.processed) == MAX_PROCESSED
    assert f"{1000 - MAX_PROCESSED} more records with this subject" in found.not_checked
    assert "processed mail of rule 'R1' (id 1): only the newest 1,000 were looked at" in (
        found.not_checked
    )


# --- tasks


async def test_the_newest_fetches_and_failed_files_from_mail_are_shown() -> None:
    setup = Setup()
    setup.rule()
    for number in range(1, 8):
        created = NOW + timedelta(minutes=number)
        setup.state.tasks += [
            FakeTask(id=number, task_id=f"f{number}", task_type="mail_fetch", created=created),
            FakeTask(
                id=number + 10,
                task_id=f"c{number}",
                status="failure",
                created=created,
                message="bad\nfile",
                trigger_source="email_consume",
            ),
        ]
    setup.state.tasks.append(FakeTask(id=99, task_id="x", status="failure", message="an upload"))

    tasks = (await setup.explain()).tasks

    assert [t.task_type for t in tasks] == ["mail_fetch"] * 5 + ["consume_file"] * 5
    assert tasks[0].created == NOW + timedelta(minutes=7)
    assert {t.message for t in tasks[5:]} == {"bad file"}


async def test_tasks_that_may_not_be_seen_are_said() -> None:
    setup = Setup("mailman", permissions={"view_mailaccount", "view_mailrule"})

    found = await setup.explain()

    assert found.tasks == ()
    assert any(gap.startswith("fetches: ") for gap in found.not_checked)


# --- logs


async def test_staff_see_the_lines_about_the_account_its_rules_and_skipped_fetches() -> None:
    setup = Setup()
    setup.rule()
    setup.rule()
    setup.state.logs["mail"] = [
        LOG.format(0, "Processing mail account Work"),
        LOG.format(1, "Processing mail account Work2"),
        LOG.format(2, "Rule Work.R1: Skipping disabled rule"),
        LOG.format(3, "Rule Other.R1: Skipping disabled rule"),
        LOG.format(4, "Something else"),
    ]
    setup.state.logs["paperless"] = [
        LOG.format(5, "Mail account processing is already running; skipping this run."),
        LOG.format(6, "No rules enabled for account Work. Skipping."),
        LOG.format(7, "Document 4 consumption finished"),
    ]

    found = await setup.explain()
    one = await setup.explain(rule_id=2)

    assert [line[20:] for line in found.logs] == [
        "Processing mail account Work",
        "Rule Work.R1: Skipping disabled rule",
        "Mail account processing is already running; skipping this run.",
        "No rules enabled for account Work. Skipping.",
    ]
    assert found.logs_from == "2026-11-25 10:00:00"
    assert not any("Rule Work.R1" in line for line in one.logs)


async def test_lines_about_another_mail_are_left_out_when_the_subject_is_known() -> None:
    setup = Setup()
    setup.rule()
    setup.state.logs["mail"] = [
        LOG.format(0, "Rule Work.R1: Processing mail Invoice from a@b with 1 attachment(s)"),
        LOG.format(1, "Rule Work.R1: Processing mail Private from a@b with 1 attachment(s)"),
        LOG.format(2, "Rule Work.R1: Skipping attachment a.pdf does not match pattern x"),
        LOG.format(3, "Rule Work.R1: Skipping attachment secret.pdf does not match pattern x"),
        LOG.format(4, "Rule Work.R1: Processed 1 matching mail(s)"),
    ]

    known = await setup.explain(subject="Invoice", attachment_names=["a.pdf"])
    unknown = await setup.explain()

    assert len(unknown.logs) == 5
    assert len(known.logs) == 3
    assert "Private" not in str(known.logs)
    assert "secret" not in str(known.logs)


async def test_a_user_who_is_no_staff_is_told_that_a_skipped_fetch_is_not_known() -> None:
    setup = Setup("mailman", permissions={"view_mailaccount", "view_mailrule"})
    setup.state.logs["mail"] = [LOG.format(0, "Processing mail account Work")]

    found = await setup.explain()

    assert (found.logs, found.logs_from) == ((), None)
    assert "the log paperless: Logs need a staff user." in found.not_checked
    assert "whether a fetch was skipped is not known" in found.not_checked


async def test_logs_are_bounded_newest_first() -> None:
    setup = Setup()
    setup.rule()
    setup.state.logs["mail"] = [
        f"[2026-11-25 10:{n:02}:00,1] [DEBUG] [paperless_mail] Rule Work.R1: {n} " + "x" * 300
        for n in range(30)
    ]

    found = await setup.explain()

    assert found.logs[-1].startswith("2026-11-25 10:29:00")
    assert len(found.logs) == min(MAX_LOGS, LOG_CHARS // len(found.logs[0]))
    assert sum(map(len, found.logs)) <= LOG_CHARS
    assert f"{30 - len(found.logs)} older log lines about the account" in found.not_checked


@pytest.mark.decision("decision-80")
async def test_no_answer_to_a_login_and_no_credentials_are_shown() -> None:
    setup = Setup()
    setup.rule()
    secret = "pa55w0rd-4711"
    setup.state.logs["mail"] = [
        LOG.format(0, f"Error while authenticating account Work: Data: [b'{secret}']"),
        LOG.format(1, f"Rule Work.R1: to https://u:{secret}@h/x"),
    ]
    setup.state.logs["paperless"] = [
        LOG.format(2, "Error while processing mail account Work"),
        '  File "mail.py", line 1, in mailbox_login',
        f"imaplib.IMAP4.error: {secret}",
    ]

    found = await setup.explain()

    assert len(found.logs) == 3
    assert secret not in found.model_dump_json()


# --- bounds and failures


@final
class Slow(FakePaperless):
    """Every list of rules takes the whole time of a call."""

    owner: Setup

    @override
    async def list_mail_rules(self) -> tuple[MailRule, ...]:
        self.owner.now += 40
        return await super().list_mail_rules()


async def test_after_forty_seconds_nothing_is_asked_and_all_of_it_is_named() -> None:
    setup = Setup(make=Slow)
    assert isinstance(setup.fake, Slow)
    setup.fake.owner = setup
    setup.rule()

    found = await setup.explain(subject="x")

    assert len(found.rules) == 1
    assert not found.complete
    late = [gap for gap in found.not_checked if gap.endswith("40 seconds have passed")]
    assert [gap.split(":")[0] for gap in late] == [
        "processed mail of rule 'R1' (id 1)",
        "fetches",
        "files that could not be consumed",
        "the log mail",
        "the log paperless",
    ]


@pytest.mark.parametrize(
    ("error", "ends"),
    [
        (UpstreamTimeout("silent"), False),
        (UpstreamUnavailable("slow down", status=429), True),
        (AuthenticationFailed("no"), True),
    ],
)
async def test_a_failed_read_is_named_and_a_rate_limit_ends_the_call(
    error: PaperlessMcpError,
    ends: bool,  # noqa: FBT001 -- the case
) -> None:
    @final
    class Failing(FakePaperless):
        @override
        async def list_mail_rules(self) -> tuple[MailRule, ...]:
            raise error

    setup = Setup(make=Failing)
    if ends:
        with pytest.raises(type(error)):
            _ = await setup.explain()
        return
    assert "the rules: silent" in (await setup.explain()).not_checked


async def test_a_long_answer_leaves_rules_out_and_counts_them() -> None:
    setup = Setup()
    for _ in range(200):
        setup.rule(filter_from="x" * 60, filter_to="y" * 60, filter_subject="z" * 60)

    found = await setup.explain(subject="s")

    assert len(found.model_dump_json()) <= MAX_ANSWER
    assert 0 < len(found.rules) < 200
    assert f"{200 - len(found.rules)} of 200 rules: the answer would be too long; pass rule_id" in (
        found.not_checked
    )
    assert not found.complete


async def test_an_answer_with_everything_read_is_complete() -> None:
    setup = Setup()
    setup.rule()

    assert (await setup.explain(subject="x")).complete
