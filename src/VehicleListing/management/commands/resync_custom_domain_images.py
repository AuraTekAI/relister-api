import time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from VehicleListing.custom_domain_adapters import resolve_for_url
from VehicleListing.image_pipeline import sync_listing_images
from VehicleListing.models import VehicleListing


class Command(BaseCommand):
    help = (
        "Re-parse custom-domain listings with the current adapter and rewrite their "
        "image list, for listings scraped before the gallery-dedup fix (which stored "
        "each photo two or three times and published the duplicates to Facebook). "
        "Only rows whose image list actually changes are touched, and each of those is "
        "flagged is_changed=True so the extension republishes it with the corrected set. "
        "Never wipes images: a listing whose re-parse yields nothing is skipped. "
        "The scheduled Check_Custom_Domain_Profile_Re-Listings task does the same thing "
        "eventually — this is the targeted, right-now version for one dealer."
    )

    def add_arguments(self, parser):
        parser.add_argument('--email', help="Only this dealer's listings.")
        parser.add_argument(
            '--listing-ids', help='Comma-separated VehicleListing ids (e.g. a single listing to try first).',
        )
        parser.add_argument('--limit', type=int, default=None, help='Stop after N listings.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing anything.',
        )
        parser.add_argument(
            '--delay', type=float, default=2.0,
            help='Seconds between page fetches (default 2.0). The dealer sites rate-limit.',
        )
        parser.add_argument(
            '--no-mark-changed', action='store_true',
            help="Rewrite the images but don't flag the listing for republish.",
        )

    def handle(self, *args, **options):
        queryset = (
            VehicleListing.objects
            .filter(custom_domain_profile__isnull=False)
            .exclude(url__isnull=True).exclude(url='')
            .order_by('id')
        )

        email = options.get('email')
        if email:
            user = User.objects.filter(email__iexact=email.strip()).first()
            if not user:
                raise CommandError(f"No user with email {email}")
            queryset = queryset.filter(user=user)

        ids = options.get('listing_ids')
        if ids:
            try:
                queryset = queryset.filter(id__in=[int(part) for part in ids.split(',') if part.strip()])
            except ValueError:
                raise CommandError('--listing-ids must be comma-separated integers')

        limit = options.get('limit')
        if limit:
            queryset = queryset[:limit]

        dry_run = options.get('dry_run')
        mark_changed = not options.get('no_mark_changed')
        delay = options.get('delay')

        checked = updated = unchanged = skipped = 0
        removed_total = 0

        for listing in queryset:
            adapter = resolve_for_url(listing.url)
            if adapter is None:
                skipped += 1
                self.stderr.write(f"[{listing.id}] no adapter for {listing.url} — skipped")
                continue

            checked += 1
            if checked > 1 and delay:
                time.sleep(delay)

            try:
                result = adapter.parse_listing(listing.url)
            except Exception as exc:
                skipped += 1
                self.stderr.write(f"[{listing.id}] parse failed ({exc}) — skipped")
                continue

            new_images = (result or {}).get('image') or []
            if not new_images:
                # A bot-challenge page or a delisted vehicle. Leaving the old list
                # in place is always better than blanking a live listing's photos.
                skipped += 1
                self.stderr.write(f"[{listing.id}] re-parse returned no images — skipped")
                continue

            old_images = list(listing.images or [])
            if old_images == list(new_images):
                unchanged += 1
                continue

            removed = len(old_images) - len(new_images)
            removed_total += max(0, removed)
            self.stdout.write(
                f"[{listing.id}] {listing.year} {listing.make} {listing.model}: "
                f"{len(old_images)} -> {len(new_images)} images"
                + (f" ({removed} duplicate/stale removed)" if removed > 0 else "")
                + (" [dry-run]" if dry_run else "")
            )
            if dry_run:
                continue

            with transaction.atomic():
                listing.images = list(new_images)
                fields = ['images', 'updated_at']
                if mark_changed:
                    listing.is_changed = True
                    fields.append('is_changed')
                listing.save(update_fields=fields)
            # Outside the atomic block: reconciling slots enqueues Celery tasks
            # on commit, and this keeps a slot failure from rolling back the
            # image rewrite that already succeeded.
            sync_listing_images(listing, list(new_images))
            updated += 1

        summary = (
            f"Checked {checked}, updated {updated}, unchanged {unchanged}, skipped {skipped}. "
            f"{removed_total} duplicate/stale image URLs dropped."
        )
        if dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN — nothing written. {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(summary))
            if updated and mark_changed:
                self.stdout.write(
                    "Updated listings are flagged is_changed=True — the extension "
                    "republishes them on its next pass."
                )
