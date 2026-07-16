"""Repair existing custom-domain rows whose `make` isn't a clean manufacturer
name (model glued on, split multi-word brand, wrong casing) — the values that
drop a Facebook Marketplace listing into the generic "Other" category.

Scope is custom-domain rows ONLY (`custom_domain_profile` set, `gumtree_profile`
unset). Gumtree is the production revenue path and is deliberately excluded.

Uses the same `resolve_make` as the custom-domain adapters, so a repaired row
matches what a fresh scrape now produces — otherwise the re-scrape
change-detector would see a phantom change and re-flag `is_changed`, recreating
the duplicate loop. Rows whose make can't be resolved to a known manufacturer
are left untouched and counted as `unresolved` in the deploy log for a human to
review (typically blank/unknown makes); nothing is silently corrupted.
"""
from django.db import migrations

from VehicleListing.make_normalizer import VALID_MAKES, resolve_make


def forwards(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")

    rows = VehicleListing.objects.filter(
        custom_domain_profile__isnull=False,
        gumtree_profile__isnull=True,
    )

    fixed = unresolved = unchanged = errored = 0
    for row in rows.iterator():
        try:
            canonical, new_model, resolved = resolve_make(row.make, row.model)
            if not resolved:
                # Already a clean canonical name → fine; otherwise it's a
                # blank/unknown make a human needs to set.
                if (row.make or "") not in VALID_MAKES:
                    unresolved += 1
                else:
                    unchanged += 1
                continue

            update_fields = []
            if row.make != canonical:
                row.make = canonical
                update_fields.append("make")
            if (row.model or "") != new_model:
                row.model = new_model
                update_fields.append("model")

            if update_fields:
                # Note: updated_at (auto_now) is intentionally NOT listed, so the
                # repair doesn't reshuffle the `-updated_at` listing feeds.
                row.save(update_fields=update_fields)
                fixed += 1
            else:
                unchanged += 1
        except Exception as exc:  # never let one bad row abort the migration
            errored += 1
            print(f"[normalize_custom_domain_make] row id={row.pk} skipped: {exc}")

    print(
        "[normalize_custom_domain_make] "
        f"fixed={fixed} unchanged={unchanged} "
        f"unresolved(blank/unknown, review in admin)={unresolved} errored={errored}"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0030_vehiclelisting_facebook_listing_id"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
