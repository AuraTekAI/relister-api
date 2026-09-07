"""carsforsale.com.au — VirtualYard MARKETPLACE adapter.

Unlike the other adapters (each one dealer's own domain), carsforsale.com.au is
VirtualYard's multi-dealer marketplace: dealers live under
`/showroom/<dealer-slug>/<id>` and vehicles under
`/cars/details/<slug>/<id>`. Two consequences:

1. Multi-dealer host → per-URL instance with a dealer-scoped HOST
   (`carsforsale.com.au/showroom/<slug>`). HOST is the pipeline's
   `seller_profile_id`; a shared host would merge every dealer's inventory and
   let one dealer's scrape reconcile-delete another's rows.

2. Client-rendered SPA → a plain GET returns only a ~9KB shell, so every fetch
   goes through ZenRows with js_render (unlike easyvehiclesaustralia.py, the
   same platform but server-rendered).

Everything parsed maps to an existing field via the standard `result` dict; no
new column/table is introduced.
"""
import logging
import re

from bs4 import BeautifulSoup
from django.conf import settings
from zenrows import ZenRowsClient

from .base import DomainAdapter, normalize_au_state
from ..make_normalizer import normalize_make

logger = logging.getLogger("custom_domain")

MARKETPLACE_HOSTS = {"carsforsale.com.au", "www.carsforsale.com.au"}
CANONICAL_HOST = "carsforsale.com.au"
BASE_URL = f"https://{CANONICAL_HOST}"

# Full-size gallery photos (…/photos/<id>.jpg); the visible src is a WebP thumb.
_PHOTO_RE = re.compile(r"https?://[^\"'\s]*virtualyard\.com\.au/photos/[A-Za-z0-9_\-]+\.jpg", re.I)
_SHOWROOM_RE = re.compile(r"/showroom/([^/?#]+)/([^/?#]+)", re.I)
_DETAIL_RE = re.compile(r"/(?:cars/)?details/([^/?#\"']+)/([A-Za-z0-9_\-]+)", re.I)

# AU-only marketplace, so a premium AU proxy dodges datacentre throttling;
# `wait` lets Framework7 hydrate the vehicle DOM before capture.
_ZENROWS_PARAMS = {"js_render": "true", "premium_proxy": "true", "proxy_country": "au", "wait": "9000"}
# The un-hydrated shell has none of these — reject it so a shell/challenge page
# never becomes a hollow listing.
_HYDRATED_MARKERS = ("cardTitle", "item-after", "details-price")
_MAX_IMAGES = 30

# Spec label (item-title) → result key. Only labels that map to an existing
# field are listed; everything else on the page is ignored.
_SPEC_LABEL_MAP = {
    "odometer": "mileage", "colour": "color", "color": "color",
    "body": "body_type", "body type": "body_type",
    "fuel type": "fuel_type", "fuel": "fuel_type",
    "transmission": "transmission", "vin": "vin",
}


def _render(url):
    """Fetch `url` through ZenRows with JS rendering. None on missing key,
    error, or an un-hydrated shell."""
    if not settings.ZENROWS_API_KEY:
        logger.error("ZENROWS_API_KEY not configured — cannot render %s", url)
        return None
    try:
        response = ZenRowsClient(settings.ZENROWS_API_KEY).get(url, params=_ZENROWS_PARAMS)
    except Exception as exc:
        logger.error("ZenRows render errored for %s: %s", url, exc)
        return None
    if response.status_code != 200:
        logger.error("ZenRows %s for %s", response.status_code, url)
        return None
    html = response.text or ""
    if not any(m in html for m in _HYDRATED_MARKERS):
        logger.warning("Render for %s returned an un-hydrated shell — skipping", url)
        return None
    return html


def _digits(text):
    if not text:
        return None
    m = re.search(r"[\d,]{1,12}", str(text))
    return int(m.group(0).replace(",", "")) if m else None


def _clean(text):
    return re.sub(r"\s+", " ", text).strip() if text else None


