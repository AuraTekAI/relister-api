// Shared MCP tool definitions + backend auth. Used by the hosted HTTP server.
//
// The server authenticates to the Django API with a service admin account
// (RELISTER_ADMIN_EMAIL / RELISTER_ADMIN_PASSWORD) and auto-refreshes its token.
// (WHO may connect is gated separately by the OAuth login in server.js.)

import { z } from 'zod';

const API_BASE = (process.env.RELISTER_API_BASE || 'http://web:8000/api').replace(/\/+$/, '');
// When calling the API directly (bypassing nginx), Django checks ALLOWED_HOSTS
// against the Host header — `web:8000` is rejected (DisallowedHost). Send an
// allowed host instead.
const API_HOST = process.env.RELISTER_API_HOST || '';
const ADMIN_EMAIL = process.env.RELISTER_ADMIN_EMAIL || '';
const ADMIN_PASSWORD = process.env.RELISTER_ADMIN_PASSWORD || '';
const STATIC_TOKEN = process.env.RELISTER_ADMIN_TOKEN || '';

let tokens = { access: STATIC_TOKEN || null, refresh: null };
let loginInFlight = null;

async function rawFetch(path, { method = 'GET', headers = {}, body } = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(API_HOST ? { host: API_HOST } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  return { res, data };
}

export async function loginService() {
  if (!ADMIN_EMAIL || !ADMIN_PASSWORD) throw new Error('RELISTER_ADMIN_EMAIL / RELISTER_ADMIN_PASSWORD not set');
  const { res, data } = await rawFetch('/login/', { method: 'POST', body: { email: ADMIN_EMAIL, password: ADMIN_PASSWORD } });
  if (!res.ok || !data?.access) throw new Error(`service login failed (HTTP ${res.status})`);
  tokens = { access: data.access, refresh: data.refresh || null };
  return tokens.access;
}

async function refresh() {
  if (!tokens.refresh) return loginService();
  const { res, data } = await rawFetch('/refresh-token/', { method: 'POST', body: { refresh: tokens.refresh } });
  if (!res.ok || !data?.access) return loginService();
  tokens = { access: data.access, refresh: data.refresh || tokens.refresh };
  return tokens.access;
}

async function ensureAccess() {
  if (tokens.access) return tokens.access;
  if (!loginInFlight) loginInFlight = (tokens.refresh ? refresh() : loginService()).finally(() => { loginInFlight = null; });
  return loginInFlight;
}

export async function apiFetch(path, { method = 'GET', body, query } = {}) {
  let qs = '';
  if (query) {
    qs = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&');
  }
  const full = qs ? `${path}?${qs}` : path;
  const doCall = async () => {
    const access = await ensureAccess();
    return rawFetch(full, { method, body, headers: { Authorization: `Bearer ${access}` } });
  };
  let { res, data } = await doCall();
  if (res.status === 401) {
    tokens.access = null;
    await ensureAccess();
    ({ res, data } = await doCall());
  }
  if (!res.ok) {
    const detail = typeof data === 'string' ? data : JSON.stringify(data);
    throw new Error(`${method} ${path} → HTTP ${res.status}: ${detail?.slice?.(0, 800) || res.statusText}`);
  }
  return data;
}

/**
 * Verify a set of admin credentials are valid AND belong to a staff/superuser.
 * Used by the OAuth login gate so only admins can connect the connector.
 * Returns the user's email on success, throws otherwise.
 */
export async function verifyAdminCredentials(email, password) {
  const { res, data } = await rawFetch('/login/', { method: 'POST', body: { email, password } });
  if (!res.ok || !data?.access) throw new Error('invalid email or password');
  // Confirm staff by hitting an IsAdminUser-only endpoint with their token.
  const check = await rawFetch('/extension-logs/health/', { headers: { Authorization: `Bearer ${data.access}` } });
  if (check.res.status === 401 || check.res.status === 403) throw new Error('this account is not an admin');
  if (!check.res.ok) throw new Error(`could not verify admin status (HTTP ${check.res.status})`);
  return email;
}

const dealerParam = (dealer) => {
  const s = String(dealer).trim();
  return /^\d+$/.test(s) ? { user_id: s } : { email: s };
};
const ok = (obj) => ({ content: [{ type: 'text', text: typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2) }] });
const fail = (msg) => ({ isError: true, content: [{ type: 'text', text: `❌ ${msg}` }] });

const COMMANDS = ['stop_auto', 'start_auto', 'override_auto', 'remove_duplicates', 'delete_all', 'delete_listing', 'relist_listing', 'republish_listing', 'report_status', 'refresh', 'cancel', 'start_video', 'stop_video', 'close_extension', 'reload_extension', 'relist_all', 'relist_aged', 'delete_aged', 'delete_unmatched', 'publish_unpublished', 'delete_orphans'];

