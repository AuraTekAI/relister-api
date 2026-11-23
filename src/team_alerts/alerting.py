"""
Central alert sender for AutoRelister — the ONE place monitoring emails leave
from. Every monitor calls `raise_alert` while a problem exists and
`resolve_alert` when it is gone; this module decides what is actually sent:

    first occurrence      → "[ALERT] ..."            sent immediately
    still failing         → "[STILL FAILING] ..."    re-sent only after
                                                     ALERT_REMIND_HOURS (24)
    severity escalates    → sent immediately (e.g. disk warning → critical)
    fixed                 → "[RESOLVED] ..."          sent once, state cleared

Recipient groups (lists in .env, comma-separated):
    'kaz'   KAZ_ONLY      Gumtree, Facebook, restricted / checkpoint / logged-out
    'team'  KAZ_AND_TEAM  website, database, Redis, disk, backups, email and
                          image storage, scheduled tasks, ZenRows credits
support@autorelister.com.au is never used: Flashpost rejects it
(recipient_suppressed), so it is stripped from any list with a warning.

Delivery: Flashpost through the Django email backend with fail_silently=False.
`send_email` returns True ONLY when Flashpost accepted the message; a failure
is logged with the subject, recipients and error, and nothing is recorded as
sent — the monitor's next run raises again and the send is retried.

State: a small JSON file (ALERT_STATE_FILE under LOG_DIR, guarded by a file
lock) so it works while the database or Redis are the thing that is down.
Containers do not share LOG_DIR unless a volume is mounted, so de-duplication
is per container — fine, because each alert kind is produced by one process.

Weekly test: Beat task "Weekly_Alert_Test_Email" (Monday 09:00 UTC by default)
emails BOTH groups a "[TEST]" message listing the open alerts, so a broken
mail path is noticed within a week.
"""
import fcntl
import json
import logging
import os
import re
import tempfile
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

logger = logging.getLogger('relister_views')

SUPPRESSED_RECIPIENTS = {'support@autorelister.com.au'}
GROUP_SETTINGS = {'kaz': 'KAZ_ONLY', 'team': 'KAZ_AND_TEAM'}
_TAG_RE = re.compile(r'^\s*\[(ALERT|WARNING|CRITICAL|STILL FAILING|RESOLVED|TEST)\]\s*', re.I)


def _setting(name, default):
    return getattr(settings, name, default)


# ── recipients ───────────────────────────────────────────────────────────────

def recipients(group):
    """Addresses for a group name ('kaz' | 'team') or an explicit list."""
    if isinstance(group, (list, tuple)):
        raw = list(group)
    else:
        raw = _setting(GROUP_SETTINGS.get(group, 'KAZ_ONLY'), ['kazp@citysubaru.com.au'])
    if isinstance(raw, str):
        raw = raw.split(',')
    out = []
    for addr in raw:
        addr = (addr or '').strip()
        if not addr:
            continue
        if addr.lower() in SUPPRESSED_RECIPIENTS:
            logger.warning(f"alerting: dropping recipient {addr} — Flashpost rejects it (recipient_suppressed).")
            continue
        if addr not in out:
            out.append(addr)
    return out


# ── delivery ─────────────────────────────────────────────────────────────────

def send_email(group, subject, body):
    """Send one email. True ONLY when Flashpost accepted it. Never raises."""
    to = recipients(group)
    if not to:
        logger.error(f"alerting: no valid recipients for group {group!r} — '{subject}' NOT sent.")
        return False
    try:
        sent = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=to).send(fail_silently=False)
    except Exception as exc:
        logger.error(f"alerting: DELIVERY FAILED to {to} for '{subject}': {type(exc).__name__}: {exc}")
        return False
    if sent != 1:
        logger.error(f"alerting: DELIVERY FAILED to {to} for '{subject}': backend reported {sent} sent.")
        return False
    logger.info(f"alerting: sent '{subject}' to {to}")
    return True


# ── state ────────────────────────────────────────────────────────────────────

def _state_file():
    return _setting('ALERT_STATE_FILE', os.path.join(tempfile.gettempdir(), 'relister_alert_state.json'))


class _locked_state:
    """with _locked_state() as state: ... — read-modify-write under a file lock."""

    def __enter__(self):
        path = _state_file()
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self._lock = open(path + '.lock', 'a+')
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX)
        except Exception as exc:  # pragma: no cover - platform without flock
            logger.warning(f"alerting: could not lock state file: {exc}")
        self.state = {}
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.state = data
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning(f"alerting: could not read state file {path}: {exc}")
        return self.state

    def __exit__(self, exc_type, exc, tb):
        path = _state_file()
        try:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.state, fh)
            os.replace(tmp, path)
        except Exception as write_exc:
            logger.error(f"alerting: could not write state file {path}: {write_exc}")
        finally:
            try:
                fcntl.flock(self._lock, fcntl.LOCK_UN)
            except Exception:
                pass
            self._lock.close()
        return False


