"""
Daily extension-usage tracking (business logic for DailyActiveUser).

One row per user per day, keyed on the Australia/Perth date (settings.TIME_ZONE):

- "open"      the extension was opened. The first open of the day sets
               first_seen; every open (and every action) moves last_seen.
- "listed"    a product was published to Facebook.
- "relisted"  a product was relisted (delete + republish of an existing ad).
- "deleted"   a product was deleted from Facebook.
- "other"     any other counted action.

Action events also mark the user seen (active) for the day. All updates are
single atomic UPDATEs with F() expressions, so concurrent events (two tabs, a
retry racing the original) never lose a count.
"""
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import DailyActiveUser

OPEN_EVENT = 'open'
ACTION_FIELDS = {
    'listed': 'listed_count',
    'relisted': 'relisted_count',
    'deleted': 'deleted_count',
    'other': 'other_count',
}
VALID_EVENTS = (OPEN_EVENT, *ACTION_FIELDS)
MAX_COUNT_PER_EVENT = 100
MAX_SUMMARY_DAYS = 90


def _today_row(user, day):
    """Get or create today's row, tolerating a concurrent create (unique user+date)."""
    try:
        with transaction.atomic():
            row, _ = DailyActiveUser.objects.get_or_create(user=user, date=day)
        return row
    except IntegrityError:
        return DailyActiveUser.objects.get(user=user, date=day)


def record_activity(user, event, count=1, now=None):
    """
    Record one event for `user` and return the updated DailyActiveUser row.
    Raises ValueError for an unknown event or an out-of-range count.
    """
    if event not in VALID_EVENTS:
        raise ValueError(f"event must be one of: {', '.join(VALID_EVENTS)}")
    if event != OPEN_EVENT:
        if not isinstance(count, int) or isinstance(count, bool) or not (1 <= count <= MAX_COUNT_PER_EVENT):
            raise ValueError(f'count must be an integer between 1 and {MAX_COUNT_PER_EVENT}')

    now = now or timezone.now()
    day = timezone.localdate(now)
    row = _today_row(user, day)

    updates = {
        'active': True,
        'first_seen': Coalesce(F('first_seen'), now),  # set once, on the first event of the day
        'last_seen': now,
    }
    if event in ACTION_FIELDS:
        field = ACTION_FIELDS[event]
        updates[field] = F(field) + count
        updates['action_count'] = F('action_count') + count

    DailyActiveUser.objects.filter(pk=row.pk).update(**updates)
    row.refresh_from_db()
    return row


def serialize_day(row):
    return {
        'date': row.date.isoformat(),
        'active': row.active,
        'first_seen': row.first_seen.isoformat() if row.first_seen else None,
        'last_seen': row.last_seen.isoformat() if row.last_seen else None,
        'action_count': row.action_count,
        'listed_count': row.listed_count,
        'relisted_count': row.relisted_count,
        'deleted_count': row.deleted_count,
        'other_count': row.other_count,
    }


def _empty_day(day):
    return {
        'date': day.isoformat(),
        'active': False,
        'first_seen': None,
        'last_seen': None,
        'action_count': 0,
        'listed_count': 0,
        'relisted_count': 0,
        'deleted_count': 0,
        'other_count': 0,
    }


def daily_summary(user, days=14, today=None):
    """
    The last `days` days (newest first, today included). A day with no row is
    returned as inactive with zero counts, so the list always has `days` entries.
    """
    days = max(1, min(int(days), MAX_SUMMARY_DAYS))
    today = today or timezone.localdate()
    start = today - timedelta(days=days - 1)
    rows = {
        r.date: r
        for r in DailyActiveUser.objects.filter(user=user, date__gte=start, date__lte=today)
    }
    result = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        result.append(serialize_day(rows[day]) if day in rows else _empty_day(day))

    totals = {k: sum(d[k] for d in result) for k in (
        'action_count', 'listed_count', 'relisted_count', 'deleted_count', 'other_count')}
    totals['active_days'] = sum(1 for d in result if d['active'])
    return {'days': days, 'from': start.isoformat(), 'to': today.isoformat(),
            'totals': totals, 'usage': result}
