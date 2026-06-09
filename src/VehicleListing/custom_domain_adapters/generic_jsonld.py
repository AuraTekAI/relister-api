"""Generic Schema.org JSON-LD adapter.

Fallback adapter for any custom-domain URL that doesn't have a hand-written
site-specific adapter. It extracts vehicle data from schema.org structured
data embedded as JSON-LD on the page (`<script type="application/ld+json">`).

Coverage: dealership platforms that ship `Vehicle` / `Car` / `MotorVehicle` /
`Product` JSON-LD on their detail pages — common on WordPress automotive
themes and most modern automotive CMSes.

Returns the same dict shape as the site-specific adapters
(`DNACarSalesAdapter`, `BuckinghamAutosAdapter`) so the existing orchestrator
in `custom_domain_scraper.py` writes identical `VehicleListing` rows
regardless of which adapter produced them.
"""
import hashlib
import json
import logging
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import DomainAdapter, normalize_au_state
from ..make_normalizer import normalize_make

logger = logging.getLogger("custom_domain")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HTTP_TIMEOUT = 30
MAX_PAGINATION_PAGES = 50
MAX_LISTINGS = 1000

VEHICLE_TYPES = {"Vehicle", "Car", "MotorVehicle", "Product"}
# carSSR `car_type` values we accept as "used". Anything else (e.g. 'new',
# 'demo') is filtered out so the scrape only ever produces used-car rows.
USED_CARSSR_TYPES = {"used"}
# schema.org/Vehicle `itemCondition` values we accept. Schema URLs may be
# bare strings ("UsedCondition") or fully-qualified IRIs.
USED_CONDITION_VALUES = {
    "UsedCondition",
    "https://schema.org/UsedCondition",
    "http://schema.org/UsedCondition",
}
NEW_CONDITION_VALUES = {
    "NewCondition",
    "https://schema.org/NewCondition",
    "http://schema.org/NewCondition",
    "DamagedCondition",
    "https://schema.org/DamagedCondition",
    "http://schema.org/DamagedCondition",
    "RefurbishedCondition",
    "https://schema.org/RefurbishedCondition",
    "http://schema.org/RefurbishedCondition",
}
NAV_PATHS = {
    "/about", "/contact", "/services", "/finance", "/blog", "/news",
    "/login", "/register", "/terms", "/privacy", "/sitemap", "/careers",
    "/locations", "/team",
}


def _http_get(url: str) -> requests.Response:
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)


# ---------------------------------------------------------------------------
# JSON-LD parsing helpers
# ---------------------------------------------------------------------------


def _flatten(payload):
    """Yield every dict in a JSON-LD payload, walking @graph arrays."""
    if isinstance(payload, list):
        for entry in payload:
            yield from _flatten(entry)
        return
    if not isinstance(payload, dict):
        return
    if isinstance(payload.get("@graph"), list):
        for entry in payload["@graph"]:
            yield from _flatten(entry)
    yield payload


def _iter_jsonld_blocks(html: str):
    """Yield every JSON-LD object embedded in the page.

    Malformed blocks (trailing commas, non-strict JSON) are tolerated where
    possible, then silently skipped. This mirrors what real-world dealership
    pages ship.
    """
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            try:
                cleaned = re.sub(r",(\s*[}\]])", r"\1", raw)
                payload = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                logger.debug(f"Generic adapter: skipping malformed JSON-LD: {exc}")
                continue
        for entry in _flatten(payload):
            yield entry


def _has_type(obj: dict, wanted: set[str]) -> bool:
    tval = obj.get("@type")
    if isinstance(tval, str):
        return tval in wanted
    if isinstance(tval, list):
        return any(t in wanted for t in tval if isinstance(t, str))
    return False


def _unwrap_name(value):
    """schema.org fields like brand/model can be strings or {@type, name}."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None
    if isinstance(value, list) and value:
        return _unwrap_name(value[0])
    return None


def _parse_digits(value):
    """Coerce a JSON-LD numeric-ish value into an integer.

    Handles plain numbers, integer strings, and money-style strings
    (`$36,990`, `36,990.00`, `AUD 36990.00`). The decimal portion is
    intentionally truncated: every price-bearing field in `VehicleListing`
    stores a string of digits (no decimal) for parity with the Gumtree path.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    # Drop any decimal portion before stripping non-digits — otherwise
    # "$36,990.00" becomes 3699000 instead of 36990.
    integer_part = text.split(".", 1)[0]
    digits = re.sub(r"[^\d]", "", integer_part)
    return int(digits) if digits else None


