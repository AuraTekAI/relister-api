"""Canonical manufacturer normalisation for custom-domain listings.

Facebook Marketplace's vehicle form only accepts an exact manufacturer name in
the ``make`` field. Anything else — a model glued onto the make
("Toyota Hiace"), a split multi-word brand ("Land" for "Land Rover"), or a
blank — drops the car into the generic "Other" category, which is why those
listings render broken. Custom-domain scrapers derive ``make`` from free-form
page text, so we canonicalise it here.

This helper is the single source of truth used in TWO places that MUST agree:

  * at ingest, by the custom-domain adapters (DNA / Buckingham / generic), and
  * by migration ``0031_normalize_custom_domain_make``, which repairs the rows
    already in the table.

They must produce byte-identical (make, model) for the same input. The
custom-domain re-scrape change-detector compares the stored row against the
freshly-scraped values (see ``custom_domain_scraper._apply_listing_update`` /
the field comparison around it); if the migration normalised a stored row but
ingest produced a different value, the comparator would see a phantom change,
set ``is_changed=True``, and trigger a delete+republish — recreating the
duplicate loop. Calling the same function from both sides guarantees they match.

Gumtree is deliberately NOT covered here. It is the production revenue path and
its make extraction + change-detector are out of scope for this change.
"""

# Canonical manufacturer spellings (the exact casing Facebook Marketplace
# accepts). Multi-word brands MUST appear here so a brand the source split
# across make/model (e.g. make="Land", model="Rover ...") is recovered by the
# combined-token lookup in ``resolve_make`` without needing a special alias.
VALID_MAKES = {
    'Abarth', 'Alfa Romeo', 'Alpina', 'Alpine', 'Alvis', 'Armstrong Whitworth',
    'Aston Martin', 'Audi', 'Austin', 'BMW', 'BRP', 'BYD', 'Bentley', 'Bolwell',
    'Bufori', 'Caterham', 'Chery', 'Chevrolet', 'Chrysler', 'Citroën', 'Cupra',
    'DKW', 'DS', 'Daewoo', 'Daihatsu', 'Daimler', 'DeSoto', 'Delahaye', 'Dodge',
    'Dover', 'Elfin', 'Eunos', 'FIAT', 'FPV', 'Facel Vega', 'Ferrari',
    'Fleetwood', 'Ford', 'Foton', 'Franklin', 'Frazer Nash', 'GMC', 'GWM',
    'Geely', 'Genesis', 'Great Wall', 'HINO', 'HSV', 'Haval', 'Holden', 'Honda',
    'Hudson', 'Hummer', 'Hyundai', 'INFINITI', 'Isuzu', 'Iveco', 'JMC', 'Jaguar',
    'Jeep', 'Jewett', 'Kia', 'LDV', 'Lada', 'Lamborghini', 'Land Rover', 'Lexus',
    'Leyland', 'Lotus', 'MG', 'MINI', 'Mahindra', 'Maserati', 'Maybach', 'Mazda',
    'McLaren', 'Mercedes-Benz', 'Minerva', 'Mitsubishi', 'Morgan', 'Morris',
    'NSU', 'Nash', 'Nissan', 'Noble', 'Opel', 'Option RV', 'Packard', 'Pagani',
    'Panther', 'Peugeot', 'Pierce-Arrow', 'Plymouth', 'Polestar', 'Pontiac',
    'Porsche', 'Proton', 'Python', 'Rambler', 'Renault', 'Riley', 'Rolls-Royce',
    'Rover', 'SEAT', 'Saab', 'Shelby', 'Smart', 'Sparks', 'Ssangyong',
    'Studebaker', 'Subaru', 'Superformance', 'Suzuki', 'Swallow', 'Swift', 'TVR',
    'Tata', 'Tatra', 'Tesla', 'Toyota', 'Triumph', 'Universal', 'Volkswagen',
    'Volvo', 'Willys', 'Wolseley', 'ZX Auto', 'Škoda',
}

