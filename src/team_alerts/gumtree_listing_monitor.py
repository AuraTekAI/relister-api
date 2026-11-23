"""
Gumtree listing-count monitoring — runs right after each scheduled Gumtree
check (Check_Gumtree_Profile_Re-Listings, 03:00 and 08:00 UTC).

Data source: the existing append-only GumtreeSyncRun log (one row per fetch,
written by gumtree_scraper.get_gumtree_listings) — nothing new is stored.
    outcome='success'  fetched_count = ads Gumtree returned
    outcome='empty'    the count request SUCCEEDED and said 0
    outcome='failed'   ZenRows/network/parse error — count is NOT known

For every (dealer, Gumtree profile) fetched in this batch:
    current  = that batch's run           (skipped + logged when 'failed')
    previous = the most recent earlier run whose outcome is success/empty
               (a failed run is never a baseline)
Alert when  current == 0,  or  previous > 0 and current < RATIO × previous
(GUMTREE_DROP_ALERT_RATIO, default 0.5). With no previous good run only the
zero rule applies and the missing baseline is logged.

One email per batch to KAZ_ONLY listing every affected dealer. Each alert is
logged in the existing TeamAlertLog (alert_type 'gumtree_listing_drop',
active_listings = current count, inactive_listings = previous count) and that
log is also the de-duplication: a dealer is not re-alerted while the condition
persists from one run to the next (e.g. a profile that stays at 0). Once a run
is back to normal, a later drop alerts again.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from .failure_monitor import kaz_only
from .models import TeamAlertLog

logger = logging.getLogger('relister_views')

ALERT_TYPE = 'gumtree_listing_drop'
GOOD_OUTCOMES = ('success', 'empty')


def _ratio():
    try:
        return float(getattr(settings, 'GUMTREE_DROP_ALERT_RATIO', 0.5))
    except (TypeError, ValueError):
        return 0.5


def evaluate_counts(current, previous, ratio=None):
    """Pure rule. previous=None means no reliable baseline.
    Returns (should_alert, reason) ."""
    ratio = _ratio() if ratio is None else ratio
    if current == 0:
        return True, 'returned 0 listings'
    if previous is None:
        return False, 'no previous successful run to compare against'
    if previous > 0 and current < ratio * previous:
        return True, f"dropped to {current} from {previous} ({100.0 * current / previous:.0f}% of previous, threshold {ratio:.0%})"
    return False, 'ok'


def _previous_good_run(run):
    from VehicleListing.models import GumtreeSyncRun
    qs = GumtreeSyncRun.objects.filter(user_id=run.user_id, outcome__in=GOOD_OUTCOMES, created_at__lt=run.created_at)
    if run.seller_profile_id:
        qs = qs.filter(seller_profile_id=run.seller_profile_id)
    return qs.order_by('-created_at').first()


def _already_alerted_since(run, previous):
    """An alert logged after the previous run means the condition has persisted
    since then and was already reported — do not repeat it."""
    qs = TeamAlertLog.objects.filter(user_id=run.user_id, alert_type=ALERT_TYPE)
    if previous is not None:
        qs = qs.filter(sent_at__gt=previous.created_at)
    return qs.exists()


def _send_alert(items):
    to = kaz_only()
    names = ', '.join(i['dealer'] for i in items)
    subject = f"[ALERT] Gumtree listings dropped for {len(items)} dealer(s): {names}"[:200]
    lines = [
        f"The scheduled Gumtree check found {len(items)} dealer profile(s) returning 0 listings or under "
        f"{_ratio():.0%} of their previous run.",
        '',
    ]
    for i in items:
        lines += [
            f"Dealer: {i['dealer']}  (id {i['user_id']}, {i['email']})",
            f"  Gumtree profile: {i['profile']}",
            f"  Now: {i['current']} listings  ({i['reason']})",
            f"  Previous run: {i['previous_text']}",
            '',
        ]
    lines += [
        'A genuine drop means the dealer removed ads on Gumtree; an unexpected one usually means the profile URL changed or Gumtree is blocking the fetch.',
        'Each dealer is reported once per drop; it alerts again only after a normal run.',
        f"Checked at: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    try:
        sent = EmailMessage(subject=subject, body='\n'.join(lines) + '\n', from_email=settings.DEFAULT_FROM_EMAIL, to=to).send(fail_silently=False)
        if sent != 1:
            logger.error(f"gumtree_listing_monitor: Flashpost reported {sent} sent for '{subject}'")
            return False
        logger.info(f"gumtree_listing_monitor: alert sent to {to}: {names}")
        return True
    except Exception as exc:
        logger.error(f"gumtree_listing_monitor: FAILED to send alert to {to}: {exc}")
        return False


def monitor_gumtree_listing_counts(since, source='cron'):
    """Evaluate every Gumtree fetch recorded since `since` (the batch start).
    Never raises — a monitoring problem must not fail the Gumtree task."""
    from VehicleListing.models import GumtreeSyncRun
    summary = {'checked': 0, 'failed_runs': 0, 'alerts': 0, 'suppressed': 0, 'no_baseline': 0}
    try:
        runs = GumtreeSyncRun.objects.filter(created_at__gte=since, source=source).select_related('user').order_by('created_at')
        latest = {}
        for run in runs:  # keep only the last run per (dealer, profile) in this batch
            latest[(run.user_id, run.seller_profile_id)] = run

        items = []
        for run in latest.values():
            summary['checked'] += 1
            dealer = getattr(run.user, 'dealership_name', None) or run.user.email
            if run.outcome not in GOOD_OUTCOMES:
                summary['failed_runs'] += 1
                logger.warning(
                    f"gumtree_listing_monitor: {dealer} profile {run.seller_profile_id}: fetch FAILED "
                    f"(HTTP {run.http_status}, {run.error_detail}) — count unknown, not treated as 0."
                )
                continue
            previous = _previous_good_run(run)
            prev_count = previous.fetched_count if previous else None
            if previous is None:
                summary['no_baseline'] += 1
                logger.info(f"gumtree_listing_monitor: {dealer} profile {run.seller_profile_id}: first good run ({run.fetched_count} listings), no baseline yet.")
            alert, reason = evaluate_counts(run.fetched_count, prev_count)
            if not alert:
                logger.info(f"gumtree_listing_monitor: {dealer} profile {run.seller_profile_id}: {run.fetched_count} listings (previous {prev_count}) — {reason}")
                continue
            if _already_alerted_since(run, previous):
                summary['suppressed'] += 1
                logger.warning(f"gumtree_listing_monitor: {dealer} profile {run.seller_profile_id}: still {run.fetched_count} listings — already alerted, not repeating.")
                continue
            logger.error(f"gumtree_listing_monitor: {dealer} profile {run.seller_profile_id}: {reason} (previous run {prev_count}).")
            items.append({
                'run': run, 'previous': previous, 'user_id': run.user_id, 'email': run.user.email, 'dealer': dealer,
                'profile': run.seller_profile_id or (run.profile.url if run.profile else 'unknown'),
                'current': run.fetched_count, 'reason': reason,
                'previous_text': (f"{prev_count} listings at {previous.created_at.strftime('%Y-%m-%d %H:%M UTC')}" if previous else 'none recorded'),
            })

        if items and _send_alert(items):
            for i in items:
                TeamAlertLog.objects.create(
                    user_id=i['user_id'], alert_type=ALERT_TYPE,
                    active_listings=i['current'],
                    inactive_listings=i['previous'].fetched_count if i['previous'] else 0,
                )
            summary['alerts'] = len(items)
        elif items:
            logger.error("gumtree_listing_monitor: alert email not sent — nothing logged, so the next run will report again.")
    except Exception as exc:
        logger.error(f"gumtree_listing_monitor: unexpected error: {exc}", exc_info=True)
    logger.info(f"gumtree_listing_monitor: {summary}")
    return summary
