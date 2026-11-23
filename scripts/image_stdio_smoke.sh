#!/usr/bin/env bash
# Smoke test of a built stdio target (decision-38): `scripts/image_stdio_smoke.sh IMAGE`.
# It refuses to start without a Paperless token, then runs read-only as uid 10001, the way the
# Docker MCP Catalog starts it, and answers an MCP initialize and tools/list over stdin and stdout.
# Paperless need not exist: an unreachable Paperless does not stop the server.
set -euo pipefail

image="${1:?usage: image_stdio_smoke.sh IMAGE}"
name="mcp-server-paperless-stdio-smoke-$$"
tmp="$(mktemp -d)"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT

echo "1/4 refuses to start without a Paperless token"
if output="$(docker run --rm -i -e PAPERLESS_URL=http://paperless.invalid:8000 "$image" \
        < /dev/null 2>&1)"; then
    echo "the container started without PAPERLESS_TOKEN" >&2
    exit 1
fi
grep -q PAPERLESS_TOKEN <<< "$output" \
    || { echo "refusal does not name the variable: $output" >&2; exit 1; }

# stdin stays open (descriptor 3) until both answers are there: the server stops at its end.
mkfifo "$tmp/stdin"
docker run -i --init --name "$name" --read-only --cap-drop ALL --security-opt no-new-privileges \
    -e PAPERLESS_URL=http://paperless.invalid:8000 -e PAPERLESS_TOKEN=smoke-paperless-token \
    "$image" < "$tmp/stdin" > "$tmp/stdout" 2> "$tmp/stderr" &
exec 3> "$tmp/stdin"

# Waits for the response with this id that satisfies the jq condition; stdout is JSON-RPC only.
answer() {
    for _ in $(seq 1 60); do
        if [[ "$(jq "select(.id == $1) | $2" "$tmp/stdout" 2>/dev/null)" == true ]]; then return; fi
        sleep 1
    done
    echo "no valid answer to request $1" >&2
    cat "$tmp/stdout" "$tmp/stderr" >&2
    exit 1
}

echo "2/4 answers an MCP initialize"
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' >&3
answer 1 '.result.serverInfo.name == "mcp-server-paperless"'

echo "3/4 runs as uid 10001"
[[ "$(docker exec "$name" id -u)" == 10001 ]] || { echo "the container does not run as uid 10001" >&2; exit 1; }

echo "4/4 answers tools/list"
echo '{"jsonrpc":"2.0","method":"notifications/initialized"}' >&3
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' >&3
answer 2 '[.result.tools[].name] | index("search_documents") != null'
exec 3>&-
echo "stdio image smoke test passed"
