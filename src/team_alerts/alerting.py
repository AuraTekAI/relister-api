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

Fallback transport: `transport='ses'` sends through Amazon SES (boto3, Kaz's
AWS account — ALERT_FALLBACK_SES_* settings) instead of Flashpost. It is used
for alerts ABOUT Flashpost (relister/email_backend.py reports 403 "sending
suspended", 422 "recipient suppressed" and any other delivery error here via
report_flashpost_error), so Flashpost is never asked to report its own
failure. An open alert remembers its transport so the [RESOLVED] email takes
the same route.

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

def _ses_send(to, subject, body):
    """Amazon SES fallback (never touches Flashpost). True only when SES accepted."""
    import boto3
    region = _setting('ALERT_FALLBACK_SES_REGION', '')
    access_key = _setting('ALERT_FALLBACK_SES_ACCESS_KEY_ID', '')
    secret_key = _setting('ALERT_FALLBACK_SES_SECRET_ACCESS_KEY', '')
    sender = _setting('ALERT_FALLBACK_SES_FROM_EMAIL', '') or settings.DEFAULT_FROM_EMAIL
    if not (region and access_key and secret_key):
        logger.error(f"alerting: SES fallback not configured (ALERT_FALLBACK_SES_REGION / _ACCESS_KEY_ID / _SECRET_ACCESS_KEY) — '{subject}' NOT sent.")
        return False
    client = boto3.client('ses', region_name=region, aws_access_key_id=access_key, aws_secret_access_key=secret_key)
    resp = client.send_email(
        Source=sender,
        Destination={'ToAddresses': to},
        Message={'Subject': {'Data': subject, 'Charset': 'UTF-8'}, 'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}}},
    )
    if not resp.get('MessageId'):
        raise RuntimeError(f"SES returned no MessageId: {resp}")
    return True


def send_email(group, subject, body, transport='flashpost'):
    """Send one email. True ONLY when the provider accepted it. Never raises.
    transport: 'flashpost' (default, the Django email backend) or 'ses'."""
    to = recipients(group)
    if not to:
        logger.error(f"alerting: no valid recipients for group {group!r} — '{subject}' NOT sent.")
        return False
    try:
        if transport == 'ses':
            ok = _ses_send(to, subject, body)
        else:
            ok = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=to).send(fail_silently=False) == 1
    except Exception as exc:
        logger.error(f"alerting: DELIVERY FAILED via {transport} to {to} for '{subject}': {type(exc).__name__}: {exc}")
        return False
    if not ok:
        logger.error(f"alerting: DELIVERY FAILED via {transport} to {to} for '{subject}': provider did not accept the message.")
        return False
    logger.info(f"alerting: sent '{subject}' to {to} via {transport}")
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

