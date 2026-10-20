"""
Gumtree ⇄ database difference check for dealer diagnostics.

Answers, for one dealer: which ads currently on Gumtree are missing from our
database, which database records no longer exist on Gumtree, and why — reusing
the last successful sync by default (zero ZenRows cost) or a fresh small request
when live=True.

Design decisions (from the feature request):
- Ads are matched by their EXACT Gumtree identity (`list_id`), never by title.
- The "current Gumtree ads" set comes from the last SUCCESSFUL GumtreeSyncRun by
  default; live=True fetches it fresh in one small request.
- A FAILED sync (or zero fetched ads on failure) is NEVER read as "no ads exist":
  we fall back to the last successful run and flag the failure.
- Reconciliation identity: gumtree_total == matched_in_db + missing_from_db.

Reads only existing data (VehicleListing + GumtreeProfileListing + GumtreeSyncRun).
"""
from .models import VehicleListing, GumtreeProfileListing, GumtreeSyncRun

# Reasons a Gumtree⇄DB difference exists.
REASON_NOT_YET_SYNCED = 'NOT_YET_SYNCED'   # on Gumtree, created after the last successful sync
REASON_SOLD_OR_REMOVED = 'SOLD_OR_REMOVED'  # in DB, no longer on Gumtree
REASON_SYNC_FAILURE = 'SYNC_FAILURE'       # the last sync failed, so the gap can't be trusted/confirmed
REASON_UNKNOWN = 'UNKNOWN'                  # difference the reason for which can't be determined


def _run_dict(run):
    if not run:
        return None
    return {
        'outcome': run.outcome,
        'source': run.source,
        'fetched_count': run.fetched_count,
        'http_status': run.http_status,
        'error_detail': run.error_detail,
        'created_at': run.created_at.isoformat() if run.created_at else None,
    }


def _title(vl):
    # VehicleListing has no own title column; derive from the Vehicle spec delegates.
    return ' '.join(str(p) for p in (vl.year, vl.make, vl.model) if p) or None


def build_gumtree_reconciliation(user, live=False, live_fetcher=None):
    """Diff a dealer's current Gumtree ads against the database.

    Args:
        user: the dealer (accounts.User).
        live: fetch the current Gumtree ad ids fresh (one small request) instead
            of reusing the last successful sync run.
        live_fetcher: optional callable(seller_id) -> (ok, ids, http_status, error);
            injectable for tests. Defaults to the real ZenRows helper.

    Returns a JSON-serialisable dict (see module docstring).
    """
    last_run = GumtreeSyncRun.objects.filter(user=user).order_by('-created_at').first()
    last_success = (
        GumtreeSyncRun.objects
        .filter(user=user, outcome__in=['success', 'empty'])
        .order_by('-created_at').first()
    )
    last_success_ids = set(str(i) for i in (last_success.returned_list_ids or [])) if last_success else set()
    last_run_failed = bool(last_run and last_run.outcome == 'failed')

    seller_ids = list(
        GumtreeProfileListing.objects.filter(user=user)
        .exclude(profile_id__isnull=True).exclude(profile_id='')
        .values_list('profile_id', flat=True)
    )

    # --- Current Gumtree ad set ----------------------------------------
    gumtree_ids = set()
    ad_set_source = 'none'
    live_error = None
    if live:
        fetch = live_fetcher
        if fetch is None:
            from .gumtree_scraper import fetch_current_gumtree_ad_ids as fetch
        got_any = False
        for sid in (seller_ids or []):
            ok, ids, _http, err = fetch(sid)
            if ok:
                gumtree_ids |= set(str(i) for i in ids)
                got_any = True
            else:
                live_error = err or 'live fetch failed'
        if got_any:
            ad_set_source = 'live'
        elif last_success:
            # Live fetch failed — fall back to the last successful sync, NEVER "0 ads".
            gumtree_ids = set(last_success_ids)
            ad_set_source = 'last_sync_fallback'
    elif last_success:
        gumtree_ids = set(last_success_ids)
        ad_set_source = 'last_sync'

    gumtree_known = ad_set_source != 'none'

    # --- Database Gumtree rows -----------------------------------------
    db_by_list_id = {}
    for vl in (
        VehicleListing.objects.filter(user=user)
        .exclude(seller_profile_id__isnull=True).exclude(seller_profile_id='')
        .select_related('vehicle')
    ):
        if vl.list_id is not None:
            db_by_list_id[str(vl.list_id)] = vl
    db_ids = set(db_by_list_id.keys())

    # --- Differences ----------------------------------------------------
    matched_ids = gumtree_ids & db_ids

    missing_from_db = []
    for ad_id in sorted(gumtree_ids - db_ids):
        if ad_id not in last_success_ids:
            reason, detail = REASON_NOT_YET_SYNCED, 'On Gumtree but not in the last successful sync — awaiting next sync'
        elif last_run_failed:
            reason, detail = REASON_SYNC_FAILURE, 'Last sync failed — cannot confirm why this ad has no database record'
        else:
            reason, detail = REASON_UNKNOWN, 'Returned by a sync but no database record was created'
        missing_from_db.append({'list_id': ad_id, 'reason': reason, 'reason_detail': detail})

    no_longer_on_gumtree = []
    if gumtree_known:
        for ad_id in sorted(db_ids - gumtree_ids):
            vl = db_by_list_id[ad_id]
            db_status = (vl.status or '').strip()
            if last_run_failed:
                reason, detail = REASON_SYNC_FAILURE, 'Last sync failed — "removed from Gumtree" is based on the last successful sync and may be stale'
            else:
                reason = REASON_SOLD_OR_REMOVED
                if db_status.lower() in ('sold', 'deleted', 'failed_deletion') or vl.sales:
                    detail = f'No longer on Gumtree; database status = "{db_status or "sold"}"'
                else:
                    detail = f'No longer on Gumtree while database status = "{db_status}" (not yet marked sold)'
            no_longer_on_gumtree.append({
                'list_id': ad_id,
                'listing_id': vl.id,
                'title': _title(vl),
                'db_status': db_status or None,
                'reason': reason,
                'reason_detail': detail,
            })

    # --- Summary + reconciliation identity -----------------------------
    unresolved = sum(1 for d in missing_from_db if d['reason'] == REASON_UNKNOWN)
    summary = {
        'gumtree_total': len(gumtree_ids) if gumtree_known else None,
        'matched_in_db': len(matched_ids),
        'missing_from_db': len(missing_from_db),
        'no_longer_on_gumtree': len(no_longer_on_gumtree),
        'unresolved': unresolved,
        # gumtree_total == matched_in_db + missing_from_db (holds by construction).
        'reconciles': (len(gumtree_ids) == len(matched_ids) + len(missing_from_db)) if gumtree_known else None,
    }

    if ad_set_source == 'none':
        note = 'No successful Gumtree sync on record — totals cannot be computed.'
    elif last_run_failed:
        note = 'Last sync FAILED — showing the last successful sync; results may be stale.'
    else:
        note = None

    return {
        'sync': {
            'last_run': _run_dict(last_run),
            'last_success_at': last_success.created_at.isoformat() if last_success and last_success.created_at else None,
            'ad_set_source': ad_set_source,   # last_sync | live | last_sync_fallback | none
            'live_error': live_error,
            'seller_profile_ids': seller_ids,
            'note': note,
        },
        'summary': summary,
        'missing_from_db': missing_from_db,
        'no_longer_on_gumtree': no_longer_on_gumtree,
    }
