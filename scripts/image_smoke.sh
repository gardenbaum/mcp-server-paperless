#!/usr/bin/env bash
# Smoke test of a built image (decision-28): `scripts/image_smoke.sh IMAGE`.
# It refuses to start without tokens, then runs read-only as an unprivileged user with throwaway
# tokens, becomes healthy, answers 401 without a token, 421 for a foreign Host and an MCP
# initialize with a token.
# Paperless need not exist: an unreachable Paperless does not stop the server.
set -euo pipefail

image="${1:?usage: image_smoke.sh IMAGE}"
name="mcp-server-paperless-smoke-$$"
secret="smoke-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

echo "1/5 refuses to start without tokens"
if output="$(docker run --rm -e PAPERLESS_URL=http://paperless.invalid:8000 \
        -e PAPERLESS_TOKEN=smoke-paperless-token "$image" 2>&1)"; then
    echo "the container started without PAPERLESS_MCP_HTTP_AUTH_TOKENS" >&2
    exit 1
fi
grep -q PAPERLESS_MCP_HTTP_AUTH_TOKENS <<< "$output" \
    || { echo "refusal does not name the variable: $output" >&2; exit 1; }

echo "2/5 starts read-only as a non-root user and becomes healthy"
docker run -d --name "$name" --read-only --cap-drop ALL --security-opt no-new-privileges \
    -p 127.0.0.1::8000 \
    -e PAPERLESS_URL=http://paperless.invalid:8000 -e PAPERLESS_TOKEN=smoke-paperless-token \
    -e PAPERLESS_MCP_HTTP_AUTH_TOKENS="$secret" -e PAPERLESS_MCP_HTTP_ALLOWED_HOSTS=mcp.example.org \
    "$image" > /dev/null
[[ "$(docker exec "$name" id -u)" != 0 ]] || { echo "the container runs as root" >&2; exit 1; }
for _ in $(seq 1 60); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$name")"
    [[ "$status" == healthy ]] && break
    sleep 2
done
[[ "$status" == healthy ]] || { echo "health: $status" >&2; docker logs "$name" >&2; exit 1; }

endpoint="http://$(docker port "$name" 8000/tcp | head -1)/mcp"
echo "3/5 answers 401 without a token"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$endpoint")"
[[ "$code" == 401 ]] || { echo "expected 401, got $code" >&2; exit 1; }

echo "4/5 answers 421 for a foreign Host header even with a valid token"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$endpoint" -H 'Host: evil.example' \
    -H "Authorization: Bearer $secret" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' -d '{}')"
[[ "$code" == 421 ]] || { echo "expected 421, got $code" >&2; exit 1; }

echo "5/5 answers an MCP initialize with a token"
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$endpoint" \
    -H "Authorization: Bearer $secret" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}')"
[[ "$code" == 200 ]] || { echo "expected 200, got $code" >&2; exit 1; }
echo "image smoke test passed"
