"""Clear the legacy hard-coded ``"Wangara, WA"`` location off DNA + Buckingham
``VehicleListing`` rows so the extension's per-dealer location prompt fires
correctly at publish time.

Background. Earlier versions of the DNA and Buckingham adapters set
``LOCATION_DEFAULT = "Wangara, WA"`` for every per-listing location, because
the source pages don't expose a per-vehicle suburb. That worked for DNA
themselves but is the **wrong** suburb for every reseller using the same
backend — and "every dealer claims Wangara, WA" is precisely the cross-seller
fingerprint that gets Facebook Marketplace accounts banned.

Round-2 fix: both adapters now return ``location: None``. The extension reads
that as "missing", prompts the dealer once for their own suburb, caches it
in ``chrome.storage.local`` keyed by user id, and patches it onto every
listing at publish time (GUARD 2b in ``publishListing.ts``).

Why migrations 0026 / 0027 left existing rows alone. They explicitly noted:

    Existing rows currently set to "Wangara, WA" are NOT touched here to
    avoid breaking in-progress publishes; only future scrapes write None.

That guard assumed the cron's day-old staleness check would naturally
overwrite the bad data on the next re-scrape. In practice it doesn't:
``check_custom_domain_profile_relisting_task`` only updates a listing if it
was created or last-listed ≥ 1 day ago, so rows scraped less than 24 h
before any subsequent run keep the stale ``"Wangara, WA"`` value. With the
extension now in pre-burner test, leaving 358 rows with the wrong suburb
would invalidate clause C1 of ``extension_signoff.md``.

What this migration does. For every ``VehicleListing`` row whose
``seller_profile_id`` belongs to a hand-written custom-domain adapter that
declared ``LOCATION_DEFAULT = None`` (DNA, Buckingham), set ``location`` to
``NULL`` if it currently equals the legacy ``"Wangara, WA"`` string.

What this migration does NOT do. Touch any Gumtree row, any other source's
rows, or any field other than ``location``. Gumtree continues to ship its
own real ``"{suburb}, {state}"`` value verbatim — that path is untouched.

Whitespace tolerance. We match ``ILIKE 'wangara, wa'`` rather than exact
equality so we also catch any accidental leading/trailing whitespace or
casing drift from the legacy hard-coded string.
"""
from django.db import migrations


_AFFECTED_SELLER_PROFILES = (
    "www.dnacarsales.com.au",
    "www.buckinghamautos.com.au",
)


def forwards_clear_legacy_location(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    qs = VehicleListing.objects.filter(
        seller_profile_id__in=_AFFECTED_SELLER_PROFILES,
        location__iexact="Wangara, WA",
    )
    updated = qs.update(location=None)
    if updated:
        # Visible in `manage.py migrate` output so the operator can confirm
        # the row counts match what they expected before deploying.
        print(f"  Cleared legacy 'Wangara, WA' location on {updated} row(s).")


def backwards_noop(apps, schema_editor):
    # Restoring the legacy hard-coded suburb would re-introduce the
    # cross-seller fingerprint this migration exists to remove, so the
    # reverse direction is deliberately a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0027_normalize_buckingham_listings"),
    ]

    operations = [
        migrations.RunPython(
            forwards_clear_legacy_location,
            reverse_code=backwards_noop,
        ),
    ]
