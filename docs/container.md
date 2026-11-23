# Run in a container

Version tags (`v*`) publish an image to `ghcr.io/gardenbaum/mcp-server-paperless`, for
linux/amd64 and linux/arm64 (arm64 from 0.3.1), with a provenance and an SBOM attestation (the first
image is 0.3.0; the images of earlier releases were removed). It runs
`mcp-server-paperless-http` as a non-root user (uid 10001) from a read-only filesystem, listens on
`0.0.0.0:8000` inside the container and, like the plain command, **does not start without
`PAPERLESS_MCP_HTTP_AUTH_TOKENS` (or the [optional OIDC layer](claude-ai-connector.md)) and
`PAPERLESS_MCP_HTTP_ALLOWED_HOSTS`**. The unauthenticated
loopback flag has no place in a container, which is never loopback. The image contains no
credentials: everything comes from the environment.

## Next to Paperless with compose

The two secrets come from an `.env` file next to it, which is
not committed; create the Paperless token under "My Profile" and generate the MCP token with
`openssl rand -hex 32`. The service below belongs in the compose project that runs Paperless
(here the Paperless service is called `webserver`, as in the paperless-ngx example): only there
does `depends_on: [webserver]` work and the name `webserver` resolve. The stock Paperless
publishes port 8000 itself, so the server is published on 8001:

```yaml
services:
  mcp-server-paperless:
    image: ghcr.io/gardenbaum/mcp-server-paperless:0.3.0   # a published version tag; pin the digest too
    restart: unless-stopped
    depends_on:
      - webserver                                      # the paperless-ngx service
    environment:
      PAPERLESS_URL: http://webserver:8000             # how this container reaches Paperless
      PAPERLESS_TOKEN: ${PAPERLESS_API_TOKEN:?set it in .env}
      PAPERLESS_MCP_HTTP_AUTH_TOKENS: ${MCP_BEARER_TOKEN:?set it in .env}
      PAPERLESS_MCP_HTTP_ALLOWED_HOSTS: mcp.example.org  # the host name clients use (and your proxy forwards)
    ports:
      - "127.0.0.1:8001:8000"                          # a reverse proxy in front adds TLS
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
```

## With the OIDC layer

The [OIDC layer](claude-ai-connector.md) is in images built from `main` and in releases after
0.1.0. Add its variables to `environment` (the secrets from `.env` as well)
and, so that claude.ai stays connected across restarts, a writable directory for its state. The
container keeps its read-only filesystem: only the mounted directory is written, and it must
belong to the server's user (uid 10001). Without the mount, leave
`PAPERLESS_MCP_OIDC_STORAGE_DIR` unset and the state lives in memory:

```yaml
    environment:
      # ... as above, plus:
      PAPERLESS_MCP_OIDC_CONFIG_URL: https://authentik.example.org/application/o/mcp-server-paperless/.well-known/openid-configuration
      PAPERLESS_MCP_OIDC_CLIENT_ID: ${OIDC_CLIENT_ID:?set it in .env}
      PAPERLESS_MCP_OIDC_CLIENT_SECRET: ${OIDC_CLIENT_SECRET:?set it in .env}
      PAPERLESS_MCP_OIDC_BASE_URL: https://mcp.example.org      # its host is in PAPERLESS_MCP_HTTP_ALLOWED_HOSTS
      PAPERLESS_MCP_OIDC_JWT_SIGNING_KEY: ${OIDC_SIGNING_KEY:?set it in .env}
      PAPERLESS_MCP_OIDC_STORAGE_DIR: /var/lib/paperless-mcp
    volumes:
      - ./paperless-mcp-oauth:/var/lib/paperless-mcp          # mkdir it, then: sudo chown 10001:10001 paperless-mcp-oauth
```

With the OIDC layer on, `PAPERLESS_MCP_HTTP_AUTH_TOKENS` becomes optional: keep it for Claude
Code, drop it to allow sign-in through the provider only.

## In its own compose project

If the container lives in its own compose project, join Paperless's network instead (drop
`depends_on`, and use the service name or container name that network resolves):

```yaml
services:
  mcp-server-paperless:
    # ... as above, with PAPERLESS_URL: http://webserver:8000
    networks: [paperless]
networks:
  paperless:
    external: true
    name: paperless_default   # `docker network ls`: the network of the Paperless project
```

If you set `PAPERLESS_ALLOWED_HOSTS` in Paperless, it must include the name this container uses
to reach it (`webserver` above), or Paperless answers the server with 400.

## Reverse proxy, health and building it yourself

Put TLS, rate limiting and public exposure in a reverse proxy and list the host name it
forwards in `PAPERLESS_MCP_HTTP_ALLOWED_HOSTS`; clients then connect to
`https://mcp.example.org/mcp` with `Authorization: Bearer <MCP_BEARER_TOKEN>`. The container's
health check opens a TCP connection to the port (the server has no health route, decision-27), so
`docker ps` shows `healthy` once the server accepts connections. It does not tell you whether
Paperless is reachable: the tools report that. All other [settings](configuration.md) work
unchanged as environment variables. To build the image yourself, `just image` builds it and
`just image-smoke` also checks that it refuses to start without tokens, runs read-only as
non-root, becomes healthy and answers 401 without a token, 421 for a foreign `Host` and an MCP initialize with a token. The rules
are decision-28, carried over into decision-38.

The published image serves HTTP only. The Dockerfile also has the build target `stdio`, for the
[Docker MCP Catalog](https://hub.docker.com/mcp), which builds local servers from their repository
and starts them over stdin and stdout. `docker build --target stdio .` builds it and
`just image-stdio-smoke` also checks it. It runs as the same user, listens on no port and needs only
`PAPERLESS_URL` and `PAPERLESS_TOKEN`:

```sh
docker build --target stdio --tag mcp-server-paperless-stdio .
docker run --rm -i --init -e PAPERLESS_URL=https://paperless.example.org \
    -e PAPERLESS_TOKEN=your-api-token mcp-server-paperless-stdio
```

Uploading by path works only for directories you mount (`-v`, readable for uid 10001) and name in
`PAPERLESS_MCP_UPLOAD_DIRS` by their path in the container. The rules are decision-38.