def _extract_price(obj: dict):
    offers = obj.get("offers")
    candidates = []
    if isinstance(offers, list):
        candidates.extend(offers)
    elif isinstance(offers, dict):
        candidates.append(offers)
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        value = _parse_digits(offer.get("price"))
        if value is not None:
            return value
        spec = offer.get("priceSpecification")
        if isinstance(spec, dict):
            value = _parse_digits(spec.get("price"))
            if value is not None:
                return value
    return _parse_digits(obj.get("price"))


def _extract_mileage(obj: dict):
    raw = obj.get("mileageFromOdometer")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str):
        return _parse_digits(raw)
    if isinstance(raw, dict):
        digits = _parse_digits(raw.get("value"))
        if digits is None:
            return None
        unit = str(raw.get("unitCode") or raw.get("unitText") or "").upper()
        # UN/CEFACT codes: SMI = statute mile; convert to km.
        if unit in {"SMI", "MI", "MILE", "MILES"}:
            return int(round(digits * 1.609344))
        return digits
    return None


def _extract_year(obj: dict):
    for field in ("vehicleModelDate", "modelDate", "productionDate", "releaseDate"):
        raw = obj.get(field)
        if not raw:
            continue
        match = re.search(r"\d{4}", str(raw))
        if match:
            return match.group(0)
    return None


def _extract_images(obj: dict, base_url: str) -> list[str]:
    raw = obj.get("image")
    if raw is None:
        return []
    candidates = raw if isinstance(raw, list) else [raw]
    seen: set[str] = set()
    images: list[str] = []
    for entry in candidates:
        url = None
        if isinstance(entry, str):
            url = entry
        elif isinstance(entry, dict):
            url = entry.get("url") or entry.get("contentUrl")
        if not url:
            continue
        absolute = urljoin(base_url, url)
        if absolute not in seen:
            seen.add(absolute)
            images.append(absolute)
    return images


def _extract_location(obj: dict) -> str | None:
    seller = obj.get("seller") or obj.get("offeredBy")
    if not isinstance(seller, dict):
        return None
    addr = seller.get("address")
    if isinstance(addr, dict):
        parts = [addr.get("addressLocality"), addr.get("addressRegion")]
        joined = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
        return joined or None
    if isinstance(addr, str):
        return addr.strip() or None
    return None


# ---------------------------------------------------------------------------
# Next.js carSSR fallback — used by a family of AU dealer sites built on the
# same Next.js platform (e.g. Buckingham Autos, Perth City Auto Group). These
# sites embed full vehicle data inside RSC (React Server Components) payloads
# rather than as schema.org/Vehicle JSON-LD. The shape of `carSSR` is stable
# across every dealer on the platform.
# ---------------------------------------------------------------------------


def _decode_rsc_payload(html: str) -> str:
    """Concatenate every `self.__next_f.push([1, "..."])` payload and decode
    its JSON-string escapes. Each push body is a valid JSON string, so we
    wrap it and let json.loads handle every escape (\\", \\\\, \\n, \\uXXXX)
    correctly — naive .replace() chains mishandle interleaving like \\\\\\"."""
    payloads = re.findall(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)", html, re.S)
    chunks = []
    for raw in payloads:
        try:
            chunks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return "".join(chunks)


def _extract_car_object(rsc: str) -> dict | None:
    """Find the `carSSR` object inside the decoded RSC payload and parse it."""
    marker = '"carSSR":'
    start = rsc.find(marker)
    if start < 0:
        return None
    obj_start = rsc.find("{", start)
    if obj_start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(obj_start, len(rsc)):
        ch = rsc[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    raw = rsc[obj_start : i + 1]
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as exc:
                        logger.debug(f"Generic adapter: carSSR JSON parse fail: {exc}")
                        return None
    return None


def _extract_carssr_images(rsc: str) -> list[str]:
    """Pull image URLs out of the carSSR images array. Each image is encoded
    as `"image":{"url":"..."}`. Thumbnails are dropped."""
    images: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'"image"\s*:\s*\{\s*"url"\s*:\s*"([^"]+)"', rsc):
        url = m.group(1)
        if not url or url in seen:
            continue
        if "thumb" in url.lower():
            continue
        seen.add(url)
        images.append(url)
    return images