def open_alerts():
    with _locked_state() as state:
        return dict(state.get('open', {}))


def _fmt(iso):
    try:
        return timezone.datetime.fromisoformat(iso).strftime('%Y-%m-%d %H:%M UTC')
    except Exception:
        return iso or 'unknown'


# ── lifecycle ────────────────────────────────────────────────────────────────

def raise_alert(key, group, subject, body, severity=None):
    """
    Report that `key` is failing right now. Returns one of
        'sent'       first email for this issue
        'escalated'  severity changed (e.g. warning → critical), emailed now
        'reminded'   still failing after ALERT_REMIND_HOURS, emailed again
        'suppressed' already reported, nothing sent
        'failed'     delivery failed — nothing recorded, will retry next call
    """
    now = timezone.now()
    remind_hours = float(_setting('ALERT_REMIND_HOURS', 24))
    with _locked_state() as state:
        open_ = state.setdefault('open', {})
        entry = open_.get(key)
        if entry is None:
            kind = 'sent'
            full_subject = subject
            footer = 'First occurrence. You will get a reminder only if this is still failing after ' \
                     f'{remind_hours:g} hours, and a [RESOLVED] email when it is fixed.'
        elif severity and entry.get('severity') and severity != entry['severity']:
            kind = 'escalated'
            full_subject = subject
            footer = f"Severity changed from {entry['severity']} to {severity}. Open since {_fmt(entry['first_at'])}."
        elif remind_hours > 0 and now - timezone.datetime.fromisoformat(entry['last_sent_at']) >= timedelta(hours=remind_hours):
            kind = 'reminded'
            full_subject = f"[STILL FAILING] {_TAG_RE.sub('', subject)}"
            footer = f"Still failing since {_fmt(entry['first_at'])} (reminder #{int(entry.get('reminders', 0)) + 1}, every {remind_hours:g} hours)."
        else:
            logger.info(f"alerting: '{key}' already reported at {entry['last_sent_at']} — suppressed.")
            return 'suppressed'

        ok = send_email(group, full_subject, f"{body.rstrip()}\n\n{footer}\nAlert key: {key}\n")
        if not ok:
            return 'failed'
        new_entry = entry or {'first_at': now.isoformat(), 'reminders': 0}
        new_entry.update({
            'group': group if isinstance(group, str) else list(group),
            'subject': subject, 'severity': severity or new_entry.get('severity'),
            'last_sent_at': now.isoformat(),
            'reminders': int(new_entry.get('reminders', 0)) + (1 if kind == 'reminded' else 0),
        })
        open_[key] = new_entry
        return kind


def resolve_alert(key, note=None):
    """Report that `key` is no longer failing. Sends ONE [RESOLVED] email if the
    issue was open; True when the issue is closed (or was never open)."""
    now = timezone.now()
    with _locked_state() as state:
        open_ = state.setdefault('open', {})
        entry = open_.get(key)
        if entry is None:
            return True
        subject = f"[RESOLVED] {_TAG_RE.sub('', entry.get('subject') or key)}"
        body = (
            f"This issue is resolved.\n\n"
            f"Open since: {_fmt(entry.get('first_at'))}\nResolved at: {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Reminders sent: {entry.get('reminders', 0)}\n"
            + (f"\n{note}\n" if note else '')
            + f"\nAlert key: {key}\n"
        )
        if not send_email(entry.get('group', 'team'), subject, body):
            return False  # stays open; resolve is retried by the next monitor run
        del open_[key]
        return True


# ── weekly test ──────────────────────────────────────────────────────────────

@shared_task(queue='scheduling_queue')
def weekly_alert_test_email():
    opened = open_alerts()
    now = timezone.now().strftime('%Y-%m-%d %H:%M UTC')
    results = {}
    for group in ('kaz', 'team'):
        to = recipients(group)
        lines = [
            'This is the weekly test of the AutoRelister alert system. If you received it, alert emails are working.',
            '',
            f"Recipient list: {GROUP_SETTINGS[group]} = {', '.join(to) or '(none)'}",
            f"Reminder interval for ongoing issues: {_setting('ALERT_REMIND_HOURS', 24)} hours",
            f"Open alerts right now: {len(opened)}",
        ]
        for key, e in sorted(opened.items()):
            lines.append(f"  - {key}: {e.get('subject')} (since {_fmt(e.get('first_at'))})")
        lines += ['', 'No action is needed for this email.', f"Sent at: {now}"]
        results[group] = send_email(group, '[TEST] AutoRelister alert system is working', '\n'.join(lines) + '\n')
    logger.info(f"alerting: weekly test email results {results}")
    return results
