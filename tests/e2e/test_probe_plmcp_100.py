"""Temporary probes P1 and P2 of PLMCP-100; deleted once they have run."""

import json
import uuid
from typing import TYPE_CHECKING, cast

import httpx2
import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.client.elicitation import ElicitResult
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult
from test_confirmation_http import messages, running

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = [pytest.mark.e2e, pytest.mark.anyio]

ONE_OF = {
    "type": "object",
    "properties": {
        "choice": {
            "type": "string",
            "title": "Which one?",
            "oneOf": [{"const": "1", "title": "A (id 1)"}, {"const": "2", "title": "A (id 2)"}],
        }
    },
    "required": ["choice"],
}
NAMES = {
    "type": "object",
    "properties": {
        "choice": {
            "type": "string",
            "title": "Which one?",
            "enum": ["1", "2"],
            "enumNames": ["A (id 1)", "A (id 2)"],
        }
    },
    "required": ["choice"],
}


def probe() -> FastMCP:
    server = FastMCP("probe")

    @server.tool
    async def ask(ctx: Context, schema: str, rounds: int = 1) -> str:
        wanted = ONE_OF if schema == "one_of" else NAMES
        if ctx.session.protocol_version >= "2026-07-28":
            done = int(ctx.request_state or "0")
            if done < rounds:
                params = ElicitRequestFormParams(message="m", requested_schema=wanted)
                return cast(
                    "str",
                    cast(
                        "object",
                        InputRequiredResult(
                            result_type="input_required",
                            input_requests={
                                "choice": ElicitRequest(method="elicitation/create", params=params)
                            },
                            request_state=str(done + 1),
                        ),
                    ),
                )
            return f"{done} {ctx.input_responses}"
        answer = None
        for _ in range(rounds):
            answer = await ctx.session.elicit_form(
                "m", wanted, related_request_id=ctx.request_id
            )
        return f"{ctx.session.protocol_version} {answer}"

    return server


@pytest.mark.parametrize("schema", ["one_of", "names"])
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_p1(schema: str, mode: str) -> None:
    async def handler(message, kind, params, context):  # noqa: ANN001, ANN202
        print("P1", mode, schema, "seen", kind, params.model_dump_json(by_alias=True))  # noqa: T201
        return ElicitResult(action="accept", content={"choice": "2"})

    for rounds in (1, 8, 11):
        try:
            async with Client(probe(), mode=mode, elicitation_handler=handler) as caller:
                result = await caller.call_tool(
                    "ask", {"schema": schema, "rounds": rounds}, raise_on_error=False
                )
            print("P1", mode, schema, rounds, result.is_error, result.content)  # noqa: T201
        except Exception as error:  # noqa: BLE001
            print("P1", mode, schema, rounds, "FAILED", type(error), error)  # noqa: T201
    pytest.fail("shows the output above")


@pytest.mark.parametrize("schema", ["one_of", "names"])
@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"])
async def test_p1_raw(schema: str, version: str) -> None:
    app = probe().http_app()
    async with httpx2.AsyncClient(timeout=30) as http:
        with running(app) as url:
            head = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version}
            start = {
                "protocolVersion": version,
                "capabilities": {"elicitation": {}},
                "clientInfo": {"name": "t", "version": "1"},
            }
            rpc = {"jsonrpc": "2.0"}
            opened = await http.post(
                url, headers=head, json={**rpc, "id": 1, "method": "initialize", "params": start}
            )
            print("P1raw", version, schema, opened.status_code, opened.text[:300])  # noqa: T201
            head["Mcp-Session-Id"] = opened.headers.get("mcp-session-id", "")
            _ = await http.post(url, headers=head, json={**rpc, "method": "notifications/initialized"})
            call = {"name": "ask", "arguments": {"schema": schema}}
            body = {**rpc, "id": 2, "method": "tools/call", "params": call}
            async with http.stream("POST", url, headers=head, json=body) as response:
                async for message in messages(response):
                    print("P1raw", version, schema, json.dumps(message))  # noqa: T201
                    if message.get("method") == "elicitation/create":
                        reply = {"action": "accept", "content": {"choice": "2"}}
                        _ = await http.post(
                            url, headers=head, json={**rpc, "id": message["id"], "result": reply}
                        )
                    elif "method" not in message:
                        break
    pytest.fail("shows the output above")


KINDS = {
    "tags": {},
    "correspondents": {},
    "document_types": {},
    "storage_paths": {"path": "{{ title }}"},
    "custom_fields": {"data_type": "string"},
    "saved_views": {"filter_rules": [], "sort_field": "created", "sort_reverse": True},
    "workflows": {
        "triggers": [{"type": 2}],
        "actions": [{"type": 1}],
    },
    "users": {},
    "groups": {"permissions": []},
}


def test_p2(stack: Stack) -> None:
    """Owners: the admin, nobody and the reader."""
    for kind, more in KINDS.items():
        name = f"p2{uuid.uuid4().hex[:8]}"
        key = "username" if kind == "users" else "name"
        for label, who, body in (
            ("admin", stack.admin, {key: name, **more}),
            ("admin again", stack.admin, {key: name, **more}),
            ("unowned", stack.admin, {key: name, "owner": None, **more}),
            ("unowned again", stack.admin, {key: name, "owner": None, **more}),
            ("reader", stack.reader, {key: name, **more}),
            ("case", stack.admin, {key: name.upper(), **more}),
        ):
            if label == "reader" and kind not in {"users", "groups"}:
                body = {**body}
                body.pop("owner", None)
                made = stack.admin.post(f"/api/{kind}/", json={**body, "owner": 3})
            else:
                made = who.post(f"/api/{kind}/", json=body)
            print("P2", kind, label, made.status_code, made.text[:200])  # noqa: T201
        rows = stack.admin.get(f"/api/{kind}/", params={"page_size": 1000}).json()["results"]
        found = [(r["id"], r.get("owner")) for r in rows if str(r.get(key)).lower() == name]
        print("P2", kind, "admin sees", json.dumps(found))  # noqa: T201
        for number, _ in found:
            stack.admin.delete(f"/api/{kind}/{number}/")
    pytest.fail("shows the output above")

