---
id: PLMCP-71
title: Add a privacy page
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:57:03Z'
updated_date: '2026-11-23T12:05:53Z'
labels: []
dependencies: []
priority: medium
type: docs
ordinal: 62000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Anthropic's docs ask local connectors for a privacy policy; users also want to know where their documents go. Owner approved on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 PRIVACY.md says what the server reads, writes, stores and logs, and to whom data goes (Paperless, the MCP client and its model, PyPI and the image registry on install, Paperless's own LLM for the ai tools), each statement checked against the code
- [ ] #2 README and docs link it, and the plugin manifest names it as its privacy policy URL once the plugin is merged
- [ ] #3 Tests keep the links valid
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
1. Research directory requirements and prove data flows (code reading plus canary/audit-hook runs over stdio and HTTP). 2. Red: PRIVACY.md joins the page guards; README section and docs links. 3. PRIVACY.md, README privacy section, docs links. 4. just check, evidence per statement in notes. The manifest URL waits for PLMCP-68.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Directory requirement (read 2026-11-23): connectors/building/submission requires of local connectors a "Privacy Policy" README section, manifest.json privacy_policies, HTTPS URL, covering collection, usage and storage, third-party sharing, retention, contact ("missing or incomplete ... immediate rejection"); but that page no longer accepts local MCPB servers and sends them to plugins. The plugin pre-submission checklist does not mention a privacy policy, and no check blocks on one. Software Directory Policy 3.A (binding for plugins): software that collects user data or connects to a remote service must provide a privacy policy link on collection, usage and retention; so in effect yes. plugin.json has an optional privacyPolicyUrl for the listing (manifest reference); the portal also asks data handling questions.

Evidence per statement of PRIVACY.md:
- No data to the project, no telemetry, no update check: only AsyncClient is paperless/client.py connect(); _build refuses any other origin; cli.py show_banner=False and uvicorn instead of FastMCP run (no PyPI check, decision-7); uv.lock holds opentelemetry-api only, no SDK. Run with sys.addaudithook (socket and file events, all toolsets, stdio and HTTP): connections only to the stub Paperless, one bind, no file opened for writing. Trace error messages: decision-15.
- Configuration: settings.py (no env_file); FastMCP settings.py ENV_FILE .env.
- Paperless reads, startup probe: server.py start(), paperless/instance.py; toolset modules. Writes/read-only: tools/selection.py hidden_tools.
- Files: services/upload_source.py, server.py by_path (stdio and upload_dirs), os.open read-only flags.
- Memory: services/taxonomy.py _cache, TaxonomyKind; TAXONOMY_TTL 300 s, entries replaced, not evicted; app.py stateless_http=False.
- Disk and encryption: app.py oauth_storage (FileTreeStore, Fernet, MemoryStore); FastMCP oauth_proxy/models.py UpstreamTokenSet/ClientCode; audit hook saw no write. Cookies: consent.py max_age 15 min (the 365-day ones are in "remember" mode, app.py passes True).
- Logs: observability.py CallLogging/RequestContext, app.py access_log=False. Canary runs (token, bearer, wrong bearer, query string, search query, invalid arguments, upload, result, host): none in INFO logs except tool name and the items PRIVACY.md lists (resource error text, via tools/_shared.py resource_boundary and FastMCP; prompt argument in a FastMCP traceback; startup traceback over HTTP quoting Paperless's answer, all reproduced). Paths on failure: client.py _send/_exchange/download. OIDC ids and unknown codes: FastMCP jwt.py, oauth_proxy/proxy.py 1370, cimd.py 442.
- DEBUG: reproduced: PAPERLESS_MCP_LOG_LEVEL=DEBUG logs answers over HTTP (sse_starlette chunk), FASTMCP_LOG_LEVEL=DEBUG logs tool arguments.
- Where data goes: tools/ai.py, tools/sharing.py, tools/mail.py, tools/workflows.py docstrings; app.py oidc_proxy (enable_cimd=True); httpx2 trust_env default True (proxies); README/Dockerfile install paths.
- Operator: one PAPERLESS_TOKEN per server; server speaks plain HTTP (docs/http.md, uvicorn without TLS).
- Retention: TAXONOMY_TTL; MCP DEFAULT_SESSION_IDLE_TIMEOUT 1800 s via session_idle_timeout="auto"; FastMCP oauth_proxy: transaction 15 min, code 5 min, token TTL max(...), refresh fallback 1 year, registrations without TTL, custom client_storage never culled.
- Contact: GitHub issues enabled; no SECURITY.md, private vulnerability reporting disabled (gh api, read only).

docs/configuration.md claimed no error messages are logged; corrected to the observed behaviour (tools only). Code unchanged.
AC #2 manifest part open: plugin/.claude-plugin/plugin.json exists only on plmcp-68-plugin; after its merge add privacyPolicyUrl https://github.com/gardenbaum/mcp-server-paperless/blob/main/PRIVACY.md. PAGES in test_docs.py conflicts trivially with that branch.
<!-- SECTION:NOTES:END -->