def _dealer_location_from_blocks(blocks: list[dict]) -> str | None:
    """Pull a city-level location string from an AutoDealer / Organization /
    LocalBusiness JSON-LD block. carSSR detail pages usually ship these
    alongside the (non-Vehicle) page-level JSON-LD."""
    for b in blocks:
        if not _has_type(b, {"AutoDealer", "LocalBusiness", "Organization"}):
            continue
        addr = b.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion")]
            joined = ", ".join(p for p in parts if isinstance(p, str) and p.strip())
            if joined:
                return joined
        if isinstance(addr, str) and addr.strip():
            return addr.strip()
    return None


def _parse_via_carssr(html: str, stock_url: str, listing_id: str) -> dict | None:
    """Build a parse_listing-shape dict from a Next.js carSSR payload.

    Returns None if the page doesn't embed carSSR data. The dict shape
    matches the JSON-LD path's output exactly so the orchestrator writes
    identical VehicleListing rows regardless of which strategy succeeded.
    """
    rsc = _decode_rsc_payload(html)
    if "\"carSSR\":" not in rsc:
        return None
    car = _extract_car_object(rsc)
    if not car:
        return None

    # Used-only filter: skip listings whose carSSR car_type isn't "used".
    # If the field is missing/null we accept (some installations of the
    # platform omit it for used-only inventory).
    car_type = car.get("car_type")
    if isinstance(car_type, str) and car_type.strip().lower() and car_type.strip().lower() not in USED_CARSSR_TYPES:
        logger.info(
            f"Generic adapter: skipping carSSR listing — car_type='{car_type}' "
            f"(not 'used') at {stock_url}"
        )
        return None

    year = car.get("year")
    make = car.get("make")
    model = car.get("model")
    # Canonicalise make → exact manufacturer name. Must match migration 0031.
    make, model = normalize_make(make, model)
    badge = car.get("badge") or ""
    series = car.get("series") or ""
    variant = " ".join(p for p in (badge, series) if p).strip() or None
    price = car.get("egcprice") or car.get("price")
    mileage = (
        car.get("km") or car.get("odometer_reading")
        or car.get("odometer") or car.get("kms") or car.get("kilometres")
    )
    body_type = car.get("simple_body") or car.get("body")
    fuel_type = car.get("simple_fuel") or car.get("fuel")
    transmission = car.get("simple_transmission") or car.get("trans")
    colour = car.get("colour")
    description = car.get("description") or ""
    if mileage is not None:
        mileage_text = f"Mileage: {mileage}km"
        if mileage_text.lower() not in description.lower():
            description = f"{mileage_text}\n{description}".strip()
    images = _extract_carssr_images(rsc)
    title = car.get("name") or " ".join(
        str(p) for p in (year, make, model, badge) if p
    )

    # Dealer-level location from any AutoDealer/Organization JSON-LD on the
    # page — these dealer-platform sites ship one alongside the vehicle data.
    location = _dealer_location_from_blocks(list(_iter_jsonld_blocks(html)))

    return {
        "list_id": str(listing_id),
        "title": title,
        "price": price,
        "description": description,
        "image": images,
        "location": location,
        "body_type": body_type,
        "fuel_type": fuel_type,
        "color": colour,
        "variant": variant,
        "year": str(year) if year is not None else None,
        "model": model,
        "make": make,
        "mileage": mileage,
        "transmission": transmission,
        "url": stock_url,
    }


# ---------------------------------------------------------------------------
# Stock-page link discovery
# ---------------------------------------------------------------------------


