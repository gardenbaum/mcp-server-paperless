<!-- mcp-name: io.github.gardenbaum/mcp-server-paperless -->
<div align="center">

# mcp-server-paperless

**Talk to your [paperless-ngx](https://docs.paperless-ngx.com) archive.**<br>
Claude, Cursor, VS Code and every other MCP client can find, read, file, upload and organise
your documents, by name, and nothing final happens without your yes.

[![PyPI](https://img.shields.io/pypi/v/mcp-server-paperless?color=3776ab)](https://pypi.org/project/mcp-server-paperless/)
[![Downloads](https://img.shields.io/pypi/dm/mcp-server-paperless)](https://pypi.org/project/mcp-server-paperless/)
[![CI](https://github.com/gardenbaum/mcp-server-paperless/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/gardenbaum/mcp-server-paperless/actions/workflows/ci.yml)
[![E2E full suite](https://github.com/gardenbaum/mcp-server-paperless/actions/workflows/e2e-full.yml/badge.svg?branch=main)](https://github.com/gardenbaum/mcp-server-paperless/actions/workflows/e2e-full.yml)
[![paperless-ngx 3.x](https://img.shields.io/badge/paperless--ngx-3.x-17541f)](https://github.com/paperless-ngx/paperless-ngx)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776ab)](https://pypi.org/project/mcp-server-paperless/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/gardenbaum/mcp-server-paperless/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/gardenbaum/mcp-server-paperless?style=social)](https://github.com/gardenbaum/mcp-server-paperless)

[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_server-0098FF?logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=paperless&inputs=%5B%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22paperless_url%22%2C%22description%22%3A%22Paperless%20URL%2C%20e.g.%20https%3A%2F%2Fpaperless.example.org%22%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22paperless_token%22%2C%22description%22%3A%22Paperless%20API%20token%22%2C%22password%22%3Atrue%7D%5D&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22mcp-server-paperless%40latest%22%5D%2C%22env%22%3A%7B%22PAPERLESS_URL%22%3A%22%24%7Binput%3Apaperless_url%7D%22%2C%22PAPERLESS_TOKEN%22%3A%22%24%7Binput%3Apaperless_token%7D%22%7D%7D)
[![Install in Cursor](https://img.shields.io/badge/Cursor-Install_server-000000?logo=cursor&logoColor=white)](https://cursor.com/en/install-mcp?name=paperless&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJtY3Atc2VydmVyLXBhcGVybGVzc0BsYXRlc3QiXSwiZW52Ijp7IlBBUEVSTEVTU19VUkwiOiJodHRwczovL3BhcGVybGVzcy5leGFtcGxlLm9yZyIsIlBBUEVSTEVTU19UT0tFTiI6InlvdXItYXBpLXRva2VuIn19)
[![Claude Code](https://img.shields.io/badge/Claude_Code-one_command-D97757?logo=claude&logoColor=white)](#claude-code)

[Quick start](#quick-start) · [What you can ask](#what-you-can-ask) · [Comparison](#how-it-compares) ·
[Toolsets](#toolsets) · [Safety](#how-it-keeps-your-archive-safe) · [Documentation](#documentation)

![Terminal recording: the server finds three electricity bills by the name of the sender, files a new scan from the inbox like the others, and refuses to delete a correspondent for good until the call is repeated with confirm=true. Questions, the yes and tool calls are scripted, the answers are real, shortened, and the documents are invented.](https://raw.githubusercontent.com/gardenbaum/mcp-server-paperless/main/docs/demo/demo.gif)

<sub>Version 0.3.2 against a real paperless-ngx 3.4.2 with invented documents. The questions, the
yes and the tool calls are scripted, the answers are the server's own, shortened
([how it is recorded](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/development.md#demo-recording)).</sub>

</div>

```sh
claude mcp add paperless --env PAPERLESS_URL=https://paperless.example.org \
    --env PAPERLESS_TOKEN=your-api-token -- uvx mcp-server-paperless@latest
```

mcp-server-paperless is a [Model Context Protocol](https://modelcontextprotocol.io) server for
paperless-ngx 3.x, built on FastMCP 4. It gives Claude Code, Claude Desktop, claude.ai, Cursor, VS
Code and any other MCP client 97 tools in 13 toolsets that cover most of what the Paperless API
can do, from a search to saved views, mail rules and workflows. It starts small and careful: 20
tools by default, names instead of ids, and a confirmation step before anything that cannot be
undone.

```mermaid
flowchart LR
    A["Claude Code · Claude Desktop<br>Cursor · VS Code"] -- stdio --> S
    B["claude.ai<br>connector"] -- "HTTPS + OIDC" --> S
    C["Other MCP<br>clients"] -- "HTTP + bearer token" --> S
    S(["mcp-server-paperless"]) -- "REST API v10" --> P[("paperless-ngx 3.x")]
```

## What you can ask

- "What is in my inbox? Suggest a correspondent, type and tags for each document and file them
  once I agree."
- "Find last year's electricity bills and add up what I paid."
- "Upload this PDF as an invoice from ACME and tag it tax-2026."
- "Which documents mention the rental deposit? Show me the paragraph."
- "The date next to the signature is unreadable in the text. Show me that part of page 2."
- "Merge the three scans of the contract into one document and rotate page 2."
- "Give me a link to my insurance policy that expires in a week."
- "Set up a mail rule that files the invoices from billing@example.org."
- "Find duplicate and unused tags and propose how to clean them up."
- "Which documents have no document type yet? Then run my saved view *To do*."

An example of how that goes (illustrative):

> **You:** What is still in my inbox?
>
> **Claude:** Three documents. Here is how I would file them:
>
> | Document | Correspondent | Type | Tags |
> |---|---|---|---|
> | Electricity bill, March | City Power | Invoice | utilities, tax-2026 |
> | Policy update | Northwind Insurance | Letter | insurance |
> | Receipt, hardware store | *new:* Brick & Bolt | Receipt | household |
>
> "Brick & Bolt" does not exist yet as a correspondent; I would create it. Shall I apply this?
>
> **You:** Yes, and tag the policy update tax-2026 too.

Four built-in prompts start such tasks from the client's prompt menu: `triage_inbox`,
`find_document`, `annual_review` and `cleanup_taxonomy`. Each one proposes its changes and waits
for your yes before it writes.

## Why this one

There are already several MCP servers for paperless-ngx. This one is a new build, made for
people who want all of the following in one server:

- 🗂️ **Much more than search.** Besides finding, reading and filing documents it handles notes,
  custom fields, saved views, file versions, the trash, PDF operations, share links, mail rules,
  workflows, permissions and user administration: most of what paperless-ngx 3.x can do.
- 🏷️ **Names instead of numbers.** You say "invoices from ACME tagged tax-2026" and the server
  finds the matching ids itself. If a name does not exist or fits more than one entry, it says so
  and lists the candidates instead of guessing.
- 🛡️ **Nothing final without your yes.** Before a tool deletes, overwrites, publishes or shares
  anything, it reports what would happen and waits for your confirmation. Changes to many
  documents at once can be previewed first.
- 🪶 **Small to start with.** Only 20 tools by default out of the 97, so the assistant starts
  with the essentials and you decide what else it may do. Search results are short lists and long
  documents are read piece by piece, so a conversation does not fill up with text it does not
  need.
- 🧪 **Tested against a real Paperless.** More than 600 tests run against a real paperless-ngx
  after every change and every night, on top of more than 4,500 faster tests. Every week the
  whole suite also runs against the newest Paperless release.
- 🔐 **Safe to reach from outside.** Over the network the server does not start without
  authentication. Claude Code signs in with a token, claude.ai through your own identity
  provider, and the container image runs without root rights.

## How it compares

Some other paperless-ngx MCP servers, as their code stood on 23 November 2026 (read from their
sources, not run; ❌ means not found; open an issue if something is wrong or has changed):

| | Tools by default / in total | Toolsets and read-only mode | Names instead of ids | Confirms what is final | Runs saved views | Notices misread full-text syntax | HTTP | Tested on a real Paperless |
|---|---|---|---|---|---|---|---|---|
| **mcp-server-paperless** | 20 / 91 | ✅ opt-in | ✅ tags, types, people, views | ✅ with a preview | ✅ | ✅ | bearer, OIDC | 3.5.1 after every merge, newest 3.x weekly |
| [OrellBuehler/paperless-mcp](https://github.com/OrellBuehler/paperless-mcp) | 123 / 126 | ✅ all on by default | ❌ | hints only | ❌ | ❌ | bearer, OAuth | ❌ |
| [freeformz/paperless-ngx-mcp](https://github.com/freeformz/paperless-ngx-mcp) | 123 / 123 | ❌ | ❌ | ❌ | ❌ | ❌ | stdio only | ❌ |
| [cubinet-code/paperless-ngx-mcp](https://github.com/cubinet-code/paperless-ngx-mcp) | 109 / 109 | ❌ | in results | ✅ | ❌ | ❌ | no auth | 3.2.1 on every change |
| [tobee89/mcp-paperless-ngx](https://github.com/tobee89/mcp-paperless-ngx) | 85 / 99 | ✅ 7 of 9 on by default | ❌ | hints only | ❌ | ❌ | stdio only | by hand |
| [pvliesdonk/paperless-mcp](https://github.com/pvliesdonk/paperless-mcp) | 50 / 52 | tool allow list | ❌ | hints only | ❌ | ❌ | bearer, OIDC | not in CI |
| [baruchiro/paperless-mcp](https://github.com/baruchiro/paperless-mcp) | 47 / 47 | ❌ | in results | ✅ | ❌ | ❌ | bearer | 2.20 and newest on every change |
| [barryw/PaperlessMCP](https://github.com/barryw/PaperlessMCP) | 44 / 44 | ❌ | ❌ | ✅ with a preview | ❌ | ❌ | no auth | ❌ |
| [nloui/paperless-mcp](https://github.com/nloui/paperless-mcp) | 16 / 16 | ❌ | ❌ | ❌ | ❌ | ❌ | no auth | ❌ |

Others exist; [tb1337](https://github.com/tb1337/paperless-mcp) and
[chaosbit-dev](https://github.com/chaosbit-dev/paperless-mcp) also run saved views.

More tools is not better by itself: every tool a client loads costs context in every
conversation. This server keeps the default small and lets you switch on the rest.

## Quick start

You need a Paperless API token: in Paperless open your profile menu, "My Profile", and create an
API auth token. The server acts as that user and sees and changes only what that user may.

### Claude Code

With [uv](https://docs.astral.sh/uv/) installed, one command, no clone:

```sh
claude mcp add paperless \
    --env PAPERLESS_URL=https://paperless.example.org \
    --env PAPERLESS_TOKEN=your-api-token \
    -- uvx mcp-server-paperless@latest
```

Add `--scope user` to have it in every project. Then ask Claude for an overview of your Paperless
instance. `@latest` makes uvx check for a new release at every start; to stay on one, name its
version instead (`mcp-server-paperless@0.2.0`).

Or as a plugin, which asks for seven options (only the URL and the token are required), so the token
is in no command and Claude Code keeps it in its credential store. In a session (Claude Code 2.1.275 or later):

```text
/plugin install mcp-server-paperless --marketplace gardenbaum/mcp-server-paperless
```

From your shell, followed by `/plugin configure mcp-server-paperless@gardenbaum` in a session and a
restart of Claude Code:

```sh
claude plugin marketplace add gardenbaum/mcp-server-paperless
claude plugin install mcp-server-paperless@gardenbaum
```

The plugin also brings four skills (inbox triage, finding a document, annual review, taxonomy
cleanup) that Claude can pick by itself. The
[plugin](https://github.com/gardenbaum/mcp-server-paperless/blob/main/plugin/README.md) runs a
fixed release of the package (its dependencies are resolved at install). For the next one, refresh
the marketplace first, then restart Claude Code or run `/reload-plugins`:

```sh
claude plugin marketplace update gardenbaum
claude plugin update mcp-server-paperless@gardenbaum
```

Remove an entry you added with `claude mcp add` (`claude mcp remove paperless`), or every tool
appears twice.

> [!WARNING]
> The package is **`mcp-server-paperless`**. The PyPI project
> [`paperless-mcp`](https://pypi.org/project/paperless-mcp/) is a different server by someone
> else; `uvx paperless-mcp` would run it with your Paperless token.

### Claude Desktop

Add the server to `claude_desktop_config.json` (Settings, Developer, Edit Config) and restart:

```json
{
  "mcpServers": {
    "paperless": {
      "command": "uvx",
      "args": ["mcp-server-paperless@latest"],
      "env": {
        "PAPERLESS_URL": "https://paperless.example.org",
        "PAPERLESS_TOKEN": "your-api-token"
      }
    }
  }
}
```

If Claude Desktop cannot find `uvx`, give its full path (`which uvx`). Other MCP clients that
start a stdio server (Windsurf, Zed, Cline, Continue, ...) take the same command, arguments and
environment.

### VS Code

[Install in VS Code](https://insiders.vscode.dev/redirect/mcp/install?name=paperless&inputs=%5B%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22paperless_url%22%2C%22description%22%3A%22Paperless%20URL%2C%20e.g.%20https%3A%2F%2Fpaperless.example.org%22%7D%2C%7B%22type%22%3A%22promptString%22%2C%22id%22%3A%22paperless_token%22%2C%22description%22%3A%22Paperless%20API%20token%22%2C%22password%22%3Atrue%7D%5D&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22mcp-server-paperless%40latest%22%5D%2C%22env%22%3A%7B%22PAPERLESS_URL%22%3A%22%24%7Binput%3Apaperless_url%7D%22%2C%22PAPERLESS_TOKEN%22%3A%22%24%7Binput%3Apaperless_token%7D%22%7D%7D): VS Code asks for the Paperless URL and the API token and keeps the
token as a secret. By hand, add the server to `.vscode/mcp.json` with the same `uvx` command as
for Claude Desktop.

### Cursor

[Install in Cursor](https://cursor.com/en/install-mcp?name=paperless&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJtY3Atc2VydmVyLXBhcGVybGVzc0BsYXRlc3QiXSwiZW52Ijp7IlBBUEVSTEVTU19VUkwiOiJodHRwczovL3BhcGVybGVzcy5leGFtcGxlLm9yZyIsIlBBUEVSTEVTU19UT0tFTiI6InlvdXItYXBpLXRva2VuIn19), then replace the placeholder URL and token in the server settings
(`~/.cursor/mcp.json`). The repository is also a Cursor
[plugin](https://github.com/gardenbaum/mcp-server-paperless/blob/main/plugin/README.md#install-in-cursor)
for teams that set the two values in the Cursor dashboard.

### Container, for remote clients

The image serves MCP over Streamable HTTP and does not start without authentication:

```sh
export MCP_TOKEN="$(openssl rand -hex 32)"
docker run -d --name mcp-server-paperless -p 127.0.0.1:8001:8000 \
    -e PAPERLESS_URL=https://paperless.example.org \
    -e PAPERLESS_TOKEN=your-api-token \
    -e PAPERLESS_MCP_HTTP_AUTH_TOKENS="$MCP_TOKEN" \
    -e PAPERLESS_MCP_HTTP_ALLOWED_HOSTS=localhost \
    ghcr.io/gardenbaum/mcp-server-paperless:0.3.0

claude mcp add --transport http paperless http://127.0.0.1:8001/mcp \
    --header "Authorization: Bearer $MCP_TOKEN"
```

[Run in a container](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/container.md) has the compose file next to Paperless and the reverse
proxy setup, [Run over HTTP](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/http.md) the details of the transport.

### claude.ai

claude.ai custom connectors sign in with OAuth. Put the HTTP server behind your identity provider
(Authentik, Keycloak, Google, Auth0 or any other OpenID Connect provider) with five environment
variables: [Connect claude.ai](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/claude-ai-connector.md).

## Toolsets

The 97 tools are grouped into toolsets, and only two are on by default, so the model is not
handed tools you never use. Switch more on with `PAPERLESS_MCP_TOOLSETS=core,ai,pdf,trash` or
`all`, and restart the server. The assistant is told which toolsets are off, what each adds and how to
switch them on (never for a session: only you decide, see
[Configuration](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md#switching-a-toolset-on)).

| Toolset | Tools | Default | What it is for |
|---|---|---|---|
| `core` | 18 | on | Search, read, see pages as images, download, file and upload documents; run saved views; notes; tags, correspondents, types, storage paths and custom fields; background tasks |
| `ai` | 2 | on | Ask Paperless AI about your documents and get its filing suggestions (offered only while Paperless AI is enabled) |
| `bulk` | 5 | | Read many documents in one call, change many documents or taxonomy items in one step and resolve duplicates, with a dry run first; over stdio with upload directories also upload several files or a directory |
| `pdf` | 10 | | See all pages at a glance with their sizes, insert pages of another document, rotate, merge, split with metadata per part, extract and delete pages, edit PDFs, remove passwords, reprocess |
| `versions` | 6 | | The file versions of a document: list, upload, label, delete, merge documents as versions, correct the text |
| `trash` | 5 | | List and filter the trash, look into an entry, get its file, restore it, empty the trash |
| `views` | 5 | | Saved views: list, create, change and delete them (running them is in `core`) |
| `sharing` | 8 | | Public share links and bundles, and sending documents by e-mail |
| `permissions` | 3 | | Owners and view and change rights of documents and taxonomy; list users and groups |
| `mail` | 13 | | Mail accounts and rules for the mail import, and what it processed |
| `workflows` | 5 | | The workflows Paperless runs by itself: triggers and actions |
| `admin` | 16 | | Users, groups, application configuration, logs, tasks, bulk download |
| `raw` | 1 | | A read-only escape hatch: GET any path of the Paperless API the other tools do not cover |

Every tool with its kind and a one-line description is in the [tool
reference](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/tools.md); what to know before switching a toolset on is in
[Configuration](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md#toolsets).

## How it keeps your archive safe

An assistant that can file your documents can also delete them. The server is built so that this
takes more than a misunderstanding:

- **Confirmation before anything final.** Tools that delete, overwrite, publish or grant access
  first say what would happen (which documents, who gains access, what cannot be undone). Where
  your client can show a dialog you are asked in it, and nothing happens unless you accept; with
  any other client nothing changes until the call is repeated with `confirm=true`,
  unless you allow only dialogs with `PAPERLESS_MCP_REQUIRE_DIALOG=true`
  ([details](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md#confirmation)).
- **Dry runs for bulk edits.** `bulk_edit_documents`, `bulk_edit_taxonomy` and
  `resolve_duplicates` preview what would change before they ask for confirmation. A search filter or
  saved view never acts by itself: every document it would change is named first.
- **The trash first.** Deleting a document moves it to the Paperless trash, from where it can be
  restored. Emptying the trash is a separate tool in a toolset that is off by default.
- **You choose the surface.** Toolsets are opt-in, `PAPERLESS_MCP_READ_ONLY=true` offers only
  tools that change nothing, and `PAPERLESS_MCP_DISABLED_TOOLS` takes single tools away.
- **Paperless stays in charge.** The server acts as the user of its API token: the permissions
  set in Paperless apply to every call.
- **Secrets stay secret.** Passwords and API keys are write-only. Tool calls log neither tokens nor
  document text nor search queries at the default log level; the [privacy policy](https://github.com/gardenbaum/mcp-server-paperless/blob/main/PRIVACY.md) names the exceptions.
- **No reach beyond Paperless.** Callers cannot make the server read local files or reach other
  hosts. Uploading by file path and saving downloads exist only over stdio and only for directories you name.

Found a security problem? [Report it privately](https://github.com/gardenbaum/mcp-server-paperless/security/advisories/new).

## Configuration

The settings you are most likely to touch; [Configuration](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md) has all of
them.

| Variable | Meaning |
|---|---|
| `PAPERLESS_URL` | Base URL of your Paperless instance (required) |
| `PAPERLESS_TOKEN` | Paperless API token (required) |
| `PAPERLESS_PUBLIC_URL` | Base URL for links shown to you, if it differs from `PAPERLESS_URL` |
| `PAPERLESS_MCP_TOOLSETS` | Toolsets to offer, comma-separated, or `all` (default `core,ai`) |
| `PAPERLESS_MCP_READ_ONLY` | `true` to offer only tools that change nothing |
| `PAPERLESS_MCP_DISABLED_TOOLS` | Tools never offered, by name |
| `PAPERLESS_MCP_UPLOAD_DIRS` | Directories the model may upload files from by path (stdio only; default: none) |
| `PAPERLESS_MCP_DOWNLOAD_DIRS` | Directories the model may save downloads into (stdio only; default: none) |

## Compatibility

| | |
|---|---|
| **paperless-ngx** | 3.5.1 or later (API version 10). Pull requests run a fast E2E tier against 3.5.1, the whole suite runs after every merge and nightly, and the latest 3.x release weekly. 3.4.x, 3.5.0 and 2.x are not supported: the server warns about an older release in its log and in `get_instance_overview` |
| **Clients** | Claude Code and Claude Desktop over stdio, claude.ai custom connectors and other MCP clients over Streamable HTTP |
| **Runtime** | Python 3.14 or later (`uvx` fetches it for you), or the container image |

## Documentation

- [Configuration](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md): every variable, the toolsets, read-only mode, uploads
- [Tool reference](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/tools.md): all tools and prompts
- [API coverage](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/api-coverage.md): every Paperless endpoint, the tools that cover it, and what is still open
- [Run over HTTP](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/http.md): bearer tokens, allowed hosts, what the transport refuses
- [Connect claude.ai](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/claude-ai-connector.md): sign-in through your identity provider
- [Run in a container](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/container.md): the image, compose files, reverse proxy
- [Development](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/development.md): architecture, how it is tested, how to contribute

## Privacy Policy

You run the server yourself: it sends your data to your Paperless and your MCP client, keeps no
copy of your documents on disk and has no telemetry. The [privacy policy](https://github.com/gardenbaum/mcp-server-paperless/blob/main/PRIVACY.md) says what it reads,
writes, stores and logs, and where data goes (your client's AI model and Paperless's own AI
included).

## Known limitations

- **One Paperless user per server.** Every client of a server acts as the user of its
  `PAPERLESS_TOKEN`, also when clients sign in through an identity provider. Run one server per
  Paperless account.
- **`search_documents` with `more_like_id` can return nothing on Paperless 3.5.1.** Paperless
  3.5.1 (Tantivy 0.26) panics while scoring a more-like-this query once edited or deleted
  documents have left stale entries in the search index. Paperless catches the panic, logs
  `More Like This scoring panicked` and answers an empty list with HTTP 200, so the server cannot
  tell it from "no similar documents". If a result is empty although similar documents should
  exist, have an administrator run `document_index reindex` in the Paperless container (for
  example `docker compose exec webserver document_index reindex`). The fix is upstream in Tantivy
  (tantivy PR 2964); it needs a Paperless release that ships it.

## How this project is built

The code is written by AI agents and held to rules that are checked, not remembered: a layered
architecture enforced by import contracts, every design decision recorded with the test that
guards it (`backlog/decisions/`), a contract suite that runs the same tests against a fake and a
real Paperless, and mutation testing with a gate. Plans and task history are in `backlog/`. See
[Development](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/development.md) to run the checks yourself; issues and pull requests are
welcome.

## Credits

This is a new build, not a fork, but it learned from three existing paperless-ngx MCP servers and
adopts some of their ideas deliberately:

- [pvliesdonk/paperless-mcp](https://github.com/pvliesdonk/paperless-mcp) (MIT): field
  projection in lists, content slicing with a next offset, retries only for idempotent calls,
  token redaction, errors that name the next call, a central tool-annotation table, `web_url`,
  and the OIDC proxy beside bearer tokens.
- [baruchiro/paperless-mcp](https://github.com/baruchiro/paperless-mcp) (ISC): names instead of
  IDs in output, `confirm: true` for destructive calls, an upload allowlist, live end-to-end tests
  against a real Paperless, a test over every advertised tool schema.
- [OrellBuehler/paperless-mcp](https://github.com/OrellBuehler/paperless-mcp) (MIT): toolsets,
  a read-only mode and a test that every tool belongs to a toolset, coverage of Paperless 3.x
  features (versions, AI suggestions, trash, PDF operations), prompts that ask for confirmation,
  a test that every tool named in a prompt exists.

Thanks to the [paperless-ngx](https://github.com/paperless-ngx/paperless-ngx) and
[FastMCP](https://github.com/PrefectHQ/fastmcp) projects, which this server stands on.

## Licence

MIT, see [LICENSE](https://github.com/gardenbaum/mcp-server-paperless/blob/main/LICENSE).

---

<div align="center">

If mcp-server-paperless saves you time, a ⭐ helps other Paperless users find it.

</div>
