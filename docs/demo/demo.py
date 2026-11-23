# /// script
# requires-python = "==3.14.*"
# dependencies = ["fastmcp==4.2.0"]
# ///
"""The demo at the top of the README, recorded by `just demo` (PLMCP-69).

An MCP client runs the released server over stdio against a throwaway Paperless. ``seed`` fills
that Paperless with invented documents, ``play`` shows the conversation. The questions and the
tool calls are scripted (no model takes part); every answer on the screen is printed from what the
server returned, shortened to the fields shown and without the hint to the assistant.
"""

import asyncio
import base64
import json
import sys
import textwrap
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

SERVER = "mcp-server-paperless==0.3.2"
PAPERLESS = "http://paperless:8000"
# The throwaway account of tests/e2e/compose.yaml.
ACCOUNT = {"username": "admin", "password": "e2e-admin-password"}

DIM, BOLD, GREEN, YELLOW, BLUE, PINK, RESET = (
    f"\x1b[{code}m" for code in ("38;5;250", "1", "32", "33", "34", "35", "0")
)
WIDTH = 72

SCAN = "scan_0042"
TAXONOMY = {
    "tag": ("utilities", "tax-2026", "insurance"),
    "correspondent": ("City Power", "City Powr", "Northwind Insurance"),
    "document_type": ("Invoice", "Contract"),
}
BILL = "City Power. Electricity bill for {} 2026. Consumption {} kWh. Amount due EUR {}."
# The arguments of upload_document; the text becomes the file.
DOCUMENTS: list[dict[str, object]] = [
    {
        "title": f"Electricity bill, {month}",
        "text": BILL.format(month, kwh, amount),
        "created": f"2026-{number:02}-28",
        "correspondent": "City Power",
        "document_type": "Invoice",
        "tags": ["utilities", "tax-2026"],
    }
    for number, month, kwh, amount in (
        (1, "January", 312, "96.40"),
        (2, "February", 287, "88.15"),
        (3, "March", 241, "74.90"),
    )
] + [
    {
        "title": "Home insurance policy",
        "text": "Northwind Insurance. Home insurance policy for the year 2026.",
        "created": "2026-01-05",
        "correspondent": "Northwind Insurance",
        "document_type": "Contract",
        "tags": ["insurance"],
    },
    {"title": SCAN, "text": BILL.format("April", 198, "61.55"), "created": "2026-04-28"},
]


def wait_for_token() -> str:
    """The API token of the throwaway account, once Paperless is up."""
    body = urllib.parse.urlencode(ACCOUNT).encode()
    deadline = time.monotonic() + 300
    while True:
        try:
            with urllib.request.urlopen(f"{PAPERLESS}/api/token/", body, timeout=10) as answer:  # noqa: S310 -- a fixed http URL
                return json.load(answer)["token"]
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def server() -> Client:
    """A client of the released server, as Claude Code starts it."""
    env = {"PAPERLESS_URL": PAPERLESS, "PAPERLESS_TOKEN": wait_for_token()}
    # The server logs to stderr, which is not part of the conversation.
    return Client(StdioTransport("uvx", [SERVER], env=env, log_file=Path("server.log")))


async def call(client: Client, tool: str, **arguments: object) -> dict[str, Any]:
    """What the server answers to one call, as its structured content."""
    return (await client.call_tool(tool, arguments)).structured_content


async def seed() -> None:
    """Fill Paperless with the invented documents, through the server's own tools."""
    async with server() as client:
        for kind, names in TAXONOMY.items():
            for name in names:
                await call(client, "create_taxonomy_item", kind=kind, name=name)
        for document in DOCUMENTS:
            if document["title"] == SCAN:  # Paperless puts the inbox tag on every new document
                options = {"kind": "tag", "name": "inbox", "is_inbox_tag": True}
                await call(client, "create_taxonomy_item", **options)
            text = str(document["text"]).encode()
            upload = {key: value for key, value in document.items() if key != "text"} | {
                "content_base64": base64.b64encode(text).decode(),
                "filename": f"{document['title']}.txt",
            }
            started = await call(client, "upload_document", **upload)
            while True:
                task = await call(client, "get_task", task_id=started["task_id"], wait_seconds=30)
                if task["state"] == "success":
                    break
                if task["state"] in {"failure", "revoked"}:
                    sys.exit(f"Paperless did not consume {document['title']}: {task}")


def say(text: str, end: str = "\n") -> None:
    """Put text on the screen at once."""
    _ = sys.stdout.write(text + end)
    _ = sys.stdout.flush()


def show(text: str = "", pause: float = 0.05) -> None:
    """Print one line and give the viewer a moment."""
    say(text)
    time.sleep(pause)


