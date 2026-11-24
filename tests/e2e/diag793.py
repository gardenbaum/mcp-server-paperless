"""DIAGNOSTIC PLMCP-79.3: temporary, removed before the end."""

import json
from datetime import datetime

from pydantic import JsonValue, RootModel

from mcp_server_paperless.paperless import pdf
from mcp_server_paperless.paperless.client import PaperlessClient


class Raw(RootModel[JsonValue]):
    pass


async def dump(client: PaperlessClient, request: str | None) -> None:
    """Print every task row as this caller gets it, and what each rule decides."""
    settings = (await client.get("/api/ui_settings/", Raw)).root
    user = settings["user"]["id"]
    print("DIAG user", json.dumps(settings["user"]), sorted(settings["permissions"]))
    start = None
    for kind in ("bulk_update", "consume_file", None):
        params = {"ordering": "-date_created", "page_size": 100}
        if kind:
            params["task_type"] = kind
        page = (await client.get("/api/tasks/", Raw, params=params)).root
        print("DIAG", kind, "count", page["count"], "next", page["next"])
        for row in page["results"]:
            if row["task_id"] == request and row["date_started"]:
                start = datetime.fromisoformat(row["date_started"])
        print("DIAG request", request, "start", start)
        for row in page["results"]:
            if kind is None and row["task_type"] in {"bulk_update", "consume_file"}:
                continue
            keep = ("id", "task_id", "task_type", "status", "owner", "input_data")
            keep += ("trigger_source", "related_document_ids")
            keep += ("date_created", "date_started", "date_done")
            task = pdf._Task.model_validate(row)
            rules = {
                "is_request": task.task_id == request,
                "owner_ok": task.owner in {None, user},
                "own_work": task.own_work,
                "copying": task.operation in pdf.COPYING,
            }
            if start is not None and kind == "bulk_update":
                done, begun = task.date_done, task.date_started
                rules["at_once"] = bool(begun and (done is None or done >= start))
                rules["earlier"] = bool(begun and done and start - pdf.BACKLOG <= done < start)
            print("DIAG row", json.dumps({k: row[k] for k in keep}), json.dumps(rules))
    page = (
        await client.get(
            "/api/documents/",
            Raw,
            params={
                "owner__id": user,
                "fields": "id,original_file_name,added,owner",
                "page_size": 100,
            },
        )
    ).root
    print("DIAG documents", json.dumps(page["results"]))
