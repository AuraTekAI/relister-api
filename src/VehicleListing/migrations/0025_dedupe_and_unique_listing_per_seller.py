"""Dedupe duplicate VehicleListing rows + add a unique constraint.

Race-condition bug: concurrent background-scrape threads (e.g. an initial POST
overlapping with a cron-triggered re-scrape) could both observe "no row for
this (user, list_id, seller_profile_id)" and both insert, producing 2-3x
duplicates of every listing.

Step 1 (data): for each duplicate group, keep the oldest row (lowest id) and
delete the rest. The oldest row is the one whose `listed_on` / `is_listed`
/ workflow state most likely matches anything downstream pointed at it; the
later duplicates are by definition fresher data that the cron would re-fetch
on its next run anyway.

Step 2 (schema): add `unique_together = (user, list_id, seller_profile_id)`
so future races raise IntegrityError instead of silently inserting. The
orchestrator (`custom_domain_scraper.py`) wraps create in `transaction.atomic`
and catches that error.

Gumtree is unaffected — verified at migration-design time that there are
zero existing Gumtree duplicates, so the constraint applies cleanly across
both Gumtree and Custom Domain rows.
"""
from django.db import migrations, models
from django.db.models import Count, Min


def dedupe_listings(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    # Find every (user, list_id, seller_profile_id) group with >1 row.
    duplicate_groups = (
        VehicleListing.objects
        .values("user_id", "list_id", "seller_profile_id")
        .annotate(c=Count("id"), keep_id=Min("id"))
        .filter(c__gt=1)
    )
    total_deleted = 0
    for group in duplicate_groups:
        deleted, _ = (
            VehicleListing.objects
            .filter(
                user_id=group["user_id"],
                list_id=group["list_id"],
                seller_profile_id=group["seller_profile_id"],
            )
            .exclude(id=group["keep_id"])
            .delete()
        )
        total_deleted += deleted
    if total_deleted:
        print(f"  Deleted {total_deleted} duplicate VehicleListing rows across "
              f"{len(duplicate_groups)} groups")


def noop_reverse(apps, schema_editor):
    # No way to recreate deleted duplicates — and recreating them would
    # immediately re-trigger the bug this migration is fixing.
    pass


class Migration(migrations.Migration):
    # Operations run in separate transactions. Required because the bulk
    # DELETE in the data step sets up deferred FK-cascade triggers, and the
    # subsequent ALTER TABLE refuses to run while those triggers are pending
    # in the same transaction ("cannot ALTER TABLE because it has pending
    # trigger events").
    atomic = False

    dependencies = [
        ("VehicleListing", "0024_rename_dnacarsales_to_custom_domain"),
    ]

    operations = [
        migrations.RunPython(dedupe_listings, reverse_code=noop_reverse),
        migrations.AlterUniqueTogether(
            name="vehiclelisting",
            unique_together={("user", "list_id", "seller_profile_id")},
        ),
    ]