def scene(question: str) -> None:
    """A clean screen with the header, then the question as if typed."""
    say("\x1b[H\x1b[2J\x1b[3J", end="")
    show(f"{BOLD}{SERVER}{RESET}{DIM}  with paperless-ngx 3.4.2{RESET}", 0)
    show(f"{DIM}invented documents · scripted questions and calls · real answers{RESET}", 0)
    ask(question, 0.4)


def ask(question: str, pause: float) -> None:
    """Type the question."""
    say(f"\n{PINK}{BOLD}❯{RESET} {BOLD}", end="")  # noqa: RUF001 -- a prompt sign
    time.sleep(pause)
    for letter in question:
        say(letter, end="")
        time.sleep(0.026)
    say(RESET)
    time.sleep(0.4)


def wrapped(text: str, lead: str, colour: str) -> None:
    """Print the text of an answer, all of it, under a coloured lead."""
    for number, line in enumerate(textwrap.wrap(text, WIDTH - 4)):
        show(f"  {colour}{lead if number == 0 else ' '}{RESET} {line}")


def announce(tool: str, arguments: dict[str, object]) -> None:
    """Print the call that is made next, with the arguments it is made with."""
    # No-break spaces keep one argument on one line.
    listed = [f"{key}={json.dumps(value)}".replace(" ", "\xa0") for key, value in arguments.items()]
    lines = textwrap.wrap(f"{tool}({', '.join(listed)})", WIDTH - 4, break_on_hyphens=False)
    text = "\n    ".join(lines).replace("\xa0", " ").removeprefix(tool)
    text = text.replace("confirm=true", f"{RESET}{YELLOW}{BOLD}confirm=true{RESET}{DIM}")
    show(f"\n{BLUE}●{RESET} {BOLD}{tool}{RESET}{DIM}{text}{RESET}", 0.6)


async def shown(client: Client, tool: str, **arguments: object) -> dict[str, Any]:
    """Announce a call, make it, and return the answer."""
    announce(tool, arguments)
    return await call(client, tool, **arguments)


def named(value: dict[str, Any] | None) -> str:
    """The name of a correspondent or type in an answer."""
    return value["name"] if value else f"{DIM}none{RESET}"


def tags(document: dict[str, Any]) -> str:
    """The names of a document's tags."""
    return ", ".join(tag["name"] for tag in document["tags"])


async def play() -> None:
    """The conversation that is recorded."""
    async with server() as client:
        say("\x1b[?25l", end="")  # no cursor
        scene("Which bills did City Power send me?")
        found = await shown(
            client, "search_documents", correspondent="City Power", tags_all=["utilities"]
        )
        show(f"  {GREEN}✓{RESET} {found['count']} documents")
        for row in found["documents"]:
            show(
                f"    {DIM}{row['id']:>3}{RESET}  {row['title']:<28}{DIM}{row['created']}{RESET}"
                f"  {tags(row)}",
                0.25,
            )
        await asyncio.sleep(2.4)

        scene("Is there a new one in my inbox? File it like the others.")
        found = await shown(client, "search_documents", query="City Power", inbox=True)
        [scan] = found["documents"]
        show(f"  {GREEN}✓{RESET} {found['count']} document")
        show(
            f"    {DIM}{scan['id']:>3}{RESET}  {scan['title']:<28}{DIM}{scan['created']}{RESET}"
            f"  {tags(scan)}",
            1,
        )
        filed = await shown(
            client,
            "update_document",
            document_id=scan["id"],
            title="Electricity bill, April",
            correspondent="City Power",
            document_type="Invoice",
            tags_set=["utilities", "tax-2026"],
        )
        show(f"  {GREEN}✓{RESET} {filed['title']}")
        show(
            f"    {named(filed['correspondent'])}  {DIM}·{RESET}  {named(filed['document_type'])}"
            f"  {DIM}·{RESET}  {tags(filed)}",
        )
        await asyncio.sleep(2.6)

        scene("Delete the misspelt correspondent City Powr.")
        deletion = {"kind": "correspondent", "item": "City Powr"}
        try:
            await shown(client, "delete_taxonomy_item", **deletion)
        except ToolError as refusal:
            # The rest of the message is the hint to the assistant, not to the user.
            wrapped(str(refusal).partition(" Hint: ")[0], "■", YELLOW)
        else:
            sys.exit("the server deleted without a confirmation")
        await asyncio.sleep(2.2)
        ask("Yes, go ahead.", 0.2)
        deleted = await shown(client, "delete_taxonomy_item", **deletion, confirm=True)
        wrapped(deleted["message"], "✓", GREEN)


if __name__ == "__main__":
    asyncio.run({"seed": seed, "play": play}[sys.argv[1]]())
    if sys.argv[1] == "play":
        Path("played").touch()  # the recording is only good when it got here
        time.sleep(30)  # the tape ends it
