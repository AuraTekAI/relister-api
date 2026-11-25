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

Emails go to KAZ_ONLY through team_alerts.alerting, one alert key per
(dealer, profile): first drop emails Kaz, a persisting drop is suppressed
(reminder only after ALERT_REMIND_HOURS), and a normal run sends [RESOLVED].
Each email actually sent is also logged in the existing TeamAlertLog
(alert_type 'gumtree_listing_drop', active_listings = current count,
inactive_listings = previous count) for the admin audit trail.
"""
import logging

from django.conf import settings
from django.utils import timezone

from .alerting import raise_alert, resolve_alert
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


def _key(run):
    return f"gumtree:drop:{run.user_id}:{run.seller_profile_id or 'profile'}"


def _email(run, dealer, previous, reason):
    prev_count = previous.fetched_count if previous else None
    profile = run.seller_profile_id or (run.profile.url if run.profile else 'unknown')
    subject = f"[ALERT] Gumtree listings dropped — {dealer}: {run.fetched_count} listings ({reason.split(' (')[0]})"
    body = '\n'.join([
        f"The scheduled Gumtree check found dealer {dealer} {reason}.",
        '',
        f"Dealer: {dealer}  (id {run.user_id}, {run.user.email})",
        f"Gumtree profile: {profile}",
        f"Now: {run.fetched_count} listings",
        f"Previous run: {f'{prev_count} listings at {previous.created_at:%Y-%m-%d %H:%M} UTC' if previous else 'none recorded'}",
        '',
        'A genuine drop means the dealer removed ads on Gumtree; an unexpected one usually means the profile URL changed or Gumtree is blocking the fetch.',
        f"Checked at: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}",
    ]) + '\n'
    return subject, body


def monitor_gumtree_listing_counts(since, source='cron'):
    """Evaluate every Gumtree fetch recorded since `since` (the batch start).
    Never raises — a monitoring problem must not fail the Gumtree task."""
    from VehicleListing.models import GumtreeSyncRun
    summary = {'checked': 0, 'failed_runs': 0, 'alerts': 0, 'suppressed': 0, 'no_baseline': 0}
    try:
        runs = GumtreeSyncRun.objects.filter(created_at__gte=since, source=source).select_related('user', 'profile').order_by('created_at')
        latest = {}
        for run in runs:  # keep only the last run per (dealer, profile) in this batch
            latest[(run.user_id, run.seller_profile_id)] = run

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
                resolve_alert(_key(run), note=f"{dealer} now returns {run.fetched_count} listings (previous {prev_count}).")
                continue
            logger.error(f"gumtree_listing_monitor: {dealer} profile {run.seller_profile_id}: {reason} (previous run {prev_count}).")
            subject, body = _email(run, dealer, previous, reason)
            result = raise_alert(_key(run), 'kaz', subject, body)
            if result in ('sent', 'reminded', 'escalated'):
                summary['alerts'] += 1
                TeamAlertLog.objects.create(
                    user_id=run.user_id, alert_type=ALERT_TYPE,
                    active_listings=run.fetched_count, inactive_listings=prev_count or 0,
                )
            elif result == 'suppressed':
                summary['suppressed'] += 1
    except Exception as exc:
        logger.error(f"gumtree_listing_monitor: unexpected error: {exc}", exc_info=True)
    logger.info(f"gumtree_listing_monitor: {summary}")
    return summary
