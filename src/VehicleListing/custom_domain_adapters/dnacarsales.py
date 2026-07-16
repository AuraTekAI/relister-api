import logging
import random
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from .base import DomainAdapter
from ..make_normalizer import normalize_make

logger = logging.getLogger("custom_domain")

DNA_BASE_URL = "https://www.dnacarsales.com.au"
DNA_STOCK_PATH = "/used-cars-in-wangara/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _http_get(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def _format_description(description):
    if not description:
        return ""
    description = re.sub(r"(?i)<br\s*/?>", "\n", description)
    return BeautifulSoup(description, "html.parser").get_text().strip()


# DNA appends an identical warranty/PPSR/finance trailer to every listing's
# description. Gumtree dealers write unique copy per vehicle, so FB never sees
# the same paragraph twice. We strip the trailer here so each listing keeps
# only its per-vehicle text, matching Gumtree's pattern.
_BOILERPLATE_RE = re.compile(
    r"\*\s*1\s*year[,\s]+3\s*year[,\s]+5\s*year\s+Warranties",
    re.IGNORECASE,
)


def _strip_dna_boilerplate(text):
    if not text:
        return text
    match = _BOILERPLATE_RE.search(text)
    if not match:
        return text
    return text[: match.start()].rstrip()


# DNA's body/fuel/transmission fields use the AU government PPSR vehicle
# registration vocabulary (e.g. "5D WAGON", "Petrol - Unleaded ULP",
# "6 SP AUTO SEQ SPORTS MODE"). Facebook Marketplace's dropdowns expect
# consumer-facing labels — the same shape Gumtree's API already returns.
# Map DNA's vocabulary to that shape so the extension's form-filler can
# match the dropdown options cleanly.
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


def _normalize_body(value):
    if not value:
        return None
    key = value.strip().upper()
    if key in _BODY_MAP:
        return _BODY_MAP[key]
    # Heuristic fallback for unknown PPSR codes.
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
        return None
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
        return None
    key = value.strip().upper()
    if "MANUAL" in key:
        return "Manual"
    if any(token in key for token in ("AUTO", "CVT", "DSG", "TIPTRONIC", "STEPTRONIC", "SEQ", "TRONIC", "CONTINUOUS")):
        return "Automatic"
    return value


# Acronyms that should stay all-caps after title-casing. Most makes are
# proper nouns (Toyota, Mercedes-Benz) and Python's str.title() handles
# them correctly including the hyphenated form. LDV and RAM are common in AU.
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
    # "BLACK / BLACK", "BLUE / null" → keep only the primary colour
    main = value.split("/")[0].strip()
    # "SILVER OR CHROME" → keep only the first option
    main = re.split(r"\s+OR\s+", main, flags=re.IGNORECASE)[0].strip()
    return main.title() if main else None


