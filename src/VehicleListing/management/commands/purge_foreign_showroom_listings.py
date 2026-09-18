"""Recovery for custom-domain profiles whose DB rows were contaminated by the
carsforsale discovery bug: discover_stock_links used to regex the WHOLE
rendered page, and the SPA keeps the home page (Featured / Just arrived
carousels — ~150 rotating cars belonging to OTHER dealers) in the DOM under
the showroom. Every scheduled scrape therefore imported a fresh batch of
strangers' stock under the dealer's seller_profile_id (a 26-car dealer
accumulated 111+ rows).

The adapter is fixed (discovery is now scoped to the dealer's own showroom
container), but the normal scrape can no longer clean up after the old bug:
custom_domain_scraper's reconcile sanity guard sees "26 discovered vs 111
existing" and — correctly — refuses to cascade-delete. This command IS the
operator override that guard's message points to: it re-discovers with the
fixed adapter and reconciles the rows for one profile, with the same
semantics as the scraper's own reconcile step (pending/failed/sold rows
absent from the showroom are deleted; completed ones are marked sales=True
so relist history is never destroyed).

Usage:
    python manage.py purge_foreign_showroom_listings --email dealer@x.com              # dry-run
    python manage.py purge_foreign_showroom_listings --email dealer@x.com --apply      # reconcile

Dry-run (the default) is READ-ONLY. Refuses to apply when discovery returns
zero links — an empty discovery means a render failure, not an empty showroom,
and must never wipe a profile.
"""
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from VehicleListing.custom_domain_adapters import resolve_for_url
from VehicleListing.models import CustomDomainProfileListing, VehicleListing


class Command(BaseCommand):
    help = (
        "Re-discover a custom-domain profile's stock with the scoped adapter and "
        "delete/mark-sold the rows the old whole-page discovery wrongly imported."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="Dealer user's email")
        parser.add_argument(
            "--url",
            help="Profile URL to reconcile (defaults to the user's only "
            "CustomDomainProfileListing; required when they have several)",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually delete/mark rows. Without it: read-only report.",
        )

    def handle(self, *args, **opts):
        user = User.objects.filter(email=opts["email"]).first()
        if not user:
            raise CommandError(f"No user with email {opts['email']}")

        profiles = CustomDomainProfileListing.objects.filter(user=user)
        if opts.get("url"):
            profiles = profiles.filter(url=opts["url"])
        if not profiles.exists():
            raise CommandError("No matching CustomDomainProfileListing for this user")
        if profiles.count() > 1:
            raise CommandError(
                "User has several profiles — pass --url to pick one:\n"
                + "\n".join(f"  {p.url}" for p in profiles)
            )
        profile = profiles.first()

        adapter = resolve_for_url(profile.url)
        if adapter is None:
            raise CommandError(f"No adapter resolves {profile.url}")
        profile_id = adapter.HOST

        stock_links = adapter.discover_stock_links(profile.url)
        if not stock_links:
            raise CommandError(
                "Discovery returned 0 links — render failure or template change; "
                "refusing to reconcile against an empty set."
            )
        incoming = {
            str(adapter.extract_listing_id(u))
            for u in stock_links
            if adapter.extract_listing_id(u)
        }
        self.stdout.write(
            f"Discovered {len(incoming)} listings on the showroom for {profile_id}"
        )

        rows = VehicleListing.objects.filter(user=user, seller_profile_id=profile_id)
        keep = rows.filter(list_id__in=incoming).count()
        foreign = rows.exclude(list_id__in=incoming)
        to_delete = [r for r in foreign if r.status in ("pending", "failed", "sold")]
        to_mark_sold = [r for r in foreign if r.status == "completed"]
        untouched = [
            r for r in foreign
            if r.status not in ("pending", "failed", "sold", "completed")
        ]

        self.stdout.write(f"DB rows for this profile: {rows.count()}")
        self.stdout.write(f"  kept (on showroom):     {keep}")
        self.stdout.write(f"  delete (pending/failed/sold, not on showroom): {len(to_delete)}")
        self.stdout.write(f"  mark sales=True (completed, not on showroom):  {len(to_mark_sold)}")
        if untouched:
            self.stdout.write(f"  left as-is (unknown status): {len(untouched)}")
        for r in to_delete:
            self.stdout.write(f"    DELETE  {r.list_id}  {r.url or ''}  [{r.status}]")
        for r in to_mark_sold:
            self.stdout.write(f"    SOLD    {r.list_id}  {r.url or ''}")

        if not opts["apply"]:
            self.stdout.write(self.style.WARNING("Dry-run only — re-run with --apply to write."))
            return

        for r in to_delete:
            r.delete()
        for r in to_mark_sold:
            r.sales = True
            r.save(update_fields=["sales"])
        self.stdout.write(self.style.SUCCESS(
            f"Applied: {len(to_delete)} deleted, {len(to_mark_sold)} marked sold."
        ))
