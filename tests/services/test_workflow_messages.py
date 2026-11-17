"""The exact words of the errors workflows give: they are what an LLM acts on.

One recorded snapshot, so a changed message or hint is a deliberate change that shows up in
review (and a mutated literal cannot go unnoticed).
"""

from typing import TYPE_CHECKING

import pytest

from paperless_mcp.domain.errors import PaperlessMcpError
from paperless_mcp.domain.workflows import (
    ActionInput,
    EmailSettings,
    TriggerInput,
    WebhookSettings,
    WorkflowAction,
    WorkflowCreate,
    WorkflowEdit,
    WorkflowTrigger,
)
from paperless_mcp.services.workflows import action_type, trigger_type
from services.test_workflows import MAIL, NEVER, Setup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from syrupy.assertion import SnapshotAssertion

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-21")]


def said(action: Callable[[], object]) -> str:
    try:
        _ = action()
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


async def said_later(action: Callable[[], Awaitable[object]]) -> str:
    try:
        _ = await action()
    except PaperlessMcpError as error:
        return error.describe()
    return "accepted"


def test_type_errors(snapshot: SnapshotAssertion) -> None:
    found = {f"trigger {t!r}": said(lambda t=t: trigger_type(t)) for t in (0, 9, "hourly", "added")}
    found |= {
        f"action {a!r}": said(lambda a=a: action_type(a)) for a in (0, 5, 8, 99, "shell", "email")
    }

    assert found == snapshot


async def test_workflow_service_errors(snapshot: SnapshotAssertion) -> None:
    setup = Setup()
    flows = setup.workflows
    _ = setup.seed("Filing", triggers=[WorkflowTrigger(type=2)], actions=[WorkflowAction(type=6)])
    _ = setup.seed(
        "Odd",
        enabled=False,
        actions=[
            WorkflowAction(id=3, type=5, settings={"passwords": "x"}),
            WorkflowAction(id=4, type=8),
            WorkflowAction(id=5, type=9),
        ],
    )
    _ = setup.seed(
        "Hook",
        enabled=False,
        actions=[
            WorkflowAction(
                id=6,
                type=4,
                settings={"webhook": {"url": "https://old.example/x", "headers": {"X-A": "a"}}},
            )
        ],
    )
    hook = WebhookSettings(url="https://h.example/x", headers={"X-Token": "(hidden)"})
    created: dict[str, WorkflowCreate] = {
        "blank name": WorkflowCreate(name=" "),
        "taken name": WorkflowCreate(name="FILING"),
        "unknown trigger": WorkflowCreate(name="n", triggers=(TriggerInput(type=9),)),
        "password removal": WorkflowCreate(name="n", actions=(ActionInput(type=5),)),
        "path on added": WorkflowCreate(name="n", triggers=(TriggerInput(type=2, path="x"),)),
        "schedule on added": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=2, schedule_offset_days=1),)
        ),
        "tags on consumption": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=1, filename="f", has_tags=["e2e-finance"]),)
        ),
        "match without algorithm": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=2, match="x"),)
        ),
        "bad algorithm": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=2, matching_algorithm="x"),)
        ),
        "bad algorithm number": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=2, matching_algorithm=9),)
        ),
        "bad source": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=1, filename="f", sources=["ftp"]),)
        ),
        "bad date field": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=4, schedule_date_field="x"),)
        ),
        "bad json": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=2, custom_field_query="[x"),)
        ),
        "unknown tag": WorkflowCreate(
            name="n", triggers=(TriggerInput(type=2, has_tags=["nope"]),)
        ),
        "title on trash": WorkflowCreate(
            name="n", actions=(ActionInput(type=6, assign_title="x"),)
        ),
        "removal on assignment": WorkflowCreate(
            name="n", actions=(ActionInput(type=1, remove_tags=["e2e-inbox"]),)
        ),
        "email without settings": WorkflowCreate(name="n", actions=(ActionInput(type=3),)),
        "webhook without settings": WorkflowCreate(name="n", actions=(ActionInput(type=4),)),
        "hidden header": WorkflowCreate(
            name="n", actions=(ActionInput(type=4, webhook=hook),), enabled=False
        ),
        "trash": WorkflowCreate(name="n", triggers=(NEVER,), actions=(ActionInput(type=6),)),
        "mail": WorkflowCreate(name="n", triggers=(NEVER,), actions=(MAIL,)),
        "mail with document": WorkflowCreate(
            name="n",
            triggers=(TriggerInput(type="scheduled", match="x", matching_algorithm="any"),),
            actions=(
                ActionInput(
                    type=3,
                    email=EmailSettings(subject="s", body="b", to="a@x.org", include_document=True),
                ),
            ),
        ),
        "webhook": WorkflowCreate(
            name="n",
            triggers=(NEVER,),
            actions=(
                ActionInput(
                    type=4,
                    webhook=WebhookSettings(
                        url="https://user:pw@h.example:8443/p?token=1", include_document=True
                    ),
                ),
            ),
        ),
        "removal": WorkflowCreate(
            name="n",
            actions=(
                ActionInput(type=2, remove_all_tags=True, remove_view_users=["admin"]),
                ActionInput(type=1, assign_owner="admin"),
            ),
        ),
        "no trigger": WorkflowCreate(name="n", actions=(ActionInput(type=6),)),
    }
    edited: dict[str, WorkflowEdit] = {
        "nothing": WorkflowEdit(),
        "blank name": WorkflowEdit(name=" "),
        "taken name": WorkflowEdit(name="FILING"),
        "switch on": WorkflowEdit(enabled=True),
    }
    found: dict[str, str] = {}
    for name, spec in created.items():
        found[f"create, {name}"] = await said_later(lambda s=spec: flows.create(s, confirm=False))
    for name, edit in edited.items():
        found[f"update Hook, {name}"] = await said_later(
            lambda e=edit: flows.update("Hook", e, confirm=False)
        )
    found["update Filing, new actions"] = await said_later(
        lambda: flows.update("Filing", WorkflowEdit(actions=(ActionInput(type=6),)), confirm=False)
    )
    found["update Odd, new actions"] = await said_later(
        lambda: flows.update("Odd", WorkflowEdit(actions=(ActionInput(type=6),)), confirm=False)
    )
    found["update, hidden header"] = await said_later(
        lambda: flows.update(
            "Hook", WorkflowEdit(actions=(ActionInput(type=4, id=9, webhook=hook),)), confirm=False
        )
    )
    found["update, hidden header moved"] = await said_later(
        lambda: flows.update(
            "Hook",
            WorkflowEdit(
                actions=(
                    ActionInput(
                        type=4,
                        id=6,
                        webhook=WebhookSettings(
                            url="https://other.example/x", headers={"X-A": "(hidden)"}
                        ),
                    ),
                )
            ),
            confirm=False,
        )
    )
    found["update, missing"] = await said_later(
        lambda: flows.update("nope", WorkflowEdit(name="x"), confirm=False)
    )
    found["get, near miss"] = await said_later(lambda: flows.get("Filling"))
    found["get, missing id"] = await said_later(lambda: flows.get(99))
    found["delete, unconfirmed"] = await said_later(lambda: flows.delete("Filing", confirm=False))
    found["delete, empty"] = await said_later(lambda: flows.delete("Hook", confirm=False))

    assert found == snapshot
