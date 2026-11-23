# The recorder of the demo (`just demo`): vhs, with uv to run the released server from PyPI.
# renovate: datasource=docker depName=ghcr.io/astral-sh/uv
FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# renovate: datasource=docker depName=ghcr.io/charmbracelet/vhs
FROM ghcr.io/charmbracelet/vhs:v0.12.1@sha256:ea49a6a1c529be83153e88321892b5585964418f0b9055e8c1e0d732194234a3

COPY --from=uv /uv /uvx /usr/local/bin/
COPY demo.py demo.tape /demo/
WORKDIR /demo
# The packages as of this day, for the script and for the server; raise it with SERVER in demo.py.
ENV UV_EXCLUDE_NEWER=2026-11-23T12:00:00Z
# The seed also downloads everything the recording needs, so nothing is fetched on camera.
ENTRYPOINT ["sh", "-c", "uv run demo.py seed && vhs demo.tape && test -e played && cp new.gif /out/demo.gif"]
