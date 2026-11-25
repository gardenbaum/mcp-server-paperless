"""Workflows: found by name or id (decision-5), written with taxonomy by name (decision-21).

A workflow runs by itself on every document its triggers match, so what a caller may write is
narrower than what Paperless stores: only the trigger and action types in
``domain.workflows.WRITABLE_*``, and a setting that does not belong to the type it sits under is an
error instead of a value Paperless would store and ignore. Replacing a list never drops what this
server cannot write, and loses hidden values only confirmed; ``workflow_parts`` writes single
triggers and actions. Secrets (webhook header values, credentials in
webhook URLs, PDF passwords) are never shown.
"""

import dataclasses
import json
import re
from typing import TYPE_CHECKING, Final, Literal, cast
from urllib.parse import urlsplit

from mcp_server_paperless.domain.documents import NameOrId  # noqa: TC001 -- cast target at runtime
from mcp_server_paperless.domain.errors import (
    AnswerTooLarge,
    ConfirmationRequired,
    NotFound,
    PermissionDenied,
    Validation,
    destination,
    not_shown,
    quoted,
)
from mcp_server_paperless.domain.taxonomy import TaxonomyItem
from mcp_server_paperless.domain.urls import (
    URL_NOT_SHOWN,
    shown_url,
    without_credentials,
    without_empty_user,
)
from mcp_server_paperless.domain.workflows import (
    ACTION_FIELDS,
    ACTION_TYPES,
    MATCHING_ALGORITHMS,
    SCHEDULE_DATE_FIELDS,
    SOURCES,
    TRIGGER_FIELDS,
    TRIGGER_TYPES,
    WRITABLE_ACTION_TYPES,
    WRITABLE_TRIGGER_TYPES,
    ActionInfo,
    Field_,
    ListedWorkflow,
    TriggerInfo,
    Workflow,
    WorkflowAction,
    WorkflowDeletion,
    WorkflowHolder,
    WorkflowInfo,
    WorkflowListing,
    WorkflowPart,
    WorkflowRead,
    WorkflowTrigger,
    WorkflowValues,
)
from mcp_server_paperless.services.documents import readable
from mcp_server_paperless.services.removal import WAY_BACK
from mcp_server_paperless.services.tag_hierarchy import close_over_ancestors, descendants
from mcp_server_paperless.services.taxonomy import fold, pick
from mcp_server_paperless.services.views import plural
from mcp_server_paperless.services.workflow_usage import workflow_uses

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.ports import WorkflowReader, WorkflowWriter
    from mcp_server_paperless.domain.taxonomy import TaxonomyKind
    from mcp_server_paperless.domain.workflows import (
        ActionInput,
        PartUse,
        TriggerInput,
        TriggerTypeName,
        Usage,
        WorkflowCreate,
        WorkflowEdit,
    )
    from mcp_server_paperless.services.taxonomy import TaxonomyResolver

# What Paperless shows for a value that was never set; other values are shown. Settings whose
# default is not "empty" are listed here.
NON_EMPTY_DEFAULTS: Final[Mapping[str, JsonValue]] = {
    "sources": [1, 2, 3],
    "is_insensitive": True,
    "schedule_recurring_interval_days": 1,
    "schedule_date_field": "added",
}
HIDDEN: Final = "(hidden)"
# The settings that only a consumption trigger has, and that only a scheduled one has.
CONSUMPTION_ONLY: Final = frozenset({"sources", "path", "filename", "mailrule"})
SCHEDULED_ONLY: Final = frozenset(n for n in TRIGGER_FIELDS if n.startswith("schedule_"))
CONSUMPTION, SCHEDULED, EMAIL, WEBHOOK, MANUAL = 1, 4, 3, 4, 5
# Short names for the types, besides their labels.
TRIGGER_ALIASES: Final[Mapping[int, tuple[str, ...]]] = {
    1: ("consumption",),
    2: ("added",),
    3: ("updated",),
}
ACTION_ALIASES: Final[Mapping[int, tuple[str, ...]]] = {6: ("trash",), 7: ("ocr",)}
# Why a tool may not write a trigger type that Paperless has.
UNWRITABLE_TRIGGERS: Final[Mapping[int, str]] = {
    5: "this server runs such a workflow (run_workflow) but does not make its trigger",
}
# Why a tool may not write an action type that Paperless has.
UNWRITABLE: Final[Mapping[int, str]] = {
    5: "it holds passwords, which this server never shows and so could not keep",
    8: "it makes Paperless ask its AI backend about every matching document",
}
OWNERSHIP: Final = (
    "assign_owner",
    "assign_view_users",
    "assign_view_groups",
    "assign_change_users",
    "assign_change_groups",
)
TRASH_ACTION: Final = 6
# E-mail, webhook, password removal and AI suggestions: Paperless tells no one whether they worked.
UNSEEN_ACTIONS: Final = frozenset({3, 4, 5, 8})
ASSIGNMENT, REMOVAL, REMOTE_OCR, SUGGEST = 1, 2, 7, 8
AI_FIELDS: Final = frozenset(
    {"title", "tags", "correspondent", "document_type", "storage_path", "created"}
)
STORED: Final = (
    "It is stored in the workflow: change that with update_workflow, or run the workflow in the "
    "Paperless web UI."
)
# Paperless does it last, wherever it stands.
TRASHES: Final = "move each of them to the trash, after everything else"
# What Paperless 3.5.1 reads of a manual trigger: not where a document came from, nor a schedule,
# nor a match without an algorithm.
MANUAL_FILTERS: Final = frozenset(
    {"matching_algorithm", "filename", "custom_field_query"}
    | {name for name in TRIGGER_FIELDS if name.startswith("has_")}
)
NO_OCR: Final = "run remote OCR (Paperless ignores it here: it needs a document being consumed)"
TRASH_NOTE: Final = f"A document this workflow moves to the trash {WAY_BACK}"
SUGGESTIONS: Final = 5
# What the actions that need no settings do; password removal and AI suggestions cannot be written
# here but a workflow made in the web UI can hold them (switching it on needs confirm too).
FIXED_ACTS: Final[Mapping[int, str]] = {
    5: "unlock each matching document with stored passwords and add the result as a new version",
    6: "move the matching documents to the trash",
    8: "ask the AI backend about every matching document and apply what it suggests",
}


def _key(text: str) -> str:
    """``text`` folded, without spaces, hyphens and underscores: 'E-Mail' and 'email' alike."""
    return re.sub(r"[\s_-]+", "", fold(text))


def _by_name(
    labels: Mapping[int, str], aliases: Mapping[int, tuple[str, ...]], value: int | str
) -> int | None:
    """The number that ``value`` (a number, digits, a label or a short name) names, or None."""
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return int(text)
    names = {_key(label): number for number, label in labels.items()}
    names.update({_key(a): number for number, short in aliases.items() for a in short})
    return names.get(_key(text))


def trigger_type(value: int | str) -> int:
    """The trigger type number that ``value`` names.

    Raises:
        Validation: No trigger type has that number or name; the hint lists the types.
    """
    number = _by_name(TRIGGER_TYPES, TRIGGER_ALIASES, value)
    if number in TRIGGER_TYPES:
        return cast("int", number)
    shown = f"{number}" if number is not None else f"is called {str(value).strip()!r}"
    message = f"No workflow trigger type {shown}."
    raise Validation(
        message,
        hint=(
            "Use consumption (1), added (2), updated (3) or scheduled (4), "
            "or a label such as 'document added'."
        ),
    )


def require_writable_triggers(specs: Sequence[TriggerInput]) -> None:
    """Refuse a trigger type that Paperless has and this server does not write.

    Raises:
        Validation: A type has no such number or name, or is one that stays with the web UI.
    """
    for spec in specs:
        number = trigger_type(spec.type)
        if number not in WRITABLE_TRIGGER_TYPES:
            message = (
                f"The workflow trigger type {TRIGGER_TYPES[number]!r} ({number}) cannot be "
                f"written by this server: {UNWRITABLE_TRIGGERS[number]}."
            )
            raise Validation(message, hint="Set it up in the Paperless web UI.")


