"""Backfill existing DNA Car Sales listings to match Gumtree's data shape.

Pairs with the same-name normalization helpers added to
`custom_domain_adapters/dnacarsales.py`. Future scrapes produce clean data
directly; this migration brings the 260 rows already in the DB up to the same
shape so they don't trigger Facebook's spam classifier when re-published.

What this fixes (and why, in terms of how Gumtree behaves):

- **Description**: strip DNA's identical warranty/PPSR/finance trailer block
  that's appended to every listing. Gumtree dealers write unique per-vehicle
  copy; the duplicate trailer is the single biggest fingerprint FB uses for
  "same content across many listings → scraper account".
- **Body / Fuel / Transmission**: rewrite DNA's PPSR registration vocabulary
  ("5D WAGON", "Petrol - Premium ULP", "6 SP AUTO SEQ SPORTS MODE") into the
  consumer-facing labels FB Marketplace's dropdowns accept — the same shape
  Gumtree's API already returns.
- **Make / Colour**: title-case "TOYOTA" → "Toyota", "GREY" → "Grey" so they
  don't read as automated. Acronym makes (BMW, MG, HSV, …) stay uppercase.
- **Location**: cleared to NULL. DNA's HTML doesn't expose per-listing
  location; the hard-coded "Wangara, WA" was wrong for every reseller who
  wasn't DNA themselves. The extension/dealer profile supplies the real
  location at publish time. (Existing rows currently set to "Wangara, WA"
  are NOT touched here to avoid breaking in-progress publishes; only future
  scrapes write None.)

The helpers are inlined inside this migration so it stays self-contained —
Django migrations should keep working even if the adapter module changes
later.
"""
import re

from django.db import migrations


_BOILERPLATE_RE = re.compile(
    r"\*\s*1\s*year[,\s]+3\s*year[,\s]+5\s*year\s+Warranties",
    re.IGNORECASE,
)

_BODY_MAP = {
    "2D CONVERTIBLE": "Convertible",
    "2D COUPE": "Coupe",
    "2D VAN": "Van",
    "3D COUPE": "Coupe",
    "3D VAN": "Van",
    "4D SEDAN": "Sedan",
    "4D SPORTWAGON": "Wagon",
    "4D VAN": "Van",
    "4D WAGON": "Wagon",
    "5D HATCHBACK": "Hatchback",
    "5D WAGON": "Wagon",
    "BUS": "Van",
    "C/CHAS": "Ute",
    "CREW C/CHAS": "Ute",
    "CREW CAB P/UP": "Ute",
    "CREW CAB UTILITY": "Ute",
    "DOUBLE C/CHAS": "Ute",
    "DOUBLE CAB P/UP": "Ute",
    "DUAL C/CHAS": "Ute",
    "DUAL CAB P/UP": "Ute",
    "DUAL CAB UTILITY": "Ute",
    "UTILITY": "Ute",
    "VAN": "Van",
}

_MAKE_ACRONYMS = {"BMW", "MG", "HSV", "VW", "GMC", "DAF", "BYD", "MAN", "SAAB"}


def _strip_boilerplate(text):
    if not text:
        return text
    m = _BOILERPLATE_RE.search(text)
    if not m:
        return text
    return text[: m.start()].rstrip()


def _normalize_body(value):
    if not value:
        return value
    key = value.strip().upper()
    if key in _BODY_MAP:
        return _BODY_MAP[key]
    if "COUPE" in key:
        return "Coupe"
    if "CONVERTIBLE" in key or "CABRIOLET" in key:
        return "Convertible"
    if "SEDAN" in key:
        return "Sedan"
    if "HATCH" in key:
        return "Hatchback"
    if "WAGON" in key:
        return "Wagon"
    if "SUV" in key:
        return "SUV"
    if "P/UP" in key or "C/CHAS" in key or "PICKUP" in key or "UTILITY" in key:
        return "Ute"
    if "VAN" in key:
        return "Van"
    return value


def _normalize_fuel(value):
    if not value:
        return value
    key = value.strip().upper()
    if "DIESEL" in key:
        return "Diesel"
    if "HYBRID" in key or "/ELECTRIC" in key:
        return "Hybrid"
    if key in ("ELECTRIC", "EV"):
        return "Electric"
    if "PETROL" in key or "UNLEADED" in key or "ULP" in key:
        return "Petrol"
    if "LPG" in key:
        return "LPG"
    return value


def _normalize_transmission(value):
    if not value:
        return value
    key = value.strip().upper()
    if "MANUAL" in key:
        return "Manual"
    if any(t in key for t in ("AUTO", "CVT", "DSG", "TIPTRONIC", "STEPTRONIC", "SEQ", "TRONIC", "CONTINUOUS")):
        return "Automatic"
    return value


def _normalize_make(value):
    if not value:
        return value
    raw = value.strip()
    key = raw.upper()
    if key in _MAKE_ACRONYMS:
        return key
    return raw.title()


def _normalize_color(value):
    if not value:
        return value
    main = value.split("/")[0].strip()
    main = re.split(r"\s+OR\s+", main, flags=re.IGNORECASE)[0].strip()
    return main.title() if main else value


def forwards_normalize(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    qs = VehicleListing.objects.filter(seller_profile_id="www.dnacarsales.com.au")
    updated = 0
    for vl in qs.iterator():
        changed = False

        new_desc = _strip_boilerplate(vl.description)
        if new_desc != vl.description:
            vl.description = new_desc
            changed = True

        new_body = _normalize_body(vl.body_type)
        if new_body != vl.body_type:
            vl.body_type = new_body
            changed = True

        new_fuel = _normalize_fuel(vl.fuel_type)
        if new_fuel != vl.fuel_type:
            vl.fuel_type = new_fuel
            changed = True

        new_trans = _normalize_transmission(vl.transmission)
        if new_trans != vl.transmission:
            vl.transmission = new_trans
            changed = True

        new_make = _normalize_make(vl.make)
        if new_make != vl.make:
            vl.make = new_make
            changed = True

        new_color = _normalize_color(vl.color)
        if new_color != vl.color:
            vl.color = new_color
            changed = True

        if changed:
            vl.save(update_fields=[
                "description", "body_type", "fuel_type",
                "transmission", "make", "color", "updated_at",
            ])
            updated += 1
    if updated:
        print(f"  Normalized {updated} DNA Car Sales VehicleListing rows.")


def backwards_noop(apps, schema_editor):
    # Reverse direction is undefined — we don't have the original PPSR
    # vocabulary stored anywhere. Reapplying the migration is a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0025_dedupe_and_unique_listing_per_seller"),
    ]

    operations = [
        migrations.RunPython(forwards_normalize, reverse_code=backwards_noop),
    ]
