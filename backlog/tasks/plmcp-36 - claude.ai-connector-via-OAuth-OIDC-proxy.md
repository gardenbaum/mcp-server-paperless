---
id: PLMCP-36
title: claude.ai connector via OAuth/OIDC proxy
status: To Do
assignee: []
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-22T19:27:49Z'
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
- [ ] #2 The OIDC proxy is plugged in at the composition root only, enabled only when its variables are set; nothing below server.py changes and the static-token mode is unchanged
- [ ] #3 An automated test runs the OAuth flow with the FastMCP client against a stub OIDC issuer; the claude.ai callback is allowlisted; incomplete OIDC settings refuse to start
- [ ] #4 README documents the OIDC setup as an optional layer with one worked provider example; the owner verifies it manually as a claude.ai connector
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
