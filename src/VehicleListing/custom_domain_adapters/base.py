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
