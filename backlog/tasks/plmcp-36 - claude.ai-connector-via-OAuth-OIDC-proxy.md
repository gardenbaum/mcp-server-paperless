---
id: PLMCP-36
title: claude.ai connector via OAuth/OIDC proxy
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-22T20:20:36Z'
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
- [x] #1 A Backlog decision records the provider-agnostic OIDC proxy configuration and why, with its Enforcement section
- [x] #2 An automated test runs the OAuth flow with the FastMCP client against a stub OIDC issuer; the claude.ai callback is allowlisted; incomplete OIDC settings refuse to start
- [ ] #3 README documents the OIDC setup as an optional layer with one worked provider example; the owner verifies it manually as a claude.ai connector
- [x] #4 The OIDC proxy is plugged in at the composition root only and enabled only when its variables are set; it can run together with the static bearer token (claude.ai via OIDC, Claude Code via bearer), as pvliesdonk/paperless-mcp offers; nothing below server.py changes
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Decision first (decision-31, supersedes decision-27): optional provider-agnostic OIDC layer (FastMCP OIDCProxy, verify the upstream id_token, MultiAuth with the static bearer verifier), its settings, startup refusals, routes it adds, the own-origin allowance for the consent form, storage (memory by default, encrypted file store in PAPERLESS_MCP_OIDC_STORAGE_DIR), required signing key, redirect allowlist (claude.ai callback + loopback). Everything else of decision-27 carried over.
2. Failing tests first: tests/test_settings.py (OidcSettings formats, secrets masked), tests/test_app.py (startup matrix none/bearer/OIDC/both, incomplete OIDC refuses naming the missing variables, base URL host must be allowed, routes only with OIDC), new tests/test_oidc.py with a stub OIDC issuer (local loopback HTTP server: discovery, authorize, token, JWKS) and a scripted browser on a FastMCP OAuth client: full flow -> tools/list, claude.ai callback registers, foreign callback refused, bearer works alongside, no secrets in logs; tests/test_cli.py (incomplete OIDC exits 1 without echoing secrets).
3. Implement at the composition root only: settings.OidcSettings (PAPERLESS_MCP_OIDC_*), app.check_startup/authenticator/build_app/create_app, cli.main_http. Nothing below server.py.
4. README: optional OIDC layer section, one worked provider example (Authentik), claude.ai connector steps, container variables and the read-only filesystem.
5. just check, mutation on app and settings, mutation-equivalents.toml, notes and final summary (status stays In Progress).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Prior art (owner asked to follow the three other servers, 2026-11-22): pvliesdonk/paperless-mcp uses FastMCP's OIDCProxy configured by env (base URL, OIDC config URL, client id/secret), also a JWT-verify-only 'remote' mode and bearer+OIDC together, all with one shared Paperless token, and documents claude.ai connectors; baruchiro/paperless-mcp has no OAuth, it passes each request's bearer through as the user's Paperless token; OrellBuehler/paperless-mcp runs its own OAuth 2.1 server (DCR, PKCE) and exchanges the upstream ID token at Paperless's headless SSO endpoint for that user's own Paperless token. PLMCP-36 follows pvliesdonk (provider-agnostic OIDCProxy, bearer alongside, one Paperless token). The JWT-verify-only mode is out of scope unless needed. For PLMCP-37 (deferred) the per-user options seen are baruchiro's token passthrough and OrellBuehler's Paperless SSO token exchange.

