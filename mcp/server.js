#!/usr/bin/env node
/**
 * Hosted Relister debug MCP server (remote / Streamable HTTP) with a built-in
 * OAuth 2.1 provider, so it can be added to Claude Desktop as a custom connector
 * at https://mcp.autorelister.com.au.
 *
 * OAuth: Dynamic Client Registration + PKCE (S256). The /authorize step asks the
 * connecting person to log in with their **admin email + password**, verified
 * against the Django backend (must be staff/superuser). That is the whole gate —
 * once authenticated, tool calls run under the server's service admin account.
 *
 * Everything is in-memory (single instance); clients/tokens re-register after a
 * restart, which Claude handles transparently by re-running the OAuth flow.
 */

import crypto from 'node:crypto';
import express from 'express';
import cors from 'cors';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { isInitializeRequest } from '@modelcontextprotocol/sdk/types.js';
import { registerTools, verifyAdminCredentials, loginService } from './tools.js';

const PORT = parseInt(process.env.PORT || '9090', 10);
const ISSUER = (process.env.MCP_PUBLIC_URL || 'https://mcp.autorelister.com.au').replace(/\/+$/, '');
const TOKEN_TTL = 3600; // seconds

// ── in-memory OAuth stores ───────────────────────────────────────────────────
const clients = new Map();   // client_id -> { redirect_uris:Set }
const codes = new Map();     // code -> { client_id, redirect_uri, code_challenge, exp }
const accessTokens = new Map();  // token -> exp (ms)
const refreshTokens = new Map(); // refresh -> true
const rand = (n = 32) => crypto.randomBytes(n).toString('base64url');
const now = () => Date.now();

function s256(verifier) {
  return crypto.createHash('sha256').update(verifier).digest('base64url');
}

// ── build the MCP server (fresh per session) ─────────────────────────────────
function buildMcpServer() {
  const server = new McpServer({ name: 'relister-debug', version: '1.0.0' });
  registerTools(server);
  return server;
}

const app = express();
app.use(cors({ exposedHeaders: ['Mcp-Session-Id', 'WWW-Authenticate'], allowedHeaders: ['Content-Type', 'Authorization', 'Mcp-Session-Id', 'Mcp-Protocol-Version'] }));
app.use(express.json({ limit: '4mb' }));
app.use(express.urlencoded({ extended: true }));

// ── OAuth discovery (RFC 8414 / RFC 9728) ────────────────────────────────────
const authServerMetadata = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/authorize`,
  token_endpoint: `${ISSUER}/token`,
  registration_endpoint: `${ISSUER}/register`,
  response_types_supported: ['code'],
  grant_types_supported: ['authorization_code', 'refresh_token'],
  code_challenge_methods_supported: ['S256'],
  token_endpoint_auth_methods_supported: ['none'],
  scopes_supported: ['mcp'],
};
app.get('/.well-known/oauth-authorization-server', (_req, res) => res.json(authServerMetadata));
// Some clients look for OpenID-style discovery — serve the same doc.
app.get('/.well-known/openid-configuration', (_req, res) => res.json(authServerMetadata));
app.get('/.well-known/oauth-protected-resource', (_req, res) => res.json({
  resource: ISSUER,
  authorization_servers: [ISSUER],
  bearer_methods_supported: ['header'],
}));

// ── Dynamic Client Registration (RFC 7591) ───────────────────────────────────
app.post('/register', (req, res) => {
  const redirectUris = Array.isArray(req.body?.redirect_uris) ? req.body.redirect_uris : [];
  const clientId = rand(16);
  clients.set(clientId, { redirect_uris: new Set(redirectUris) });
  res.status(201).json({
    client_id: clientId,
    redirect_uris: redirectUris,
    token_endpoint_auth_method: 'none',
    grant_types: ['authorization_code', 'refresh_token'],
    response_types: ['code'],
    client_id_issued_at: Math.floor(now() / 1000),
  });
});

// ── Authorization endpoint: login form gated by admin credentials ────────────
function loginPage({ params, error }) {
  const hidden = Object.entries(params).map(([k, v]) => `<input type="hidden" name="${k}" value="${String(v ?? '').replace(/"/g, '&quot;')}">`).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Claude — Relister</title><style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b1020;color:#e5e7eb;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}
