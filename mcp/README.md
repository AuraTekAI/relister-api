# Relister Debug MCP — hosted connector

Remote MCP server (Streamable HTTP + OAuth 2.1) so **Claude Desktop** can connect
to `https://mcp.autorelister.com.au` as a custom connector and debug a dealer's
extension/webapp/backend.

## Connect in Claude Desktop

Settings → Connectors → **Add custom connector** → URL:

```
https://mcp.autorelister.com.au
```

Claude runs the OAuth flow and opens a login page — sign in with your **admin
email + password** (must be a staff/superuser account). That's it; the
`relister-debug` tools appear in chat.

## Tools

`find_dealer`, `get_dealer_diagnostics`, `get_extension_logs`,
`list_dealer_listings`, `get_live_status`, `send_extension_command`,
`get_backend_health` — same as the local `mcp-debug` server, see its README for
details and example prompts.

## How it works

- Express app (`server.js`) serving OAuth discovery, Dynamic Client Registration,
  `/authorize` (login gate), `/token` (PKCE S256), and the MCP endpoint at `/`.
- The `/authorize` login is verified against the Django API (`/api/login/` + a
  staff-only endpoint) — only admins can connect.
- Tool calls run under a **service admin** account (`RELISTER_ADMIN_EMAIL` /
  `RELISTER_ADMIN_PASSWORD`), talking to the API over the internal Docker network
  (`http://web:8000/api`).

## Deploy

Runs as the `mcp` service in `docker-compose.prod.yaml`, behind the host nginx
(`mcp/nginx.mcp.conf`). Env for the service account lives in the compose `.env`
(`MCP_ADMIN_EMAIL` / `MCP_ADMIN_PASSWORD`). See `mcp/nginx.mcp.conf` for the nginx
+ certbot steps.

```bash
docker compose -f docker-compose.prod.yaml up -d --build mcp
```
