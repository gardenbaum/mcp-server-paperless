"""The application configuration, the logs, background tasks and bulk download (decision-26).

Every change of the configuration needs ``confirm``: it changes how every user's documents are
read, which AI service sees their text and where, and the refusal lists each setting with its old
and new value (decision-15). API keys are write-only (decision-18): they go in as ``SecretStr``
and nothing returned says more than that one is saved. Starting a task needs ``confirm`` when it
retrains what automatic matching uses or sends every document to an AI service; a sanity check
only reads. Acknowledging a task hides it from the list of new ones and needs none. A bulk
download is a read: the archive is attached only within the download limit.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Final

from mcp_server_paperless.domain.admin import (
    CONFIG_OUTBOUND,
    CONFIG_READ_ONLY,
    CONFIG_SECRETS,
    CONFIG_SETTINGS,
    Acknowledged,
    AppConfig,
    ArchiveDelivery,
    ArchiveReport,
    ConfigOutcome,
    ConfigValues,
    LogExcerpt,
    LogList,
    NotText,
    StartedTask,
    TaskList,
    check_secret,
)
from mcp_server_paperless.domain.errors import ConfirmationRequired, Validation, quoted
from mcp_server_paperless.domain.files import ArchivePartReport, FilePart
from mcp_server_paperless.services.transfer import (
    APPEND,
    MAX_SECONDS,
    PART_WAIT_SECONDS,
    changed,
    check_part,
    read_all,
    time_limit,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue, SecretStr

    from mcp_server_paperless.domain.admin import (
        AppConfigRecord,
        ArchiveContent,
        Compression,
        RunnableTask,
        TaskType,
    )
    from mcp_server_paperless.domain.ports import AdminReader, AdminWriter
    from mcp_server_paperless.domain.tasks import TaskState

DEFAULT_LOG_LINES: Final = 100
MAX_LOG_LINES: Final = 1000
MAX_LOG_CHARS: Final = 20_000
MAX_DOCUMENTS: Final = 100
MAX_TASKS: Final = 100
ARCHIVE_NAME: Final = "paperless-documents.zip"
ZIP: Final = "application/zip"
# The name Paperless gives the archive, for one that is read in parts or saved.
SAVED_NAME: Final = "documents.zip"
# Settings a confirmation names in full; the rest are counted.
SHOWN: Final = 5
# The longest value a confirmation shows; `quoted` cuts text at the same 60 characters.
VALUE_LIMIT: Final = 60
TASK_EFFECTS: Final[dict[str, str]] = {
    "train_classifier": (
        "train the classifier again: it learns from the documents' current tags and metadata "
        "and replaces the model that automatic matching uses for new documents"
    ),
    "llm_index": (
        "rebuild the AI index, which sends the text of every document to the embedding service "
        "Paperless is configured with, at that service's cost and for a long time"
    ),
}


def _shown(value: JsonValue) -> str:
    """A setting's value for a message: text quoted, anything else as JSON, cut short."""
    if isinstance(value, str):
        return quoted(value)
    text = json.dumps(value)
    return text if len(text) <= VALUE_LIMIT else text[: VALUE_LIMIT - 1] + "…"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _config(record: AppConfigRecord) -> AppConfig:
    return AppConfig(
        settings=record.settings,
        keys_configured=record.keys_configured,
        externally_configured_variables=record.externally_configured_variables,
    )


def _check_ids(document_ids: Sequence[int]) -> None:
    if not 1 <= len(document_ids) <= MAX_DOCUMENTS or len(set(document_ids)) != len(document_ids):
        message = f"Give between 1 and {MAX_DOCUMENTS} document ids, each once."
        raise Validation(message, hint="Find ids with search_documents.")