.card{background:#111827;padding:32px;border-radius:14px;width:340px;box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{font-size:18px;margin:0 0 4px}p{color:#9ca3af;font-size:13px;margin:0 0 20px}
label{display:block;font-size:12px;color:#9ca3af;margin:14px 0 6px}
input[type=email],input[type=password]{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #374151;background:#0b1020;color:#e5e7eb;font-size:14px}
button{margin-top:22px;width:100%;padding:11px;border:0;border-radius:8px;background:#6366f1;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
.err{background:#3f1d2b;border:1px solid #7f1d1d;color:#fca5a5;padding:8px 10px;border-radius:8px;font-size:13px;margin-bottom:8px}
</style></head><body><form class="card" method="post" action="/authorize">
<h1>Connect Claude to Relister</h1><p>Sign in with your admin account to authorize this connector.</p>
${error ? `<div class="err">${error}</div>` : ''}
<label>Admin email</label><input type="email" name="email" required autofocus>
<label>Password</label><input type="password" name="password" required>
${hidden}<button type="submit">Authorize</button></form></body></html>`;
}

const AUTH_FIELDS = ['client_id', 'redirect_uri', 'response_type', 'code_challenge', 'code_challenge_method', 'state', 'scope'];

app.get('/authorize', (req, res) => {
  const params = Object.fromEntries(AUTH_FIELDS.map((k) => [k, req.query[k]]));
  if (!params.client_id || !clients.has(params.client_id)) return res.status(400).send('unknown client_id');
  if (params.response_type !== 'code') return res.status(400).send('response_type must be code');
  if (params.code_challenge_method !== 'S256') return res.status(400).send('PKCE S256 required');
  res.set('Content-Type', 'text/html').send(loginPage({ params, error: '' }));
});

app.post('/authorize', async (req, res) => {
  const params = Object.fromEntries(AUTH_FIELDS.map((k) => [k, req.body[k]]));
  const client = clients.get(params.client_id);
  if (!client) return res.status(400).send('unknown client_id');
  if (!client.redirect_uris.has(params.redirect_uri) && client.redirect_uris.size > 0) return res.status(400).send('redirect_uri mismatch');
  try {
    await verifyAdminCredentials(req.body.email, req.body.password);
  } catch (e) {
    return res.status(200).set('Content-Type', 'text/html').send(loginPage({ params, error: e.message || 'login failed' }));
  }
  const code = rand(24);
  codes.set(code, { client_id: params.client_id, redirect_uri: params.redirect_uri, code_challenge: params.code_challenge, exp: now() + 5 * 60_000 });
  const u = new URL(params.redirect_uri);
  u.searchParams.set('code', code);
  if (params.state) u.searchParams.set('state', params.state);
  res.redirect(u.toString());
});

// ── Token endpoint ───────────────────────────────────────────────────────────
app.post('/token', (req, res) => {
  const grant = req.body.grant_type;
  if (grant === 'authorization_code') {
    const rec = codes.get(req.body.code);
    if (!rec || rec.exp < now()) return res.status(400).json({ error: 'invalid_grant' });
    codes.delete(req.body.code);
    if (rec.redirect_uri !== req.body.redirect_uri) return res.status(400).json({ error: 'invalid_grant', error_description: 'redirect_uri mismatch' });
    if (!req.body.code_verifier || s256(req.body.code_verifier) !== rec.code_challenge) return res.status(400).json({ error: 'invalid_grant', error_description: 'PKCE failed' });
    const access = rand(32); const refresh = rand(32);
    accessTokens.set(access, now() + TOKEN_TTL * 1000);
    refreshTokens.set(refresh, true);
    return res.json({ access_token: access, token_type: 'Bearer', expires_in: TOKEN_TTL, refresh_token: refresh, scope: 'mcp' });
  }
  if (grant === 'refresh_token') {
    if (!refreshTokens.has(req.body.refresh_token)) return res.status(400).json({ error: 'invalid_grant' });
    refreshTokens.delete(req.body.refresh_token);
    const access = rand(32); const refresh = rand(32);
    accessTokens.set(access, now() + TOKEN_TTL * 1000);
    refreshTokens.set(refresh, true);
    return res.json({ access_token: access, token_type: 'Bearer', expires_in: TOKEN_TTL, refresh_token: refresh, scope: 'mcp' });
  }
  return res.status(400).json({ error: 'unsupported_grant_type' });
});

// ── Bearer auth for the MCP endpoint ─────────────────────────────────────────
function requireAuth(req, res, next) {
  const h = req.headers.authorization || '';
  const token = h.startsWith('Bearer ') ? h.slice(7) : null;
  const exp = token && accessTokens.get(token);
  if (!exp || exp < now()) {
    if (token) accessTokens.delete(token);
    res.set('WWW-Authenticate', `Bearer resource_metadata="${ISSUER}/.well-known/oauth-protected-resource"`);
    return res.status(401).json({ error: 'invalid_token' });
  }
  next();
}

// ── MCP endpoint (Streamable HTTP) at root ───────────────────────────────────
const transports = {};

app.post('/', requireAuth, async (req, res) => {
  try {
    const sid = req.headers['mcp-session-id'];
    let transport = sid ? transports[sid] : undefined;
    if (!transport && isInitializeRequest(req.body)) {
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => crypto.randomUUID(),
        onsessioninitialized: (id) => { transports[id] = transport; },
      });
      transport.onclose = () => { if (transport.sessionId) delete transports[transport.sessionId]; };
      await buildMcpServer().connect(transport);
    } else if (!transport) {
      return res.status(400).json({ jsonrpc: '2.0', error: { code: -32000, message: 'No valid session; send an initialize request first.' }, id: null });
    }
    await transport.handleRequest(req, res, req.body);
  } catch (e) {
    if (!res.headersSent) res.status(500).json({ jsonrpc: '2.0', error: { code: -32603, message: String(e?.message || e) }, id: null });
  }
});

const sessionStream = async (req, res) => {
  const sid = req.headers['mcp-session-id'];
  const transport = sid ? transports[sid] : undefined;
  if (!transport) return res.status(400).send('unknown session');
  await transport.handleRequest(req, res);
};
app.get('/', requireAuth, sessionStream);
app.delete('/', requireAuth, sessionStream);
app.head('/', (_req, res) => res.status(200).end());

app.get('/healthz', (_req, res) => res.json({ ok: true, issuer: ISSUER }));

app.listen(PORT, '0.0.0.0', async () => {
  console.log(`[relister-mcp] listening on :${PORT}, issuer ${ISSUER}`);
  try { await loginService(); console.log('[relister-mcp] service admin login OK'); }
  catch (e) { console.error('[relister-mcp] WARNING service admin login failed:', e.message); }
});
