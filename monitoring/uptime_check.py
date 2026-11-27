#!/usr/bin/env python3
"""
External uptime monitor for the Relister website + API.

Runs OUTSIDE the production server (any other machine: the testing server, a
laptop, a tiny VPS) from cron every few minutes. It has NO dependency on the
monitored server or on Django — only the Python 3 standard library — so it
keeps working when production is completely down.

    */3 * * * *  /usr/bin/python3 /path/to/relister-api/monitoring/uptime_check.py >> /var/log/relister-uptime.log 2>&1

Each run:
  1. GETs the website, the API and the Redis health endpoint
     (UPTIME_WEBSITE_URL / UPTIME_API_URL / UPTIME_REDIS_URL).
       website: up when HTTP 200 within UPTIME_TIMEOUT_SECONDS.
       api:     up when Django answers at all (any HTTP status below 500) —
                the login endpoint is used, so no credentials are needed.
       redis:   up when /api/team-alerts/health/?component=redis answers 200.
                That endpoint PINGs Redis from the web process, so it works
                when Redis — Celery's broker — is down and no Celery task can.
  2. Keeps a consecutive-failure count per target in UPTIME_STATE_FILE.
  3. After UPTIME_FAILURES_BEFORE_ALERT (2) failures in a row it emails
     KAZ_AND_TEAM ONCE. website/api go through Flashpost (same API the
     backend uses); redis goes through the Amazon SES fallback
     (ALERT_FALLBACK_SES_*, same keys as the backend .env) and only falls back
     to Flashpost when SES is not configured. Nothing more is sent while the
     target stays down; a short "recovered" email goes out when it comes back,
     and the counter resets.

Configuration: environment variables, or a KEY=VALUE file next to this script
named uptime.env (see uptime.env.sample). Never commit uptime.env.
"""
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path):
    """Minimal KEY=VALUE loader; real environment variables win."""
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def cfg():
    load_env_file(os.environ.get('UPTIME_ENV_FILE', os.path.join(HERE, 'uptime.env')))
    recipients = [r.strip() for r in os.environ.get('KAZ_AND_TEAM', 'kazp@citysubaru.com.au').split(',') if r.strip()]
    return {
        'targets': [
            # (name, url, is_up(status)[, transport])
            ('website', os.environ.get('UPTIME_WEBSITE_URL', 'https://autorelister.com.au/'), lambda s: s == 200),
            ('api', os.environ.get('UPTIME_API_URL', 'https://api.autorelister.com.au/api/login/'), lambda s: s < 500),
            ('redis', os.environ.get('UPTIME_REDIS_URL', 'https://api.autorelister.com.au/api/team-alerts/health/?component=redis'),
             lambda s: s == 200, 'ses'),
        ],
        'timeout': float(os.environ.get('UPTIME_TIMEOUT_SECONDS', '15')),
        'failures_before_alert': int(os.environ.get('UPTIME_FAILURES_BEFORE_ALERT', '2')),
        'state_file': os.environ.get('UPTIME_STATE_FILE', os.path.join(HERE, 'uptime_state.json')),
        'recipients': recipients,
        'from_email': os.environ.get('DEFAULT_FROM_EMAIL', 'hello@autorelister.com.au'),
        'flashpost_url': os.environ.get('FLASHPOST_API_URL', '').rstrip('/'),
        'flashpost_key': os.environ.get('FLASHPOST_API_KEY', ''),
        'notify_recovery': os.environ.get('UPTIME_NOTIFY_RECOVERY', '1') not in ('0', 'false', 'no'),
        # Amazon SES fallback — same variable names as the backend .env.
        'ses_region': os.environ.get('ALERT_FALLBACK_SES_REGION', ''),
        'ses_access_key': os.environ.get('ALERT_FALLBACK_SES_ACCESS_KEY_ID', ''),
        'ses_secret_key': os.environ.get('ALERT_FALLBACK_SES_SECRET_ACCESS_KEY', ''),
        'ses_from': os.environ.get('ALERT_FALLBACK_SES_FROM_EMAIL', '') or os.environ.get('DEFAULT_FROM_EMAIL', 'hello@autorelister.com.au'),
    }


def now_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def log(msg):
    print(f"[{now_utc()}] {msg}", flush=True)


# ── checks ───────────────────────────────────────────────────────────────────

