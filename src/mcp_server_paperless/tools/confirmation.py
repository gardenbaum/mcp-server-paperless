"""Asks the user in a dialog, where the client can show one: to confirm, or to pick a match.

The tool boundary runs every tool through ``confirmed``. A tool that needs a confirmation it does
not have raises ``ConfirmationRequired`` (decision-15); ``confirmed`` then shows its message as an
MCP form elicitation and runs the tool again with the user's acceptance, which passes exactly that
refusal (decision-44). Handshake connections (up to 2025-11-25) are asked while the call waits. On
2026-07-28 the call returns the question, and the client calls again with the answer and the
server's own state, which the SDK seals and binds to tool, arguments, caller and process.

A client without form dialogs gets the refusal, and ``confirm=true`` is its way on. Not so when the
operator requires dialogs (decision-53): then ``confirm`` never reaches a tool as true, and whatever
is not an accepted dialog ends the call.

A write tool that finds a name ambiguous raises ``Ambiguous`` with a question. ``confirmed`` shows
the matches in the same ways and runs the tool again with the pick, which confirms nothing
(decision-109). Whatever is not a pick of an offered match ends the call with that error, without
running the tool again: the name may be unique by then, and the call would write.
"""

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Final, cast

from fastmcp.server.dependencies import get_context, get_http_request
from mcp import MCPError
from mcp.types import (
    METHOD_NOT_FOUND,
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from mcp_server_paperless.domain.choices import Pick, picking
from mcp_server_paperless.domain.errors import (
    Ambiguous,
    ConfirmationRequired,
    NotConfirmed,
    accepting,
    quoted,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping

    from fastmcp import Context
    from mcp.types import ClientCapabilities

    from mcp_server_paperless.domain.choices import Question

# Key of the ``Dialogs`` in the server's lifespan context.
DIALOGS: Final = "dialogs"
# The first protocol version without requests from the server to the client.
SESSIONLESS: Final = "2026-07-28"
# Most dialogs one waiting call shows, when its target keeps changing under them.
MAX_DIALOGS: Final = 3
# Most picks of one call, and most matches of one (the client gives a 2026-07-28 call up after ten
# rounds).
MAX_PICKS: Final = 5
MAX_CHOICES: Final = 20
# The first protocol version with titled options as ``oneOf``.
ONE_OF: Final = "2025-11-25"

KEY: Final = "confirm"
# One required answer without a default: nothing is ticked in advance.
SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        KEY: {"type": "boolean", "title": "Go ahead", "description": "Do this now?"},
    },
    "required": [KEY],
}
WAITING: Final = "Waiting for the user to confirm."
# Added to every reason when the operator requires dialogs.
REQUIRED: Final = (
    "the operator requires the user's answer in a dialog (PAPERLESS_MCP_REQUIRE_DIALOG=true) and "
    "confirm=true does not replace it"
)
NO_FORMS: Final = (
    "the user cannot be asked on this connection, or the client does not declare form elicitation "
    "(MCP dialogs)"
)
NOT_SHOWN: Final = "the dialog could not be shown"
WAY_OUT: Final = "a client that shows dialogs can confirm, or the operator can switch that off"
CHOICE: Final = "choice"
CHOOSING: Final = "Waiting for the user to choose."
# Ends the error after a dialog that the user declined or closed.
NOT_PICKED: Final = (
    "The user was asked which one is meant and picked none: do not pick one yourself, ask them."
)
KEPT_CHANGING: Final = "what would be changed kept changing while the user was asked"


