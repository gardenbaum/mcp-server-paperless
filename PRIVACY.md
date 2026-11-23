# Privacy policy

This policy covers mcp-server-paperless, the software in this repository. You run the server, or
someone you trust does; this project operates no service for it. Paperless, your MCP client, its
AI model and an identity provider are separate and have their own policies.

In short: the project receives no data from a running server. The server talks to the Paperless
instance you configure and to your MCP client, keeps no copy of your documents on disk, and has no
telemetry of its own.

## What the server reads and writes

- **Configuration.** It reads environment variables, including your Paperless URL and API token.
  The FastMCP library also reads its own settings from a `.env` file in the working directory, if
  there is one.
- **Paperless data.** When a tool, resource or prompt of your MCP client needs it, the server reads
  from Paperless: documents and their files, text and metadata, and, depending on the
  [toolsets](docs/configuration.md#toolsets) you switch on, also mail settings, users, permissions,
  workflows, logs and configuration. At startup it reads the Paperless version, statistics, system
  status, your user name and whether Paperless AI is on.
- **Changes in Paperless.** Tools can create, change and delete data there, with the permissions of
  the token's user. `PAPERLESS_MCP_READ_ONLY=true` removes the tools that change anything.
- **Files on the machine.** The server reads your files only over stdio, and only from the
  directories you list in `PAPERLESS_MCP_UPLOAD_DIRS` (default: none), when the model uploads one
  of them. It does not write to these directories.

## What the server stores

- **In memory.** Requests and answers while they are handled, the configured secrets, and
  Paperless lists used to resolve names (such as tags, correspondents, custom fields, saved views,
  users and groups). HTTP sessions are in memory too.
- **On disk.** None of your data, unless an HTTP deployment signs clients in through an identity provider
  and sets `PAPERLESS_MCP_OIDC_STORAGE_DIR`. That directory then holds client registrations,
  sign-in state and the provider's tokens (an ID token names you at the provider), encrypted with
  a key derived from `PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY`. Without the directory they are in memory.
- **In your browser.** With that sign-in, the consent page sets short-lived cookies.

## What the server logs

The server logs to standard error. Where that output ends up, and for how long, is up to your MCP
client or whoever runs the server.

At the default log levels it logs:

- for each tool call: the tool's name, a request id, the duration, and success or the type of error;
- for each HTTP request: method, path, status, duration and the request id (which the caller may
  choose), but not the query string, headers, body or the caller's IP address;
- startup messages, and failed or retried requests to Paperless with their path, which can
  contain ids and, for `paperless_api_get`, is the path the model asked for;
- errors outside tool calls: when a resource or prompt fails, its address or name and the error
  message, and when the startup check fails, possibly a traceback. These can quote a prompt's
  argument or Paperless's answer;
- with sign-in through an identity provider, identifiers such as client ids, your user id at the
  provider, and authorization codes the server does not recognise.

Apart from what this list names, it does not log your Paperless token, bearer tokens, tool
arguments, search queries, document text or file content at these levels.

**Debug logging is different.** With `PAPERLESS_MCP_LOG_LEVEL=DEBUG` or `FASTMCP_LOG_LEVEL=DEBUG`,
libraries can log tool arguments and answers, document content included. Use it only for
troubleshooting.

## Where your data goes

- **Your Paperless instance**, at `PAPERLESS_URL`: every request with the API token, and what you
  upload or change. The connection is encrypted if that URL is `https`.
- **Your MCP client and its AI model.** Answers, including document text and files, go to the
  client. The client decides what its model provider receives.
- **Services Paperless uses.** `ask_paperless_ai` and `get_ai_suggestions` make Paperless send
  document text to the language model it is configured with. Other tools can make Paperless publish
  a document under a public link, send it by e-mail, contact a mail server, or set up workflows
  that send e-mail or call webhooks.
- **Your identity provider**, if an HTTP deployment uses one: the sign-in and its tokens. The
  server also fetches the metadata of a client that identifies itself by a URL from that URL.
- **PyPI and the image registry**, when you install or update: `uvx` contacts PyPI (and may
  download Python), Docker contacts `ghcr.io`. `@latest` checks at every start. These are requests
  of your installer, not of the server.
- **A proxy**, if the usual proxy environment variables (such as `HTTPS_PROXY`) name one: the
  server's requests go through it.

There is no telemetry, analytics, crash reporting or update check. FastMCP includes the
OpenTelemetry interface, but no exporter is installed: trace data leaves the server only if whoever
runs it installs and configures one. Such traces can include error messages.

## If someone else runs the server

Everyone who uses an HTTP deployment works with the one Paperless account it is configured with.
Its operator holds that account's token, can read the logs, and controls a process that handles
every request and answer unencrypted. The same goes for the reverse proxy that adds TLS in front of
it. Use a deployment only if you trust its operator.

## How long data is kept

- **Requests and answers:** in memory while they are handled; the server makes no archive of them.
- **Name lists:** reread when they are older than five minutes, gone when the server stops.
- **HTTP sessions:** closed after 30 minutes without a request.
- **Sign-in state:** a pending sign-in expires after 15 minutes and an authorization code after 5.
  The provider's tokens are kept until the longest-lived of them expires; if the provider states
  no lifetime, up to a year from sign-in or the last refresh. Client registrations do not expire. In memory all of it is
  gone when the server stops. In the storage directory, expired files can remain: delete the
  directory's contents to remove everything.
- **Consent cookies:** at most 15 minutes.
- **Logs:** as long as your client or operator keeps them; the server never deletes any.
- **Data in Paperless, your client or at the model provider:** by their rules.

## Contact

Questions, problems and corrections: open an issue at
<https://github.com/gardenbaum/mcp-server-paperless/issues>. Issues are public, so leave out
tokens, document content and personal data. For data in a deployment, ask its operator.
