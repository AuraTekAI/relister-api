"""One-off / occasional cleanup for VehicleListing rows created BEFORE
duplicate_matching.py existed — back then a relisted vehicle whose source
(Gumtree ad, dealer-site stock token) issued it a new id always became a
second row for the same physical car. The live scrapers no longer do this
(see gumtree_scraper.py / custom_domain_scraper.py), but this command finds
and merges whatever duplicates that already accumulated in production.

Usage:
    python manage.py merge_duplicate_vehicle_listings                 # dry-run, all dealers
    python manage.py merge_duplicate_vehicle_listings --user-id 42    # dry-run, one dealer
    python manage.py merge_duplicate_vehicle_listings --apply         # actually merge

Dry-run (the default) is READ-ONLY — it only prints what it would do. Nothing
is written until --apply is passed. Always run without --apply first and
read the report before applying, especially the AMBIGUOUS groups it lists —
those are left untouched on purpose (see duplicate_matching.find_existing_vehicle's
docstring on why an ambiguous match is never auto-merged).
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from VehicleListing.duplicate_matching import is_valid_vin, normalize_for_matching
from VehicleListing.models import (
    FacebookListing,
    FacebookListingSnapshot,
    RelistingFacebooklisting,
    UnpublishedListingSnapshot,
    VehicleListing,
    VehicleListingImage,
)

# Two rows with matching make/model/year/variant/color but odometer readings
# further apart than this are treated as two DIFFERENT physical cars (a
# dealer with two visually-identical units in stock), not a duplicate — see
# _cluster_by_structure. Deliberately generous: two scrapes of the truly same
# car, taken weeks apart, can easily show a few hundred km of genuine driving.
MAX_MILEAGE_GAP_FOR_DUPLICATE = 1000

# Backend fields it's safe to backfill from a duplicate onto the canonical row
# when the canonical is missing them. Deliberately excludes facebook_listing_id,
# status, listed_on, sold_at, relist_count, retry_count — the canonical's OWN
# lifecycle state wins; a duplicate's copy of those is not "extra data", it's a
# competing (and by definition less-authoritative) history.
#
# Split by owner since migration 0056: spec attributes live only on the
# canonical Vehicle row (VehicleListing's copies are read-only delegates), so
# they are gap-filled vehicle→vehicle, everything else listing→listing.
LISTING_BACKFILL_FIELDS = [
    'description', 'location', 'price', 'images', 'url',
]
VEHICLE_BACKFILL_FIELDS = [
    'vin', 'variant', 'color', 'body_type', 'fuel_type', 'transmission', 'mileage',
]


class Command(BaseCommand):
    help = "Find and merge duplicate VehicleListing rows for the same physical vehicle."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually merge. Without this flag, only a report is printed.',
        )
        parser.add_argument(
            '--user-id', type=int, default=None,
            help='Only scan this user (dealer account) id.',
        )
        parser.add_argument(
            '--max-mileage-gap', type=int, default=MAX_MILEAGE_GAP_FOR_DUPLICATE,
            help=f'Max odometer difference (km) to still treat a structural match as one '
                 f'car, not two (default {MAX_MILEAGE_GAP_FOR_DUPLICATE}).',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        max_gap = options['max_mileage_gap']

        base_qs = VehicleListing.objects.all()
        if options['user_id']:
            base_qs = base_qs.filter(user_id=options['user_id'])

        dealer_keys = list(
            base_qs.values_list('user_id', 'seller_profile_id').distinct()
        )
        self.stdout.write(f"Scanning {len(dealer_keys)} dealer(s)...")

        merged_rows = 0
        merged_clusters = 0
        ambiguous_groups = 0

        for user_id, seller_profile_id in dealer_keys:
            # Bounded to one dealer's inventory at a time — typically tens to a
            # few hundred rows, never the whole table — so this comfortably
            # scales to a large multi-dealer table without a giant in-memory set.
            dealer_qs = (
                VehicleListing.objects
                .filter(user_id=user_id, seller_profile_id=seller_profile_id)
                .select_related('vehicle')  # spec reads below resolve via the vehicle relation
                .annotate(
                    _image_count=Count('image_slots', distinct=True),
                    _fb_history_count=Count('facebooklisting', distinct=True),
                    _relist_history_count=Count('relistingfacebooklisting', distinct=True),
                )
                .order_by('created_at')
            )
            rows = list(dealer_qs)
            if len(rows) < 2:
                continue

            vin_clusters, remaining = self._cluster_by_vin(rows)
            structural_clusters, ambiguous = self._cluster_by_structure(remaining, max_gap)

            for cluster in vin_clusters + structural_clusters:
                canonical, duplicates = self._pick_canonical(cluster)
                merged_clusters += 1
                merged_rows += len(duplicates)
                self.stdout.write(
                    f"[{'APPLY' if apply_changes else 'DRY-RUN'}] user={user_id} seller={seller_profile_id!r}: "
                    f"canonical id={canonical.id} (list_id={canonical.list_id!r}, "
                    f"{canonical.year} {canonical.make} {canonical.model} {canonical.color}) "
                    f"absorbs {[d.id for d in duplicates]} (list_ids={[d.list_id for d in duplicates]})"
                )
                if apply_changes:
                    with transaction.atomic():
                        self._merge(canonical, duplicates)

            for group in ambiguous:
                ambiguous_groups += 1
                sample = group[0]
                self.stdout.write(self.style.WARNING(
                    f"AMBIGUOUS — NOT merged — user={user_id} seller={seller_profile_id!r}: "
                    f"ids={[c.id for c in group]} ({sample.year} {sample.make} {sample.model} {sample.color}) "
                    f"— multiple candidates couldn't be disambiguated; review manually"
                ))

        verb = 'Merged' if apply_changes else 'Would merge'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {merged_rows} duplicate row(s) into {merged_clusters} canonical vehicle(s). "
            f"{ambiguous_groups} ambiguous group(s) left untouched for manual review."
        ))
        if not apply_changes and merged_clusters:
            self.stdout.write("Re-run with --apply to perform the merge above.")

    # ------------------------------------------------------------------
    # Clustering
    # ------------------------------------------------------------------

    def _cluster_by_vin(self, rows):
        """Group rows sharing a valid, identical VIN. Any group of size >1 is
        an unambiguous duplicate cluster — a VIN is unique to one physical car.
        Returns (clusters, remaining_rows_not_in_any_vin_cluster)."""
        by_vin = defaultdict(list)
        no_vin = []
        for row in rows:
            if is_valid_vin(row.vin):
                by_vin[row.vin.strip().upper()].append(row)
            else:
                no_vin.append(row)

        clusters = [group for group in by_vin.values() if len(group) > 1]
        # Singletons (a VIN seen exactly once) go back in the pool for the
        # structural pass too, in case a sibling row is missing its VIN.
        remaining = no_vin + [group[0] for group in by_vin.values() if len(group) == 1]
        return clusters, remaining

    def _cluster_by_structure(self, rows, max_gap):
        """Group remaining (no shared VIN) rows by make/model/year/variant/color.
        A group of exactly 2 is merged only if their odometer readings are
        close enough to plausibly be the same car; anything else (3+ candidates,
        or 2 with a large mileage gap) is reported as ambiguous rather than
        guessed at. Returns (clusters, ambiguous_groups)."""
        buckets = defaultdict(list)
        for row in rows:
            key = (
                normalize_for_matching(row.make), normalize_for_matching(row.model), str(row.year or ''),
                normalize_for_matching(row.variant), normalize_for_matching(row.color),
            )
            buckets[key].append(row)

        clusters, ambiguous = [], []
        for group in buckets.values():
            if len(group) < 2:
                continue
            if len(group) == 2:
                a, b = group
                if a.mileage is not None and b.mileage is not None and abs(a.mileage - b.mileage) > max_gap:
                    ambiguous.append(group)
                else:
                    clusters.append(group)
            else:
                # 3+ identical-looking cars: could be a genuine small fleet of
                # the same model, or a data-quality mess. Too risky to decide
                # automatically — surface for a human.
                ambiguous.append(group)
        return clusters, ambiguous

    # ------------------------------------------------------------------
    # Canonical selection + merge
    # ------------------------------------------------------------------

    def _pick_canonical(self, cluster):
        """The row that should survive. Prefers, in order: currently has a
        live Facebook mapping, has more publish/relist history, has more
        images already ingested, has a VIN, then oldest (most established)
        row as the final tiebreak."""
        def score(row):
            return (
                1 if row.facebook_listing_id else 0,
                row._relist_history_count,
                row._fb_history_count,
                row._image_count,
                1 if is_valid_vin(row.vin) else 0,
            )

        ranked = sorted(cluster, key=score, reverse=True)
        canonical, duplicates = ranked[0], ranked[1:]

        other_fb_ids = {d.facebook_listing_id for d in duplicates if d.facebook_listing_id}
        if canonical.facebook_listing_id and other_fb_ids - {canonical.facebook_listing_id}:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ canonical id={canonical.id} and duplicate(s) both have a facebook_listing_id "
                f"({canonical.facebook_listing_id!r} vs {sorted(other_fb_ids)}) — this backend merge does "
                f"NOT delete the extra live Facebook listing; that still needs the extension/manual cleanup."
            ))
        return canonical, duplicates

    def _merge(self, canonical, duplicates):
        for dup in duplicates:
            FacebookListing.objects.filter(listing=dup).update(listing=canonical)
            RelistingFacebooklisting.objects.filter(listing=dup).update(listing=canonical)
            FacebookListingSnapshot.objects.filter(matched_listing=dup).update(matched_listing=canonical)
            UnpublishedListingSnapshot.objects.filter(listing=dup).update(listing=canonical)

            # VehicleListingImage has a unique (listing, source_url) constraint —
            # a URL canonical already has stays put; only genuinely new photos
            # move across, so nothing is silently dropped that wasn't already
            # represented on the surviving row.
            canonical_urls = set(
                VehicleListingImage.objects.filter(listing=canonical).values_list('source_url', flat=True)
            )
            for img in VehicleListingImage.objects.filter(listing=dup):
                if img.source_url in canonical_urls:
                    img.delete()
                else:
                    img.listing = canonical
                    img.save(update_fields=['listing'])
                    canonical_urls.add(img.source_url)

            # Fill gaps only — never overwrite data the canonical already has.
            changed_fields = []
            for field in LISTING_BACKFILL_FIELDS:
                if not getattr(canonical, field) and getattr(dup, field):
                    setattr(canonical, field, getattr(dup, field))
                    changed_fields.append(field)

            # Spec attributes live on the Vehicle rows. If the canonical has no
            # vehicle at all, inherit the duplicate's; otherwise gap-fill the
            # canonical's vehicle from the duplicate's. (The duplicate's own
            # Vehicle row, if any, is left in place once orphaned — harmless,
            # and vehicle_sync can re-link it on a future scrape.)
            if canonical.vehicle_id is None and dup.vehicle_id is not None:
                canonical.vehicle_id = dup.vehicle_id
                changed_fields.append('vehicle_id')
            elif canonical.vehicle_id and dup.vehicle_id and canonical.vehicle_id != dup.vehicle_id:
                vehicle_changed = []
                for field in VEHICLE_BACKFILL_FIELDS:
                    if not getattr(canonical.vehicle, field) and getattr(dup.vehicle, field):
                        setattr(canonical.vehicle, field, getattr(dup.vehicle, field))
                        vehicle_changed.append(field)
                if vehicle_changed:
                    canonical.vehicle.save(update_fields=vehicle_changed + ['updated_at'])

            if changed_fields:
                canonical.save(update_fields=changed_fields)

            dup.delete()
