import json
import logging
import re

import requests

from .base import DomainAdapter

logger = logging.getLogger("custom_domain")

BASE_URL = "https://www.buckinghamautos.com.au"
SEARCH_PATH = "/search/used-cars"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _http_get(url):
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def _normalize_color(value):
    """Title-case and strip cruft from Buckingham's raw colour strings.

    Buckingham's carSSR payload ships colours like ``WHITE``, ``GREY``,
    ``"SILVER OR CHROME"``, ``"WHITE / null"``. Same shape Gumtree-style
    consumer labels — strip the secondary colour / alternate listing and
    title-case the result. Matches the DNA adapter's normalization.
    """
    if not value:
        return None
    main = value.split("/")[0].strip()
    main = re.split(r"\s+OR\s+", main, flags=re.IGNORECASE)[0].strip()
    return main.title() if main else None


# Acronym makes that should stay all-caps when title-casing. Mirrors the DNA
# adapter's list (some carSSR sites ship "SKODA" / "LDV" in upper case so they
# need normalising too — uppercase brand names are an FB Marketplace
# scraper-account fingerprint).
_MAKE_ACRONYMS = {"BMW", "MG", "HSV", "VW", "GMC", "DAF", "BYD", "MAN", "SAAB", "LDV", "RAM"}


def _normalize_make(value):
    if not value:
        return None
    raw = value.strip()
    key = raw.upper()
    if key in _MAKE_ACRONYMS:
        return key
    return raw.title()


def _decode_rsc_payload(html: str) -> str:
    """Concatenate all self.__next_f.push payloads and decode JSON string escapes."""
    # Each push payload is a valid JSON string body. Wrap in quotes and let
    # json.loads handle every escape (\", \\, \n, \uXXXX, …) correctly —
    # naive sequential .replace() calls mishandle interleaving like \\\".
    payloads = re.findall(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)", html, re.S)
    decoded = []
    for raw in payloads:
        try:
            decoded.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return "".join(decoded)


def _extract_car_object(rsc: str) -> dict | None:
    """Find the carSSR JSON object inside the RSC payload and parse it."""
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
                        logger.error(f"Failed to parse Buckingham carSSR JSON: {exc}")
                        return None
    return None


def _extract_images(rsc: str) -> list[str]:
    """Pull image URLs out of the carSSR images array."""
    images = []
    for m in re.finditer(r'"image"\s*:\s*\{\s*"url"\s*:\s*"([^"]+)"', rsc):
        url = m.group(1)
        if url and url not in images and "/photo/" in url and "thumb" not in url:
            images.append(url)
    return images


