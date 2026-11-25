"""
Host-level server monitoring — disk space and memory of the SERVER, not the
container. Runs every HOST_MONITOR_INTERVAL_MINUTES via Celery Beat (registered
in VehicleListing/apps.py as "Host_Resource_Monitor"). Loaded by Celery through
CELERY_IMPORTS in settings.

How the host (not the container) is measured
  • Memory: /proc/meminfo inside a Docker container is the HOST kernel's
    meminfo — MemAvailable is the whole server's available memory.
  • Disk: each path in HOST_MONITOR_DISK_PATHS is measured with statvfs. Mount
    the host root read-only into the worker (`- /:/host:ro`) and list `/host`
    so the real root filesystem is measured; a path that is not mounted is
    skipped with a log line. `/` (the container overlay) is also reported, and
    on Docker it reflects the host disk that holds /var/lib/docker.

Alert rules (emails go to KAZ_AND_TEAM through team_alerts.alerting, which
sends the first occurrence, a reminder after ALERT_REMIND_HOURS, an immediate
email when the severity escalates, and a [RESOLVED] email on recovery)
  • Disk: WARNING when usage >= HOST_MONITOR_DISK_WARN_PCT (80), CRITICAL when
    >= HOST_MONITOR_DISK_CRIT_PCT (90).
  • Memory: alert when MemAvailable has been below HOST_MONITOR_MEMORY_LOW_MB
    (300) for HOST_MONITOR_MEMORY_LOW_MINUTES (10) consecutively — i.e.
    ceil(10 / interval) consecutive low readings; resolved when it recovers.

State (consecutive counts, current level per path) lives in a JSON file so it
survives between runs without depending on the DB.
"""
import json
import logging
import math
import os
import tempfile

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .alerting import raise_alert, resolve_alert

logger = logging.getLogger('relister_views')

MEMINFO_PATH = '/proc/meminfo'


# ── settings helpers ─────────────────────────────────────────────────────────

def _setting(name, default):
    return getattr(settings, name, default)


def _state_file():
    return _setting('HOST_MONITOR_STATE_FILE', os.path.join(tempfile.gettempdir(), 'relister_host_monitor.json'))


