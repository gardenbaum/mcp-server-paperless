#!/usr/bin/env bash
# Smoke test of a built stdio target (decision-38): `scripts/image_stdio_smoke.sh IMAGE`.
# It refuses to start without a Paperless token, then runs read-only as uid 10001, the way the
# Docker MCP Catalog starts it, answers an MCP initialize and tools/list over stdin and stdout,
# exits with 0 at the end of stdin, and wrote nothing but JSON-RPC to stdout.
# Paperless need not exist: an unreachable Paperless does not stop the server.
set -euo pipefail

image="${1:?usage: image_stdio_smoke.sh IMAGE}"
name="mcp-server-paperless-stdio-smoke-$$"
tmp="$(mktemp -d)"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT

echo "1/6 refuses to start without a Paperless token"
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
pid=$!
exec 3> "$tmp/stdin"

# Waits for the response with this id that satisfies the jq condition.
answer() {
    for _ in $(seq 1 60); do
        if [[ "$(jq "select(.id == $1) | $2" "$tmp/stdout" 2>/dev/null)" == true ]]; then return; fi
        sleep 1
    done
    echo "no valid answer to request $1" >&2
    cat "$tmp/stdout" "$tmp/stderr" >&2
    exit 1
}

echo "2/6 answers an MCP initialize"
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' >&3
answer 1 '.result.serverInfo.name == "mcp-server-paperless"'

echo "3/6 runs as 10001:10001"
# Every process in the container, not only the user `docker exec` gets.
ids="$(docker exec "$name" sh -c 'cat /proc/[0-9]*/status' | grep -E '^(Uid|Gid):' | sort -u | tr '\t\n' '  ')"
[[ "$ids" == "Gid: 10001 10001 10001 10001 Uid: 10001 10001 10001 10001 " ]] \
    || { echo "expected 10001:10001, got $ids" >&2; exit 1; }

echo "4/6 answers tools/list"
echo '{"jsonrpc":"2.0","method":"notifications/initialized"}' >&3
echo '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' >&3
answer 2 '[.result.tools[].name] | index("search_documents") != null'

echo "5/6 exits with 0 at the end of stdin, with nothing but JSON-RPC 2.0 on stdout"
exec 3>&-
for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done
if kill -0 "$pid" 2>/dev/null; then echo "still running 30 s after the end of stdin" >&2; exit 1; fi
wait "$pid" || { echo "exit status $? after the end of stdin" >&2; cat "$tmp/stderr" >&2; exit 1; }
jq -Rne 'all(inputs; (try fromjson catch null)
    | type == "object" and .jsonrpc == "2.0" and (has("method") or has("result") or has("error")))' \
    "$tmp/stdout" > /dev/null || { echo "stdout is not only JSON-RPC:" >&2; cat "$tmp/stdout" >&2; exit 1; }
echo "6/6 renders a page in its child process"
"$(dirname "$0")/image_render_smoke.sh" "$image"
echo "stdio image smoke test passed"