/** Register the debug tools on a McpServer instance. */
export function registerTools(server) {
  server.tool('find_dealer',
    'Search dealers by email, name, or dealership. Returns user_id + identity to pass to the other tools. Use first when you only have a name or email.',
    { query: z.string().describe('email, name, or dealership substring') },
    async ({ query }) => {
      try {
        const data = await apiFetch('/users/', { query: { search: query, page_size: 20 } });
        const rows = Array.isArray(data) ? data : (data.results || data.users || []);
        return ok({ count: rows.length, dealers: rows.map((u) => ({ user_id: u.id ?? u.user_id, email: u.email, name: u.dealership_name || u.contact_person_name || u.name || null, is_active: u.is_active })) });
      } catch (e) { return fail(e.message); }
    });

  server.tool('get_dealer_diagnostics',
    'One-shot health picture of a dealer: identity, subscription, extension sync status (incl. WHY Facebook is broken), listing counts, not-published listings WITH reasons, and recent extension logs. Start here for "why isn\'t X working for this dealer".',
    { dealer: z.string().describe('user_id or email'), log_lines: z.number().optional() },
    async ({ dealer, log_lines = 15 }) => {
      try {
        const dp = dealerParam(dealer);
        const [meta, snap, logs] = await Promise.all([
          apiFetch('/extension-logs/dealer-meta/', { query: dp }),
          apiFetch('/vehicle-listing/fb-snapshots/', { query: { ...dp, page_size: 1 } }).catch((e) => ({ _error: e.message })),
          apiFetch('/extension-logs/admin/', { query: { ...dp, limit: log_lines } }).catch((e) => ({ _error: e.message })),
        ]);
        const u = snap && snap.users && snap.users[0];
        return ok({
          user: meta.user, subscription: meta.subscription, sync_status: meta.sync_status,
          facebook: u ? { total_live: u.total, aged: u.aged, duplicates: u.duplicates, matched: u.matched, unpublished_count: u.unpublished_count } : null,
          not_published: u ? u.unpublished : (snap._error ? `error: ${snap._error}` : []),
          recent_logs: logs.logs ? logs.logs.map((l) => ({ at: l.created_at, log: l.log })) : (logs._error || []),
        });
      } catch (e) { return fail(e.message); }
    });

  server.tool('get_extension_logs',
    'Recent raw extension log entries for a dealer (newest first). Use `contains` to grep for a car title, listing id, or error string.',
    { dealer: z.string(), limit: z.number().optional(), contains: z.string().optional() },
    async ({ dealer, limit = 50, contains }) => {
      try { return ok(await apiFetch('/extension-logs/admin/', { query: { ...dealerParam(dealer), limit, contains } })); }
      catch (e) { return fail(e.message); }
    });

  server.tool('list_dealer_listings',
    'List a dealer\'s Facebook listings and their not-published backend listings (with skip reason). Optional title filter.',
    { dealer: z.string(), title_contains: z.string().optional() },
    async ({ dealer, title_contains }) => {
      try {
        const snap = await apiFetch('/vehicle-listing/fb-snapshots/', { query: { ...dealerParam(dealer), page_size: 1 } });
        const u = snap.users && snap.users[0];
        if (!u) return ok({ published: [], not_published: [], note: 'no snapshot for this dealer yet' });
        const q = (title_contains || '').toLowerCase();
        const pub = (u.listings || []).filter((l) => !q || (l.title || '').toLowerCase().includes(q));
        const unpub = (u.unpublished || []).filter((l) => !q || (l.title || '').toLowerCase().includes(q));
        return ok({
          published_count: pub.length,
          published: pub.map((l) => ({ fb_listing_id: l.fb_listing_id, title: l.title, price: l.price, days_on_fb: l.days_on_facebook, is_duplicate: l.is_duplicate, matched_listing_id: l.matched_listing_id, fb_url: l.fb_url })),
          not_published_count: unpub.length,
          not_published: unpub.map((l) => ({ listing_id: l.listing_id, title: l.title, price: l.price, images: l.images_count, reason: l.reason, reason_detail: l.reason_detail, source_url: l.source_url })),
        });
      } catch (e) { return fail(e.message); }
    });

  server.tool('get_live_status',
    'Ask the dealer\'s RUNNING extension for its live status now (auto paused/active, processing?, current activity, version, rate-limited). Times out if the extension is offline.',
    { dealer: z.string() },
    async ({ dealer }) => {
      try {
        const dp = dealerParam(dealer);
        const body = { command: 'report_status', wait: true };
        if (dp.user_id) body.user_id = Number(dp.user_id); else body.email = dp.email;
        const data = await apiFetch('/extension-logs/command/', { method: 'POST', body });
        if (data.result && data.result.timed_out) return ok({ online: false, note: 'extension did not respond — likely offline.' });
        return ok({ online: true, status: data.result?.status ?? data.result });
      } catch (e) { return fail(e.message); }
    });

  server.tool('send_extension_command',
    `Drive the dealer's extension in real time (same channel as the admin webapp). Waits for the ack and returns the real result. Commands: ${COMMANDS.join(', ')}. delete/relist/republish take payload {listing_id} or {fb_listing_id, title}. delete_all is destructive. cancel stops any running action.`,
    { dealer: z.string(), command: z.enum(COMMANDS), payload: z.record(z.any()).optional(), wait: z.boolean().optional() },
    async ({ dealer, command, payload, wait = true }) => {
      try {
        const dp = dealerParam(dealer);
        const body = { command, payload: payload || {}, wait };
        if (dp.user_id) body.user_id = Number(dp.user_id); else body.email = dp.email;
        return ok(await apiFetch('/extension-logs/command/', { method: 'POST', body }));
      } catch (e) { return fail(e.message); }
    });

  server.tool('get_backend_health',
    'Check the backend: DB + Redis/channel-layer reachability and dealer error counts. Tells a backend problem apart from an extension one.',
    {},
    async () => { try { return ok(await apiFetch('/extension-logs/health/')); } catch (e) { return fail(e.message); } });
}
