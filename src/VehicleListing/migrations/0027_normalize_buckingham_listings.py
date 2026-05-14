"""Backfill existing Buckingham Autos listings to match Gumtree's data shape.

Pairs with the colour-normalization change in
`custom_domain_adapters/buckinghamautos.py`. Buckingham's body / fuel /
transmission / make values are already FB-compatible out of the box (the
carSSR JSON ships clean consumer labels), so the only field that needs
backfilling is colour — Buckingham's source stores all-caps strings and
occasional "OR CHROME" / "/ null" cruft (e.g. ``WHITE``, ``SILVER OR
CHROME``, ``"WHITE / null"``).

The hard-coded ``LOCATION_DEFAULT = "Wangara, WA"`` was removed from the
adapter so future scrapes return None; existing rows' location is **not**
touched here, to avoid breaking in-progress publishes — the next cron
re-scrape will overwrite them naturally.

Helpers inlined so the migration is frozen against future adapter changes.
"""
import re

from django.db import migrations


def _normalize_color(value):
    if not value:
        return value
    main = value.split("/")[0].strip()
    main = re.split(r"\s+OR\s+", main, flags=re.IGNORECASE)[0].strip()
    return main.title() if main else value


def forwards_normalize(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    qs = VehicleListing.objects.filter(seller_profile_id="www.buckinghamautos.com.au")
    updated = 0
    for vl in qs.iterator():
        new_color = _normalize_color(vl.color)
        if new_color != vl.color:
            vl.color = new_color
            vl.save(update_fields=["color", "updated_at"])
            updated += 1
    if updated:
        print(f"  Normalized colour on {updated} Buckingham Autos VehicleListing rows.")


def backwards_noop(apps, schema_editor):
    # No way to recover the original casing / "OR CHROME" / "/ null"
    # suffixes once they're stripped. Reverse is a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0026_normalize_dna_listings"),
    ]

    operations = [
        migrations.RunPython(forwards_normalize, reverse_code=backwards_noop),
    ]
