import re


class DomainAdapter:
    HOST: str = ""
    LOCATION_DEFAULT: str | None = None
    # Hosts (including any CDNs) that this adapter is authoritative for when the
    # image-proxy resolver needs to decide who owns a given image URL. Specific
    # adapters list their dealership host plus any CDN hostnames serving their
    # images; the generic JSON-LD adapter leaves this empty since it discovers
    # hosts at resolve time.
    KNOWN_HOSTS: set[str] = set()

    def discover_stock_links(self, profile_url: str) -> list[str]:
        raise NotImplementedError

    def parse_listing(self, stock_url: str) -> dict | None:
        raise NotImplementedError

    def extract_listing_id(self, stock_url: str) -> str | None:
        raise NotImplementedError

    def needs_image_proxy(self, image_url: str) -> bool:
        return False

    def discover_dealer_location(self, profile_url: str) -> dict | None:
        """Best-effort discovery of the dealership's own suburb / state (and,
        where available, full address) from their custom-domain site. Returns
        ``None`` if nothing can be determined, otherwise a dict with:

          * ``suburb`` (str) and ``state`` (str, 2–3 char AU code — WA/NSW/
            VIC/QLD/SA/TAS/ACT/NT) — REQUIRED. Both must be present or the
            caller discards the whole result.
          * ``address`` (str, optional) — a single-line formatted street
            address (street + suburb + state + postcode, whatever the source
            exposes), stored as-is for a future Google Maps geocoding lookup.
            Omit or return ``None`` when only suburb/state are known (e.g. a
            hardcoded adapter default with no street-level data).

        Called once at signup (and on profile-edit / scheduled refresh) so the
        listing-response layer can fill in ``location`` for custom-domain rows
        that don't carry a per-listing address — avoiding the extension's
        manual prompt. Failure must be silent; callers will simply leave the
        user's saved location/address null.
        """
        return None


# Maps the variety of state spellings JSON-LD authors actually use (full name,
# 2-3 letter code, dotted abbreviation, mixed case) to the canonical 2-3 letter
# code stored on User.dealership_state. Kept local to the adapter package so
# Gumtree's get_full_state_name in VehicleListing/utils.py stays untouched.
_STATE_CODE_MAP = {
    "wa": "WA", "w.a.": "WA", "western australia": "WA",
    "nsw": "NSW", "n.s.w.": "NSW", "new south wales": "NSW",
    "vic": "VIC", "v.i.c.": "VIC", "victoria": "VIC",
    "qld": "QLD", "q.l.d.": "QLD", "queensland": "QLD",
    "sa": "SA", "s.a.": "SA", "south australia": "SA",
    "tas": "TAS", "t.a.s.": "TAS", "tasmania": "TAS",
    "act": "ACT", "a.c.t.": "ACT", "australian capital territory": "ACT",
    "nt": "NT", "n.t.": "NT", "northern territory": "NT",
}


def normalize_au_state(value: str | None) -> str | None:
    """Coerce a free-form state string into one of the AU state codes
    User.dealership_state accepts. Returns None for unrecognised input."""
    if not value or not isinstance(value, str):
        return None
    return _STATE_CODE_MAP.get(value.strip().lower())


# ---------------------------------------------------------------------------
# carSSR image scoping
#
# A family of AU dealer sites (Buckingham Autos, Perth City Auto Group, and
# others on the same Next.js platform) embed vehicle data in RSC payloads as a
# `carSSR` object. Both the Buckingham adapter and the generic adapter's carSSR
# fallback used to pull photos by regexing the ENTIRE decoded RSC payload for
# `"image":{"url":"..."}`.
#
# That is not scoped to the vehicle being scraped. A detail page's payload also
# carries the dealer's related/similar/recently-viewed stock, so an Alto's
# listing collected the Alto's photos AND the Corolla's, the Nissan's, and so
# on — cross-vehicle contamination that then got written straight to
# VehicleListing.images and reconciled into image slots.
#
# The parsed `carSSR` object is the authoritative boundary for "this vehicle",
# so photos are collected by walking THAT object instead of the whole payload.
# ---------------------------------------------------------------------------


def _walk_carssr_image_urls(node, out):
    """Recursively collect `{"image": {"url": ...}}` URLs from a parsed object.

    Mirrors the shape the legacy whole-payload regex matched, so scoping to the
    car object changes *which* photos are found, never *how* they're recognised.
    The walk is key-agnostic below the top level — the platform nests the photo
    list under `images`, `photos` and `gallery` on different sites, and any of
    them is fine as long as it sits inside this vehicle's own object.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, dict):
                url = value.get("url")
                if isinstance(url, str) and url:
                    out.append(url)
            _walk_carssr_image_urls(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_carssr_image_urls(item, out)


def carssr_image_urls(car, rsc, accept=None, logger=None, context=""):
    """Photo URLs belonging to the vehicle described by `car` — and only it.

    `car`     parsed carSSR object for THIS vehicle (the scoping boundary).
    `rsc`     full decoded RSC payload, used ONLY for the legacy fallback.
    `accept`  optional predicate to keep an adapter's own URL filtering
              (Buckingham requires "/photo/", both drop thumbnails).

    Falls back to the old whole-payload scan when the scoped walk finds
    nothing, so a platform template change that moves photos outside the
    carSSR object degrades to "possibly contaminated" rather than "no photos
    at all" — the latter would leave the listing under the extension's
    two-image minimum and block publishing entirely. The fallback is logged
    because it means this scoping assumption needs revisiting.
    """
    def _dedupe(urls):
        seen, kept = set(), []
        for url in urls:
            if not url or url in seen:
                continue
            if accept is not None and not accept(url):
                continue
            seen.add(url)
            kept.append(url)
        return kept

    scoped = []
    if isinstance(car, dict):
        _walk_carssr_image_urls(car, scoped)
    images = _dedupe(scoped)
    if images:
        return images

    legacy = re.findall(r'"image"\s*:\s*\{\s*"url"\s*:\s*"([^"]+)"', rsc or "")
    images = _dedupe(legacy)
    if images and logger is not None:
        logger.warning(
            "carSSR photos not found inside the vehicle object%s — falling back "
            "to the whole-payload scan, which CAN include other vehicles' "
            "photos. The carSSR image shape has probably changed; re-scope "
            "carssr_image_urls().",
            f" for {context}" if context else "",
        )
    return images