class SystemAdmin:
    """Configuration, logs, tasks and bulk download."""

    def __init__(self, reader: AdminReader, writer: AdminWriter, *, max_bytes: int) -> None:
        """Read and write through the ports; archives over ``max_bytes`` bytes are not attached."""
        self._reader: AdminReader = reader
        self._writer: AdminWriter = writer
        self._max_bytes: int = max_bytes

    async def get_config(self) -> AppConfig:
        """The settings that have a value and which API keys are saved, never a key.

        Raises:
            PaperlessMcpError: Paperless has no configuration row.
        """
        return _config(await self._reader.get_config())

    @staticmethod
    def _check_names(settings: Mapping[str, JsonValue]) -> None:
        problems: list[str] = []
        for name in settings:
            if name in CONFIG_SECRETS:
                problems.append(f"{name} (pass it as the key of the same name)")
            elif name in CONFIG_READ_ONLY:
                problems.append(f"{name} (a file upload: change it in the web UI)")
            elif name not in CONFIG_SETTINGS:
                problems.append(name)
        if problems:
            message = f"These cannot be changed as settings: {', '.join(problems)}."
            raise Validation(
                message, hint="The description of update_app_config lists the settings."
            )

    @staticmethod
    def _check_keys(keys: Mapping[str, SecretStr]) -> None:
        for name, secret in keys.items():
            if name not in CONFIG_SECRETS:
                message = f"{name} is not an API key setting."
                raise Validation(message, hint=f"The keys are {', '.join(CONFIG_SECRETS)}.")
            if isinstance(secret, NotText):
                message = f"The {name} must be text."
                raise Validation(message, hint="Pass the key as a string.")
            if not secret.get_secret_value().strip():
                message = f"The {name} must not be empty."
                raise Validation(message, hint="Pass the key, or leave it out.")
            check_secret(secret.get_secret_value(), name)

    async def update_config(
        self,
        settings: Mapping[str, JsonValue] | None = None,
        *,
        keys: Mapping[str, SecretStr] | None = None,
        confirm: bool = False,
    ) -> ConfigOutcome:
        """Change settings (``None`` resets one) and API keys; any change needs ``confirm``.

        Raises:
            ConfirmationRequired: Something would change and ``confirm`` is not set.
            Validation: Nothing given, a name that is no setting, a bad key, or a value
                Paperless rejects.
            PermissionDenied: The user may not change the configuration.
        """
        wanted = dict(settings or {})
        secrets = dict(keys or {})
        if not wanted and not secrets:
            message = "Nothing to change: no setting and no key was given."
            raise Validation(message, hint="Pass settings, or a key such as llm_api_key.")
        self._check_names(wanted)
        self._check_keys(secrets)
        current = await self._reader.get_config()
        effects: list[str] = []
        changes: dict[str, JsonValue] = {}
        for name, value in wanted.items():
            now = current.settings.get(name)
            if value == now:
                continue
            changes[name] = value
            if value is None:
                effects.append(f"reset {name} (now {_shown(now)}) to the server's environment")
            elif now is None:
                effects.append(
                    f"set {name} to {_shown(value)} (it follows the server's environment now)"
                )
            else:
                effects.append(f"change {name} from {_shown(now)} to {_shown(value)}")
        effects.extend(
            f"save the {name} you gave (its value is never shown)"
            if name not in current.keys_configured
            else f"replace the saved {name} (its value is never shown)"
            for name in secrets
        )
        if not effects:
            return ConfigOutcome(
                config=_config(current),
                changed=(),
                message="Nothing changed: the configuration already is as asked.",
            )
        if not confirm:
            raise ConfirmationRequired.for_effect(self._effect(effects, [*changes, *secrets]))
        written = await self._writer.update_config(ConfigValues(settings=changes, secrets=secrets))
        done = (*changes, *secrets)
        return ConfigOutcome(
            config=_config(written),
            changed=done,
            message=f"Changed {_plural(len(done), 'setting')}: {', '.join(done)}.",
        )

    @staticmethod
    def _effect(effects: list[str], names: list[str]) -> str:
        shown = "; ".join(effects[:SHOWN])
        if len(effects) > SHOWN:
            shown += f"; and {len(effects) - SHOWN} more settings"
        if CONFIG_OUTBOUND.intersection(names):
            shown += (
                "; this changes where Paperless sends document text or how it reaches the "
                "service that gets it"
            )
        return shown

    async def list_logs(self) -> LogList:
        """The names of the log files.

        Raises:
            PermissionDenied: The user is not a staff user.
        """
        return LogList(logs=await self._reader.list_logs())

    async def get_log(self, name: str, *, lines: int = DEFAULT_LOG_LINES) -> LogExcerpt:
        """The last ``lines`` lines of a log, kept within ``MAX_LOG_CHARS`` characters.

        Raises:
            Validation: ``lines`` is outside 1 to ``MAX_LOG_LINES``.
            NotFound: No such log.
            PermissionDenied: The user is not a staff user.
        """
        if not 1 <= lines <= MAX_LOG_LINES:
            message = f"lines must be between 1 and {MAX_LOG_LINES}."
            raise Validation(message, hint="Ask for fewer lines, or read the log in the web UI.")
        found = await self._reader.get_log(name, limit=lines)
        kept: list[str] = []
        used = 0
        for line in reversed(found):
            if used + len(line) + 1 > MAX_LOG_CHARS:
                break
            kept.append(line)
            used += len(line) + 1
        kept.reverse()
        truncated = len(kept) < len(found)
        if found and not kept:
            kept = ["…" + found[-1][-(MAX_LOG_CHARS - 2) :]]
        return LogExcerpt(
            name=name, lines=tuple(kept), truncated=truncated, returned_lines=len(kept)
        )

    async def list_tasks(
        self,
        *,
        state: TaskState | None = None,
        task_type: TaskType | None = None,
        acknowledged: bool | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> TaskList:
        """One page of tasks, newest first.

        Raises:
            NotFound: ``page`` is past the last page.
            PermissionDenied: The user may not view tasks.
        """
        found = await self._reader.list_tasks(
            state=state,
            task_type=task_type,
            acknowledged=acknowledged,
            page=page,
            page_size=page_size,
        )
        return TaskList(count=found.count, page=page, next_page=found.next_page, tasks=found.rows)

    async def acknowledge_tasks(self, task_ids: Sequence[int]) -> Acknowledged:
        """Mark tasks (database ids from list_tasks) as seen.

        Raises:
            Validation: An id is listed twice or does not exist.
            PermissionDenied: The user may not change tasks.
        """
        if len(set(task_ids)) != len(task_ids):
            message = "A task id is listed twice."
            raise Validation(message, hint="List every id once.")
        count = await self._writer.acknowledge_tasks(task_ids)
        return Acknowledged(
            acknowledged=count,
            message=f"Acknowledged {_plural(count, 'task')} (they no longer show as new).",
        )

    async def run_task(self, task_type: RunnableTask, *, confirm: bool = False) -> StartedTask:
        """Start a task by hand; training the classifier and the AI index need ``confirm``.

        Raises:
            ConfirmationRequired: The task needs ``confirm`` and it is not set.
            PermissionDenied: Only a superuser may start tasks.
        """
        if task_type in TASK_EFFECTS and not confirm:
            raise ConfirmationRequired.for_effect(TASK_EFFECTS[task_type])
        task_id = await self._writer.run_task(task_type)
        return StartedTask(
            task_id=task_id,
            task_type=task_type,
            message=f"Started {task_type}; follow it with get_task(task_id).",
        )

    def _note(self, *, timed_out: bool) -> str:
        """Why the archive is not attached, and what to do about it."""
        if timed_out:
            return (
                "Paperless did not finish the archive in time, so it is not attached. Ask for "
                "fewer documents, or fetch single files with download_document."
            )
        return (
            f"The archive is over the download limit of {self._max_bytes} bytes, so it is not "
            "attached. Call again with offset=0 to read it in parts, ask for fewer documents, "
            "fetch single files with download_document, or ask the administrator to raise "
            "PAPERLESS_MCP_MAX_DOWNLOAD_BYTES."
        )

    async def download(
        self,
        document_ids: Sequence[int],
        *,
        content: ArchiveContent = "archive",
        compression: Compression = "none",
        follow_formatting: bool = False,
    ) -> ArchiveDelivery:
        """The files of documents as one ZIP archive, attached if within the download limit.

        Raises:
            Validation: No documents, a repeated id, more than ``MAX_DOCUMENTS``, or an id that
                does not exist.
            PermissionDenied: The user may not see one of the documents.
        """
        _check_ids(document_ids)
        found = await self._reader.download_documents(
            document_ids,
            content=content,
            compression=compression,
            follow_formatting=follow_formatting,
            max_bytes=self._max_bytes,
        )
        delivered = not found.truncated
        report = ArchiveReport(
            document_ids=tuple(document_ids),
            content=content,
            filename=ARCHIVE_NAME,
            mime_type=ZIP,
            size_bytes=len(found.content) if delivered else None,
            delivered=delivered,
            max_bytes=self._max_bytes,
            note=None if delivered else self._note(timed_out=found.timed_out),
        )
        return ArchiveDelivery(report=report, data=found.content if delivered else None)

    async def part(  # noqa: PLR0913 -- the archive, the window and the proof
        self,
        document_ids: Sequence[int],
        *,
        offset: int,
        content: ArchiveContent = "archive",
        compression: Compression = "none",
        follow_formatting: bool = False,
        max_bytes: int | None = None,
        checksum: str | None = None,
    ) -> FilePart[ArchivePartReport]:
        """The bytes of the archive from ``offset``, at most ``max_bytes`` and the limit.

        Paperless builds the archive anew for every call. It is read and hashed to its end each
        time: only that proves that the parts are of the same archive (decision-45).

        Raises:
            Validation: As ``download``; or a part after the first does not say which archive it
                belongs to, or the archive changed.
            PermissionDenied: The user may not see one of the documents.
            PaperlessMcpError: The archive is too large.
        """
        check_part(offset, max_bytes, checksum)
        _check_ids(document_ids)
        limit = min(max_bytes or self._max_bytes, self._max_bytes)
        async with (
            time_limit(MAX_SECONDS),
            self._reader.open_archive(
                document_ids,
                content=content,
                compression=compression,
                follow_formatting=follow_formatting,
                wait_seconds=PART_WAIT_SECONDS,
            ) as opened,
        ):
            data, size, known = await read_all(opened.chunks, offset, limit)
        if checksum not in {None, known}:
            raise changed()
        end = offset + len(data)
        following = end if end < size else None
        arguments = (
            "the same document_ids, content, compression and follow_formatting, "
            f"offset={end}, max_bytes={limit} and checksum={known}"
        )
        report = ArchivePartReport(
            document_ids=tuple(document_ids),
            content=content,
            filename=SAVED_NAME,
            mime_type=ZIP,
            offset=offset,
            length=len(data),
            total_bytes=size,
            next_offset=following,
            part_sha256=hashlib.sha256(data).hexdigest(),
            checksum=known,
            next=None if following is None else f"Call again with {arguments}; {APPEND}",
        )
        return FilePart(report=report, data=data)