class Asked(BaseModel):
    """What a 2026-07-28 client carries from the question to the answer, sealed by the SDK.

    Attributes:
        fingerprint: The refusal or the question that is asked about.
        picks: The user's picks before this question.
        error: The whole error when the user is asked for a pick, None for a confirmation.
        topic: The topic of that question.
        choices: The values the pick may have.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")

    nonce: str
    fingerprint: str
    expires: float
    picks: tuple[Pick, ...] = ()
    error: str | None = None
    topic: str = ""
    choices: tuple[str, ...] = ()

    @classmethod
    def parse(cls, state: str | None) -> Asked | None:
        """The state, or None if this module did not make it."""
        try:
            return cls.model_validate_json(state or "")
        except ValidationError:
            return None


@dataclass(frozen=True, slots=True)
class Dialogs:
    """How dialogs are run, and which states are used up; one per server, for all connections.

    Attributes:
        timeout: Seconds a dialog waits for its answer.
        progress_every: Seconds between progress notifications while a call waits.
        clock: The time in seconds since the epoch.
        required: Whether only an accepted dialog confirms (``PAPERLESS_MCP_REQUIRE_DIALOG``).
    """

    timeout: float = 600.0
    progress_every: float = 30.0
    clock: Callable[[], float] = time.time
    required: bool = False
    _used: dict[str, float] = field(default_factory=dict[str, float], init=False)

    def use(self, nonce: str, expires: float) -> bool:
        """Use a state up; False if it was already. There is no await between check and use."""
        now = self.clock()
        for old in [n for n, until in self._used.items() if until <= now]:
            del self._used[old]
        if nonce in self._used:
            return False
        self._used[nonce] = expires
        return True


def shows_forms(capabilities: ClientCapabilities | None) -> bool:
    """Whether the client declares form dialogs: ``form``, or neither ``form`` nor ``url``."""
    declared = capabilities.elicitation if capabilities else None
    return declared is not None and (declared.form is not None or declared.url is None)


@dataclass(frozen=True, slots=True)
class Picker:
    """How the user of a call is asked to pick.

    Attributes:
        title: The title of the tool, for the dialog.
        owners: Looks the names of users up by id.
    """

    title: str
    owners: Callable[[set[int]], Awaitable[Mapping[int, str]]]


async def confirmed[**P, R](
    tool: Callable[P, Awaitable[R]], picker: Picker | None, /, *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run a tool call; when it needs a confirmation or a pick, ask the user if the client can.

    Without a ``picker`` nobody is asked to pick.

    Raises:
        Ambiguous: The user did not pick, or was not asked to.
        ConfirmationRequired: The client cannot ask, or the target changed under every dialog.
        NotConfirmed: The user was asked and did not accept; or dialogs are required and there was
            no accepted one, whatever the cause.
    """
    asking = _asking()
    if not _required():
        return await _run(lambda: tool(*args, **kwargs), asking, picker)
    if KEY in kwargs:
        cast("dict[str, object]", kwargs)[KEY] = False
    try:
        return await _run(lambda: tool(*args, **kwargs), asking, picker)
    except ConfirmationRequired:
        reason = KEPT_CHANGING if asking else NO_FORMS
    except NotConfirmed as no:
        reason = no.reason
    # Not after a user's no: there is no way around that.
    parts = [reason, REQUIRED, *([WAY_OUT] if reason in {NO_FORMS, NOT_SHOWN} else [])]
    raise NotConfirmed("; ".join(parts))


async def _run[R](
    run: Callable[[], Awaitable[R]], asking: _Asking | None, picker: Picker | None
) -> R:
    if asking is None:
        return await run()
    accepted, picks = await asking.answered() if asking.sessionless else (None, ())
    dialogs = 0
    while True:
        with accepting(accepted), picking(picks) as this:
            try:
                return await run()
            except ConfirmationRequired as error:
                refusal = error
            except Ambiguous as error:
                refusal = error
        if isinstance(refusal, ConfirmationRequired):
            if asking.sessionless:
                # FastMCP takes this from any tool, whatever its return type says.
                return cast("R", asking.question(refusal, picks))
            if dialogs == MAX_DIALOGS:
                raise refusal
            dialogs += 1
            accepted = await asking.dialog(refusal)
            continue
        question = refusal.question
        if picker is None or question is None or this.written or not _can_pick(question, picks):
            raise refusal
        form = await _form(question, picker, one_of=asking.one_of)
        if asking.sessionless:
            return cast("R", asking.which(refusal, question, form, picks))
        picks = (*picks, await asking.pick(refusal, question, form))
        accepted = None  # what was accepted was accepted without this pick


def _can_pick(question: Question, picks: tuple[Pick, ...]) -> bool:
    return (
        1 < len(question.choices) <= MAX_CHOICES
        and len(picks) < MAX_PICKS
        and question.fingerprint not in {pick.question for pick in picks}  # it did not help
    )


