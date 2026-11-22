---
id: PLMCP-36
title: claude.ai connector via OAuth/OIDC proxy
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-22T19:31:09Z'
labels: []
milestone: m-5
dependencies:
  - PLMCP-34
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 36000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
claude.ai custom connectors need OAuth. Owner, 2026-11-22: no specific identity provider. The server uses FastMCP 4's generic OIDCProxy (DCR, CIMD, claude.ai callback), configured only through environment variables (issuer/discovery URL, client id, client secret, public base URL, allowed redirect URIs), so any OIDC provider works (e.g. Google, Authentik, Keycloak, Auth0). It is an optional layer on top: without those variables the HTTP transport keeps the static bearer token of decision-9/27. The README explains it as such.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A Backlog decision records the provider-agnostic OIDC proxy configuration and why, with its Enforcement section
- [ ] #2 An automated test runs the OAuth flow with the FastMCP client against a stub OIDC issuer; the claude.ai callback is allowlisted; incomplete OIDC settings refuse to start
- [ ] #3 README documents the OIDC setup as an optional layer with one worked provider example; the owner verifies it manually as a claude.ai connector
- [ ] #4 The OIDC proxy is plugged in at the composition root only and enabled only when its variables are set; it can run together with the static bearer token (claude.ai via OIDC, Claude Code via bearer), as pvliesdonk/paperless-mcp offers; nothing below server.py changes
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Prior art (owner asked to follow the three other servers, 2026-11-22): pvliesdonk/paperless-mcp uses FastMCP's OIDCProxy configured by env (base URL, OIDC config URL, client id/secret), also a JWT-verify-only 'remote' mode and bearer+OIDC together, all with one shared Paperless token, and documents claude.ai connectors; baruchiro/paperless-mcp has no OAuth, it passes each request's bearer through as the user's Paperless token; OrellBuehler/paperless-mcp runs its own OAuth 2.1 server (DCR, PKCE) and exchanges the upstream ID token at Paperless's headless SSO endpoint for that user's own Paperless token. PLMCP-36 follows pvliesdonk (provider-agnostic OIDCProxy, bearer alongside, one Paperless token). The JWT-verify-only mode is out of scope unless needed. For PLMCP-37 (deferred) the per-user options seen are baruchiro's token passthrough and OrellBuehler's Paperless SSO token exchange.
<!-- SECTION:NOTES:END -->