def raise_alert(key, group, subject, body, severity=None, transport='flashpost'):
    """
    Report that `key` is failing right now. Returns one of
        'sent'       first email for this issue
        'escalated'  severity changed (e.g. warning → critical), emailed now
        'reminded'   still failing after ALERT_REMIND_HOURS, emailed again
        'suppressed' already reported, nothing sent
        'failed'     delivery failed — nothing recorded, will retry next call
    transport: 'flashpost' (default) or 'ses' (alerts about Flashpost itself).
    """
    now = timezone.now()
    remind_hours = float(_setting('ALERT_REMIND_HOURS', 24))
    # Decide under the lock, send OUTSIDE it (a Flashpost failure during the
    # send reports itself through raise_alert again — must not deadlock), then
    # record under the lock again.
    with _locked_state() as state:
        entry = dict(state.setdefault('open', {}).get(key) or {}) or None
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

    ok = send_email(group, full_subject, f"{body.rstrip()}\n\n{footer}\nAlert key: {key}\n", transport=transport)
    if not ok:
        return 'failed'
    with _locked_state() as state:
        open_ = state.setdefault('open', {})
        new_entry = open_.get(key) or entry or {'first_at': now.isoformat(), 'reminders': 0}
        new_entry.update({
            'group': group if isinstance(group, str) else list(group),
            'transport': transport,
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
        entry = state.setdefault('open', {}).get(key)
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
    if not send_email(entry.get('group', 'team'), subject, body, transport=entry.get('transport', 'flashpost')):
        return False  # stays open; resolve is retried by the next monitor run
    with _locked_state() as state:
        state.setdefault('open', {}).pop(key, None)
    return True


# ── Flashpost self-monitoring (reported via SES, never via Flashpost) ────────

FLASHPOST_ERROR_MEANINGS = {
    403: 'sending suspended — Flashpost has blocked the account from sending',
    422: 'recipient suppressed — Flashpost refuses to deliver to this address',
}

# Set once an error was reported in this process so the next accepted send resolves it.
_flashpost_errored = False


def report_flashpost_error(status, detail, subject=None, to=None):
    """Called by relister/email_backend.py on every Flashpost delivery error.
    Raises 'flashpost:<status>' to KAZ_AND_TEAM through Amazon SES."""
    global _flashpost_errored
    _flashpost_errored = True
    code = int(status) if str(status).isdigit() else 0
    meaning = FLASHPOST_ERROR_MEANINGS.get(code, 'delivery error')
    logger.error(f"alerting: Flashpost error {status} ({meaning}) sending '{subject}' to {to}: {str(detail)[:300]}")
    try:
        return raise_alert(
            f'flashpost:{code or "error"}', 'team',
            f"[ALERT] Flashpost email error {status}: {meaning.split(' — ')[0]}",
            '\n'.join([
                f"Flashpost rejected an email with HTTP {status}: {meaning}.",
                '',
                f"Email subject: {subject or 'unknown'}",
                f"Recipients: {', '.join(to or []) or 'unknown'}",
                f"Flashpost response: {str(detail)[:500] or '(empty)'}",
                '',
                'Dealer emails (welcome, trial, invoices) and other alerts are failing until this is fixed.',
                'Check the Flashpost dashboard (account status, suppression list, sending limits).',
                'This email was sent through Amazon SES, not Flashpost.',
            ]) + '\n',
            transport='ses',
        )
    except Exception as exc:  # must never break the email backend
        logger.error(f"alerting: could not report Flashpost error via SES: {exc}")
        return 'failed'


def report_flashpost_ok():
    """Called by the backend after an accepted send; resolves any open Flashpost alert."""
    global _flashpost_errored
    if not _flashpost_errored:
        return
    _flashpost_errored = False
    try:
        for key in [k for k in open_alerts() if k.startswith('flashpost:')]:
            resolve_alert(key, note='Flashpost accepted an email again.')
    except Exception as exc:
        logger.error(f"alerting: could not resolve Flashpost alert: {exc}")


# ── S3 image upload monitoring ───────────────────────────────────────────────

_s3_errored = False


def report_s3_upload_failure(key, exc, bucket=None):
    """Called by VehicleListing/image_pipeline.py when a put_object fails.
    Raises 's3:upload' to KAZ_AND_TEAM (suppressed while the outage continues)."""
    global _s3_errored
    _s3_errored = True
    code = ''
    try:
        code = exc.response.get('Error', {}).get('Code', '')  # botocore ClientError
    except Exception:
        pass
    logger.error(f"alerting: S3 image upload FAILED for key {key} in bucket {bucket}: {code or type(exc).__name__}: {exc}")
    try:
        return raise_alert(
            's3:upload', 'team',
            f"[ALERT] S3 image upload failing{f' — {code}' if code else ''}",
            '\n'.join([
                'An image upload to Amazon S3 failed. Vehicle photos cannot be hosted until this is fixed,',
                'so new listings publish without hosted images (or fail to publish).',
                '',
                f"Bucket: {bucket or 'unknown'}",
                f"Key: {key}",
                f"Error: {code or type(exc).__name__}: {str(exc)[:500]}",
                '',
                'Check the AWS credentials, bucket policy and S3 service status. The image task retries with backoff;',
                'this alert resolves itself once an upload succeeds again.',
            ]) + '\n',
        )
    except Exception as report_exc:  # must never break the upload path
        logger.error(f"alerting: could not report S3 upload failure: {report_exc}")
        return 'failed'


def report_s3_upload_ok():
    global _s3_errored
    if not _s3_errored:
        return
    _s3_errored = False
    try:
        resolve_alert('s3:upload', note='An image upload to S3 succeeded again.')
    except Exception as exc:
        logger.error(f"alerting: could not resolve S3 upload alert: {exc}")


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
