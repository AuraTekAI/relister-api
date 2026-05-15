"""Clear DNA descriptions that contain nothing but the synthetic mileage line.

Pairs with the ``parse_listing`` change in
``custom_domain_adapters/dnacarsales.py`` that now returns ``description: None``
when DNA's source page has no per-vehicle copy. Before that change, those
listings ended up in the DB as ``"Mileage: <N>km"`` (the synthetic prefix
prepended on top of an empty body once the warranty/PPSR boilerplate was
stripped).

Why this is a fix and not a cosmetic cleanup. ~55% of DNA's stock pages
have a body that's only the dealer-wide warranty/PPSR trailer — there is
no per-vehicle text on those pages, so once
``_strip_dna_boilerplate`` runs, the body is empty. The previous adapter
still prepended ``"Mileage: <N>km"``, producing a one-line stub that's
identical in shape across every affected listing. Identical content shape
across many listings on the same dealer account is the cross-listing
fingerprint Facebook Marketplace's spam classifier scores against — i.e.
the precise risk that gets accounts banned. ``extension_signoff.md``
clause B1 / the joint sign-off depends on these rows being recognisable
as "no description" so the extension's eventual description-length guard
refuses to publish them.

What this migration does. For every ``VehicleListing`` row whose
``seller_profile_id == "www.dnacarsales.com.au"`` and whose ``description``
matches **exactly** the synthetic ``"Mileage: <digits>km"`` pattern (with
nothing else, modulo trailing whitespace), set ``description`` to ``NULL``.

What this migration does NOT do.

* Touch Gumtree rows. Gumtree dealers write unique per-vehicle copy —
  matching the same ``"Mileage: \\d+km"`` shape there is a coincidence we
  would never see in practice, and the migration filter pins to DNA's
  ``seller_profile_id`` so Gumtree is provably out of scope.
* Touch Buckingham rows. Their carSSR payload ships a real description for
  98% of listings; the remaining 2% are short variant codes, not the
  ``Mileage:`` stub pattern.
* Touch any other field. Only ``description`` is modified.
* Touch rows whose description has the mileage line **plus** additional
  text. Those are publishable (real dealer copy) and stay as-is.

Exact-match regex. We anchor the pattern with ``^…$`` and require the
description to be exactly ``Mileage: <digits>km`` (optionally with a
trailing newline). That way a row whose description begins with
``Mileage: 27919km\\nGood condition, full service history…`` is left
intact — only the bare stubs get cleared.
"""
import re

from django.db import migrations


_MILEAGE_STUB_RE = re.compile(r"^\s*Mileage:\s*\d+\s*km\s*$", re.IGNORECASE)


def forwards_clear_stub_descriptions(apps, schema_editor):
    VehicleListing = apps.get_model("VehicleListing", "VehicleListing")
    qs = VehicleListing.objects.filter(
        seller_profile_id="www.dnacarsales.com.au",
    ).exclude(description__isnull=True)

    stub_pks = []
    # Iterate in Python rather than pushing the regex into SQL: Django's
    # regex support varies across DB backends and we want the same exact
    # behaviour as the Python regex used elsewhere in the codebase.
    for vl in qs.only("pk", "description").iterator():
        if _MILEAGE_STUB_RE.match(vl.description or ""):
            stub_pks.append(vl.pk)

    if not stub_pks:
        return
    updated = VehicleListing.objects.filter(pk__in=stub_pks).update(description=None)
    if updated:
        print(f"  Cleared mileage-only description on {updated} DNA row(s).")


def backwards_noop(apps, schema_editor):
    # Recreating the synthetic ``"Mileage: <N>km"`` stub would re-introduce
    # the cross-listing fingerprint this migration exists to remove, so
    # the reverse direction is deliberately a no-op.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("VehicleListing", "0028_clear_wangara_location"),
    ]

    operations = [
        migrations.RunPython(
            forwards_clear_stub_descriptions,
            reverse_code=backwards_noop,
        ),
    ]
