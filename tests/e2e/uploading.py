"""Helpers of the live duplicate tests: files of their own, users of their own, cleaned up."""

import base64
import hashlib
import uuid
from typing import TYPE_CHECKING, cast

import httpx2
from contract.conftest import delete_and_empty_trash
from fastmcp import Client
from harness import paperless_settings, server_settings

from e2e.helpers import consume, num
from mcp_server_paperless.server import create_server

if TYPE_CHECKING:
    from fastmcp.client.client import CallToolResult

    from e2e.helpers import Stack

type Json = dict[str, object]


def unique() -> bytes:
    return f"plmcp77 {uuid.uuid4().hex}".encode()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Scope:
    """The documents and users of one test; `clean_up` deletes them, also after a failure."""

    def __init__(self, stack: Stack) -> None:
        self.stack: Stack = stack
        self.documents: list[int] = []
        self.users: list[int] = []

    def document(self, content: bytes, title: str | None = None) -> int:
        """A text document of the admin with this file, uploaded past the server."""
        name = title or f"plmcp77-{uuid.uuid4().hex}"
        _, made = consume(
            self.stack.admin, name, content, filename=f"{name}.txt", mime_type="text/plain"
        )
        assert made is not None
        self.documents.append(made)
        _ = self.stack.admin.patch(
            f"/api/documents/{made}/", json={"owner": self._me()}
        ).raise_for_status()
        return made

    def _me(self) -> int:
        settings = cast("dict[str, Json]", self.stack.admin.get("/api/ui_settings/").json())
        return num(settings["user"]["id"])

    def user(self, *permissions: str) -> str:
        """The token of a new user with these permissions, who sees nothing of the admin's."""
        name, password = f"plmcp77-{uuid.uuid4().hex[:12]}", f"pw-{uuid.uuid4().hex}"
        body = {"username": name, "password": password, "user_permissions": list(permissions)}
        made = self.stack.admin.post("/api/users/", json=body).raise_for_status()
        self.users.append(num(cast("Json", made.json())["id"]))
        with httpx2.Client(
            base_url=self.stack.base_url,
            auth=(name, password),
            headers={"Accept": "application/json; version=10"},
        ) as signed_in:
            issued = signed_in.post("/api/profile/generate_auth_token/").raise_for_status()
        return str(cast("object", issued.json()))

    async def call(
        self, tool: str, token: str | None = None, **arguments: object
    ) -> CallToolResult:
        """Call a tool of the server as HTTP clients get it, by default as the admin."""
        server = create_server(
            paperless_settings(self.stack.base_url, token or self.stack.admin_token),
            server_settings(toolsets=["all"]),
        )
        async with Client(server) as client:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
        output = result.structured_content or {}
        for name in ("document_id", "version_id"):
            made = output.get(name)
            if tool.startswith("upload_") and isinstance(made, int):
                self.documents.append(made)
        return result

    async def upload(
        self, content: bytes, token: str | None = None, **arguments: object
    ) -> CallToolResult:
        """Upload through the tool and wait; `document_id` makes it a version."""
        return await self.call(
            "upload_document_version" if "document_id" in arguments else "upload_document",
            token,
            content_base64=base64.b64encode(content).decode(),
            filename="x.txt",
            **{"wait_seconds": 30, **arguments},
        )

    def clean_up(self) -> None:
        delete_and_empty_trash(self.stack, list(dict.fromkeys(self.documents)))
        for user in self.users:
            _ = self.stack.admin.delete(f"/api/users/{user}/")


def output(result: CallToolResult) -> Json:
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    return result.structured_content


def text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def matches(check: object, name: str) -> list[tuple[int, int | None, bool]]:
    """The matches of one field of a duplicate check as (document, version, in the trash)."""
    found = cast("list[Json]", cast("Json", check)[name])
    return [
        (num(m["document_id"]), cast("int | None", m["version_id"]), bool(m["in_trash"]))
        for m in found
    ]