async def _form(
    question: Question, picker: Picker, *, one_of: bool
) -> tuple[str, dict[str, object]]:
    """The text and the schema that show the matches; nothing is chosen in advance."""
    owners = {choice.owner for choice in question.choices if choice.owner is not None}
    names: Mapping[int, str] = await picker.owners(owners) if owners else {}
    titles = {
        choice.value: choice.title
        if choice.owner is None
        else f"{choice.title}, owner {names.get(choice.owner, f'#{choice.owner}')}"
        for choice in question.choices
    }
    options: dict[str, object] = (
        {"oneOf": [{"const": value, "title": title} for value, title in titles.items()]}
        if one_of
        else {"enum": list(titles), "enumNames": list(titles.values())}
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {CHOICE: {"type": "string", "title": "Which one?", **options}},
        "required": [CHOICE],
    }
    message = (
        f"{picker.title}: {len(question.choices)} {question.what} match {quoted(question.word)}. "
        "Which one is meant? Nothing was changed."
    )
    return message, schema


def _required() -> bool:
    """Whether the running server accepts no confirmation but a dialog's."""
    try:
        ctx = get_context()
    except RuntimeError:
        return False  # a tool function called outside a server
    dialogs = cast("Dialogs | None", ctx.lifespan_context.get(DIALOGS))
    return dialogs is not None and dialogs.required


def _asking() -> _Asking | None:
    """How to ask the user of this call; None if that is not possible."""
    try:
        ctx = get_context()
    except RuntimeError:
        return None  # a tool function called outside a server
    if ctx.request_context is None or not shows_forms(ctx.session.client_capabilities):
        return None
    return _Asking(ctx, cast("Dialogs", ctx.lifespan_context[DIALOGS]))