def _parse_price(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _parse_mileage(text):
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _find_odometer(spec):
    """Best-effort odometer lookup. Prefer the exact 'Odometer' label, then
    fall back to any spec row whose label looks like an odometer
    (odometer / kilometres / kms / mileage) so a slightly different label
    doesn't leave the listing with no mileage. Restricted to odometer-like
    labels so we never mistake an unrelated number (doors, engine size) for it.
    """
    if spec.get("Odometer"):
        return _parse_mileage(spec.get("Odometer"))
    for key, value in spec.items():
        k = (key or "").lower()
        if "odom" in k or "kilom" in k or k == "kms" or "mileage" in k:
            parsed = _parse_mileage(value)
            if parsed is not None:
                return parsed
    return None


def _absolute_image_url(src):
    if not src:
        return None
    if src.startswith("http://") or src.startswith("https://"):
        return src
    if src.startswith("/"):
        return f"{DNA_BASE_URL}{src}"
    return f"{DNA_BASE_URL}/{src}"


def _strip_thumbnail_params(url):
    """Drop the `w=` and `h=` resize query params so the URL serves the
    full-resolution image. DNA's `<img>` tags ship with `?t=<ts>&w=150&h=0`,
    which returns a 5KB thumbnail; without `w`/`h` the same path serves the
    ~380KB original. The `t=` cache-buster is preserved."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if not parsed.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in ("w", "h")]
    return urlunparse(parsed._replace(query=urlencode(kept)))


class DNACarSalesAdapter(DomainAdapter):
    HOST = "www.dnacarsales.com.au"
    # No location is exposed per-listing on DNA's detail pages; returning None
    # lets the extension fall back to the dealer's own profile location rather
    # than hard-coding "Wangara, WA" onto every reseller's listing (which would
    # be wrong for any dealer that isn't DNA themselves).
    LOCATION_DEFAULT = None
    KNOWN_HOSTS = {"www.dnacarsales.com.au"}

    def extract_listing_id(self, stock_url: str) -> str | None:
        match = re.search(r"-(\d+)$", stock_url.rstrip("/"))
        return match.group(1) if match else None

    def discover_stock_links(self, profile_url: str) -> list[str]:
        links = []
        seen = set()
        page = 1
        while True:
            if page == 1:
                page_url = f"{DNA_BASE_URL}{DNA_STOCK_PATH}"
            else:
                page_url = f"{DNA_BASE_URL}{DNA_STOCK_PATH}pagenum-{page}"
            logger.info(f"Fetching DNA stock index: {page_url}")
            try:
                response = _http_get(page_url)
            except Exception as exc:
                logger.error(f"Failed to fetch {page_url}: {exc}")
                break
            if response.status_code != 200:
                logger.error(f"Non-200 ({response.status_code}) for {page_url}")
                break

            page_links = re.findall(r"""href=['"](/stock/[^'"]+)['"]""", response.text)
            new_count = 0
            for href in page_links:
                if href not in seen:
                    seen.add(href)
                    links.append(f"{DNA_BASE_URL}{href}")
                    new_count += 1

            if new_count == 0:
                break
            page += 1
            if page > 50:
                logger.warning("DNA pagination exceeded 50 pages — stopping")
                break
            time.sleep(random.uniform(settings.SIMPLE_DELAY_START_TIME, settings.SIMPLE_DELAY_END_TIME))

        logger.info(f"Total unique DNA stock links collected: {len(links)}")
        return links

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

        soup = BeautifulSoup(response.text, "html.parser")

        name_node = soup.select_one("#details-vehicle-info-vehicle-Name")
        full_name = name_node.get_text(strip=True) if name_node else ""
        parts = full_name.split(" ")
        year = parts[0] if len(parts) > 0 else None
        make = _normalize_make(parts[1]) if len(parts) > 1 else None
        model = parts[2] if len(parts) > 2 else None
        variant = " ".join(parts[3:]) if len(parts) > 3 else None
        # Canonicalise make → exact manufacturer name (FB "Other" category fix);
        # recovers split multi-word brands ("Land"/"Rover" → "Land Rover") by
        # borrowing from model. Must match migration 0031 byte-for-byte.
        make, model = normalize_make(make, model)

        price_node = soup.select_one("#details-vehicle-info-vehicle-Price")
        price = _parse_price(price_node.get_text(strip=True)) if price_node else None

        desc_node = soup.select_one("#details-vehicle-info-vehicle-Description")
        description = _format_description(desc_node.decode_contents()) if desc_node else ""
        description = _strip_dna_boilerplate(description)

        spec = {}
        for row in soup.select("tr[data-value]"):
            key = row.get("data-value")
            cells = row.find_all("td")
            if key and len(cells) >= 2:
                spec[key] = cells[-1].get_text(strip=True)

        body_type = _normalize_body(spec.get("Body"))
        fuel_type = _normalize_fuel(spec.get("Fuel"))
        color = _normalize_color(spec.get("Colour"))
        transmission = _normalize_transmission(spec.get("Transmission"))
        mileage = _find_odometer(spec)

        images = []
        for img in soup.select("ul.bxslider img"):
            src = img.get("src")
            absolute = _absolute_image_url(src)
            if not absolute:
                continue
            full_res = _strip_thumbnail_params(absolute)
            if full_res and full_res not in images:
                images.append(full_res)

        # Only prepend the mileage line when the dealer actually wrote
        # per-vehicle copy. ~55% of DNA listings have a body that's nothing
        # but the warranty/PPSR boilerplate trailer — once `_strip_dna_boilerplate`
        # removes it, the description is empty. Prepending "Mileage: <N>km"
        # to an empty body produces a one-line stub that's identical in shape
        # across every affected listing, which is the cross-listing
        # fingerprint Facebook's spam classifier scores against.
        # Returning None lets the extension's description-length guard refuse
        # to publish those listings (analogous to D2 for partial images),
        # rather than us shipping ban-bait.
        if description.strip():
            if mileage is not None:
                mileage_text = f"Mileage: {mileage}km"
                if mileage_text.lower() not in description.lower():
                    description = f"{mileage_text}\n{description}".strip()
        else:
            description = None

        listing_details = {
            "list_id": listing_id,
            "title": full_name,
            "price": price,
            "description": description,
            "image": images,
            "location": self.LOCATION_DEFAULT,
            "body_type": body_type,
            "fuel_type": fuel_type,
            "color": color,
            "variant": variant,
            "year": year,
            "model": model,
            "make": make,
            "mileage": mileage,
            "transmission": transmission,
            "url": stock_url,
        }
        logger.info(f"Parsed DNA listing {listing_id}: {full_name}")
        return listing_details

    def needs_image_proxy(self, image_url: str) -> bool:
        return bool(image_url) and image_url.startswith(DNA_BASE_URL + "/")

    def discover_dealer_location(self, profile_url: str) -> dict | None:
        # DNA Car Sales' physical office is in Wangara, WA — confirmed by
        # their /used-cars-in-wangara/ stock URL path and their contact page.
        # Resellers using the extension against DNA's site won't actually be
        # located here, but for DNA themselves (and as a sane default for
        # the rare reseller who happens to share the area) this is correct.
        return {"suburb": "Wangara", "state": "WA"}