class CarsForSaleAdapter(DomainAdapter):
    KNOWN_HOSTS = MARKETPLACE_HOSTS | {"virtualyard.com.au"}

    def __init__(self, profile_url):
        self.profile_url = profile_url
        m = _SHOWROOM_RE.search(profile_url or "")
        slug = m.group(1).lower() if m else None
        # Dealer-scoped seller identity; bare host fallback for a non-showroom URL.
        self.HOST = f"{CANONICAL_HOST}/showroom/{slug}" if slug else CANONICAL_HOST

    def discover_stock_links(self, profile_url):
        """Every vehicle detail URL on the dealer's showroom, deduped by the
        trailing listing id, first-seen order kept."""
        html = _render(profile_url or self.profile_url)
        if not html:
            logger.error("carsforsale: showroom render failed for %s", profile_url)
            return []
        seen, links = set(), []
        for slug, vid in _DETAIL_RE.findall(html):
            if vid not in seen:
                seen.add(vid)
                links.append(f"{BASE_URL}/cars/details/{slug}/{vid}")
        logger.info("carsforsale: discovered %d stock links for %s", len(links), self.HOST)
        return links

    def extract_listing_id(self, stock_url):
        m = _DETAIL_RE.search(stock_url or "")
        return m.group(2) if m else None

    def parse_listing(self, stock_url):
        listing_id = self.extract_listing_id(stock_url)
        if not listing_id:
            logger.error("carsforsale: no listing id in %s", stock_url)
            return None
        html = _render(stock_url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        # Read EVERY field from the SPA's current-vehicle container, never the
        # whole document. The rendered DOM also contains the page we navigated
        # from — on this showroom that is 169 other cars' cards — so a
        # document-wide `find()` picks whichever vehicle happens to come first
        # in the markup. That is how the Swift at .../mVSN-4GYZ4ygOsnslJCYgQ
        # got stored as a "2016 Nissan SERENA" at the Serena's price while
        # carrying the Swift's own photos and VIN: the gallery was read from one
        # vehicle and the identity from another. See _current_vehicle_page.
        scope = self._current_vehicle_page(soup)
        if scope is None:
            logger.warning(
                "carsforsale: no 'page-current vehicle' container for %s — falling "
                "back to the whole document, which can mix in another vehicle's "
                "details. Check the detail-page template.", stock_url,
            )
            scope = soup

        year, make, model, variant = self._parse_title(scope)
        spec = self._parse_spec(scope)
        price = self._parse_price(scope)
        images = self._parse_images(scope)
        make, model = normalize_make(make, model)
        mileage = spec.get("mileage")

        # Hollow-row guard: a listing with no identity pollutes dedup and shows
        # blank on Facebook — skip it rather than store it.
        if not (make and model and year):
            logger.warning("carsforsale: skipping %s — no make/model/year", listing_id)
            return None
        for name, val in (("price", price), ("images", images)):
            if not val:
                logger.warning("carsforsale: listing %s without %s — saving anyway", listing_id, name)

        title = self._title(year, make, model, variant)
        return {
            "list_id": str(listing_id), "title": title, "price": price,
            "description": self._parse_description(soup) or title,
            "image": images, "location": None,
            "year": str(year), "make": make, "model": model, "variant": variant or None,
            "body_type": spec.get("body_type"), "fuel_type": spec.get("fuel_type"),
            "color": spec.get("color"), "transmission": spec.get("transmission"),
            "vin": spec.get("vin"), "mileage": mileage,
            "mileage_unavailable": mileage in (None, 0), "url": stock_url,
        }

    def _parse_title(self, soup):
        """`.cardTitle` = `<h1>YEAR MAKE MODEL<br><small>VARIANT</small></h1>`.
        The MAKE MODEL stream is returned as `make`; normalize_make peels the
        canonical make prefix off (handles multi-word makes like Land Rover)."""
        # The vehicle's own title is an <h1>; the `cardTitle` class is reused by
        # every related-vehicle card in the same container, so prefer the h1 and
        # only fall back to the first cardTitle of any tag.
        node = soup.find("h1", class_=re.compile(r"\bcardTitle\b")) or soup.find(
            class_=re.compile(r"\bcardTitle\b")
        )
        if not node:
            return None, None, None, None
        small = node.find("small")
        variant = _clean(small.get_text(" ")) if small else None
        if small:
            small.extract()
        tokens = (_clean(node.get_text(" ")) or "").split()
        year = tokens[0] if tokens and re.fullmatch(r"(19|20)\d{2}", tokens[0]) else None
        rest = tokens[1:] if year else tokens
        return year, (" ".join(rest) if rest else None), "", variant

    def _parse_spec(self, soup):
        """VirtualYard rows: `<div class="item-title">Label</div>
        <div class="item-after">Value</div>`."""
        out = {}
        for title_div in soup.find_all(class_="item-title"):
            key = _SPEC_LABEL_MAP.get((_clean(title_div.get_text(" ")) or "").lower())
            if not key:
                continue
            after = title_div.find_next_sibling(class_="item-after")
            value = _clean(after.get_text(" ")) if after else None
            if not value:
                continue
            # FIRST occurrence wins: within the vehicle's container the primary
            # spec table comes first, and later repeats are secondary/expanded
            # blocks (e.g. a second "Fuel Type" row rendered as "DIESEL"
            # instead of "Diesel"). Last-wins let those override the clean value.
            if key in out:
                continue
            if key == "mileage":
                out["mileage"] = _digits(value)
            elif key == "body_type":  # "5D WAGON" → drop door-count prefix
                out["body_type"] = _clean(re.sub(r"^\s*\d+\s*[Dd]\b", "", value)) or value
            elif key == "color":
                out["color"] = value.title() if value.isupper() else value
            else:
                out[key] = value
        return out

    def _parse_price(self, soup):
        node = soup.find(class_=re.compile(r"\bdetails-price\b"))
        return _digits(node.get_text(" ")) if node else None

    # Blocks that carry OTHER vehicles' photos: the dealer's full stock
    # carousel ("stacked carousel seller-all") and the usual
    # related/similar/recently-viewed rails. Used only on the fallback path
    # below, where we no longer have the hero carousel to scope to.
    # The photo carousel for the vehicle being viewed. Confirmed against the
    # live hydrated DOM (2026-08): `div.vehicle-hero-carousel.open-fullscreen`,
    # holding one `div.swiper-zoom-container` per photo.
    _HERO_CLASS = "vehicle-hero-carousel"
    _SLIDE_CLASS = "swiper-zoom-container"
    # Per-slide renditions, best first. `data-cache`/`data-desktopcache` are the
    # full-size JPGs on virtualyard.com.au; `data-mobilecache` is a smaller JPG
    # of the SAME photo. Taking one per slide is what stops a 25-photo gallery
    # being stored as 50 near-duplicates (the *-src attrs are WebP on
    # storage.googleapis.com, which Facebook rejects, so they are not used).
    _SLIDE_ATTRS = ("data-cache", "data-desktopcache", "data-mobilecache")

    @staticmethod
    def _current_vehicle_page(soup):
        """The Framework7 container for the vehicle actually being viewed.

        carsforsale.com.au is an SPA: the rendered DOM keeps the page we came
        FROM alongside the one we asked for —
        `div.page.automatic.home.with-hero.page-previous` (the showroom, ~190
        other cars) plus `div.page.automatic.vehicle.page-current` (this
        vehicle). Anything read with a document-wide `soup.find()` can
        therefore come from a different car.

        Returns None when the container isn't present, so callers can fall back
        and log rather than silently widening their scope.
        """
        def classes(value):
            return value if isinstance(value, list) else str(value).split()

        return soup.find(
            "div",
            class_=lambda c: bool(c) and "page-current" in classes(c) and "vehicle" in classes(c),
        )

    def _parse_images(self, soup):
        """Full-size JPGs for THIS vehicle only.

        IMPORTANT — `div.swiper.vehicle` is NOT this vehicle's gallery.
        Verified against the live hydrated page: those containers are the
        related-stock rails ("similar vehicles", "more from this dealer"). On a
        2019 Toyota C-HR detail page there were two of them, holding photos of a
        Ford Ranger, a Corolla and two other C-HRs — and NONE of the car being
        scraped. Scoping to them is what stored other vehicles' photos against
        every carsforsale listing (an Alto showing Corolla/Nissan images).

        The real gallery is `div.vehicle-hero-carousel`, one
        `div.swiper-zoom-container` per photo, each carrying the full-size JPG
        in `data-cache` and the slide index in `data-imgno`.

        There is deliberately NO whole-page fallback: a page-wide photo scan is
        exactly what pulled in the related rails (227 photo URLs on that one
        C-HR page, only 25 of them the C-HR's). If the gallery can't be located
        we return nothing and log an error — the listing then trips the
        extension's two-image minimum and is skipped, which is the safe
        outcome. Publishing another vehicle's photos is not.
        """
        # Scope to the SPA's CURRENT vehicle page first. Navigating
        # vehicle -> vehicle leaves the previous car's detail page (and its own
        # hero gallery) in the DOM, and a document-wide lookup can pick that
        # one — the same class of mistake as the related rails, just harder to
        # spot. Everything below therefore searches inside `scope`.
        # `soup` here is normally already the current-vehicle container (see
        # parse_listing). Re-resolving is harmless and keeps this method correct
        # when called with a whole document, e.g. from tests or a REPL.
        scope = self._current_vehicle_page(soup) or soup

        def is_hero(value):
            classes = value if isinstance(value, list) else str(value).split()
            return self._HERO_CLASS in classes

        hero = scope.find(class_=lambda c: bool(c) and is_hero(c))
        slides = hero.find_all(class_=self._SLIDE_CLASS) if hero is not None else []
        if not slides:
            # `swiper-zoom-container` only ever appears inside a hero gallery,
            # so within `scope` it is a safe secondary anchor if the wrapper
            # class is renamed.
            slides = scope.find_all(class_=self._SLIDE_CLASS)
            if slides:
                logger.warning(
                    "carsforsale: '%s' wrapper not found — falling back to "
                    "'%s' slides within the current vehicle page. Check the "
                    "detail-page template.",
                    self._HERO_CLASS, self._SLIDE_CLASS,
                )
        if not slides:
            logger.error(
                "carsforsale: could not locate this vehicle's photo gallery "
                "('%s' / '%s') — returning no images rather than risking "
                "another vehicle's photos. The detail-page template has changed.",
                self._HERO_CLASS, self._SLIDE_CLASS,
            )
            return []

        def slide_index(slide):
            img = slide.find("img")
            raw = img.get("data-imgno") if img is not None else None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 10**6  # unnumbered slides sort last, order otherwise kept

        # One canonical full-size JPG per slide, in the gallery's own order.
        seen, urls = set(), []
        alts = {}
        for slide in sorted(slides, key=slide_index):
            img = slide.find("img")
            if img is None:
                continue
            chosen = None
            for attr in self._SLIDE_ATTRS:
                val = (img.get(attr) or "").strip()
                if val and _PHOTO_RE.fullmatch(val):
                    chosen = val
                    break
            if not chosen or chosen in seen:
                continue
            seen.add(chosen)
            urls.append(chosen)
            alts[chosen] = _clean(img.get("alt")) or ""

        # Every slide in a vehicle's gallery carries that vehicle's own `alt`
        # (e.g. "2019 TOYOTA C-HR TOYOTA CHR HYBRID"). More than one distinct
        # alt means the scope picked up a foreign slide, so keep only the
        # dominant vehicle's photos. Format-agnostic: it compares alts to each
        # other, never to a parsed make/model.
        distinct = {a for a in alts.values() if a}
        if len(distinct) > 1:
            dominant = max(distinct, key=lambda a: sum(1 for v in alts.values() if v == a))
            dropped = [u for u in urls if alts.get(u) and alts[u] != dominant]
            if dropped:
                logger.warning(
                    "carsforsale: gallery scope contained %d photo(s) belonging to "
                    "another vehicle (alts=%s) — dropping them, keeping %r",
                    len(dropped), sorted(distinct), dominant,
                )
                urls = [u for u in urls if not alts.get(u) or alts[u] == dominant]

        return urls[:_MAX_IMAGES]

    def _parse_description(self, soup):
        for attrs in ({"name": "description"}, {"property": "og:description"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return _clean(tag["content"])
        return None

    @staticmethod
    def _title(year, make, model, variant):
        return " ".join(str(p) for p in (year, make, model, variant) if p)

    _LOCATION_RE = re.compile(r"([A-Za-z][A-Za-z .'-]{1,40}),\s*(WA|NSW|VIC|QLD|SA|TAS|ACT|NT)\b")

    def discover_dealer_location(self, profile_url):
        """Best-effort dealer suburb/state from the showroom (used at signup to
        fill the listing `location`, which per-listing is null here). Returns
        None when nothing is certain — never a guess.

        The showroom header renders the dealer name and the "Suburb, STATE"
        line as SIBLING elements (`.showroom-header > h_/p`). Joining the whole
        page with spaces used to merge them ("A&H AUTOHUB Welshpool, WA"), and
        the suburb capture swallowed the dealer name ("H AUTOHUB Welshpool" —
        the '&' is all that stopped it taking the full name). So: read the
        header's own <p> first, and keep the whole-page fallback joined with
        newlines so a match can never span two elements."""
        html = _render(profile_url or self.profile_url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        header = soup.find(class_="showroom-header")
        candidates = [p.get_text(" ") for p in header.find_all("p")] if header else []
        # \n as the joiner: the suburb charset has no newline, so text from two
        # different elements (dealer name + address) can't fuse into one match.
        candidates.append(soup.get_text("\n"))

        for text in candidates:
            m = self._LOCATION_RE.search(text or "")
            if not m:
                continue
            suburb, state = _clean(m.group(1)), normalize_au_state(m.group(2))
            if suburb and state:
                return {"suburb": suburb, "state": state}
        return None