def _load_state():
    try:
        with open(_state_file(), 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning(f"host_monitor: could not read state file: {exc}")
    return {}


def _save_state(state):
    path = _state_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except Exception as exc:
        logger.error(f"host_monitor: could not write state file {path}: {exc}")


# ── measurements (host-level) ────────────────────────────────────────────────

def read_disk_usage(path):
    """(used_pct, used_gb, total_gb) for the filesystem holding `path`."""
    st = os.statvfs(path)
    total = st.f_blocks * st.f_frsize
    free_for_user = st.f_bavail * st.f_frsize
    used = total - free_for_user
    pct = (used / total * 100.0) if total else 0.0
    gb = 1024 ** 3
    return round(pct, 1), round(used / gb, 1), round(total / gb, 1)


def read_memory_available_mb(meminfo_path=MEMINFO_PATH):
    """Host MemAvailable in MB from /proc/meminfo (host kernel view, even in Docker)."""
    with open(meminfo_path, 'r', encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('MemAvailable:'):
                kb = int(line.split()[1])
                return kb / 1024.0
    raise RuntimeError('MemAvailable not found in meminfo')


def _body(lines):
    return '\n'.join(lines + ['', f"Detected at: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}", f'Server: {os.uname().nodename}']) + '\n'


# ── checks ───────────────────────────────────────────────────────────────────

LEVELS = {'ok': 0, 'warning': 1, 'critical': 2}


def _disk_level(pct, warn_pct, crit_pct):
    if pct >= crit_pct:
        return 'critical'
    if pct >= warn_pct:
        return 'warning'
    return 'ok'


def check_disk(state):
    warn_pct = float(_setting('HOST_MONITOR_DISK_WARN_PCT', 80))
    crit_pct = float(_setting('HOST_MONITOR_DISK_CRIT_PCT', 90))
    paths = _setting('HOST_MONITOR_DISK_PATHS', ['/host', '/'])
    disk_state = state.setdefault('disk', {})
    summary = {}

    for path in paths:
        if not os.path.isdir(path):
            logger.info(f"host_monitor: disk path {path} not mounted — skipped (mount the host root as /host:ro).")
            continue
        try:
            pct, used_gb, total_gb = read_disk_usage(path)
        except Exception as exc:
            logger.error(f"host_monitor: disk check failed for {path}: {exc}")
            continue

        level = _disk_level(pct, warn_pct, crit_pct)
        prev_level = (disk_state.get(path) or {}).get('level', 'ok')
        summary[path] = f"{level} {pct}%"
        key = f'host:disk:{path}'

        if level == 'ok':
            resolve_alert(key, note=f"Disk usage on {path} is back to {pct}%.")
        else:
            label = 'CRITICAL' if level == 'critical' else 'WARNING'
            threshold = crit_pct if level == 'critical' else warn_pct
            raise_alert(
                key, 'team',
                f"[{label}] Relister server disk {pct:.0f}% full ({path})",
                _body([
                    f"Disk usage on {path} is {pct}% ({used_gb} GB of {total_gb} GB used).",
                    f"Threshold: {threshold:.0f}% ({label.lower()}).",
                    'Free up space or extend the volume.',
                ]),
                severity=level,
            )

        disk_state[path] = {'level': level, 'pct': pct, 'checked_at': timezone.now().isoformat()}
        if prev_level != level:
            logger.warning(f"host_monitor: disk {path} {prev_level} → {level} ({pct}%).")
    return summary


def check_memory(state):
    low_mb = float(_setting('HOST_MONITOR_MEMORY_LOW_MB', 300))
    low_minutes = float(_setting('HOST_MONITOR_MEMORY_LOW_MINUTES', 10))
    interval = float(_setting('HOST_MONITOR_INTERVAL_MINUTES', 5))
    needed = max(1, math.ceil(low_minutes / max(interval, 1)))

    try:
        available_mb = read_memory_available_mb()
    except Exception as exc:
        logger.error(f"host_monitor: memory check failed: {exc}")
        return 'unknown'

    mem = state.get('memory') or {}
    low_count = int(mem.get('low_count') or 0)

    if available_mb >= low_mb:
        if low_count:
            logger.info(f"host_monitor: memory recovered — {available_mb:.0f} MB available after {low_count} low reading(s).")
            resolve_alert('host:memory', note=f"Available memory is back to {available_mb:.0f} MB.")
        state['memory'] = {'low_count': 0, 'available_mb': round(available_mb), 'checked_at': timezone.now().isoformat()}
        return f"ok {available_mb:.0f}MB"

    low_count += 1
    logger.warning(f"host_monitor: memory LOW — {available_mb:.0f} MB available (< {low_mb:.0f} MB) for {low_count} consecutive check(s).")
    if low_count >= needed:
        raise_alert(
            'host:memory', 'team',
            f"[ALERT] Relister server memory low — {available_mb:.0f} MB available",
            _body([
                f"Available memory has stayed below {low_mb:.0f} MB for about {low_count * interval:.0f} minutes"
                f" ({low_count} consecutive checks, every {interval:.0f} minutes).",
                f"Current MemAvailable: {available_mb:.0f} MB.",
            ]),
        )
    state['memory'] = {'low_count': low_count, 'available_mb': round(available_mb), 'checked_at': timezone.now().isoformat()}
    return f"low {available_mb:.0f}MB ({low_count}/{needed})"


@shared_task(queue='scheduling_queue')
def host_resource_monitor():
    state = _load_state()
    summary = {'disk': check_disk(state), 'memory': check_memory(state)}
    _save_state(state)
    logger.info(f"host_monitor: {summary}")
    return summary
