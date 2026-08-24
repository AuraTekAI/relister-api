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
import copy
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

        year, make, model, variant = self._parse_title(soup)
        spec = self._parse_spec(soup)
        price = self._parse_price(soup)
        images = self._parse_images(soup)
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
        node = soup.find(class_=re.compile(r"\bcardTitle\b"))
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
    _FOREIGN_STOCK_CLASS_RE = re.compile(
        r"seller-all|related|similar|recommend|also-like|recently-viewed|other-stock",
        re.I,
    )

    def _parse_images(self, soup):
        """Full-size JPGs for THIS vehicle only. The hero carousel is
        `div.swiper.vehicle`; the dealer's other stock sits in a separate
        `stacked carousel seller-all` block, so scoping to the hero keeps
        unrelated cars' photos out. The JPG is in `data-cache`.

        When the hero carousel can't be found (template change), we must NOT
        fall back to the whole page as-is: `seller-all` holds the dealer's
        entire inventory, so that fallback attached other vehicles' photos to
        this listing (an Alto ending up with Corolla/Nissan images). Instead,
        strip the known foreign-stock blocks out of a COPY of the tree and scan
        what's left — still degraded, but it can't borrow another car's photos.
        """
        hero = soup.find("div", class_=lambda c: bool(c) and "swiper" in c and "vehicle" in c)
        if hero is not None:
            scope = hero
        else:
            logger.warning(
                "carsforsale: hero carousel (div.swiper.vehicle) not found — "
                "falling back to a page scan with the dealer's other-stock "
                "blocks removed. Check the detail-page template."
            )
            scope = copy.copy(soup)
            for node in scope.find_all(
                class_=lambda c: bool(c) and self._FOREIGN_STOCK_CLASS_RE.search(
                    " ".join(c) if isinstance(c, list) else str(c)
                )
            ):
                node.decompose()
        seen, urls = set(), []
        for el in scope.find_all(["img", "source", "div", "a"]):
            for attr in ("data-cache", "data-src", "src", "href"):
                val = el.get(attr)
                if val and _PHOTO_RE.fullmatch(val.strip()) and val.strip() not in seen:
                    seen.add(val.strip())
                    urls.append(val.strip())
        if not urls:  # fallback: regex the (hero-scoped) markup directly
            for u in _PHOTO_RE.findall(str(scope)):
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
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
