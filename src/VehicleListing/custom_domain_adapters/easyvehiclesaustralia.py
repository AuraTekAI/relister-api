import logging
import random
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from .base import DomainAdapter
from ..make_normalizer import normalize_make

logger = logging.getLogger("custom_domain")

# The dealership host this adapter is written for. Also used as the listing's
# `seller_profile_id`, so it must stay stable regardless of whether the user
# registered the www or non-www form (both are aliased to this instance in
# custom_domain_adapters/__init__.py).
HOST = "easyvehiclesaustralia.com.au"
CANONICAL_BASE_URL = f"https://{HOST}"
STOCK_PATH = "/stock"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _http_get(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def _base_url_from(profile_url):
    """Derive scheme+host from whatever the user registered, falling back to the
    canonical host. Keeps us faithful to what they typed while guaranteeing a
    usable base even if they entered just the bare domain."""
    try:
        parsed = urlparse(profile_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        pass
    return CANONICAL_BASE_URL


# ---------------------------------------------------------------------------
# Field normalizers. VirtualYard renders body / fuel / transmission with its
# own vocabulary (all-caps consumer labels). Facebook Marketplace's dropdowns
# expect the same consumer-facing shape Gumtree's API already returns, so map
# them here — mirrors the DNA / Buckingham adapters.
# ---------------------------------------------------------------------------
_BODY_MAP = {
    "HATCH": "Hatchback",
    "HATCHBACK": "Hatchback",
    "SEDAN": "Sedan",
    "WAGON": "Wagon",
    "SPORTWAGON": "Wagon",
    "COUPE": "Coupe",
    "CONVERTIBLE": "Convertible",
    "CABRIOLET": "Convertible",
    "SUV": "SUV",
    "UTE": "Ute",
    "UTILITY": "Ute",
    "CAB CHASSIS": "Ute",
    "C/CHAS": "Ute",
    "PICKUP": "Ute",
    "P/UP": "Ute",
    "VAN": "Van",
    "BUS": "Van",
    "PEOPLE MOVER": "Van",
}


def _normalize_body(value):
    if not value:
        return None
    key = value.strip().upper()
    if key in _BODY_MAP:
        return _BODY_MAP[key]
    # Heuristic fallback for any label variants not in the map above.
    if "HATCH" in key:
        return "Hatchback"
    if "SEDAN" in key:
        return "Sedan"
    if "WAGON" in key:
        return "Wagon"
    if "CONVERTIBLE" in key or "CABRIOLET" in key:
        return "Convertible"
    if "COUPE" in key:
        return "Coupe"
    if "SUV" in key:
        return "SUV"
    if "CAB CHAS" in key or "C/CHAS" in key or "P/UP" in key or "PICKUP" in key or "UTILITY" in key or "UTE" in key:
        return "Ute"
    if "VAN" in key or "PEOPLE MOVER" in key or "BUS" in key:
        return "Van"
    return value


def _normalize_fuel(value):
    if not value:
        return None
    key = value.strip().upper()
    if "DIESEL" in key:
        return "Diesel"
    if "HYBRID" in key or "/ELECTRIC" in key:
        return "Hybrid"
    if key in ("ELECTRIC", "EV"):
        return "Electric"
    if "PETROL" in key or "UNLEADED" in key or "ULP" in key or "PULP" in key:
        return "Petrol"
    if "LPG" in key:
        return "LPG"
    return value


def _normalize_transmission(value):
    if not value:
        return None
    key = value.strip().upper()
    if "MANUAL" in key:
        return "Manual"
    if any(token in key for token in ("AUTO", "CVT", "DSG", "TIPTRONIC", "STEPTRONIC", "SEQ", "TRONIC", "CONTINUOUS")):
        return "Automatic"
    return value


# Acronym makes that must stay all-caps after title-casing (mirrors the DNA /
# Buckingham adapters). Uppercase brand names are an FB Marketplace
# scraper-account fingerprint, so we title-case everything else.
_MAKE_ACRONYMS = {"BMW", "MG", "HSV", "VW", "GMC", "DAF", "BYD", "MAN", "SAAB", "LDV", "RAM"}


def _normalize_make(value):
    if not value:
        return None
    raw = value.strip()
    key = raw.upper()
    if key in _MAKE_ACRONYMS:
        return key
    return raw.title()


def _normalize_color(value):
    if not value:
        return None
    main = value.split("/")[0].strip()
    main = re.split(r"\s+OR\s+", main, flags=re.IGNORECASE)[0].strip()
    return main.title() if main else None


def _parse_int(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


class EasyVehiclesAustraliaAdapter(DomainAdapter):
    """Adapter for easyvehiclesaustralia.com.au (Teixeira Group), a dealer site
    on the VirtualYard / carsforsale.com.au platform.

    The site is fully server-rendered HTML (no JS / bot protection), so a plain
    requests + BeautifulSoup scrape works — no Playwright needed. Detail pages
    expose a clean "Vehicle specifics" table plus schema/OpenGraph meta tags,
    and gallery images on storage.googleapis.com. This adapter deliberately
    parses by table-label and meta tag rather than fragile CSS classes so a
    minor template tweak on the platform won't silently break it.
    """

    HOST = HOST
    # Per-listing location is not exposed on the detail pages, and hard-coding
    # the dealer's suburb onto every listing is the same cross-seller
    # fingerprint that gets custom-domain listings flagged on Facebook.
    # Returning None lets the listing-response layer fall back to the dealer's
    # own registration suburb/state (User.dealership_suburb / _state).
    LOCATION_DEFAULT = None
    # Only the site's own host — the no-photo placeholder lives here. Real
    # gallery images are on storage.googleapis.com, which is intentionally NOT
    # listed so it falls through to the API's default image-proxy path (safe
    # for cross-origin fetch from the Facebook tab).
    KNOWN_HOSTS = {HOST, f"www.{HOST}"}

    def extract_listing_id(self, stock_url: str) -> str | None:
        # Detail URLs look like /buy/<year-make-model-slug>/<TOKEN> where TOKEN
        # is a stable base64url-style id, e.g.
        #   /buy/2008-ford-focus-zetec-lt/KyUa8L2djfwO997sLKisEA
        # The trailing path segment is the id.
        match = re.search(r"/buy/[^/]+/([A-Za-z0-9_\-]+)/?$", stock_url)
        return match.group(1) if match else None

    def discover_stock_links(self, profile_url: str) -> list[str]:
        base_url = _base_url_from(profile_url)
        links: list[str] = []
        seen: set[str] = set()
        page = 1
        while True:
            if page == 1:
                page_url = f"{base_url}{STOCK_PATH}"
            else:
                # VirtualYard small-inventory sites render everything on one
                # page. If the site ignores ?page=N it simply returns the same
                # links and we stop on the "no new links" check below — so this
                # is a safe, self-terminating probe for larger inventories.
                page_url = f"{base_url}{STOCK_PATH}?page={page}"
            logger.info(f"Fetching EasyVehicles stock index: {page_url}")
            try:
                response = _http_get(page_url)
            except Exception as exc:
                logger.error(f"Failed to fetch {page_url}: {exc}")
                break
            if response.status_code != 200:
                logger.error(f"Non-200 ({response.status_code}) for {page_url}")
                break

            # Match detail links only: exactly /buy/<slug>/<token> (two path
            # segments). Excludes /buy/<slug>/securepay.virtualyard.com.au/...
            # finance/enquiry links, which have extra path segments.
            page_hrefs = re.findall(
                r"""href=['"]((?:https?://[^'"/]+)?/buy/[^'"/]+/[A-Za-z0-9_\-]+)['"]""",
                response.text,
            )
            new_count = 0
            for href in page_hrefs:
                if "securepay" in href or "virtualyard.com.au" in href:
                    continue
                # Normalise to an absolute URL on the registered base.
                if href.startswith("http"):
                    absolute = href
                else:
                    absolute = f"{base_url}{href}"
                # Guard: must resolve to a listing id.
                if not self.extract_listing_id(absolute):
                    continue
                if absolute not in seen:
                    seen.add(absolute)
                    links.append(absolute)
                    new_count += 1

            if new_count == 0:
                break
            page += 1
            if page > 50:
                logger.warning("EasyVehicles pagination exceeded 50 pages — stopping")
                break
            time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))

        logger.info(f"Total unique EasyVehicles stock links collected: {len(links)}")
        return links

    def _meta(self, soup, *, prop=None, name=None):
        if prop:
            node = soup.find("meta", attrs={"property": prop})
            if node and node.get("content"):
                return node["content"].strip()
        if name:
            node = soup.find("meta", attrs={"name": name})
            if node and node.get("content"):
                return node["content"].strip()
        return None

    def _parse_specs(self, soup):
        """Read the 'Vehicle specifics' two-column table, keyed by lower-cased
        label. Robust to the exact table markup — any <tr> with ≥2 cells."""
        spec = {}
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) >= 2:
                label = cells[0].get_text(" ", strip=True)
                value = cells[-1].get_text(" ", strip=True)
                if label:
                    spec[label.strip().lower()] = value
        return spec

    def _parse_images(self, html):
        """Collect real gallery image URLs. Prefer the storage.googleapis.com
        au-assets gallery (full set); fall back to virtualyard.com.au/photos
        only if the gallery isn't present. Excludes the theme no-photo
        placeholder and site chrome.

        Same sidebar hazard as _parse_price: this scans the page for image
        URLs, so it must not reach into the "Recent vehicles" section. If the
        platform ever renders real related-car gallery images there, they'd be
        wrongly attached to THIS listing and would flicker as the sidebar
        rotates between scrapes — tripping the change-detector into a needless
        delete+republish. Cut the page at the earliest related-section marker
        first (mirrors the guard in _parse_price)."""
        lowered = html.lower()
        cut = len(html)
        for marker in ("recent vehicles", "similar vehicles",
                       "you may also like", "recommended for you"):
            idx = lowered.find(marker)
            if idx != -1:
                cut = min(cut, idx)
        html = html[:cut]
        gallery = []
        seen = set()
        for m in re.finditer(
            r"https://storage\.googleapis\.com/au-assets/[A-Za-z0-9_\-./]+\.(?:jpe?g|png|webp)",
            html,
        ):
            url = m.group(0)
            if url not in seen:
                seen.add(url)
                gallery.append(url)
        if gallery:
            return gallery
        # Fallback: VirtualYard-hosted photos (used in og:image / comments).
        for m in re.finditer(
            r"https://virtualyard\.com\.au/photos/[A-Za-z0-9_\-./]+\.(?:jpe?g|png|webp)",
            html,
        ):
            url = m.group(0)
            if url not in seen:
                seen.add(url)
                gallery.append(url)
        return gallery

    def _parse_price(self, soup, spec):
        """Full sale price for THIS vehicle. The detail page shows the sale
        price plus a smaller weekly repayment ($X per week*), and further down
        a "Recent vehicles" sidebar listing OTHER cars with their own (often
        higher) prices. Scanning the whole page and taking max() therefore
        picked up a pricier suggested car (e.g. the MINI Cabrio at $8,580 was
        mis-read as a sidebar Hilux's $26,980). So cut the text at the
        related-vehicles section first, then take the largest plausible dollar
        amount (>= $500) from what remains (the weekly figure is well below the
        sale price and filtered out by the threshold)."""
        text = soup.get_text(" ", strip=True)
        # Drop the related-/recommended-vehicle sidebar so a pricier suggested
        # car's amount can't win the max() below. Case-insensitive; cut at the
        # earliest marker found.
        lowered = text.lower()
        cut = len(text)
        for marker in ("recent vehicles", "similar vehicles",
                       "you may also like", "recommended for you"):
            idx = lowered.find(marker)
            if idx != -1:
                cut = min(cut, idx)
        text = text[:cut]
        candidates = []
        for m in re.finditer(r"\$\s?([\d]{1,3}(?:,\d{3})+|\d{3,6})", text):
            value = _parse_int(m.group(1))
            if value is not None and value >= 500:
                candidates.append(value)
        return max(candidates) if candidates else None

    def parse_listing(self, stock_url: str) -> dict | None:
        listing_id = self.extract_listing_id(stock_url)
        if not listing_id:
            logger.error(f"Could not extract listing id from {stock_url}")
            return None
        try:
            response = _http_get(stock_url)
        except Exception as exc:
            logger.error(f"Failed to fetch {stock_url}: {exc}")
            return None
        if response.status_code != 200:
            logger.error(f"Non-200 ({response.status_code}) for {stock_url}")
            return None

        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        spec = self._parse_specs(soup)

        raw_make = spec.get("make")
        model = spec.get("model")
        variant = spec.get("variant")
        year = spec.get("year")

        # Fallback to og:title ("MAKE MODEL VARIANT") if the specs table was
        # missing the make/model rows for some listing.
        if not raw_make or not model:
            og_title = self._meta(soup, prop="og:title")
            if og_title:
                parts = og_title.split()
                if not raw_make and len(parts) >= 1:
                    raw_make = parts[0]
                if not model and len(parts) >= 2:
                    model = parts[1]
                if not variant and len(parts) >= 3:
                    variant = " ".join(parts[2:])

        make = _normalize_make(raw_make)
        # Canonicalise make → exact manufacturer name (FB "Other" category fix)
        # and recover split multi-word brands ("Land"/"Rover"). MUST match
        # migration 0031 byte-for-byte or the change-detector will see a
        # phantom change and trigger a delete+republish loop.
        make, model = normalize_make(make, model)

        body_type = _normalize_body(spec.get("body"))
        fuel_type = _normalize_fuel(spec.get("fuel type") or spec.get("fuel"))
        transmission = _normalize_transmission(spec.get("transmission"))
        color = _normalize_color(spec.get("colour") or spec.get("color"))
        mileage = _parse_int(spec.get("odometer") or spec.get("kilometres") or spec.get("kms"))
        price = self._parse_price(soup, spec)

        # Description kept as-is (per product decision): the dealer's own copy
        # from the OpenGraph description meta, which mirrors the on-page
        # "Comments" section.
        description = self._meta(soup, prop="og:description") or self._meta(soup, name="description")
        if description:
            description = description.strip() or None

        images = self._parse_images(html)

        title = " ".join(str(p) for p in [year, make, model, variant] if p)

        listing_details = {
            "list_id": str(listing_id),
            "title": title,
            "price": price,
            "description": description,
            "image": images,
            "location": self.LOCATION_DEFAULT,
            "body_type": body_type,
            "fuel_type": fuel_type,
            "color": color,
            "variant": variant,
            "year": str(year) if year else None,
            "model": model,
            "make": make,
            "mileage": mileage,
            "transmission": transmission,
            "url": stock_url,
        }
        logger.info(f"Parsed EasyVehicles listing {listing_id}: {title}")
        return listing_details

    def needs_image_proxy(self, image_url: str) -> bool:
        # Gallery images are on storage.googleapis.com (not a host we own), so
        # we don't affirm proxying here — the registry's default rule proxies
        # unknown hosts, which is the safe choice for cross-origin fetch.
        return False

    def discover_dealer_location(self, profile_url: str) -> dict | None:
        # Location comes from the dealer's registration (suburb/state), so no
        # site-level discovery is attempted here.
        return None
