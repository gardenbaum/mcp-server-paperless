---
id: decision-25
title: 'Raw toolset: read-only GET under /api/ with a path allow-list'
date: '2026-11-17T00:17:00Z'
status: accepted
---

## Context

The `raw` toolset (PLMCP-32) gives the caller one tool, `paperless_api_get(path, params)`, for reading what the curated tools do not cover. It is the one tool whose request target is chosen by the caller, so it is the place where decision-7 (no arbitrary network or file access for remote callers) could be broken: an absolute URL, a `//host` or a traversal would turn the server into a fetcher for the host's network. It also bypasses what the curated tools guarantee: no secrets in results (decision-18), content slices, and files delivered only as capped MCP content blocks (decision-11).

## Decision

- **GET only, one destination.** The tool can only send GET to the configured Paperless; the URL is built only by the shared client (base URL, `Accept` version header and auth header of the current credential). The adapter re-checks the built request: same scheme, host and port as the base URL, path under `/api/`. Redirects are never followed; a 3xx answer is an error that does not reveal its `Location`.
- **Path allow-list, not a block-list.** `path` must be `/api/` followed by at least one segment of ASCII letters, digits, `_` and `-`, joined by single `/`, with an optional trailing `/` (`/api/` alone only redirects to the schema view and is refused). Everything else is rejected before any request: absolute URLs and schemes, `//host`, backslashes, `@`, `?`, `#`, `;`, `:`, spaces, control characters and new lines, every `%` (Paperless paths need no percent-encoding, so encoded dots, slashes, double encoding and `%2e%2e` are refused whole), non-ASCII text (look-alike dots and slashes), empty segments (`//`) and the segments `.` and `..`. The path is compared case-insensitively against the deny list below, so a case trick does not slip through. The query goes only in `params` (names `[A-Za-z0-9_.-]`, text, integer or boolean values, no control characters, bounded count and length). The param `format` is refused: Paperless answers it with a misleading API-version error.
- **Denied endpoints (secrets).** Any path with a segment `profile` (its `auth_token`, TOTP secret and social accounts), `config` (the LLM and OCR API keys), `mail_accounts` (mail passwords, decision-18; the mail toolset covers accounts), `token` and `oauth` (login flows; the OAuth callback also changes state on GET).
- **Denied endpoints (language model).** Any path with a segment `ai_suggestions`: it asks the model backend, which may cost money and sends document text to it, so it belongs to `get_ai_suggestions` in the `ai` toolset (cost warning, `PAPERLESS_MCP_DISABLED_TOOLS`).
- **Denied endpoints (files).** Any path with a segment `download`, `preview`, `thumb` or `bulk_download` (document files and share bundles): downloads belong to `download_document` and `get_document_thumbnail`, with their size and content rules (decision-11).
- **JSON only, secrets redacted.** Only `application/json` answers are returned; any other content type is refused without returning the body. Object keys that hold secrets (names containing `password`, `secret`, `api_key`, `apikey`, `auth_token`, `token` or `private_key`, except the flags `is_token`, `has_usable_password` and `token_expires`, and the webhook keys `headers`, `params` and `body`) are replaced with `[redacted]` at any depth, so endpoints that embed them (workflow actions with PDF passwords, global search) stay readable without leaking.
- **Size cap.** At most 1 MiB of the body is read (the connection is closed after that); a longer answer comes back as `truncated: true` with no data and a note to narrow it. An answer that was read is parsed, redacted and re-serialised compactly; if that text has 20 000 characters or fewer it comes back as `data`, otherwise as `truncated: true` with its first 20 000 characters as `text` (not valid JSON) and a note naming `page_size`, `fields` and filters. `next` and `previous` links are reduced to page numbers at any depth, so no host leaks (decision-7). Redaction always runs before the cut, so a cut text never holds a secret. The same-origin check lives in the client for every adapter call: scheme, host, port and userinfo must equal the configured base URL's and the path must stay below the base path; the adapter checks again that the built path is the base path plus the requested one. The adapter never retries (a GET of some endpoints costs money), and error bodies are read only up to 64 KiB.
- **Off by default.** The toolset is `raw`; it is not in the default `core,ai` selection (`all` selects it). The tool is read-only and idempotent, so read-only mode keeps it. It adds one tool and leaves the default profile's budget unchanged.
- **No query text in logs.** Nothing logs `params` or the response; the client logs method, path and status at DEBUG only (no query).

## Consequences

- The tool reaches every readable Paperless endpoint except the denied ones, with Paperless's own permissions per user (decision-9).
- A new Paperless endpoint that returns a secret is not denied automatically; redaction by key name covers the common names. Secrets inside values of other keys (for example a token in the URL of a webhook action) are not recognised, and Paperless answers an unknown path with a redirect (to its web app), which the tool reports as an error. A review of the vendored schema (`docs/vendor/paperless/openapi.json`) on every Paperless upgrade is the guard, and the test that lists the schema's GET endpoints with secret-like fields fails when a new one appears.
- Callers cannot read documents' files or change anything through this tool. Adding a path or URL parameter or a write method needs a new decision superseding this one (decision-7).

## Enforcement

- `tests/services/test_raw.py`: a table of rejected paths (encoded, double-encoded, traversal, absolute, scheme-relative, `@`, backslash, control characters, query smuggling, non-ASCII, case tricks) plus a Hypothesis property that no accepted path can leave `/api/`; the deny list and the redaction; the schema test that every GET endpoint of the vendored OpenAPI with secret-like response fields is denied or covered by redaction.
- `tests/adapter/test_raw.py`: the request always targets the configured host with the auth header; a redirect to another host is not followed; a request built for a hostile path is refused by the adapter itself.
- `tests/tools/test_raw.py` and `tests/tools/test_registry.py`: the tool is read-only, tagged `raw` only, absent from the default selection and from the default tool budget.
- Registry test of decision-7: no tool takes a filesystem path parameter; `paperless_api_get` takes a string API path validated as above.