def action_type(value: int | str) -> int:
    """The action type number that ``value`` names, if this server may write it.

    Raises:
        Validation: No action type has that number or name, or Paperless has it but this server
            does not write it; the hint says what to do.
    """
    number = _by_name(ACTION_TYPES, ACTION_ALIASES, value)
    if number in WRITABLE_ACTION_TYPES:
        return number
    if number in UNWRITABLE:
        message = (
            f"The workflow action type {ACTION_TYPES[number]!r} ({number}) cannot be written by "
            f"this server: {UNWRITABLE[number]}."
        )
        raise Validation(message, hint="Set it up in the Paperless web UI.")
    shown = f"{number}" if number is not None else f"is called {str(value).strip()!r}"
    message = f"No workflow action type {shown}."
    raise Validation(
        message,
        hint=(
            "Use assignment (1), removal (2), email (3), webhook (4), move to trash (6) or "
            "remote OCR (7)."
        ),
    )


def _lookup(table: Mapping[int, str], what: str, value: int | str) -> int:
    """The key of ``table`` that ``value`` (a number or a name) names, else a Validation."""
    text = str(value).strip()
    wanted = int(text) if text.isascii() and text.isdigit() else None
    if wanted is not None and wanted in table:
        return wanted
    by_name = {_key(name): number for number, name in table.items()}
    if wanted is None and _key(text) in by_name:
        return by_name[_key(text)]
    shown = f"{wanted}" if wanted is not None else f"is called {text!r}"
    names = ", ".join(table.values())
    message = f"No {what} {shown}."
    raise Validation(message, hint=f"Use one of: {names}.")


def _date_field(value: str) -> str:
    if value not in SCHEDULE_DATE_FIELDS:
        message = f"No schedule date field {value!r}."
        raise Validation(message, hint=f"Use {', '.join(SCHEDULE_DATE_FIELDS)}.")
    return value


def _json_text(value: str) -> str:
    try:
        _ = cast("object", json.loads(value))
    except ValueError:
        message = "custom_field_query is not valid JSON."
        raise Validation(message, hint="Pass the query as JSON text.") from None
    return value


# Settings given as text that are checked before they are sent.
TEXT_CHECKS: Final[Mapping[str, Callable[[str], str]]] = {
    "schedule_date_field": _date_field,
    "custom_field_query": _json_text,
}


def is_empty(value: JsonValue) -> bool:
    """Whether a stored setting holds nothing: a blank text is none."""
    if isinstance(value, str):
        return not value.strip()
    return value is None or value in ([], {}, "", 0, False)


def _is_default(wire: str, value: JsonValue) -> bool:
    if wire in NON_EMPTY_DEFAULTS:
        return value == NON_EMPTY_DEFAULTS[wire]
    return is_empty(value)


