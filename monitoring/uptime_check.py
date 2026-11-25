#!/usr/bin/env python3
"""
External uptime monitor for the Relister website + API.

Runs OUTSIDE the production server (any other machine: the testing server, a
laptop, a tiny VPS) from cron every few minutes. It has NO dependency on the
monitored server or on Django — only the Python 3 standard library — so it
keeps working when production is completely down.

    */3 * * * *  /usr/bin/python3 /path/to/relister-api/monitoring/uptime_check.py >> /var/log/relister-uptime.log 2>&1

Each run:
  1. GETs the website and the API (UPTIME_WEBSITE_URL / UPTIME_API_URL).
       website: up when HTTP 200 within UPTIME_TIMEOUT_SECONDS.
       api:     up when Django answers at all (any HTTP status below 500) —
                the login endpoint is used, so no credentials are needed.
  2. Keeps a consecutive-failure count per target in UPTIME_STATE_FILE.
  3. After UPTIME_FAILURES_BEFORE_ALERT (2) failures in a row it emails
     KAZ_AND_TEAM ONCE through Flashpost (same API the backend uses). Nothing
     more is sent while the target stays down; a short "recovered" email goes
     out when it comes back, and the counter resets.

Configuration: environment variables, or a KEY=VALUE file next to this script
named uptime.env (see uptime.env.sample). Never commit uptime.env.
"""
import json
import os
import socket
import sys
import time
import urllib.error
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
            # (name, url, is_up(status))
            ('website', os.environ.get('UPTIME_WEBSITE_URL', 'https://autorelister.com.au/'), lambda s: s == 200),
            ('api', os.environ.get('UPTIME_API_URL', 'https://api.autorelister.com.au/api/login/'), lambda s: s < 500),
        ],
        'timeout': float(os.environ.get('UPTIME_TIMEOUT_SECONDS', '15')),
        'failures_before_alert': int(os.environ.get('UPTIME_FAILURES_BEFORE_ALERT', '2')),
        'state_file': os.environ.get('UPTIME_STATE_FILE', os.path.join(HERE, 'uptime_state.json')),
        'recipients': recipients,
        'from_email': os.environ.get('DEFAULT_FROM_EMAIL', 'hello@autorelister.com.au'),
        'flashpost_url': os.environ.get('FLASHPOST_API_URL', '').rstrip('/'),
        'flashpost_key': os.environ.get('FLASHPOST_API_KEY', ''),
        'notify_recovery': os.environ.get('UPTIME_NOTIFY_RECOVERY', '1') not in ('0', 'false', 'no'),
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


# ── email (Flashpost HTTP API, as relister/email_backend.py) ─────────────────

def send_email(conf, subject, body):
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
    for name, url, is_up in conf['targets']:
        status, elapsed, error = check(url, conf['timeout'])
        up = status is not None and is_up(status)
        detail = f"HTTP {status}" if status is not None else f"no response ({error})"
        entry = state.get(name) or {'failures': 0, 'alerted': False}
        if up:
            if entry.get('alerted') and conf['notify_recovery']:
                down_since = entry.get('down_since', 'unknown')
                send(conf, f"[RECOVERED] Relister {name} is back up",
                     f"The Relister {name} is responding again ({detail}, {elapsed:.1f}s).\n\n"
                     f"URL: {url}\nDown since: {down_since}\nRecovered at: {now_utc()}\n")
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
                    "Check the server, Docker containers (web / nginx) and DNS. "
                    "This email is sent once per outage; a recovery notice follows when it is back.\n"
                )
                if send(conf, f"[ALERT] Relister {name} is DOWN — {detail}", body):
                    entry['alerted'] = True  # a failed send is retried next run
        state[name] = entry
        results[name] = 'up' if up else f"down ({entry['failures']})"
    save_state(conf['state_file'], state)
    return results


if __name__ == '__main__':
    out = run(cfg())
    log(f"summary: {out}")
    sys.exit(0)
