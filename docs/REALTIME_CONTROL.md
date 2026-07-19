# Real-time extension control channel

Lets an admin, from the webapp's **Facebook Listings** page, control a dealer's
running browser extension live: stop/start/override auto-processing, remove
duplicates, delete/relist/republish a listing, and watch the **full, untruncated
console log stream** in real time.

It's built on Django **Channels** (WebSockets) over the Redis you already run.

---

## Architecture

```
 Admin webapp ──(wss, role=admin&target=<uid>)──┐
                                                 ▼
                                     Django Channels consumer  ──(Redis channel layer)
                                                 ▲
 Dealer extension ─(wss, role=extension)─────────┘
```

- **Extension** connects as its own user, joins group `ext-<uid>`, receives
  `command` messages, executes them (through the existing publish guards + the
  global Facebook tab lock + anti-detection cooldowns), and streams its console
  output + acks + status up.
- **Admin** (staff only) connects with `?role=admin&target=<uid>`, joins group
  `obs-<uid>`, receives the extension's stream, and sends commands.
- Auth is the **same SimpleJWT access token** as the REST API, passed as
  `?token=` on the socket URL (browsers can't set WS headers). Origin validation
  is intentionally not enforced (the extension's origin is `chrome-extension://`).

Endpoint: `wss://<api-host>/ws/extension/`

### Commands
`stop_auto`, `start_auto`, `override_auto`, `remove_duplicates`,
`delete_listing` `{fb_listing_id | listing_id}`, `relist_listing` `{listing_id}`,
`republish_listing` `{listing_id}`, `report_status`, `set_log_stream` `{on}`.

Log streaming is **observer-gated**: the extension only uploads its console
stream while an admin panel is open (the panel re-arms a 5-minute window every
60s via `report_status`); when the panel closes, streaming lapses.

---

## Deploy (backend — required, only runs in your environment)

1. **Install deps** (added to `requirements.txt`):
   ```
   pip install channels==4.1.0 channels-redis==4.2.0 daphne==4.1.2
   ```
2. **Env** (optional): `CHANNELS_REDIS_DB` (default `3`) — a dedicated Redis DB
   index for the channel layer so control messages never collide with cache/celery.
   `REDIS_HOST/REDIS_PORT/REDIS_PASSWORD` are reused from your existing config.
3. **Migrate** (unrelated but shipped in the same batch — the "not published"
   snapshot table):
   ```
   python manage.py migrate VehicleListing   # applies 0038_unpublishedlistingsnapshot
   ```
4. **Serve ASGI, not WSGI** (you run in Docker — you don't launch a server by
   hand; you change the container `command`). WebSockets can't run under
   `gunicorn … relister.wsgi`. Already changed in this repo:
   - `docker-compose.prod.yaml` / `docker-compose.dev.yaml` — the `web` command
     now runs `gunicorn --workers 2 -k uvicorn.workers.UvicornWorker relister.asgi:application`
     (same gunicorn process manager + workers, but ASGI workers that speak
     WebSockets). Needs `uvicorn[standard]` (added to `requirements.txt`), so
     **rebuild the web image** (`docker compose build web`).
   - `docker-compose.yaml` (default) runs `manage.py runserver`, which serves
     WebSockets automatically because `daphne` is first in `INSTALLED_APPS` — no
     change needed there.
   - Because the channel layer is Redis-backed, the two gunicorn workers still
     see each other's groups, so admin↔extension routing works across workers.
5. **Nginx** — the WS upgrade block was added to `docker/nginx/nginx.conf`
   (`location /ws/` → `web:8000` with `Upgrade`/`Connection` headers +
   `proxy_read_timeout 3600s`). If your live TLS front-end uses
   `docker/nginx/nginxSslDomain.conf` instead, add the same `location /ws/`
   block there (it currently only has `location /`).

## Deploy (extension)

Rebuild (`bun run build`) and reload. It auto-connects to the control channel on
load and reconnects with backoff. No config needed beyond `VITE_API_BASE_URL`
(the WS URL is derived from it: `http→ws`, `https→wss`).

## Deploy (webapp)

Rebuild/redeploy. The **Facebook Listings** page now has a **Live control** switch
per dealer (presence, Start/Stop/Override/Remove-duplicates, live log console) and
per-row **Delete / Relist / Republish** action buttons.

---

## Verification checklist (in your environment)

1. Backend up on ASGI; `redis-cli -n 3 ping` works.
2. Open the extension (side panel) as a dealer → backend log shows a WS connect.
3. In the webapp → Facebook Listings → expand that dealer → toggle **Live control**:
   - chip flips to **“● extension online”** within a second or two;
   - status chips show `auto: active/paused`, version;
   - the **live log console** starts filling with the extension's console output.
4. Click **Stop** → extension logs `⏸️ Auto-processing PAUSED by admin (remote)`,
   ack shows `✓ stop_auto: OK`. Click **Start**/**Override run** → resumes / forces a run.
5. On a row, click **Delete** / **Relist** / **Republish** → the action runs through
   the normal publish/delete path (tab lock + cooldowns) and an ack returns.
6. **Remove duplicates** → extension deletes same-title extras (keeping newest),
   one every 18–28s (deletion cooldown).

## Safety

Remote commands only **enqueue** work in the extension — they still pass through
every publish guard (sold, <2 images, location, verification, failure cooldown,
daily quota) and the 90–120s publish / 18–28s delete anti-detection cooldowns.
A remote "publish now" cannot bypass those. Only `is_staff` users may send
commands, and each extension can only be controlled on its own user's channel.

---

## Production deployment (autorelister.com.au) — as actually deployed

The prod box runs the stack in Docker (`~/relister-api`, `docker compose`), behind
**host nginx** (`/etc/nginx/sites-available/autorelister.com.au`), with the
`web`/celery/beat/redis containers on the internal network and `web` published on
`127.0.0.1:8000`. React app + extension are static under `/var/www/autorelister/`.

**1. Redis — three things had to be right (each caused every WS to fail):**

  a. **≥4 databases.** The channel layer uses Redis DB index 3
     (`CHANNELS_REDIS_DB`, default 3). Redis started with `--databases 2` (only DB
     0–1) → `redis.exceptions.ResponseError: DB index is out of range`. Fixed: the
     redis `command:` in all compose files uses `--databases 16`. Keep
     `CHANNELS_REDIS_DB < --databases`.

  b. **No AUTH.** The local redis has no password (cache/Celery connect via
     `REDIS_URL`, not `REDIS_PASSWORD`). Passing `REDIS_PASSWORD` to the channel
     layer → `AuthenticationError: AUTH called without any password configured`
     and the connect 500'd. Fixed: `CHANNEL_LAYERS` connects via `REDIS_URL`
     (`redis://redis:6379/<CHANNELS_REDIS_DB>`), exactly like the cache — no
     password.

  c. **redis-py must be 5.0.x.** `channels-redis==4.2.0` is incompatible with
     redis-py 8.x (its async read-timeout cancels the channel layer's blocking
     receive → each WS drops after ~5–10s with `CancelledError`/`TimeoutError`).
     `requirements.txt` pins `redis==5.0.8`. Rebuild the image after changing it
     (a hot `pip install` is lost on container recreate).

**2. ASGI server.** All three compose files run
`gunicorn --workers 2 -k uvicorn.workers.UvicornWorker relister.asgi:application`
(needs `uvicorn[standard]`, in requirements). WSGI/`runserver` cannot serve WS.
Any code change forces a full image rebuild (the Dockerfile `COPY`s before
`pip install`), which also reinstalls Playwright — expect a slow build and watch
disk. Reclaim afterward with `docker builder prune -f`.

**3. Host nginx blocks** (added inside the `server { server_name
api.autorelister.com.au; … }` block):

```nginx
    # WebSocket control channel
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
    }
    # Django admin/DRF static (gunicorn does NOT serve static like runserver did)
    location /static/ {
        alias /var/www/autorelister/static/;
        expires 7d;
        access_log off;
    }
```
Then `sudo nginx -t && sudo systemctl reload nginx`.

**4. Admin/DRF static** (fixes the unstyled `/admin`): gunicorn doesn't serve
static. `STATIC_ROOT=/app/src/staticfiles` is bind-mounted to the host, but
`/home/ubuntu` is `750` so nginx (`www-data`) can't read it — so collect + copy to
the nginx-served path:
```bash
docker compose run --rm web python src/manage.py collectstatic --noinput
sudo cp -a ~/relister-api/src/staticfiles/. /var/www/autorelister/static/
```
Re-run these two after any Django/app upgrade that changes admin assets.

**Deploy sequence used:**
```bash
cd ~/relister-api && git pull origin master
docker compose build                 # installs channels/daphne/uvicorn (+Playwright)
docker compose run --rm web python src/manage.py migrate --noinput
docker compose run --rm web python src/manage.py collectstatic --noinput
sudo cp -a ~/relister-api/src/staticfiles/. /var/www/autorelister/static/
docker compose up -d                 # recreates redis (new --databases) + web (gunicorn/ASGI)
docker builder prune -f              # reclaim build cache
```

**Verify:**
```bash
docker logs web --tail 5             # "Using worker: uvicorn.workers.UvicornWorker"
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" \
  "https://api.autorelister.com.au/ws/extension/?role=extension"   # 403 (no token) = routed+auth-gated OK
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" \
  https://api.autorelister.com.au/static/admin/css/base.css        # 200 text/css
```
A valid dealer/admin token upgrades to `101`; check `docker logs web` shows no
`DB index is out of range`.