# Aliases / common spellings → canonical. Only needed where the source spelling
# is NOT a leading prefix of the canonical name (e.g. "Range" for "Land Rover",
# "VW" for "Volkswagen"); plain multi-word brands are handled by the canonical
# list above.
ALIASES = {
    'vw': 'Volkswagen',
    'chevy': 'Chevrolet',
    'merc': 'Mercedes-Benz',
    'mercedes': 'Mercedes-Benz',
    'mercedes benz': 'Mercedes-Benz',
    'range': 'Land Rover',
    'range rover': 'Land Rover',
    'rolls royce': 'Rolls-Royce',
    'mini cooper': 'MINI',
    'citroen': 'Citroën',
    'skoda': 'Škoda',
    'great wall motors': 'Great Wall',
    'gwm haval': 'GWM',
    'isuzu ute': 'Isuzu',
}

# Lowercase lookup covering every canonical make plus the aliases above.
KNOWN = {m.lower(): m for m in VALID_MAKES}
KNOWN.update(ALIASES)

# Brands that are stored all-caps; used only for the cosmetic fallback when a
# make can't be resolved, so we don't title-case a genuine acronym into "Bmw".
_ACRONYMS = {
    'BMW', 'MG', 'HSV', 'VW', 'GMC', 'DAF', 'BYD', 'MAN', 'SAAB', 'LDV', 'RAM',
    'FIAT', 'FPV', 'HINO', 'JMC', 'NSU', 'SEAT', 'TVR', 'BRP', 'DS', 'DKW',
    'GWM', 'MINI', 'INFINITI',
}


def _light_clean(make):
    """Cosmetic-only cleanup for makes we can't resolve to the canonical list.

    Never moves tokens between make and model — an unresolved make is returned
    recognisably intact so the migration can flag it for review rather than
    silently rewriting something we don't understand.
    """
    raw = (make or '').strip()
    if not raw:
        return raw
    if raw.upper() in _ACRONYMS:
        return raw.upper()
    return raw.title()


def resolve_make(make, model):
    """Resolve ``make`` to a canonical manufacturer name.

    Returns ``(make, model, resolved)``:

    * ``resolved=True``  — ``make`` is a canonical name and any tokens that
      belonged elsewhere have been moved into ``model`` (extra words glued onto
      a make are pushed into model; words borrowed to complete a split
      multi-word brand are removed from model).
    * ``resolved=False`` — ``make``/``model`` are returned unchanged in meaning
      (the caller decides whether to apply a cosmetic clean or flag the row).

    The token stream is ``make`` tokens followed by ``model`` tokens, so a brand
    the source split across the two fields (make="Land", model="Rover ...") is
    recovered by matching the longest leading canonical prefix.
    """
    make_s = (make or '').strip()
    model_s = (model or '').strip()
    if not make_s:
        return make_s, model_s, False

    make_tokens = make_s.split()
    model_tokens = model_s.split()
    combined = make_tokens + model_tokens

    for n in range(min(3, len(combined)), 0, -1):
        key = ' '.join(combined[:n]).lower()
        if key in KNOWN:
            canonical = KNOWN[key]
            consumed_from_model = max(0, n - len(make_tokens))
            leftover_make = make_tokens[n:] if n < len(make_tokens) else []
            new_model_tokens = leftover_make + model_tokens[consumed_from_model:]
            return canonical, ' '.join(new_model_tokens).strip(), True

    return make_s, model_s, False


def normalize_make(make, model):
    """Ingest-side wrapper: return canonicalised ``(make, model)``.

    Preserves a null/blank make as-is (so the change-detector and the
    nullable column don't flip between ``None`` and ``""``). Resolved makes get
    the canonical value + adjusted model; unresolved makes get a cosmetic clean
    only, with model untouched.
    """
    if not (make or '').strip():
        return make, model
    canonical, new_model, resolved = resolve_make(make, model)
    if resolved:
        return canonical, new_model
    return _light_clean(make), model