Decision-31 created (accepted) and decision-27 set superseded by it via the CLI (the OIDC layer changes decision-27's startup refusal, its single-route rule and its empty origin list).
Red first: tests/test_settings.py OIDC tests failed on import (no OidcSettings); tests/test_oidc.py 20 failed (check_startup/authenticator/build_app had no oidc parameter); tests/test_cli.py OIDC tests failed (main_http did not load OidcSettings; the OIDC-only serve test failed with the old cli.py restored). All green after the composition-root changes in settings.py, app.py and cli.py.
FastMCP 4.2.1 combines a token verifier with the OIDC proxy through MultiAuth(server=OIDCProxy, verifiers=...) (docs/vendor/fastmcp/docs/servers/auth/multi-auth.mdx); required_scopes=[] on MultiAuth, because the proxy enforces openid on its own sessions and the static tokens carry no scopes.

Mutation: mutmut source_paths covers only services/ and paperless/ (unchanged here), so `mutmut run "<module>*"` finds nothing for app.py and settings.py. Ran it anyway with a temporary, uncommitted source_paths of app.py and settings.py (611 mutants over both modules, pre-existing code included). In the new functions, killing tests were added (origin behind a TLS proxy with and without a port, file as storage dir, rotated signing key forgets registrations without failing, error type and wording in refusals). Remaining survivors in new code: typing.cast first arguments (equivalent); host-or-"" fallbacks and env_prefix-or-"" (never empty, equivalent); the storage salt string (any fixed salt works, equivalent); base_url rstrip/lstrip and _origin default-port variants (FastMCP normalises the trailing slash and the default port 443, equivalent); "XX"-wrapped message strings whose asserted substring stays inside; os.access mutants (unkillable as root in this sandbox; test_a_read_only_storage_directory_refuses_to_start covers them on a non-root CI runner, skipped as root); require_authorization_consent=None/omitted and enable_cimd omitted (FastMCP defaults are the same values, equivalent); NOT covered: enable_cimd=False and jwt_signing_key=None (no test tells them apart without FastMCP internals). No entries added to mutation-equivalents.toml: its scoring reads only services/ and paperless/, and entries for app/settings would be unmatched (stale) in the weekly job.
Pins of FastMCP behaviour not written red first: cross-origin preflight 403 on OAuth routes, the exact route set, the 401 challenge, CIMD/consent redirects.
gitleaks flagged a high-entropy test signing key in tests/test_settings.py; replaced it with a low-entropy one and squashed the fix into the local feature commit (nothing was pushed).
Direct dependencies added: cryptography, py-key-value-aio (already transitive via FastMCP; app.py imports them, deptry DEP003). uv.lock: only the paperless-mcp entry changed (lock revision kept at 5).
just check green: 4520 passed, 1 skipped (root-only skip above), 23 snapshots passed (tool surface unchanged), import-linter 9 kept, diff coverage 100 %.
Manual verification of a real claude.ai connector against a real provider is left to the owner (AC3); the README UI labels for claude.ai and Authentik are written from the providers' documentation as remembered, not checked live.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added an optional, provider-agnostic OIDC layer to the HTTP transport (decision-31, which supersedes decision-27 and carries the rest of it over unchanged). PAPERLESS_MCP_OIDC_CONFIG_URL, _CLIENT_ID, _CLIENT_SECRET, _BASE_URL and _JWT_SIGNING_KEY are required together; _STORAGE_DIR (encrypted file store, default in memory) and _ALLOWED_REDIRECT_URIS (default: claude.ai callback plus loopback ports) are optional. Without them nothing changes. With them, app.authenticator builds FastMCP MultiAuth(OIDCProxy, static bearer verifier): claude.ai signs in through the provider (consent page, PKCE, ID-token verification), Claude Code keeps its bearer token, and one Paperless token is shared as before. Incomplete settings, a base URL host that is not allowed, an unwritable storage dir or an unreachable discovery document refuse to start with a clear message. The changes are in settings.py, app.py and cli.py only; import contracts and snapshots are unchanged. Tests: a stub OIDC issuer on loopback plus a scripted browser on the FastMCP OAuth client run the full flow; also bearer alongside, claude.ai versus foreign callbacks, origins, routes, storage across restarts, the startup matrix and no secrets in logs. README: the optional-layer section, an Authentik example, claude.ai steps and container variables with a uid-10001 volume. Open: AC3 manual claude.ai verification (owner); DoD 1 (some FastMCP pins not red first), 2 (CI) and 4 (merge) pending.
<!-- SECTION:FINAL_SUMMARY:END -->