def check_url(url, timeout):
    """Returns (status_code_or_None, elapsed_seconds, error_text_or_None)."""
    req = urllib.request.Request(url, headers={'User-Agent': 'relister-uptime-monitor/1.0'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, time.monotonic() - started, None
    except urllib.error.HTTPError as exc:  # server answered with 4xx/5xx
        return exc.code, time.monotonic() - started, None
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return None, time.monotonic() - started, str(getattr(exc, 'reason', exc))


# ── state ────────────────────────────────────────────────────────────────────

def load_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log(f"WARNING could not read state file {path}: {exc}")
        return {}


def save_state(path, state):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, path)


# ── email: Amazon SES (SigV4, stdlib only — mirrors team_alerts.alerting._ses_send)

def _sigv4_authorization(method, host, path, body, region, service, access_key, secret_key, amz_date):
    """AWS Signature Version 4 for a form-encoded POST. Returns the Authorization header value."""
    date_stamp = amz_date[:8]
    canonical_headers = f"content-type:application/x-www-form-urlencoded; charset=utf-8\nhost:{host}\nx-amz-date:{amz_date}\n"
    signed_headers = 'content-type;host;x-amz-date'
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_request = f"{method}\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

    def _sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
    k_date = _sign(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, 'aws4_request')
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"


def send_email_ses(conf, subject, body):
    """SES SendEmail (API version 2010-12-01). True only when SES accepted it.
    Never touches Flashpost — this is the path for alerts Flashpost cannot carry."""
    region, access_key, secret_key = conf.get('ses_region'), conf.get('ses_access_key'), conf.get('ses_secret_key')
    if not (region and access_key and secret_key):
        log(f"ERROR cannot send '{subject}' via SES: ALERT_FALLBACK_SES_REGION / _ACCESS_KEY_ID / _SECRET_ACCESS_KEY not configured")
        return False
    fields = [('Action', 'SendEmail'), ('Version', '2010-12-01'), ('Source', conf.get('ses_from') or conf['from_email']),
              ('Message.Subject.Data', subject), ('Message.Subject.Charset', 'UTF-8'),
              ('Message.Body.Text.Data', body), ('Message.Body.Text.Charset', 'UTF-8')]
    fields += [(f'Destination.ToAddresses.member.{i}', addr) for i, addr in enumerate(conf['recipients'], start=1)]
    payload = urllib.parse.urlencode(fields).encode('utf-8')
    host = f"email.{region}.amazonaws.com"
    amz_date = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    req = urllib.request.Request(
        f"https://{host}/", data=payload, method='POST',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'X-Amz-Date': amz_date,
            'Authorization': _sigv4_authorization('POST', host, '/', payload, region, 'ses', access_key, secret_key, amz_date),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                log(f"alert sent via SES to {conf['recipients']}: {subject}")
                return True
            log(f"ERROR SES HTTP {resp.status} for '{subject}'")
    except urllib.error.HTTPError as exc:
        log(f"ERROR SES HTTP {exc.code} for '{subject}': {exc.read()[:300]!r}")
    except Exception as exc:
        log(f"ERROR sending '{subject}' via SES: {exc}")
    return False


# ── email (Flashpost HTTP API, as relister/email_backend.py) ─────────────────

def send_email(conf, subject, body, transport='flashpost'):
    if transport == 'ses':
        if conf.get('ses_region') and conf.get('ses_access_key') and conf.get('ses_secret_key'):
            return send_email_ses(conf, subject, body)
        log(f"WARNING SES not configured — sending '{subject}' through Flashpost instead")
    if not conf['flashpost_url'] or not conf['flashpost_key']:
        log(f"ERROR cannot send '{subject}': FLASHPOST_API_URL / FLASHPOST_API_KEY not configured")
        return False
    payload = json.dumps({
        'from': conf['from_email'], 'to': conf['recipients'], 'subject': subject, 'text_body': body,
    }).encode('utf-8')
    req = urllib.request.Request(
        conf['flashpost_url'] + '/v1/send', data=payload, method='POST',
        headers={'Authorization': f"Bearer {conf['flashpost_key']}", 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                log(f"alert sent to {conf['recipients']}: {subject}")
                return True
            log(f"ERROR Flashpost HTTP {resp.status} for '{subject}'")
    except urllib.error.HTTPError as exc:
        log(f"ERROR Flashpost HTTP {exc.code} for '{subject}': {exc.read()[:300]!r}")
    except Exception as exc:
        log(f"ERROR sending '{subject}': {exc}")
    return False


# ── main ─────────────────────────────────────────────────────────────────────

def run(conf, check=check_url, send=send_email):
    state = load_state(conf['state_file'])
    threshold = conf['failures_before_alert']
    results = {}
    for target in conf['targets']:
        name, url, is_up = target[:3]
        transport = target[3] if len(target) > 3 else 'flashpost'
        status, elapsed, error = check(url, conf['timeout'])
        up = status is not None and is_up(status)
        detail = f"HTTP {status}" if status is not None else f"no response ({error})"
        entry = state.get(name) or {'failures': 0, 'alerted': False}
        if up:
            if entry.get('alerted') and conf['notify_recovery']:
                down_since = entry.get('down_since', 'unknown')
                send(conf, f"[RECOVERED] Relister {name} is back up",
                     f"The Relister {name} is responding again ({detail}, {elapsed:.1f}s).\n\n"
                     f"URL: {url}\nDown since: {down_since}\nRecovered at: {now_utc()}\n", transport)
            entry = {'failures': 0, 'alerted': False, 'last_ok': now_utc()}
            log(f"{name} OK {detail} {elapsed:.1f}s")
        else:
            entry['failures'] = int(entry.get('failures', 0)) + 1
            entry.setdefault('down_since', now_utc())
            entry['last_error'] = detail
            log(f"{name} DOWN ({entry['failures']} in a row) {detail} after {elapsed:.1f}s")
            if entry['failures'] >= threshold and not entry.get('alerted'):
                body = (
                    f"The Relister {name} has failed {entry['failures']} consecutive uptime checks.\n\n"
                    f"URL: {url}\nLast result: {detail}\nFirst failure: {entry['down_since']}\n"
                    f"Checked from: {socket.gethostname()} at {now_utc()}\n\n"
                    + ("Redis is Celery's broker: scheduled tasks, relisting and the other alerts are stopped until it is back. "
                       "Check the redis container.\n" if name == 'redis' else
                       "Check the server, Docker containers (web / nginx) and DNS.\n")
                    + "This email is sent once per outage; a recovery notice follows when it is back.\n"
                )
                if send(conf, f"[ALERT] Relister {name} is DOWN — {detail}", body, transport):
                    entry['alerted'] = True  # a failed send is retried next run
        state[name] = entry
        results[name] = 'up' if up else f"down ({entry['failures']})"
    save_state(conf['state_file'], state)
    return results


if __name__ == '__main__':
    out = run(cfg())
    log(f"summary: {out}")
    sys.exit(0)
