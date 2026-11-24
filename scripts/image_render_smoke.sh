#!/usr/bin/env bash
# Part of both image smoke tests (decision-42): `scripts/image_render_smoke.sh IMAGE` renders a page
# and puts two files together with the image's own child process, read-only, without capabilities
# and network, as uid 10001. The image has no system fonts, so a process under the guard of the child can open no file and no socket.
set -euo pipefail

image="${1:?usage: image_render_smoke.sh IMAGE}"
docker run --rm -i --read-only --cap-drop ALL --security-opt no-new-privileges --network none \
    --entrypoint python "$image" -I - <<'PYTHON'
import asyncio, os
from mcp_server_paperless.domain.rendering import AssembleRequest, RenderRequest
from mcp_server_paperless.rendering import limits
from mcp_server_paperless.rendering.runner import ChildRenderer
page = b"1 0 0 rg 0 0 300 400 re f"
pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
pdf += b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 400]/Contents 4 0 R>>endobj "
pdf += b"4 0 obj<</Length %d>>stream\n%b\nendstream endobj trailer<</Root 1 0 R>>" % (len(page), page)
request = RenderRequest(mime_type="application/pdf", pages="1", size=400)
found = asyncio.run(ChildRenderer().render(pdf, request))
assert found.images[0].data[8:12] == b"WEBP" and os.getuid() == 10001
request = AssembleRequest(pages="1", after_page=1, target_pages=1, max_bytes=2**20)
made = asyncio.run(ChildRenderer().assemble(pdf, pdf, request))
assert made.inserted == (1,) and made.content.startswith(b"%PDF-")
assert not any(os.path.isdir(folder) for folder in limits.SYSTEM_FONTS), "system fonts: the child may open files"
PYTHON
docker run --rm -i --read-only --cap-drop ALL --security-opt no-new-privileges --network none \
    --entrypoint python "$image" -I - <<'PYTHON'
import socket
from mcp_server_paperless.rendering.limits import guard
guard(2**30, 30.0)
for act in (lambda: open("/etc/passwd"), lambda: open("/proc/1/environ"), socket.socket):
    try:
        act()
    except OSError as error:
        assert error.errno == 24, error  # EMFILE
    else:
        raise SystemExit("a process under the guard opened something")
PYTHON
