---
id: decision-46
title: The attach limits of downloads stay
date: '2026-11-23T22:17:58Z'
status: proposed
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
The attach limit is 512 KiB by default and at most 32 MiB (`PAPERLESS_MCP_MAX_DOWNLOAD_BYTES`). PLMCP-76 asks to keep or change it from measured client limits.

Measured with the MCP SDK client over streamable HTTP, a blob resource with 1.5 KB of text: in handshake (legacy) mode 760 KiB pass and 767 KiB fail ("Server-sent event exceeded the 1048576 byte limit"); in auto mode 8 MiB pass. From documentation, not measured: Claude Code has 25,000 output tokens by default (a warning at 10,000), saves a result without images over 50,000 characters to a file, and fails an HTTP body or event over 16 MiB. Read in its source: VS Code turns resources that are no images into linked resources, with no size limit seen. No official figure was found for Cursor, Codex, Claude Desktop and claude.ai.

The real clients are not measured yet; the steps are in the notes of PLMCP-76. This decision stays proposed until then.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- The default stays 512 KiB and the ceiling 32 MiB. A part is at most the attach limit.
- 512 KiB leave a third of the smallest measured limit for the report and the framing.
- Larger files are read in parts or saved (decision-45), so no client has to take a larger message.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Every client that takes 512 KiB today still does. A raised limit is the operator's choice; over HTTP about 12 MiB is the most that is useful with Claude Code.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- A lower default (what fits 25,000 tokens, about 75 KB of file): most scans would need parts, and how clients treat a blob resource is not known to count as tokens.
- A higher default: fails in clients with the SDK's limit of 1 MiB per event.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/tools/test_downloads_transport.py` (marker decision-46): a file and parts at the default limit over a real HTTP server in both modes of the client.
- `tests/test_settings.py`: the default and the ceiling.