class BuckinghamAutosAdapter(DomainAdapter):
    HOST = "www.buckinghamautos.com.au"
    # No per-listing location is exposed on Buckingham's detail pages.
    # Hard-coding "Wangara, WA" would be correct for Buckingham themselves but
    # wrong for any reseller using the extension — every listing would claim a
    # suburb that doesn't match the reseller's account, which is the same
    # cross-seller fingerprint that gets DNA listings banned. Returning None
    # lets the extension fall back to the dealer's own profile location.
    LOCATION_DEFAULT = None
    # Cloudfront CDN serves Buckingham's images with permissive CORS; mark it
    # authoritative so the generic fallback adapter doesn't try to proxy them.
    KNOWN_HOSTS = {"www.buckinghamautos.com.au", "d2s8i866417m9.cloudfront.net"}

    def extract_listing_id(self, stock_url: str) -> str | None:
        # The Buckingham listing id is always ≥4 digits, optionally followed by a
        # small "-N" duplicate suffix (e.g. /cars/...-10259-2). Models like Mazda
        # CX-5 / CX-3 / BT-50 / Mazda 6 embed 1–2 digits before the id, so a
        # naive trailing-digits regex over-matches (e.g. "5-10350").
        match = re.search(r"-(\d{4,})(?:-(\d{1,2}))?/?$", stock_url.rstrip("/"))
        if not match:
            return None
        main, suffix = match.group(1), match.group(2)
        return f"{main}-{suffix}" if suffix else main

    def discover_stock_links(self, profile_url: str) -> list[str]:
        # Buckingham's /search/used-cars is fully CDN-cached and ignores ?page=N
        # at the SSR layer — pagination happens client-side via a Supabase Edge
        # Function call, so a plain HTTP GET always returns the same 12 hits.
        # Drive a real browser through each page and harvest hrefs after JS
        # hydration runs.
        from playwright.sync_api import sync_playwright

        seen: set[str] = set()
        links: list[str] = []
        max_pages = 50

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()

                for page_num in range(1, max_pages + 1):
                    page_url = (
                        f"{BASE_URL}{SEARCH_PATH}"
                        if page_num == 1
                        else f"{BASE_URL}{SEARCH_PATH}?page={page_num}"
                    )
                    logger.info(f"Fetching Buckingham stock index page {page_num}: {page_url}")
                    try:
                        page.goto(page_url, wait_until="networkidle", timeout=60000)
                    except Exception as exc:
                        logger.error(f"Playwright navigation failed for {page_url}: {exc}")
                        break

                    hrefs = page.evaluate(
                        "() => Array.from(document.querySelectorAll('a[href^=\"/cars/\"]'))"
                        ".map(a => a.getAttribute('href'))"
                    )
                    new_count = 0
                    for href in hrefs or []:
                        if href in seen:
                            continue
                        seen.add(href)
                        links.append(f"{BASE_URL}{href}")
                        new_count += 1

                    logger.info(
                        f"Page {page_num}: harvested {new_count} new links ({len(links)} total)"
                    )
                    if new_count == 0:
                        break

                context.close()
                browser.close()
        except Exception as exc:
            logger.error(f"Buckingham Playwright session failed: {exc}")

        logger.info(f"Total unique Buckingham stock links collected: {len(links)}")
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

        rsc = _decode_rsc_payload(response.text)
        car = _extract_car_object(rsc)
        if not car:
            logger.error(f"Buckingham detail page missing carSSR object: {stock_url}")
            return None

        year = car.get("year")
        make = _normalize_make(car.get("make"))
        model = car.get("model")
        badge = car.get("badge") or ""
        series = car.get("series") or ""
        variant = " ".join([badge, series]).strip() or None

        price = car.get("egcprice") or car.get("price")
        mileage = car.get("km") or car.get("odometer_reading")
        body_type = car.get("simple_body") or car.get("body")
        fuel_type = car.get("simple_fuel") or car.get("fuel")
        transmission = car.get("simple_transmission") or car.get("trans")
        colour = _normalize_color(car.get("colour"))

        images = _extract_images(rsc)
        description = car.get("description") or ""
        # Mirror the DNA adapter's rule: only prepend the mileage line when
        # the carSSR payload actually has per-vehicle copy. ~98% of
        # Buckingham listings do, but for the rare row where `description`
        # is empty we'd otherwise ship a one-line "Mileage: <N>km" stub —
        # same cross-listing-fingerprint risk as the DNA case. Return None
        # so the extension's description-length guard refuses to publish.
        if description.strip():
            if mileage is not None:
                mileage_text = f"Mileage: {mileage}km"
                if mileage_text.lower() not in description.lower():
                    description = f"{mileage_text}\n{description}".strip()
        else:
            description = None

        title = car.get("name") or " ".join(
            str(p) for p in [year, make, model, badge] if p
        )

        listing_details = {
            "list_id": str(listing_id),
            "title": title,
            "price": price,
            "description": description,
            "image": images,
            "location": self.LOCATION_DEFAULT,
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
        logger.info(f"Parsed Buckingham listing {listing_id}: {title}")
        return listing_details

    def needs_image_proxy(self, image_url: str) -> bool:
        # Cloudfront serves Access-Control-Allow-Origin: * natively, so the
        # extension can fetch these directly from the Facebook tab without a
        # proxy. Confirmed by inspecting upstream response headers.
        return False

    def discover_dealer_location(self, profile_url: str) -> dict | None:
        # Buckingham Autos is based in Wangara, WA — same industrial estate
        # as DNA. Hardcoded here so resellers using their site as a feed get
        # a sensible default; the admin can override per-user if needed.
        return {"suburb": "Wangara", "state": "WA"}