@dataclass(frozen=True, slots=True)
class _Asking:
    """Asks the user of one call."""

    ctx: Context
    dialogs: Dialogs

    @property
    def sessionless(self) -> bool:
        return self.ctx.session.protocol_version >= SESSIONLESS

    @property
    def one_of(self) -> bool:
        """Whether the client knows ``oneOf`` with titles; else it gets the older form."""
        return self.ctx.session.protocol_version >= ONE_OF

    # --- 2026-07-28: the question is the result, the answer comes with another call

    def question(
        self, refusal: ConfirmationRequired, picks: tuple[Pick, ...]
    ) -> InputRequiredResult:
        asked = self._asked(refusal.fingerprint, picks)
        return _asks(KEY, refusal.message, SCHEMA, asked)

    def which(
        self,
        error: Ambiguous,
        question: Question,
        form: tuple[str, dict[str, object]],
        picks: tuple[Pick, ...],
    ) -> InputRequiredResult:
        values = tuple(choice.value for choice in question.choices)
        more = {"topic": question.topic, "error": error.describe(), "choices": values}
        asked = self._asked(question.fingerprint, picks).model_copy(update=more)
        return _asks(CHOICE, *form, asked)

    def _asked(self, fingerprint: str, picks: tuple[Pick, ...]) -> Asked:
        return Asked(
            nonce=secrets.token_urlsafe(16),
            fingerprint=fingerprint,
            expires=self.dialogs.clock() + self.dialogs.timeout,
            picks=picks,
        )

    async def answered(self) -> tuple[str | None, tuple[Pick, ...]]:
        """What this round accepts and the picks; nothing if it answers no question of ours."""
        asked = Asked.parse(self.ctx.request_state)
        answers = self.ctx.input_responses or {}
        if asked is None:
            return None, ()
        if asked.error is not None:
            error = Ambiguous(asked.error)
            fresh = self.dialogs.use(asked.nonce, asked.expires)
            if not fresh or self.dialogs.clock() >= asked.expires:
                raise error
            value = await self._picked(answers.get(CHOICE), asked.choices, error)
            return None, (*asked.picks, Pick(asked.topic, asked.fingerprint, value))
        if KEY not in answers:
            return None, ()
        if self.dialogs.clock() >= asked.expires:
            reason = "the dialog has expired"
            raise NotConfirmed(reason)
        if not self.dialogs.use(asked.nonce, asked.expires):
            return None, ()
        await self._check(answers[KEY])
        return asked.fingerprint, asked.picks

    # --- up to 2025-11-25: the call waits for the answer

    async def dialog(self, refusal: ConfirmationRequired) -> str:
        """The fingerprint of ``refusal`` once the user accepted it."""
        try:
            answer = await self._shown(refusal.message, SCHEMA, WAITING)
        except TimeoutError:
            reason = "nobody answered the dialog in time"
            raise NotConfirmed(reason) from None
        except MCPError as error:
            if error.error.code == METHOD_NOT_FOUND and not self.dialogs.required:
                raise refusal from None  # nobody was asked
            raise NotConfirmed(NOT_SHOWN) from None
        await self._check(answer)
        return refusal.fingerprint

    async def pick(
        self, error: Ambiguous, question: Question, form: tuple[str, dict[str, object]]
    ) -> Pick:
        """What the user picked.

        Raises:
            Ambiguous: ``error``, if there is none.
        """
        try:
            answer = await self._shown(*form, CHOOSING)
        except TimeoutError, MCPError:
            raise error from None
        values = {choice.value for choice in question.choices}
        return question.picking(await self._picked(answer, values, error))

    async def _shown(
        self, message: str, schema: dict[str, object], waiting: str
    ) -> ElicitResult | None:
        """The answer to a form; None if it cannot be read."""
        progress = asyncio.create_task(self._report_progress(waiting))
        try:
            async with asyncio.timeout(self.dialogs.timeout):
                return await self.ctx.session.elicit_form(
                    message, schema, related_request_id=self.ctx.request_id
                )
        except ValidationError:
            return None
        finally:
            _ = progress.cancel()

    async def _report_progress(self, waiting: str) -> None:
        """Some clients give a call up that is silent for minutes."""
        waited = 0.0
        while True:
            await asyncio.sleep(self.dialogs.progress_every)
            waited += self.dialogs.progress_every
            await self.ctx.report_progress(waited, self.dialogs.timeout, waiting)

    # --- both

    async def _check(self, answer: object) -> None:
        """Pass only an acceptance from a caller that is still there.

        Raises:
            NotConfirmed: Anything else.
        """
        reason = _objection(answer)
        if reason is None and await _caller_gone():
            reason = "the caller's connection was gone before the answer"
        if reason is not None:
            raise NotConfirmed(reason)

    async def _picked(self, answer: object, values: Collection[str], error: Ambiguous) -> str:
        """The value of an answer that is one of ``values``, from a caller that is still there.

        Raises:
            Ambiguous: ``error``, for anything else.
        """
        action = answer.action if isinstance(answer, ElicitResult) else None
        if action in {"decline", "cancel"}:
            message = f"{error.describe()} {NOT_PICKED}"
            raise Ambiguous(message)
        content = answer.content if isinstance(answer, ElicitResult) else None
        value = content[CHOICE] if content and content.keys() == {CHOICE} else None
        if action != "accept" or not isinstance(value, str) or value not in values:
            raise error
        if await _caller_gone():
            raise error
        return value


def _asks(key: str, message: str, schema: dict[str, object], asked: Asked) -> InputRequiredResult:
    params = ElicitRequestFormParams(message=message, requested_schema=schema)
    return InputRequiredResult(
        result_type="input_required",
        input_requests={key: ElicitRequest(method="elicitation/create", params=params)},
        request_state=asked.model_dump_json(),
    )


def _objection(answer: object) -> str | None:
    """Why ``answer`` is no acceptance; None if it is one."""
    action = answer.action if isinstance(answer, ElicitResult) else None
    if action == "cancel":
        return "the user closed the dialog without answering"
    content = answer.content if isinstance(answer, ElicitResult) else None
    ticked = content[KEY] if content and content.keys() == {KEY} else None
    if action == "decline" or (action == "accept" and ticked is False):
        return "the user declined"
    if action == "accept" and ticked is True:
        return None
    return "the answer to the dialog was not understood"


async def _caller_gone() -> bool:
    """Whether the HTTP request of this call was dropped: that does not end a waiting call."""
    try:
        request = get_http_request()
    except RuntimeError:
        return False  # stdio or in-process
    return await request.is_disconnected()
