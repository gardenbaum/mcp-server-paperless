"""Running a workflow by hand on FakePaperless, as 3.5.1 (`run_workflow` of bulk_edit).

What the contract tests pin: the request is checked as a whole (400, then 403), the manual triggers
decide (one that matches is enough), a failure on one document does not stop the others and keeps
what its earlier actions did, and e-mails and webhooks never fail a run. Not modelled: content and
custom field filters of a trigger, title templates, storage paths' files, password removal and AI
suggestions.
"""

from typing import TYPE_CHECKING, Protocol, cast

from fakes.document_values import reject
from mcp_server_paperless.paperless.workflows import run_refused

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from mcp_server_paperless.domain.workflows import Workflow, WorkflowAction, WorkflowTrigger

ASSIGNMENT, REMOVAL, TRASH, MANUAL = 1, 2, 6, 5
REFERENCES = ("correspondent", "document_type", "storage_path")


class User(Protocol):
    """The user who asks for a run."""

    @property
    def id(self) -> int:
        """The user's id."""
        ...

    @property
    def is_superuser(self) -> bool:
        """Whether the user may do everything."""
        ...

    def has(self, permission: str) -> bool:
        """Whether the user has a global permission."""
        ...


class Document(Protocol):
    """What a run reads and writes of a document."""

    id: int
    owner: int | None
    title: str
    tags: list[int]
    custom_fields: dict[int, JsonValue]


class Instance[D: Document](Protocol):
    """What a run needs of the rest of the instance."""

    def retag(self, document: D, add: Sequence[int], remove: Sequence[int]) -> None:
        """Remove tags with their children, then add tags with their parents."""
        ...

    def to_trash(self, document: D) -> None:
        """Move the document to the trash."""
        ...

    def integer(self, field: int) -> bool:
        """Whether the custom field holds integers."""
        ...


def ids(value: JsonValue) -> list[int]:
    return [item for item in value if isinstance(item, int)] if isinstance(value, list) else []


def check[D: Document](
    user: User,
    workflow: Workflow | None,
    documents: Sequence[int],
    known: dict[int, D],
    *,
    changeable: Callable[[D], bool],
) -> list[D]:
    """The documents of the run, if Paperless takes the request."""
    if len(set(documents)) != len(documents) or not set(documents) <= known.keys():
        reject("documents", "Some documents in documents don't exist or were specified twice.")
    if workflow is None:
        reject("non_field_errors", "Workflow does not exist")
    if not user.has("view_workflow"):
        raise run_refused()
    if not workflow.enabled:
        reject("non_field_errors", "Workflow is not enabled")
    if not any(t.type == MANUAL for t in workflow.triggers):
        reject("non_field_errors", "Workflow does not have a manual trigger")
    chosen = [known[number] for number in documents]
    owns = all(d.owner in {None, user.id} and changeable(d) for d in chosen)
    if not user.is_superuser and not (user.has("change_document") and owns):
        raise run_refused()
    return chosen


def _reference(document: Document, name: str) -> int | None:
    return cast("int | None", vars(document)[name])


def matches(trigger: WorkflowTrigger, document: Document) -> bool:
    """Whether the document passes the tag and reference filters of the trigger."""
    tags, settings = set(document.tags), trigger.settings

    def wanted(name: str) -> set[int]:
        return set(ids(settings.get(f"filter_has_{name}")))

    if wanted("tags") and not wanted("tags") & tags:
        return False
    if not wanted("all_tags") <= tags or wanted("not_tags") & tags:
        return False
    for name in REFERENCES:
        value = _reference(document, name)
        one, some = settings.get(f"filter_has_{name}"), wanted(f"any_{name}s")
        if one not in {None, value} or (some and value not in some):
            return False
        if value in wanted(f"not_{name}s"):
            return False
    return True


def run[D: Document](
    instance: Instance[D],
    workflow: Workflow,
    documents: Sequence[D],
    *,
    filter_documents: bool,
    fails: Callable[[D], bool],
) -> list[int]:
    """Run the actions; returns the documents it failed on."""
    failed: list[int] = []
    manual = [t for t in workflow.triggers if t.type == MANUAL]
    for document in documents:
        if filter_documents and not any(matches(t, document) for t in manual):
            continue
        try:
            for action in workflow.actions:
                _apply(instance, action, document)
            if fails(document):
                raise ValueError(document.id)  # noqa: TRY301 -- as an action that fails
        except ValueError:
            failed.append(document.id)
            continue
        if any(action.type == TRASH for action in workflow.actions):
            instance.to_trash(document)
    return failed


def _apply[D: Document](instance: Instance[D], action: WorkflowAction, document: D) -> None:
    settings = action.settings
    if action.type == ASSIGNMENT:
        instance.retag(document, ids(settings.get("assign_tags")), ())
        for name in (*REFERENCES, "owner", "title"):
            if settings.get(f"assign_{name}"):
                vars(document)[name] = settings[f"assign_{name}"]
        values = settings.get("assign_custom_fields_values")
        for field in ids(settings.get("assign_custom_fields")):
            value = values.get(str(field)) if isinstance(values, dict) else None
            if isinstance(value, str) and instance.integer(field):
                raise ValueError(value)
            document.custom_fields[field] = value
    elif action.type == REMOVAL:
        every = bool(settings.get("remove_all_tags"))
        gone = list(document.tags) if every else ids(settings.get("remove_tags"))
        instance.retag(document, (), gone)
        for name in REFERENCES:
            listed = ids(settings.get(f"remove_{name}s"))
            if settings.get(f"remove_all_{name}s") or _reference(document, name) in listed:
                vars(document)[name] = None
        if settings.get("remove_all_owners"):
            document.owner = None