def _normalize_url(url: str) -> str:
    """Strip the fragment for dedupe purposes. Query is kept since some
    platforms key detail pages on `?id=N`. Trailing slash is preserved as-is
    because some sites distinguish `/cars/123` from `/cars/123/`."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    return parsed._replace(fragment="").geturl()


def _looks_like_detail_url(href: str, base_host: str, exclude_path: str = "") -> bool:
    if not href:
        return False
    try:
        parsed = urlparse(href)
    except Exception:
        return False
    if parsed.netloc and parsed.netloc.lower() != base_host:
        return False
    path = parsed.path or ""
    if not path or path == "/":
        return False
    if path.lower().rstrip("/") in NAV_PATHS:
        return False
    if path.count("/") < 2:
        return False
    # Anchors that point back to the profile page itself (often #fragments
    # from off-canvas menus / "scroll to" links) are not detail pages.
    if exclude_path and path.rstrip("/") == exclude_path.rstrip("/"):
        return False
    return True


def _url_shape(url: str) -> str:
    """Normalize a URL path so structurally similar URLs group together."""
    path = urlparse(url).path
    segments = []
    for seg in path.split("/"):
        if not seg:
            continue
        if re.fullmatch(r"\d+", seg):
            segments.append("{n}")
        elif any(c.isdigit() for c in seg) and "-" in seg:
            segments.append("{slug-id}")
        elif len(seg) > 16 and "-" in seg:
            segments.append("{slug}")
        else:
            segments.append(seg.lower())
    return "/".join(segments)


def _pick_detail_pattern(hrefs, base_url: str):
    base_parsed = urlparse(base_url)
    base_host = base_parsed.netloc.lower()
    base_path = base_parsed.path or ""
    absolutized: list[str] = []
    seen_norm: set[str] = set()
    for href in hrefs:
        if not href:
            continue
        absolute = _normalize_url(urljoin(base_url, href))
        if absolute in seen_norm:
            continue
        if not _looks_like_detail_url(absolute, base_host, exclude_path=base_path):
            continue
        seen_norm.add(absolute)
        absolutized.append(absolute)
    if not absolutized:
        return None
    groups: dict[str, list[str]] = {}
    for url in absolutized:
        groups.setdefault(_url_shape(url), []).append(url)
    best_shape, best_urls = max(groups.items(), key=lambda kv: len(kv[1]))
    if len(best_urls) < 3:
        return None
    return best_shape, best_urls


def _candidate_next_pages(profile_url: str, html: str) -> list[str]:
    """Return URLs that look like the next paginated stock-listing page."""
    soup = BeautifulSoup(html, "html.parser")
    pages: list[str] = []
    seen: set[str] = set()

    for tag in soup.find_all(["link", "a"], rel=lambda v: v and "next" in v):
        href = tag.get("href")
        if href:
            absolute = urljoin(profile_url, href)
            if absolute not in seen:
                seen.add(absolute)
                pages.append(absolute)

    # Speculative ?page=N walk for sites without rel=next markup. Capped here;
    # the caller stops at the first page that yields no new detail URLs.
    parsed = urlparse(profile_url)
    if "page=" not in (parsed.query or ""):
        sep = "&" if parsed.query else "?"
        for n in range(2, MAX_PAGINATION_PAGES + 1):
            synthetic = f"{profile_url}{sep}page={n}"
            if synthetic not in seen:
                seen.add(synthetic)
                pages.append(synthetic)
    return pages


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class GenericJsonLdAdapter(DomainAdapter):
    """Schema.org JSON-LD fallback adapter for any unregistered dealership site."""

    LOCATION_DEFAULT = None
    # Intentionally empty — the generic adapter never claims authoritative
    # knowledge over any host's image-proxy decision. Specific adapters use
    # their KNOWN_HOSTS to short-circuit the resolver before we get here.
    KNOWN_HOSTS: set[str] = set()

    def __init__(self, host: str = ""):
        # HOST is set per-resolved-URL (one instance per dealership site) so
        # the orchestrator's `profile_id = adapter.HOST` partitions listings
        # by source host the same way specific adapters do.
        self.HOST = host.lower() if host else ""

    def extract_listing_id(self, stock_url: str) -> str | None:
        match = re.search(r"(\d{3,})(?:[-/]\d{1,3})?/?$", stock_url.rstrip("/"))
        if match:
            return match.group(1)
        # Stable fallback — Python's built-in hash() is process-randomized,
        # which would break reconcile across cron runs. Use md5 of the path
        # so the same URL always produces the same listing id.
        path = urlparse(stock_url).path or stock_url
        digest = hashlib.md5(path.encode("utf-8")).hexdigest()
        return f"sha-{digest[:12]}"

    def discover_stock_links(self, profile_url: str) -> list[str]:
        try:
            response = _http_get(profile_url)
        except Exception as exc:
            logger.error(f"Generic adapter: failed to fetch {profile_url}: {exc}")
            return []
        if response.status_code != 200:
            logger.error(
                f"Generic adapter: non-200 ({response.status_code}) for {profile_url}"
            )
            return []

        collected: list[str] = []
        seen: set[str] = set()
        profile_norm = _normalize_url(profile_url)

        # 1. Prefer ItemList JSON-LD on the profile page if present.
        for block in _iter_jsonld_blocks(response.text):
            if not _has_type(block, {"ItemList"}):
                continue
            for elem in block.get("itemListElement") or []:
                candidate = None
                if isinstance(elem, str):
                    candidate = elem
                elif isinstance(elem, dict):
                    candidate = elem.get("url")
                    if not candidate and isinstance(elem.get("item"), dict):
                        candidate = elem["item"].get("url")
                if candidate:
                    absolute = _normalize_url(urljoin(profile_url, candidate))
                    if absolute and absolute != profile_norm and absolute not in seen:
                        seen.add(absolute)
                        collected.append(absolute)

        # 2. Fall back to URL-pattern detection on the rendered anchors.
        if not collected:
            soup = BeautifulSoup(response.text, "html.parser")
            hrefs = [a.get("href") for a in soup.find_all("a") if a.get("href")]
            picked = _pick_detail_pattern(hrefs, profile_url)
            if picked:
                shape, urls = picked
                logger.info(
                    f"Generic adapter: detected {len(urls)} detail links on "
                    f"{profile_url} (shape={shape})"
                )
                for url in urls:
                    if url not in seen:
                        seen.add(url)
                        collected.append(url)

        if not collected:
            logger.warning(
                f"Generic adapter: no detail links discovered on {profile_url}"
            )
            return []

        # 3. Walk pagination, accepting URLs that match the detected shape.
        shape_of = _url_shape(collected[0])
        visited: set[str] = {profile_url}
        for follow_url in _candidate_next_pages(profile_url, response.text):
            if len(visited) >= MAX_PAGINATION_PAGES:
                break
            if follow_url in visited:
                continue
            visited.add(follow_url)
            try:
                follow_resp = _http_get(follow_url)
            except Exception as exc:
                logger.debug(
                    f"Generic adapter: pagination fetch failed for {follow_url}: {exc}"
                )
                break
            if follow_resp.status_code != 200:
                break
            soup = BeautifulSoup(follow_resp.text, "html.parser")
            new_in_page = 0
            for tag in soup.find_all("a"):
                href = tag.get("href")
                if not href:
                    continue
                absolute = _normalize_url(urljoin(follow_url, href))
                if not absolute or absolute == profile_norm or absolute in seen:
                    continue
                if _url_shape(absolute) != shape_of:
                    continue
                seen.add(absolute)
                collected.append(absolute)
                new_in_page += 1
                if len(collected) >= MAX_LISTINGS:
                    break
            if new_in_page == 0:
                # Speculative ?page=N walk: stop on the first empty page.
                break
            if len(collected) >= MAX_LISTINGS:
                logger.warning(
                    f"Generic adapter: hit cap of {MAX_LISTINGS} listings"
                )
                break

        logger.info(
            f"Generic adapter: collected {len(collected)} detail links from {profile_url}"
        )
        return collected[:MAX_LISTINGS]

    def parse_listing(self, stock_url: str) -> dict | None:
        listing_id = self.extract_listing_id(stock_url)
        if not listing_id:
            logger.error(f"Generic adapter: could not derive listing id from {stock_url}")
            return None
        try:
            response = _http_get(stock_url)
        except Exception as exc:
            logger.error(f"Generic adapter: failed to fetch {stock_url}: {exc}")
            return None
        if response.status_code != 200:
            logger.error(
                f"Generic adapter: non-200 ({response.status_code}) for {stock_url}"
            )
            return None

        vehicle = None
        for block in _iter_jsonld_blocks(response.text):
            if not _has_type(block, VEHICLE_TYPES):
                continue
            # Used-only filter: skip Vehicle JSON-LD that's explicitly
            # marked as new / damaged / refurbished. Missing itemCondition
            # is accepted on the assumption that the source URL or the
            # platform's editorial scope has already restricted to used.
            cond = block.get("itemCondition")
            cond_value = _unwrap_name(cond) if isinstance(cond, (dict, list)) else cond
            if isinstance(cond_value, str):
                if cond_value.strip() in NEW_CONDITION_VALUES:
                    logger.info(
                        f"Generic adapter: skipping Vehicle JSON-LD — "
                        f"itemCondition='{cond_value}' (not Used) at {stock_url}"
                    )
                    continue
            vehicle = block
            break
        if vehicle is None:
            # Strategy 2: Next.js carSSR fallback (used by a family of AU
            # dealer-platform sites — see _parse_via_carssr docstring).
            carssr_result = _parse_via_carssr(response.text, stock_url, listing_id)
            if carssr_result is not None:
                logger.info(
                    f"Generic adapter: parsed listing {listing_id} via carSSR: "
                    f"{carssr_result.get('title')}"
                )
                return carssr_result
            logger.warning(
                f"Generic adapter: no Vehicle JSON-LD or carSSR data at {stock_url}"
            )
            return None

        make = (
            _unwrap_name(vehicle.get("brand"))
            or _unwrap_name(vehicle.get("manufacturer"))
        )
        model = (
            _unwrap_name(vehicle.get("model"))
            or _unwrap_name(vehicle.get("vehicleModel"))
        )
        # Canonicalise make → exact manufacturer name. Must match migration 0031.
        make, model = normalize_make(make, model)
        year = _extract_year(vehicle)
        price = _extract_price(vehicle)
        mileage = _extract_mileage(vehicle)
        body_type = _unwrap_name(vehicle.get("bodyType"))
        fuel_type = _unwrap_name(vehicle.get("fuelType"))
        transmission = _unwrap_name(vehicle.get("vehicleTransmission"))
        color = _unwrap_name(vehicle.get("color"))
        variant = (
            _unwrap_name(vehicle.get("trimLevel"))
            or _unwrap_name(vehicle.get("vehicleConfiguration"))
        )
        title = _unwrap_name(vehicle.get("name")) or " ".join(
            str(p) for p in [year, make, model] if p
        )
        description = vehicle.get("description")
        description = description.strip() if isinstance(description, str) else ""
        images = _extract_images(vehicle, stock_url)
        location = _extract_location(vehicle)

        if mileage is not None:
            mileage_text = f"Mileage: {mileage}km"
            if mileage_text.lower() not in description.lower():
                description = f"{mileage_text}\n{description}".strip()

        listing_details = {
            "list_id": str(listing_id),
            "title": title,
            "price": price,
            "description": description,
            "image": images,
            "location": location,
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
        logger.info(f"Generic adapter: parsed listing {listing_id}: {title}")
        return listing_details

    def needs_image_proxy(self, image_url: str) -> bool:
        # No CORS knowledge of unknown third-party CDNs, so the safe default
        # is to proxy. The resolver short-circuits this when a specific
        # adapter claims the host via its KNOWN_HOSTS set, preserving
        # Buckingham's Cloudfront pass-through and DNA's same-origin rule.
        return bool(image_url)

    def discover_dealer_location(self, profile_url: str) -> dict | None:
        # Fetch the dealer's stock/profile page and look for an
        # AutoDealer / LocalBusiness / Organization JSON-LD block with an
        # address. Modern dealer-platform sites ship this for SEO; sites
        # without it return None and the admin fills it in manually.
        try:
            response = _http_get(profile_url)
        except Exception as exc:
            logger.warning(
                f"Generic adapter: dealer-location fetch failed for {profile_url}: {exc}"
            )
            return None
        if response.status_code != 200:
            logger.warning(
                f"Generic adapter: dealer-location non-200 ({response.status_code}) "
                f"for {profile_url}"
            )
            return None

        for block in _iter_jsonld_blocks(response.text):
            if not _has_type(block, {"AutoDealer", "LocalBusiness", "Organization"}):
                continue
            addr = block.get("address")
            if not isinstance(addr, dict):
                continue
            locality = addr.get("addressLocality")
            region = addr.get("addressRegion")
            if not (isinstance(locality, str) and locality.strip()):
                continue
            state_code = normalize_au_state(region) if isinstance(region, str) else None
            if not state_code:
                continue
            return {"suburb": locality.strip(), "state": state_code}
        return None