def _set(fields: Mapping[str, Field_], settings: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """The settings that are not at their default, under the names tools use."""
    return {
        name: settings[field.wire]
        for name, field in fields.items()
        if field.wire in settings and not _is_default(field.wire, settings[field.wire])
    }


def _action_fields(action: WorkflowAction) -> Mapping[str, Field_]:
    return ACTION_FIELDS.get(action.type, {})


def as_object(value: JsonValue) -> dict[str, JsonValue]:
    """``value`` if it is an object, else an empty one."""
    return cast("dict[str, JsonValue]", value) if isinstance(value, dict) else {}


USUAL_PORTS: Final[Mapping[str, int]] = {"http": 80, "https": 443}


def _webhook(url: str) -> str:
    """Where a webhook goes, for a message: never its credentials, path or query.

    Raises:
        Validation: It is not certain where, or it cannot be shown in full.
    """
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        host = port = None
        parts = urlsplit("")
    usual = USUAL_PORTS.get(parts.scheme)
    unclear = "\\" in re.split("[?#]", url)[0] or parts.netloc.count("@") > 1
    unclear = unclear or "%40" in (host or "")  # an encoded @: credentials, or a part of the host
    if not host or usual is None or unclear:
        cause = "it is not certain which host it leads to"
        refused = not_shown("URL of the webhook", cause)
        raise refused
    shown = destination(host, "webhook host")
    if port in {None, usual}:
        return shown
    bracketed = f"[{host}]" if ":" in host else host
    return destination(f"{parts.scheme}://{bracketed}:{port}", "webhook host", "url")


def _origin(url: JsonValue) -> tuple[str, str | None, int | None]:
    """Scheme, host and port of a URL, as a webhook's destination."""
    try:
        parts = urlsplit(str(url))
        return parts.scheme.lower(), parts.hostname, parts.port
    except ValueError:
        return str(url), None, None  # unreadable: the same only as itself


_SCHEME_AND_HOST: Final = re.compile(r"\A[^/?#]*//[^/?#]*")


def webhook_url(action: WorkflowAction | None) -> str | None:
    """The stored URL of a webhook action; None for another action, or none."""
    hook = as_object(action.settings.get("webhook")) if action and action.type == WEBHOOK else {}
    url = hook.get("url")
    return url if isinstance(url, str) else None


def _hides(url: str | None) -> bool:
    """Whether ``url`` is shown only in part, because of its credentials."""
    return url is not None and shown_url(url) != url


def has_credentials(url: str | None) -> bool:
    """Whether ``url`` has, or may have, credentials: an empty user name alone is none."""
    return url is not None and _hides(url) and without_empty_user(url) != without_credentials(url)


def _certain(url: str) -> bool:
    """Whether it is certain what the credentials in ``url`` are."""
    return without_credentials(url) is not None


def _spelled(url: str) -> str:
    """``url`` in one spelling: no whitespace around it, scheme and host in small letters."""
    return _SCHEME_AND_HOST.sub(lambda found: found[0].lower(), url.strip(), count=1)


def _shown_as(url: str, stored: str | None) -> bool:
    """Whether ``url`` is, in any spelling, the shown form of a stored URL with credentials."""
    return _hides(stored) and _spelled(url) == _spelled(shown_url(stored or ""))


def _not_told(message: str) -> Validation:
    return Validation(
        message,
        hint=(
            "Send the id the action has in get_workflow now to keep the stored credentials, or "
            "a URL with an empty user name (https://@host/path) to write it without any."
        ),
    )


def _kept_or_given(url: str, held: str | None, stored: Sequence[str | None], place: int) -> str:
    """The URL to write: ``held``, the stored one of the same action, if ``url`` is its shown form.

    Raises:
        Validation: ``url`` is the shown form of a URL that it is not certain which action holds.
    """
    plain = without_empty_user(url.strip())
    if plain != url.strip():
        return plain
    if held is not None and (url == held or _shown_as(url, held)):
        return held
    if url.strip() == URL_NOT_SHOWN or any(
        has_credentials(old) and _shown_as(url, old) for old in stored
    ):
        message = (
            f"The webhook URL of action {place} is a stored one as it is shown, without its "
            "credentials, but the action has no id that says which stored URL it stands for."
        )
        raise _not_told(message)
    return url


def _fate(old: str, new: str | None) -> str:
    """What becomes of the credentials of the stored URL ``old`` where ``new`` is written."""
    if not has_credentials(new):
        verb = "remove"
    else:
        verb = "replace" if _certain(new or "") else "remove or replace"
    return f"{verb} {'the' if _certain(old) else 'any'} credentials"


def past_fate(fate: str) -> str:
    """``fate`` as the note of a result says it."""
    done = fate.replace("remove", "removed", 1).replace("replace", "replaced", 1)
    return f"{done.capitalize()} of"


def _check_ids(specs: Sequence[ActionInput], found: Workflow) -> None:
    """Refuse a webhook with an id that is not in the workflow."""
    known = {action.id for action in found.actions}
    for place, spec in enumerate(specs, start=1):
        if spec.webhook is not None and spec.id is not None and spec.id not in known:
            message = (
                f"Nothing was changed: action {place} is a webhook with the id {spec.id}, which is "
                "not (or no longer) in the workflow."
            )
            hint = (
                "Send the id the action has in get_workflow now, or leave the id out for a new "
                "webhook."
            )
            raise Validation(message, hint=hint)


def credentials_fate(
    specs: Sequence[ActionInput], actions: Sequence[WorkflowAction], found: Workflow
) -> tuple[list[int], dict[int, str], str]:
    """What becomes of the stored credentials of webhooks.

    The places of the actions that keep hidden ones, what happens to those of others by place, and
    the ones that no action claims although a webhook could be meant to hold them ("" if none).

    Raises:
        Validation: Two actions would keep the credentials of one stored action.
    """
    stored = {a.id: webhook_url(a) for a in found.actions if a.id is not None}
    holders: dict[int, int] = {}
    kept: list[int] = []
    changed: dict[int, tuple[int, str]] = {}
    claimed: set[int] = set()
    loose: list[str | None] = []  # the URLs of webhooks that say no stored one
    for place, (spec, action) in enumerate(zip(specs, actions, strict=True), start=1):
        if spec.webhook is None:
            continue
        new = webhook_url(action)
        old = stored[spec.id] if spec.id is not None else None
        if spec.id is None or old is None:
            loose.append(new)
            continue
        claimed.add(spec.id)
        if not has_credentials(old):
            continue
        if new != old:
            changed[place] = spec.id, _fate(old, new)
            continue
        if spec.id in holders:
            message = (
                f"Nothing was changed: actions {holders[spec.id]}, {place} have the id {spec.id}, "
                "whose stored webhook credentials belong to one action."
            )
            raise Validation(message, hint="Give each action the id it has in get_workflow.")
        holders[spec.id] = place
        if spec.webhook.url != old:
            kept.append(place)
    gone = [u for n, u in stored.items() if u and n not in claimed and has_credentials(u)]
    gone = [u for u in gone if loose and u not in loose]
    which = "the" if all(_certain(u) for u in gone) else "any"
    return (
        kept,
        {place: fate for place, (held, fate) in changed.items() if held not in holders},
        f"{which} stored credentials of {plural(len(gone), 'webhook URL')}" if gone else "",
    )


def copied_places(
    specs: Sequence[ActionInput],
    actions: Sequence[WorkflowAction],
    workflows: Sequence[Workflow],
    found: Workflow | None = None,
) -> list[int]:
    """The places of new webhook URLs that a workflow of ``workflows`` stores with credentials.

    ``found`` is the workflow that is written, if there is one yet. The credentials themselves are
    never taken from another workflow.
    """
    stored = [webhook_url(action) for workflow in workflows for action in workflow.actions]
    held = {a.id: webhook_url(a) for a in found.actions if a.id is not None} if found else {}
    places: list[int] = []
    for place, (spec, action) in enumerate(zip(specs, actions, strict=True), start=1):
        url = webhook_url(action)
        given = spec.webhook.url if spec.webhook else None
        if url is None or url != given or (spec.id is not None and held.get(spec.id) == url):
            continue
        if any(has_credentials(other) and _shown_as(url, other) for other in stored):
            places.append(place)
    return places


NOT_COPIED: Final = "without the credentials that another workflow stores for this URL"
COPY_NOTE: Final = f"Written {NOT_COPIED} (never copied)"


def credentials_said(lost: Mapping[int, str], copies: Sequence[int]) -> dict[int, str]:
    """What a confirmation says of the stored credentials of webhooks, by place."""
    said = dict.fromkeys(copies, f", {NOT_COPIED}")
    for place, fate in lost.items():
        said[place] = f"{said.get(place, '')}, and {fate} of the stored URL (not shown)"
    return said


def place_notes(done: str, places: Sequence[int]) -> list[str]:
    """A sentence on the webhook URLs of the actions at ``places``, if there are any."""
    if not places:
        return []
    noun = "action" if len(places) == 1 else "actions"
    return [f"{done}: the webhook URL of {noun} {', '.join(map(str, places))}."]


def hidden_lost(
    old: WorkflowAction, new: WorkflowAction | None, *, credentials: bool = True
) -> list[str]:
    """What ``old`` stores hidden and ``new``, which takes its place, does not keep.

    Without ``credentials`` those of a URL are left out: they are told otherwise.
    """
    lost: list[str] = []
    hook = as_object(old.settings.get("webhook"))
    after = as_object(new.settings.get("webhook") if new else None)
    kept = as_object(after.get("headers"))
    headers = as_object(hook.get("headers"))
    gone = [quoted(name) for name, value in headers.items() if kept.get(name) != value]
    if gone:
        lost.append(f"the {'value' if len(gone) == 1 else 'values'} of {', '.join(gone)}")
    url = hook.get("url")
    if credentials and isinstance(url, str) and has_credentials(url) and after.get("url") != url:
        lost.append(f"{'the' if _certain(url) else 'any'} credentials of its URL")
    passwords = old.settings.get("passwords")
    if not is_empty(passwords) and (new is None or new.settings.get("passwords") != passwords):
        lost.append("its passwords")
    return lost


def action_label(action: WorkflowAction) -> str:
    """The type and id of an action, for a message."""
    return f"{ACTION_TYPES.get(action.type, f'type {action.type}')} action {action.id}"


def losses_of(
    specs: Sequence[ActionInput],
    written: Sequence[WorkflowAction],
    found: Workflow,
    *,
    told: Collection[int | None],
    others: Sequence[Workflow],
) -> list[str]:
    """What a new list of actions does not keep of the hidden values of ``found``.

    Left out are the credentials in the URLs of the actions ``told`` otherwise, and the actions that
    one of the workflows ``others`` holds, too.
    """
    elsewhere = {a.id for w in others if w.id != found.id for a in w.actions}
    lines: list[str] = []
    for old in found.actions:
        if old.id in elsewhere:
            continue
        credentials = old.id not in told
        claimed = [new for spec, new in zip(specs, written, strict=True) if spec.id == old.id]
        rows = [hidden_lost(old, new, credentials=credentials) for new in claimed or written]
        lost = min(rows, key=len, default=hidden_lost(old, None, credentials=credentials))
        if lost:
            lines.append(f"of the {action_label(old)} {', '.join(lost)}")
    return lines


def _told(
    specs: Sequence[ActionInput], lost: Mapping[int, str], gone: str, found: Workflow
) -> set[int | None]:
    """The stored actions whose URL credentials a confirmation speaks of by place or number."""
    told = {specs[place - 1].id for place in lost}
    if gone:
        # A webhook that is claimed is kept, or in lost.
        told |= {action.id for action in found.actions if webhook_url(action)}
    return told


def losing(losses: Sequence[str]) -> str:
    """The clause of a confirmation that says which hidden values are lost."""
    return f"lose for good what is stored hidden: {'; '.join(losses)}"


# Most recipients and webhook hosts of one workflow a confirmation lists; with more, nobody reads
# them. The same number as the recipients of one e-mail (services/sharing.py).
MAX_DESTINATIONS: Final = 20


def _few(count: int) -> int:
    """``count`` destinations, if a user can be expected to read them.

    Raises:
        Validation: There are more than ``MAX_DESTINATIONS``.
    """
    if count > MAX_DESTINATIONS:
        message = (
            f"Nothing was changed: the workflow has {count} destinations or more; at most "
            f"{MAX_DESTINATIONS} can be shown to the user to confirm."
        )
        raise Validation(message, hint="Use a mailing list, or the Paperless web UI.")
    return count


def _post(settings: Mapping[str, JsonValue], said: str) -> str:
    """What a webhook does, whether it has credentials, and what else is ``said`` of them.

    Raises:
        Validation: It is not certain where it goes, or it cannot be shown in full.
    """
    hook = as_object(settings.get("webhook"))
    url = str(hook.get("url"))
    text = f"send a POST request to {_webhook(url)}"
    if hook.get("include_document"):
        text += " with the document"
    if has_credentials(url):
        told = "with" if _certain(url) else "with, or possibly with,"
        text += f", {told} credentials (not shown)"
    return text + said


def far_acts(
    actions: Sequence[WorkflowAction], said: Mapping[int, str] | None = None, gone: str = ""
) -> list[str]:
    """What the actions do that reaches beyond tagging: the reason a workflow needs confirm.

    ``said`` holds by place what becomes of the stored credentials of a webhook, ``gone`` those
    that no action keeps.

    Raises:
        Validation: There are more destinations than ``MAX_DESTINATIONS``, or one that cannot be
            shown in full.
    """
    found: list[str] = []
    reached = 0
    for place, action in enumerate(actions, start=1):
        settings = action.settings
        if action.type in FIXED_ACTS:
            found.append(FIXED_ACTS[action.type])
        elif action.type not in ACTION_TYPES:
            found.append(f"run an action of type {action.type}")
        elif action.type == EMAIL:
            mail = as_object(settings.get("email"))
            attached = " with the document attached" if mail.get("include_document") else ""
            to = [part.strip() for part in str(mail.get("to")).split(",")]
            reached = _few(reached + len(to))
            shown = ", ".join(destination(address, "e-mail address", "email") for address in to)
            found.append(f"send an e-mail to {shown}{attached}")
        elif action.type == WEBHOOK:
            reached = _few(reached + 1)
            found.append(_post(settings, (said or {}).get(place, "")))
        elif action.type == 2:  # noqa: PLR2004 -- removal
            removed = [
                name.removeprefix("remove_").replace("_", " ")
                for name in _set(ACTION_FIELDS[2], settings)
            ]
            if removed:
                found.append(f"remove from the matching documents: {', '.join(removed)}")
        elif action.type == 1 and any(not is_empty(settings.get(n)) for n in OWNERSHIP):
            found.append("change who owns or may see the matching documents")
    if gone:
        found.append(f"remove {gone} that no action keeps (not shown)")
    return found


def check_match(settings: Mapping[str, JsonValue]) -> None:
    """Refuse a content filter Paperless ignores.

    Raises:
        Validation: There is a match, and no algorithm that applies it.
    """
    if not is_empty(settings.get("match")) and not settings.get("matching_algorithm"):
        message = "A match needs a matching_algorithm other than none to restrict anything."
        hint = "Use any, all, exact, regex or fuzzy; with none Paperless ignores the match."
        raise Validation(message, hint=hint)


def trigger_filters(trigger: WorkflowTrigger) -> list[str]:
    """The filters of a trigger that limit it."""
    found = list(_set(TRIGGER_FIELDS, trigger.settings))
    if not trigger.settings.get("matching_algorithm") and "match" in found:
        found.remove("match")  # Paperless ignores it
    return [n for n in found if n in MANUAL_FILTERS] if trigger.type == MANUAL else found


def trigger_text(trigger: WorkflowTrigger) -> str:
    """The type of a trigger and which filters it has."""
    text = TRIGGER_TYPES.get(trigger.type, f"unknown type {trigger.type}")
    filters = ", ".join(trigger_filters(trigger))
    if filters:
        text += f", filtered by {filters}"
    if trigger.type == SCHEDULED:
        text += ", also on documents that already exist"
    return text


def unfiltered(triggers: Sequence[WorkflowTrigger]) -> bool:
    """Whether a manual trigger has no filters, so that every document matches."""
    return any(not trigger_filters(t) for t in triggers if t.type == MANUAL)


def workflow_effect(lead: str, triggers: Sequence[WorkflowTrigger], acts: Sequence[str]) -> str:
    """What an enabled workflow does: by itself, or only when a user runs it (manual triggers)."""
    automatic = "; ".join(trigger_text(t) for t in triggers if t.type != MANUAL)
    by_hand = "; ".join(trigger_text(t) for t in triggers if t.type == MANUAL)
    will = "; ".join(acts)
    if not triggers:
        automatic = "no trigger yet: it does nothing until one is added"
    if not by_hand:
        return (
            f"{lead}, which is enabled and runs by itself on every document that matches its "
            f"triggers ({automatic}) and will: {will}"
        )
    if not automatic:
        return (
            f"{lead}, which is enabled and runs only when a user runs it by hand ({by_hand}), "
            f"possibly on documents its trigger filters do not match, and will: {will}"
        )
    return (
        f"{lead}, which is enabled and runs by itself on every document that matches its "
        f"triggers ({automatic}); when a user runs it by hand ({by_hand}) it may also run on "
        f"documents its filters do not match; it will: {will}"
    )


class WorkflowService:
    """Reads and writes the workflows of Paperless, with triggers and actions."""

    def __init__(
        self, reader: WorkflowReader, writer: WorkflowWriter, taxonomy: TaxonomyResolver
    ) -> None:
        """Read through ``reader``, write through ``writer``, look names up with ``taxonomy``."""
        self._reader: WorkflowReader = reader
        self._writer: WorkflowWriter = writer
        self._taxonomy: TaxonomyResolver = taxonomy

    async def list(
        self,
        *,
        enabled: bool | None = None,
        trigger: TriggerTypeName | None = None,
        uses: Usage | None = None,
    ) -> WorkflowListing:
        """The workflows, with their triggers and actions.

        ``enabled`` and ``trigger`` limit them to (not) enabled ones and to those with such a
        trigger, ``uses`` to those that use an object.

        Raises:
            NotFound: No object has the name in ``uses``; the hint lists the closest names, or
                the id of an object whose name is made of digits.
            Ambiguous: The name matches several objects.
            Validation: A mail rule is given by a name.
            PermissionDenied: The user may not view workflows, or the named object's kind.
        """
        wanted = None if uses is None else await self._used(uses)
        workflows = await self._reader.list_workflows(
            enabled=enabled, trigger_type=None if trigger is None else trigger_type(trigger)
        )
        found: dict[int, tuple[PartUse, ...]] = {}
        note = None
        if uses is not None and wanted is not None:
            number, name = wanted
            found = {w.id: workflow_uses(w, uses.kind, number, name) for w in workflows}
            workflows = [w for w in workflows if found[w.id]]
            if uses.kind != "mail_rule" and name is None:
                label = uses.kind.replace("_", " ")
                note = f"You see no {label} with the id {number}: it was searched by its id only"
                if uses.kind == "custom_field":
                    note += ", so a custom field query that names it is not found"
                note += "."
        names = await self._names(workflows)
        return WorkflowListing(
            count=len(workflows),
            workflows=tuple(
                ListedWorkflow.model_validate(
                    {**self._info(w, names).model_dump(), "used_in": found.get(w.id, ())}
                )
                for w in workflows
            ),
            note=note,
        )

    async def _used(self, uses: Usage) -> tuple[int, str | None]:
        """The id and, if the user sees it, the name of the object in ``uses``."""
        number = _number(uses.value)
        if uses.kind == "mail_rule":
            if number is None:
                message = "A mail rule is given by its id."
                raise Validation(
                    message, hint="Call list_mail_rules to see the ids (toolset mail)."
                )
            return number, None
        try:
            item = await self._taxonomy.resolve(uses.kind, uses.value)
        except PermissionDenied:
            if number is None:
                raise
            return number, None
        except NotFound:
            text = str(uses.value).strip()
            if number is None or text in {i.name for i in await self._taxonomy.items(uses.kind)}:
                raise  # a name of digits: the hint says the id
            return number, None
        return item.id, item.name

    async def read(
        self,
        *,
        workflow: NameOrId | None = None,
        trigger_id: int | None = None,
        action_id: int | None = None,
    ) -> WorkflowRead:
        """A workflow, or a single trigger or action with the workflows that hold it.

        Raises:
            Validation: Not exactly one of the three is given.
            NotFound: No such workflow, trigger or action.
            Ambiguous: The name matches several workflows.
            PermissionDenied: The user may view neither it nor the workflows that could hold it.
        """
        if [workflow, trigger_id, action_id].count(None) != 2:  # noqa: PLR2004 -- one of three
            message = "Give exactly one of workflow, trigger_id and action_id."
            raise Validation(message, hint="Call list_workflows to see names and ids.")
        if workflow is not None:
            return WorkflowRead.model_validate((await self.get(workflow)).model_dump())
        try:
            workflows = await self._reader.list_workflows()
        except PermissionDenied, AnswerTooLarge:
            workflows = None
        if trigger_id is not None:
            holders = _holders(workflows, "triggers", trigger_id)
            trigger = await self._part(
                self._reader.get_trigger,
                "trigger",
                trigger_id,
                None if workflows is None else [t for w in workflows for t in w.triggers],
            )
            names = await self._names_of([trigger], [])
            part = WorkflowPart(trigger=_trigger_info(trigger, names), workflows=holders)
        else:
            number = cast("int", action_id)
            holders = _holders(workflows, "actions", number)
            action = await self._part(
                self._reader.get_action,
                "action",
                number,
                None if workflows is None else [a for w in workflows for a in w.actions],
            )
            names = await self._names_of([], [action])
            part = WorkflowPart(action=_action_info(action, names), workflows=holders)
        return WorkflowRead(part=part)

    @staticmethod
    async def _part[T: (WorkflowTrigger, WorkflowAction)](
        read: Callable[[int], Awaitable[T]], kind: str, number: int, held: Sequence[T] | None
    ) -> T:
        """The part ``read`` gives, or the one of the workflows if the user may not view parts."""
        try:
            return await read(number)
        except PermissionDenied:
            if held is None:
                raise
        for part in held:
            if part.id == number:
                return part
        message = f"No workflow holds the {kind} {number}; you may not view ones outside workflows."
        hint = "Call list_workflows to see the ids of triggers and actions."
        raise NotFound(message, hint=hint)

    async def get(self, workflow: NameOrId) -> WorkflowInfo:
        """The workflow that ``workflow`` (a name or an id) names.

        Raises:
            NotFound: No such workflow; the hint lists the closest names.
            Ambiguous: The name matches several workflows.
            PermissionDenied: The user may not view workflows.
        """
        number = _number(workflow)
        try:
            found = await (
                self.find(workflow) if number is None else self._reader.get_workflow(number)
            )
        except NotFound:
            found = await self.find(workflow)  # it says what there is instead
        return self._info(found, await self._names([found]))

    async def told(
        self, workflow: Workflow, titles: Callable[[Sequence[int]], Awaitable[Mapping[int, str]]]
    ) -> tuple[str, ...]:
        """What a run does to a document, the most far-reaching first, with the values it writes.

        Each clause has the number of its step, because the order decides what is left.

        ``titles`` reads the titles of linked documents the user may see.

        Raises:
            Validation: It has a destination that cannot be shown, or too many of them.
        """
        try:
            _ = far_acts(workflow.actions)  # counts the destinations of all actions together
        except Validation as error:
            message = error.message.replace("Nothing was changed", "Nothing was run", 1)
            raise Validation(message, hint=STORED) from None
        if any("tag" in _kinds(_action_fields(a), a.settings) for a in workflow.actions):
            self._taxonomy.invalidate("tag")  # a new child tag would come off unnamed
        texts = _Texts(
            names=await self._names([workflow]),
            fields=await self._known("custom_field", workflow),
            tags=await self._known("tag", workflow),
        )
        linked = [n for action in workflow.actions for n in texts.linked(action)]
        texts.titles = await titles(linked) if linked else {}
        found: list[tuple[bool, str]] = []
        total = len(workflow.actions)
        for place, action in enumerate(workflow.actions, start=1):
            step = f"step {place} of {total}: "
            found += [(not _far(action), f"{step}{text}") for text in texts.of(action)]
        return tuple(text for _, text in sorted(found, key=lambda row: row[0])) or ("do nothing",)

    async def _known(
        self, kind: TaxonomyKind, workflow: Workflow
    ) -> dict[int, TaxonomyItem] | None:
        """The objects of ``kind`` by id, if an action uses it; None if the user may not list it."""
        if not any(kind in _kinds(_action_fields(a), a.settings) for a in workflow.actions):
            return {}
        try:
            return {item.id: item for item in await self._taxonomy.items(kind)}
        except PermissionDenied:
            return None

    async def create(self, spec: WorkflowCreate, *, confirm: bool) -> WorkflowInfo:
        """Create a workflow; names are resolved and types checked before anything is sent.

        Raises:
            ConfirmationRequired: It is enabled and acts outward or takes things away, and
                ``confirm`` is false. Nothing was changed.
            Validation: The name is empty or taken, or a type, a setting or a value is unusable.
            NotFound: A name matches nothing.
            Ambiguous: A name matches several objects.
            PermissionDenied: The user may not add workflows.
        """
        name = self._name(spec.name)
        workflows = await self._free(name, except_id=None)
        require_writable_triggers(spec.triggers)
        triggers = tuple([await self.build_trigger(t) for t in spec.triggers])
        actions = tuple(
            [await self.build_action(a, (), n) for n, a in enumerate(spec.actions, start=1)]
        )
        # A disabled workflow reaches nobody; it is checked when it is switched on.
        copies = copied_places(spec.actions, actions, workflows)
        acts = far_acts(actions, credentials_said({}, copies)) if spec.enabled else []
        if acts and not confirm:
            ConfirmationRequired.require(
                workflow_effect(f"create the workflow {quoted(name)}", triggers, acts),
                scope=(triggers, actions),
            )
        fields: dict[str, object] = {
            "enabled": spec.enabled,
            "triggers": triggers,
            "actions": actions,
        }
        if spec.order is not None:
            fields["order"] = spec.order
        made = await self._writer.create_workflow(name, WorkflowValues.model_validate(fields))
        return await self.answer(made, place_notes(COPY_NOTE, copies))

    async def update(
        self, workflow: NameOrId, edit: WorkflowEdit, *, confirm: bool
    ) -> WorkflowInfo:
        """Change the named settings of a workflow; new triggers or actions replace all old ones.

        Raises:
            ConfirmationRequired: The change leaves an enabled workflow that acts outward or
                takes things away (by new triggers or actions, or by switching it on), or loses
                hidden values, and ``confirm`` is false. Nothing was changed.
            Validation: Nothing to change, a name that is empty or taken, a setting or value that
                is unusable, a trigger of a type this server does not write (manual), or new
                triggers or actions that would drop ones this server cannot write.
            NotFound: No such workflow, or a name matches nothing.
            Ambiguous: A name matches several workflows or objects.
            PermissionDenied: The user may not change workflows.
        """
        workflows = await self._reader.list_workflows()
        found = self.pick_from(workflows, workflow)
        changes: dict[str, object] = {}
        kept: list[int] = []
        lost: dict[int, str] = {}
        gone = ""
        copies: list[int] = []
        if edit.name is not None:
            changes["name"] = self._name(edit.name)
            _ = await self._free(str(changes["name"]), except_id=found.id)
        if edit.order is not None:
            changes["order"] = edit.order
        if edit.enabled is not None:
            changes["enabled"] = edit.enabled
        if edit.triggers is not None:
            require_writable_triggers(edit.triggers)
            self._keeps_unwritable_triggers(found)
            changes["triggers"] = tuple([await self.build_trigger(t) for t in edit.triggers])
        if edit.actions is not None:
            self._keeps_unwritable(found)
            _check_ids(edit.actions, found)
            written = tuple(
                [
                    await self.build_action(a, found.actions, n)
                    for n, a in enumerate(edit.actions, start=1)
                ]
            )
            kept, lost, gone = credentials_fate(edit.actions, written, found)
            copies = copied_places(edit.actions, written, workflows, found)
            changes["actions"] = written
        if not changes:
            message = "Nothing to change: no setting was named."
            raise Validation(message, hint="Name a new name, order, enabled, triggers or actions.")
        values = WorkflowValues.model_validate(changes)
        triggers = values.triggers if values.triggers is not None else found.triggers
        actions = values.actions if values.actions is not None else found.actions
        enabled = found.enabled if values.enabled is None else values.enabled
        switched_on = values.enabled is True and not found.enabled
        reshaped = values.triggers is not None or values.actions is not None
        # Switching off, renaming and reordering must work whatever the actions hold.
        acts = (
            far_acts(actions, credentials_said(lost, copies), gone)
            if enabled and (reshaped or switched_on)
            else []
        )
        losses = (
            []
            if edit.actions is None
            else losses_of(
                edit.actions,
                actions,
                found,
                told=_told(edit.actions, lost, gone, found) if acts else (),
                others=workflows,
            )
        )
        if (acts or losses) and not confirm:
            lead = f"change the workflow {quoted(found.name)} (id {found.id})"
            if losses:
                lead += f" and {losing(losses)}"
            effect = workflow_effect(lead, triggers, acts) if acts else lead
            ConfirmationRequired.require(effect, scope=(triggers, actions, found.actions))
        done = await self._writer.update_workflow(found.id, values)
        notes = place_notes(COPY_NOTE, copies)
        for fate in dict.fromkeys(lost.values()):
            places = [place for place, what in lost.items() if what == fate]
            notes += place_notes(past_fate(fate), places)
        notes += [f"Removed {gone} that no action kept by its id."] if gone else []
        notes += place_notes("Kept as stored, with any credentials", kept)
        return await self.answer(done, notes)

    async def delete(self, workflow: NameOrId, *, confirm: bool) -> WorkflowDeletion:
        """Delete a workflow for good; without ``confirm`` refuse and say what deleting it does.

        Raises:
            ConfirmationRequired: ``confirm`` is false. Nothing was changed.
            NotFound: No such workflow.
            Ambiguous: The name matches several workflows.
            PermissionDenied: The user may not view or delete workflows.
        """
        found = await self.find(workflow)
        subject = f"the workflow {quoted(found.name)} (id {found.id})"
        if not confirm:
            parts = (
                f"{plural(len(found.triggers), 'trigger')} and "
                f"{plural(len(found.actions), 'action')}"
            )
            effect = (
                f"delete {subject} for good: it stops running at once and its {parts} are lost; "
                "no document is touched"
            )
            ConfirmationRequired.require(effect, scope=found)
        await self._writer.delete_workflow(found.id)
        message = f"{subject[0].upper()}{subject[1:]} was deleted; no document was touched."
        return WorkflowDeletion(id=found.id, name=found.name, message=message)

    async def find(self, workflow: NameOrId) -> Workflow:
        """The workflow as Paperless stores it, like ``get``."""
        return self.pick_from(await self._reader.list_workflows(), workflow)

    @staticmethod
    def pick_from(workflows: Sequence[Workflow], workflow: NameOrId) -> Workflow:
        """The one of ``workflows`` that ``workflow`` names; see ``pick``."""
        rows = [TaxonomyItem(id=w.id, name=w.name, document_count=None) for w in workflows]
        wanted = pick("workflow", rows, workflow, listing="list_workflows").id
        return next(w for w in workflows if w.id == wanted)

    @staticmethod
    def _name(name: str) -> str:
        text = name.strip()
        if not text:
            message = "The workflow name is empty."
            raise Validation(message, hint="Give the workflow a name.")
        return text

    async def _free(self, name: str, *, except_id: int | None) -> Sequence[Workflow]:
        """The workflows; refuse a name another one has (ignoring case): names find workflows."""
        workflows = await self._reader.list_workflows()
        for workflow in workflows:
            if workflow.id != except_id and fold(workflow.name) == fold(name):
                message = f"A workflow is already named {quoted(workflow.name)} (id {workflow.id})."
                raise Validation(message, hint="Choose another name, or update that workflow.")
        return workflows

    @staticmethod
    def _keeps_unwritable_triggers(found: Workflow) -> None:
        """Refuse to replace triggers when that would delete one this server cannot write."""
        kept = [t for t in found.triggers if t.type not in WRITABLE_TRIGGER_TYPES]
        if kept:
            names = ", ".join(
                f"{TRIGGER_TYPES.get(t.type, f'unknown type {t.type}')} (id {t.id})" for t in kept
            )
            message = (
                f"The workflow {quoted(found.name)} holds triggers this server cannot write: "
                f"{names}. Replacing the triggers would delete them."
            )
            raise Validation(
                message,
                hint="Change the other settings, or edit the triggers in the Paperless web UI.",
            )

    @staticmethod
    def _keeps_unwritable(found: Workflow) -> None:
        """Refuse to replace actions when that would delete one this server cannot write."""
        kept = [a for a in found.actions if a.type not in WRITABLE_ACTION_TYPES]
        if kept:
            names = ", ".join(
                f"{ACTION_TYPES.get(a.type, f'unknown type {a.type}')} (id {a.id})" for a in kept
            )
            message = (
                f"The workflow {quoted(found.name)} holds actions this server cannot write: "
                f"{names}. Replacing the actions would delete them."
            )
            raise Validation(
                message,
                hint=("Change the other settings, or edit the actions in the Paperless web UI."),
            )

    async def _id(self, kind: TaxonomyKind, value: NameOrId) -> int:
        return (await self._taxonomy.resolve(kind, value)).id

    async def _ids(self, kind: TaxonomyKind, values: Sequence[NameOrId]) -> list[JsonValue]:
        ids = [await self._id(kind, value) for value in values]
        return [*dict.fromkeys(ids)]

    async def _setting(self, name: str, field: Field_, value: object) -> JsonValue:
        """The stored form of the setting ``name`` given as ``value``; a list without doubles.

        Raises:
            Validation: A text of a trigger is blank: Paperless would store no filter.
        """
        if name in TRIGGER_FIELDS and isinstance(value, str) and value and not value.strip():
            message = f"Nothing was changed: {name!r} is blank."
            raise Validation(message, hint="Leave it out, or give what it should match.")
        if name == "sources":
            sources = (_lookup(SOURCES, "source", s) for s in cast("list[int | str]", value))
            return [*dict.fromkeys(sources)]
        if name == "matching_algorithm":
            return _lookup(MATCHING_ALGORITHMS, "matching algorithm", cast("int | str", value))
        if name in TEXT_CHECKS:
            return TEXT_CHECKS[name](cast("str", value))
        if field.kind is not None and field.shape == "list":
            return await self._ids(field.kind, cast("list[NameOrId]", value))
        if field.kind is not None:
            return await self._id(field.kind, cast("NameOrId", value))
        return cast("JsonValue", value)

    async def build_trigger(self, spec: TriggerInput, *, change: bool = False) -> WorkflowTrigger:
        """The trigger to write, with the settings ``spec`` gives.

        With ``change`` they join those of a stored one, and ``check_match`` is left to the caller.
        """
        number = trigger_type(spec.type)
        label = TRIGGER_TYPES[number]
        settings: dict[str, JsonValue] = {}
        for name, field in TRIGGER_FIELDS.items():
            value = cast("object", getattr(spec, name))
            if value is None:
                continue
            if name in CONSUMPTION_ONLY and number != CONSUMPTION:
                message = f"{name!r} only applies to consumption triggers, not to {label} triggers."
                raise Validation(message, hint="Leave it out, or use a consumption trigger.")
            if name in SCHEDULED_ONLY and number != SCHEDULED:
                message = f"{name!r} only applies to scheduled triggers, not to {label} triggers."
                raise Validation(message, hint="Leave it out, or use a scheduled trigger.")
            if number == CONSUMPTION and name not in CONSUMPTION_ONLY:
                message = (
                    f"{name!r} does not apply to consumption triggers: no document exists yet."
                )
                raise Validation(message, hint="Use a document added or updated trigger.")
            settings[field.wire] = await self._setting(name, field, value)
        if not change:
            check_match(settings)
        return WorkflowTrigger(type=number, settings=settings)

    async def build_action(
        self, spec: ActionInput, stored: Sequence[WorkflowAction], place: int
    ) -> WorkflowAction:
        """The action to write at ``place``; ``stored`` are the ones there are now."""
        number = action_type(spec.type)
        settings: dict[str, JsonValue] = {}
        for owner, fields in ACTION_FIELDS.items():
            for name, field in fields.items():
                value = cast("object", getattr(spec, name))
                if value is None:
                    continue
                if owner != number:
                    message = (
                        f"{name!r} belongs to {ACTION_TYPES[owner]} actions, not to "
                        f"{ACTION_TYPES[number]} actions."
                    )
                    raise Validation(message, hint="Leave it out, or change the action type.")
                settings[field.wire] = await self._action_setting(
                    name, field, value, spec, stored=stored, place=place
                )
        if number == EMAIL and "email" not in settings:
            message = "An email action needs email settings."
            raise Validation(message, hint="Pass email with subject, body and to.")
        if number == WEBHOOK and "webhook" not in settings:
            message = "A webhook action needs webhook settings."
            raise Validation(message, hint="Pass webhook with a url.")
        values = as_object(settings.get("assign_custom_fields_values"))
        if values:
            held = cast("list[JsonValue]", settings.get("assign_custom_fields", []))
            settings["assign_custom_fields"] = [
                *held,
                *(int(k) for k in values if int(k) not in held),
            ]
        return WorkflowAction(type=number, settings=settings)

    async def _action_setting(  # noqa: PLR0913 -- the setting and its action
        self,
        name: str,
        field: Field_,
        value: object,
        spec: ActionInput,
        *,
        stored: Sequence[WorkflowAction],
        place: int,
    ) -> JsonValue:
        if name == "email":
            return cast("JsonValue", spec.email.model_dump() if spec.email else None)
        if name == "webhook" and spec.webhook is not None:
            hook = spec.webhook.model_dump()
            held = next((a for a in stored if a.id is not None and a.id == spec.id), None)
            hook["url"] = _kept_or_given(
                spec.webhook.url, webhook_url(held), [webhook_url(a) for a in stored], place
            )
            old = as_object(held.settings.get("webhook") if held else None)
            kept = as_object(old.get("headers"))
            moved = _origin(old.get("url")) != _origin(hook["url"])
            hook["headers"] = self._headers(spec.webhook.headers, kept, moved=moved)
            return cast("JsonValue", hook)
        if name == "assign_custom_fields_values":
            fields = cast("dict[str, JsonValue]", value)
            return {str(await self._id("custom_field", k)): v for k, v in fields.items()}
        return await self._setting(name, field, value)

    @staticmethod
    def _headers(
        given: Mapping[str, str] | None, kept: Mapping[str, JsonValue], *, moved: bool
    ) -> dict[str, JsonValue] | None:
        """The headers to store: a hidden value stands for the one already stored.

        A stored value is never carried to another scheme, host or port (``moved``): it could be
        a credential, and the change would send it there without anyone seeing it.
        """
        if given is None:
            return None
        headers: dict[str, JsonValue] = {}
        for header, value in given.items():
            if value != HIDDEN:
                headers[header] = value
            elif header in kept and moved:
                message = (
                    f"Header {header!r} cannot keep its stored value: the URL now points to "
                    "another scheme, host or port than the stored one."
                )
                raise Validation(message, hint="Send the real value of the header for the new URL.")
            elif header in kept:
                headers[header] = kept[header]
            else:
                message = (
                    f"Header {header!r} is shown as {HIDDEN!r} and has no stored value to keep."
                )
                raise Validation(
                    message,
                    hint="Send the real value, or the id of the action that holds the stored one.",
                )
        return headers

    async def _names(self, workflows: Sequence[Workflow]) -> dict[TaxonomyKind, dict[int, str]]:
        """The names of the objects the workflows refer to, for the kinds they use."""
        return await self._names_of(
            [t for w in workflows for t in w.triggers], [a for w in workflows for a in w.actions]
        )

    async def _names_of(
        self, triggers: Sequence[WorkflowTrigger], actions: Sequence[WorkflowAction]
    ) -> dict[TaxonomyKind, dict[int, str]]:
        used: set[TaxonomyKind] = set()
        for trigger in triggers:
            used |= _kinds(TRIGGER_FIELDS, trigger.settings)
        for action in actions:
            used |= _kinds(_action_fields(action), action.settings)
        names: dict[TaxonomyKind, dict[int, str]] = {}
        for kind in used:
            try:
                names[kind] = {item.id: item.name for item in await self._taxonomy.items(kind)}
            except PermissionDenied:
                names[kind] = {}
        return names

    async def answer(self, workflow: Workflow, notes: Sequence[str] = ()) -> WorkflowInfo:
        """The workflow as create and update answer it: with notes and the way back from trash."""
        info = self._info(workflow, await self._names([workflow]))
        trashes = any(a.type == TRASH_ACTION for a in workflow.actions)
        note = " ".join([*notes, *([TRASH_NOTE] if trashes else [])])
        return info.model_copy(update={"note": note or None})

    @staticmethod
    def _info(workflow: Workflow, names: Mapping[TaxonomyKind, Mapping[int, str]]) -> WorkflowInfo:
        return WorkflowInfo(
            id=workflow.id,
            name=workflow.name,
            order=workflow.order,
            enabled=workflow.enabled,
            triggers=tuple(_trigger_info(t, names) for t in workflow.triggers),
            actions=tuple(_action_info(a, names) for a in workflow.actions),
        )


def _number(value: NameOrId) -> int | None:
    """The id that ``value`` is, if it is one (as ``pick`` reads it)."""
    text = str(value).strip()
    return int(text) if text.isascii() and text.isdigit() else None


def _holders(
    workflows: Sequence[Workflow] | None, name: Literal["triggers", "actions"], number: int
) -> tuple[WorkflowHolder, ...] | None:
    """The workflows that hold a part; None if the user may not view workflows."""
    if workflows is None:
        return None
    return tuple(
        WorkflowHolder(id=w.id, name=w.name, enabled=w.enabled)
        for w in workflows
        if any(part.id == number for part in (w.triggers if name == "triggers" else w.actions))
    )


def _trigger_info(
    trigger: WorkflowTrigger, names: Mapping[TaxonomyKind, Mapping[int, str]]
) -> TriggerInfo:
    settings = {
        n: _show(n, TRIGGER_FIELDS[n], v, names)
        for n, v in _set(TRIGGER_FIELDS, trigger.settings).items()
    }
    label = TRIGGER_TYPES.get(trigger.type, f"unknown type {trigger.type}")
    return TriggerInfo(id=trigger.id, type=trigger.type, type_name=label, settings=settings)


def _action_info(
    action: WorkflowAction, names: Mapping[TaxonomyKind, Mapping[int, str]]
) -> ActionInfo:
    fields = _action_fields(action)
    settings = {n: _show(n, fields[n], v, names) for n, v in _set(fields, action.settings).items()}
    settings.update(_read_only(action))
    return ActionInfo(
        id=action.id,
        type=action.type,
        type_name=ACTION_TYPES.get(action.type, f"unknown type {action.type}"),
        settings=settings,
        shown_in_part=("webhook.url",) if _hides(webhook_url(action)) else (),
    )


def _kinds(fields: Mapping[str, Field_], settings: Mapping[str, JsonValue]) -> set[TaxonomyKind]:
    """The taxonomy kinds that the settings which are set refer to."""
    found: set[TaxonomyKind] = set()
    for name in _set(fields, settings):
        kind = fields[name].kind
        if kind is not None:
            found.add(kind)
    return found


def _read_only(action: WorkflowAction) -> dict[str, JsonValue]:
    """Settings of action types that a tool reads but never writes."""
    settings = action.settings
    if action.type == 5:  # noqa: PLR2004 -- password removal
        return {"passwords": HIDDEN} if not is_empty(settings.get("passwords")) else {}
    if action.type == 8:  # noqa: PLR2004 -- apply AI suggestions
        names = ("ai_suggestion_fields", "ai_create_missing", "ai_overwrite_existing")
        return {n: settings[n] for n in names if n in settings and not is_empty(settings[n])}
    return {}


def _nested(value: JsonValue) -> dict[str, JsonValue]:
    """An e-mail or webhook as shown: no header values, no credentials in the URL, what is set."""
    shown: dict[str, JsonValue] = {k: v for k, v in as_object(value).items() if v is not None}
    if isinstance(shown.get("url"), str):
        shown["url"] = shown_url(str(shown["url"]))
    if "headers" in shown:
        shown["headers"] = dict.fromkeys(as_object(shown["headers"]), HIDDEN)
    return shown


def _far(action: WorkflowAction) -> bool:
    """Whether an action does more than file: sends, trashes, asks the AI, changes rights."""
    if action.type in {ASSIGNMENT, REMOVAL}:
        fields = _set(_action_fields(action), action.settings)
        return any(re.search("owner|permissions|_users|_groups", name) for name in fields)
    return action.type != REMOTE_OCR


def _ids(value: JsonValue) -> list[int]:
    return [n for n in value if isinstance(n, int)] if isinstance(value, list) else []


@dataclasses.dataclass(slots=True)
class _Texts:
    """Words for the actions of a run; every name and text is quoted and cut."""

    names: Mapping[TaxonomyKind, Mapping[int, str]]
    # None where the user may not list them.
    fields: Mapping[int, TaxonomyItem] | None
    tags: Mapping[int, TaxonomyItem] | None
    titles: Mapping[int, str] = dataclasses.field(default_factory=dict[int, str])

    def of(self, action: WorkflowAction) -> list[str]:
        """What the action does, in one or two clauses."""
        if action.type == REMOTE_OCR:
            return [NO_OCR]
        if action.type == TRASH_ACTION:
            return [TRASHES]
        if action.type == SUGGEST:
            return [_suggestions(action.settings)]
        if action.type not in {ASSIGNMENT, REMOVAL}:
            return far_acts([action])
        values = self._values(action)
        verb = "assign" if action.type == ASSIGNMENT else "remove"
        told = [f"{verb} {values}"] if values else []
        return [*far_acts([action]), *told] if action.type == ASSIGNMENT else told

    def linked(self, action: WorkflowAction) -> list[int]:
        """The documents an assignment links to."""
        values = as_object(action.settings.get("assign_custom_fields_values"))
        fields = _ids(action.settings.get("assign_custom_fields"))
        return [n for at in fields if self._links(at) for n in _ids(values.get(str(at)))]

    def _links(self, number: int) -> bool:
        item = (self.fields or {}).get(number)
        return item is not None and item.data_type == "documentlink"

    def _name(self, kind: TaxonomyKind | None, number: int) -> str:
        name = self.names.get(kind, {}).get(number) if kind is not None else None
        return f"#{number}" if name is None else quoted(name)

    def _values(self, action: WorkflowAction) -> str:
        found: list[str] = []
        for name, value in _set(ACTION_FIELDS[action.type], action.settings).items():
            setting = ACTION_FIELDS[action.type][name]
            if name == "assign_custom_fields_values":
                continue  # Paperless writes those of assign_custom_fields only
            what = name.removeprefix("assign_").removeprefix("remove_").replace("_", " ")
            if name == "assign_custom_fields":
                values = as_object(action.settings.get("assign_custom_fields_values"))
                what += f" {', '.join(self._field(n, values.get(str(n))) for n in _ids(value))}"
            elif setting.shape == "list":
                what += f" {', '.join(self._name(setting.kind, n) for n in _ids(value))}"
                what += self._along(action, _ids(value)) if setting.kind == "tag" else ""
            elif isinstance(value, int) and setting.shape == "one":
                what += f" {self._name(setting.kind, value)}"
            elif setting.shape != "flag":
                what += f" {quoted(str(value))}"
            found.append(what)
        return ", ".join(found)

    def _field(self, number: int, value: JsonValue) -> str:
        name = self._name("custom_field", number)
        if value in (None, ""):
            return f"{name} (without a value, where it is missing)"
        if self._links(number):
            rows = [
                f"{n} {quoted(self.titles[n])}" if n in self.titles else f"#{n}"
                for n in _ids(value)
            ]
            return f"{name}: {', '.join(rows) or 'no document'}"
        shown = readable(value, (self.fields or {}).get(number))
        return f"{name}: {quoted(shown if isinstance(shown, str) else json.dumps(shown))}"

    def _along(self, action: WorkflowAction, named: list[int]) -> str:
        """The tags Paperless adds or removes with those that are named."""
        adds = action.type == ASSIGNMENT
        which = "parent" if adds else "child"
        if self.tags is None:
            return f" (with their {which} tags, which you may not list)"
        parents = {number: tag.parent for number, tag in self.tags.items()}
        if adds:
            others = [n for n in close_over_ancestors(named, parents) if n not in named]
        else:
            others = sorted({n for tag in named for n in descendants(tag, parents)} - set(named))
        listed = ", ".join(self._name("tag", n) for n in others)
        if adds:
            return f" (with their parent tags {listed})" if listed else ""
        if listed:
            return f" (with their child tags {listed} and any you cannot see)"
        return " (with any child tags you cannot see)"


def _suggestions(settings: Mapping[str, JsonValue]) -> str:
    """What an action that applies AI suggestions may do."""
    given = settings.get("ai_suggestion_fields")
    fields = [str(name) for name in given] if isinstance(given, list) else []
    if not fields:
        return "apply AI suggestions to no field (Paperless skips it)"
    told = ", ".join(
        name.replace("_", " ") if name in AI_FIELDS else quoted(name) for name in fields
    )
    over = "only where there is no value"
    if settings.get("ai_overwrite_existing"):
        over = "over existing values"
    if "tags" in fields:
        over += " (tags are only added)"
    makes = (
        "creating tags, correspondents and document types that do not exist"
        if settings.get("ai_create_missing")
        else "without creating anything"
    )
    return (
        "ask the AI backend about the text of each of them and apply what it suggests for "
        f"{told}, {over}, {makes}"
    )


def _show(
    name: str, field: Field_, value: JsonValue, names: Mapping[TaxonomyKind, Mapping[int, str]]
) -> JsonValue:
    """A setting as tools show it: ids as names where known, labels for the numbers."""

    def named(item: JsonValue) -> JsonValue:
        known: Mapping[int, str] = names.get(field.kind, {}) if field.kind else {}
        return known.get(item, item) if isinstance(item, int) else item

    if name == "sources":
        return [
            SOURCES.get(s, s) if isinstance(s, int) else s for s in cast("list[JsonValue]", value)
        ]
    if name == "matching_algorithm":
        return MATCHING_ALGORITHMS.get(cast("int", value), value)
    if name == "assign_custom_fields_values":
        known = names.get("custom_field", {})
        return {
            known.get(int(k), k) if k.isascii() and k.isdigit() else k: v
            for k, v in as_object(value).items()
        }
    if name in {"email", "webhook"}:
        return _nested(value)
    if field.kind is not None and field.shape == "list":
        return [named(item) for item in cast("list[JsonValue]", value)]
    return named(value) if field.kind is not None else value
