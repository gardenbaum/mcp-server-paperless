---
id: PLMCP-92
title: Serve the sign-in pages without a third party's logo
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T12:54:26Z'
updated_date: '2026-11-23T13:14:05Z'
labels: []
dependencies: []
priority: high
type: bug
ordinal: 73000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
FastMCP's OIDC consent and error pages load a logo from gofastmcp.com because the server sets no icon, so a third party sees the user's IP address during sign-in (found by the review of PLMCP-71). Owner approved the own embedded icon on 2026-11-23, before 0.3.3.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The consent and error pages make no request to a third party, proven by a test on the rendered pages
- [ ] #2 They show the project's own icon, embedded
- [ ] #3 The server's icon in initialize, if it is set by this, is deliberate, small, and noted
- [ ] #4 PRIVACY.md no longer names gofastmcp.com
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
1. Red tests on the rendered consent and error pages (tests/test_oidc.py).
2. FastMCP's icons setting reaches only the consent and unregistered-client pages; the callback error pages hard-code the FastMCP logo. So replace the logo URL in the HTML responses with one small ASGI middleware, added by build_app only with the OIDC layer; patching FastMCP's constant is against the repository rules. No server icon in initialize.
3. Ship icon.svg inside the package (assets/ is not in the wheel), kept equal to assets/icon.svg by a test.
4. PRIVACY.md: drop the gofastmcp.com entry.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
FastMCP's icons setting was not used: the callback error pages (create_error_html in oauth_proxy/proxy.py) ignore it and it would add a roughly 1 KB data URI to serverInfo for every client, stdio included. Server icons stay empty (tested). app.OwnIcon, added only with the OIDC layer, replaces FASTMCP_LOGO_URL in text/html responses; other responses, such as event streams, pass unchanged. Covers consent, unregistered client and all callback error pages. FastMCP's CSP already allows img-src data:; it is unchanged. The plain consent errors (an h1 and a p) never had a logo and still have none. Callback error pages keep FastMCP's alt text 'FastMCP'. The consent tooltip keeps its links to gofastmcp.com and modelcontextprotocol.io (loaded only on a click).
icon.svg (667 bytes) is in wheel and sdist (checked by building and importing the wheel); a test keeps it equal to assets/icon.svg. Red: six rendered pages, the HTML unit test and the icon copy failed; green after.

Review fix (I1): OwnIcon replaces only the first src="<logo URL>" attribute, quotes included. FastMCP escapes quotes in client and provider text, so that text stays unchanged and the page does not grow (before: a 960 KB client name gave a 36.7 MB page). Tests added for client_name, redirect_uri, error_description, page size, no OwnIcon without OIDC, and repeated Set-Cookie headers. The body limit covers /register (tested), but at the default upload limit it is 14046552 bytes: a 14 MB client name registers unauthenticated and its consent page is 28 MB. That is FastMCP's own behaviour and needs an owner decision; not changed. Alt text stays disclosed.
<!-- SECTION:NOTES:END -->
