"""Asks the user to confirm a destructive call in a dialog, where the client can show one.

The tool boundary runs every tool through ``confirmed``. A tool that needs a confirmation it does
not have raises ``ConfirmationRequired`` (decision-15); ``confirmed`` then shows its message as an
MCP form elicitation and runs the tool again with the user's acceptance, which passes exactly that
refusal (decision-44). Handshake connections (up to 2025-11-25) are asked while the call waits. On
2026-07-28 the call returns the question, and the client calls again with the answer and the
server's own state, which the SDK seals and binds to tool, arguments, caller and process.

A client without form dialogs gets the refusal, and ``confirm=true`` is its way on. Not so when the
operator requires dialogs (decision-53): then ``confirm`` never reaches a tool as true, and whatever
is not an accepted dialog ends the call.
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

from mcp_server_paperless.domain.errors import ConfirmationRequired, NotConfirmed, accepting

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastmcp import Context
    from mcp.types import ClientCapabilities

# Key of the ``Dialogs`` in the server's lifespan context.
DIALOGS: Final = "dialogs"
# The first protocol version without requests from the server to the client.
SESSIONLESS: Final = "2026-07-28"
# Most dialogs one waiting call shows, when its target keeps changing under them.
MAX_DIALOGS: Final = 3

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
NO_FORMS: Final = "this client does not say that it can show dialogs (MCP form elicitation)"
KEPT_CHANGING: Final = "what would be changed kept changing while the user was asked"


class Asked(BaseModel):
    """What a 2026-07-28 client carries from the question to the answer, sealed by the SDK."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, strict=True, extra="forbid")

    nonce: str
    fingerprint: str
    expires: float

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


async def confirmed[**P, R](
    tool: Callable[P, Awaitable[R]], *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run a tool call; when it needs a confirmation, ask the user for it if the client can.

    Raises:
        ConfirmationRequired: The client cannot ask, or the target changed under every dialog.
        NotConfirmed: The user was asked and did not accept; or dialogs are required and there was
            no accepted one, whatever the cause.
    """
    asking = _asking()
    if not _required():
        return await _run(lambda: tool(*args, **kwargs), asking)
    if KEY in kwargs:
        cast("dict[str, object]", kwargs)[KEY] = False
    try:
        return await _run(lambda: tool(*args, **kwargs), asking)
    except ConfirmationRequired:
        reason = KEPT_CHANGING if asking else NO_FORMS
    except NotConfirmed as no:
        reason = no.reason
    reason = f"{reason}; {REQUIRED}"
    raise NotConfirmed(reason)


async def _run[R](run: Callable[[], Awaitable[R]], asking: _Asking | None) -> R:
    if asking is None:
        return await run()
    accepted = await asking.answered() if asking.sessionless else None
    for _ in range(MAX_DIALOGS):
        try:
            with accepting(accepted):
                return await run()
        except ConfirmationRequired as refusal:
            if asking.sessionless:
                # FastMCP takes this from any tool, whatever its return type says.
                return cast("R", asking.question(refusal))
            accepted = await asking.dialog(refusal)
    with accepting(accepted):
        return await run()


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

    # --- 2026-07-28: the question is the result, the answer comes with another call

    def question(self, refusal: ConfirmationRequired) -> InputRequiredResult:
        asked = Asked(
            nonce=secrets.token_urlsafe(16),
            fingerprint=refusal.fingerprint,
            expires=self.dialogs.clock() + self.dialogs.timeout,
        )
        params = ElicitRequestFormParams(message=refusal.message, requested_schema=SCHEMA)
        return InputRequiredResult(
            result_type="input_required",
            input_requests={KEY: ElicitRequest(method="elicitation/create", params=params)},
            request_state=asked.model_dump_json(),
        )

    async def answered(self) -> str | None:
        """The fingerprint this round accepts; None if it brings no answer to a question of ours."""
        asked = Asked.parse(self.ctx.request_state)
        answers = self.ctx.input_responses or {}
        if asked is None or KEY not in answers:
            return None
        if self.dialogs.clock() >= asked.expires:
            reason = "the dialog has expired"
            raise NotConfirmed(reason)
        if not self.dialogs.use(asked.nonce, asked.expires):
            return None
        await self._check(answers[KEY])
        return asked.fingerprint

    # --- up to 2025-11-25: the call waits for the answer

    async def dialog(self, refusal: ConfirmationRequired) -> str:
        """The fingerprint of ``refusal`` once the user accepted it."""
        progress = asyncio.create_task(self._report_progress())
        try:
            async with asyncio.timeout(self.dialogs.timeout):
                answer = await self.ctx.session.elicit_form(
                    refusal.message, SCHEMA, related_request_id=self.ctx.request_id
                )
        except TimeoutError:
            reason = "nobody answered the dialog in time"
            raise NotConfirmed(reason) from None
        except MCPError as error:
            if error.error.code == METHOD_NOT_FOUND and not self.dialogs.required:
                raise refusal from None  # nobody was asked
            reason = "the dialog could not be shown"
            raise NotConfirmed(reason) from None
        except ValidationError:
            answer = None
        finally:
            _ = progress.cancel()
        await self._check(answer)
        return refusal.fingerprint

    async def _report_progress(self) -> None:
        """Some clients give a call up that is silent for minutes."""
        waited = 0.0
        while True:
            await asyncio.sleep(self.dialogs.progress_every)
            waited += self.dialogs.progress_every
            await self.ctx.report_progress(waited, self.dialogs.timeout, WAITING)

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
