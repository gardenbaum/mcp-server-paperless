# MCP Server for Paperless-ngx

Talk to your [paperless-ngx](https://docs.paperless-ngx.com) 3.x archive: find, read, file, tag and
upload documents, by name, with a confirmation step before anything that cannot be undone. This
plugin installs
[mcp-server-paperless](https://github.com/gardenbaum/mcp-server-paperless), a community project
that is not affiliated with paperless-ngx.

## What you need

- paperless-ngx 3.5.1 or later that your computer can reach
- A Paperless API token: in Paperless open your profile menu, "My Profile", and create an API auth
  token. The server acts as that user and sees and changes only what that user may
- [uv](https://docs.astral.sh/uv/) on your computer; it fetches Python 3.14 by itself

## Skills

The plugin brings four skills for common jobs; the assistant can pick one by itself, or you name it.
They tell the assistant to propose first, to change nothing before your yes, and to treat text from
documents, including titles, notes, names and field values, as data, never as instructions. An
assistant can still err: the server's own confirmation step guards what is final, such as deleting,
but not a changed title, tag or correspondent. The skills need only the default toolsets.

| Skill | What it does |
|---|---|
| `paperless-triage-inbox` | Proposes a title, correspondent, type and tags for the documents in the inbox |
| `paperless-find-document` | Finds a document from a loose description |
| `paperless-annual-review` | Collects a year of documents, for example for the tax return |
| `paperless-cleanup-taxonomy` | Finds duplicate, unused and badly named tags, correspondents, types and storage paths |

The skills are written to use the same server configured elsewhere too, such as a claude.ai connector
(not tested).
Without any server they have no tools (chat on claude.ai starts no local server); the assistant is
told to say so instead of working around it.

## Install in Claude Code

In a session (Claude Code 2.1.275 or later):

```text
/plugin install mcp-server-paperless --marketplace gardenbaum/mcp-server-paperless
```

Or from your shell:

```sh
claude plugin marketplace add gardenbaum/mcp-server-paperless
claude plugin install mcp-server-paperless@gardenbaum
```

Claude Code asks for seven options, of which only the Paperless URL and the token are required. After
an install from the shell, or to change a value later, run
`/plugin configure mcp-server-paperless@gardenbaum` in a session, then restart Claude Code; the server
starts once the two required values are set. Claude Code keeps the token in its credential store (the
Keychain on macOS, or `.credentials.json` in its configuration directory, as on Linux) and the other
values in your `settings.json`. Then ask Claude for an overview of your Paperless instance.

If you added the server before with `claude mcp add`, remove that entry
(`claude mcp remove paperless`), or every tool appears twice.

To get a newer release, refresh the marketplace first, then restart Claude Code or run
`/reload-plugins` in a session:

```sh
claude plugin marketplace update gardenbaum
claude plugin update mcp-server-paperless@gardenbaum
```

| Option | Meaning |
|---|---|
| Paperless URL | Base URL of your Paperless instance (required) |
| Paperless API token | The token of the Paperless user to act as (required) |
| Public URL | Base URL for links shown to you, if it differs from the Paperless URL (default: none) |
| Toolsets | Toolsets to offer, comma-separated, or `all` (default: `core,ai`). Once set, must not be empty: type `core,ai` to restore the default |
| Read-only | Offer only tools that change nothing (default: off) |
| Disabled tools | Tools never offered, by name (default: none) |
| Upload directories | Directories the model may upload files from by path (default: none) |

[Configuration](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md)
explains the toolsets and options. The assistant knows which toolsets are off and tells you to
change the option Toolsets (then restart Claude Code); it cannot change it itself. The plugin is for Claude Code: chat on claude.ai starts no local
servers, and Cowork does not ask for the options. For claude.ai, see
[Connect claude.ai](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/claude-ai-connector.md).

## Install in Cursor

For yourself, use the [install link](https://github.com/gardenbaum/mcp-server-paperless/blob/main/README.md#cursor)
in the project README: Cursor's docs describe entering a
plugin's values only in the dashboard, by team admins; whether individuals can is untested.

For a team, an admin imports the repository as a team marketplace and sets `PAPERLESS_URL` and
`PAPERLESS_TOKEN` in the dashboard (Plugins, Configure). Everyone who shares these values acts as the
same Paperless user. The other options are not offered in Cursor.

To test only that the plugin loads, clone the repository, copy its `plugin/` folder to
`~/.cursor/plugins/local/mcp-server-paperless` and reload the window.

## What it runs and sends

The plugin holds no code, only the skills' instructions. It starts `uvx mcp-server-paperless==<version>`, which downloads that
release of the open-source package and its dependencies from PyPI, and a Python runtime if none
fits, and runs it on your computer. The version pins the package itself; its dependencies are the
newest ones it allows when uvx installs it. The server sends requests only to your Paperless URL, with your
token. What its tools return, including document text, goes to the AI model you use. Two tools
(`ask_paperless_ai` and `get_ai_suggestions`) make Paperless query the LLM backend you configured in
Paperless. The server reads local files only from the upload directories you name.

## License

MIT
