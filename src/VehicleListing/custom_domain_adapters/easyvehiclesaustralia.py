import logging
import random
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from zenrows import ZenRowsClient

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


# ---------------------------------------------------------------------------
# Fetch layer: bot-protection detection + ZenRows fallback.
#
# VirtualYard rate-limits repeated hits from one IP. Once tripped it 302s to
# /security.php?err=<code> and serves a "prove you're human" page — crucially
# with HTTP *200*, so a status-code check alone treats it as success. Parsing
# that page yields no specs, which is how hollow listings (no year/make/model,
# no price, no images) got written to the DB.
#
# So: detect the challenge explicitly, and on a block retry the same URL through
# ZenRows (rotating premium AU proxies) so the request arrives from a different
# IP. If both attempts fail the caller gets None and SKIPS the listing — never
# a half-empty row. ZenRows is a fallback rather than the default path so we
# only spend credits on requests the direct fetch actually lost (~8 of 28 per
# run at time of writing) and behaviour stays unchanged when the site is happy.
# ---------------------------------------------------------------------------

# Bot-challenge fingerprints. The redirect target is the strongest signal; the
# <title> catches the case where a proxy already followed the redirect for us
# (ZenRows returns the final page, so response.url is the ZenRows API URL and
# can't be inspected). Deliberately NOT size-based — a small-but-legitimate
# page must not be mistaken for a block.
_BLOCK_URL_MARKER = "security.php"
_BLOCK_TITLE_RE = re.compile(r"<title>\s*security\s*</title>", re.IGNORECASE)

# ZenRows knobs. premium_proxy + AU geo because the block is IP-reputation
# based (the site is AU-only and datacentre ranges are the first to be
# throttled). No JS rendering: the site is fully server-rendered, so paying for
# js_render would be wasted credits.
_ZENROWS_PARAMS = {"premium_proxy": "true", "proxy_country": "au"}

# Transport/throttle failures worth a second attempt through the proxy. 404/410
# are deliberately absent — a genuinely missing page shouldn't burn credits.
_RETRYABLE_STATUSES = frozenset({403, 408, 429, 500, 502, 503, 504})

# (connect, read) timeout for the per-photo availability HEAD in _parse_images.
# Deliberately short: the check is a best-effort guard, not something a scrape
# should stall on.
_IMAGE_CHECK_TIMEOUT = (5, 10)

FETCH_OK = "ok"
FETCH_BLOCKED = "blocked"
FETCH_ERROR = "error"


def _http_get(url):
    """Plain direct fetch — the un-proxied primitive used by _fetch()."""
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def _looks_blocked(response):
    """True when `response` is the bot-challenge page rather than real content."""
    if _BLOCK_URL_MARKER in (getattr(response, "url", "") or "").lower():
        return True
    for prior in getattr(response, "history", None) or []:
        location = (prior.headers.get("location") or "").lower()
        if _BLOCK_URL_MARKER in location:
            return True
    try:
        return bool(_BLOCK_TITLE_RE.search(response.text or ""))
    except Exception:
        return False


def _zenrows_get(url):
    """Re-fetch `url` through ZenRows. Returns a response or None."""
    if not settings.ZENROWS_API_KEY:
        logger.error(
            "ZENROWS_API_KEY is not configured — cannot retry blocked "
            f"EasyVehicles fetch for {url}"
        )
        return None
    try:
        client = ZenRowsClient(settings.ZENROWS_API_KEY)
        response = client.get(url, params=_ZENROWS_PARAMS)
    except Exception as exc:
        logger.error(f"ZenRows request errored for {url}: {exc}")
        return None
    if response.status_code == 402:
        logger.error(
            f"ZenRows returned 402 (out of credits / check API key) for {url}"
        )
        return None
    return response


def _fetch(url):
    """Fetch `url`, retrying through ZenRows if the direct hit is blocked.

    Returns ``(html, status)`` where status is FETCH_OK / FETCH_BLOCKED /
    FETCH_ERROR. ``html`` is None unless the status is FETCH_OK, so callers can
    never accidentally parse a challenge page.
    """
    response = None
    try:
        response = _http_get(url)
    except Exception as exc:
        logger.error(f"Direct fetch failed for {url}: {exc}")

    if response is not None:
        blocked = _looks_blocked(response)
        if response.status_code == 200 and not blocked:
            return response.text, FETCH_OK
        if not blocked and response.status_code not in _RETRYABLE_STATUSES:
            logger.error(f"Non-200 ({response.status_code}) for {url}")
            return None, FETCH_ERROR
        logger.warning(
            f"EasyVehicles fetch obstructed for {url} "
            f"(status={response.status_code}, bot_challenge={blocked}) — "
            "retrying via ZenRows"
        )
    else:
        logger.warning(f"Retrying {url} via ZenRows after transport failure")

    proxied = _zenrows_get(url)
    if proxied is None:
        return None, FETCH_BLOCKED if response is not None else FETCH_ERROR
    if proxied.status_code != 200:
        logger.error(f"ZenRows non-200 ({proxied.status_code}) for {url}")
        return None, FETCH_ERROR
    if _looks_blocked(proxied):
        logger.error(f"Still bot-challenged via ZenRows for {url} — giving up this run")
        return None, FETCH_BLOCKED
    logger.info(f"ZenRows fallback succeeded for {url}")
    return proxied.text, FETCH_OK


def _base_url_from(profile_url):
    """Derive the scheme+host to scrape from.

    We only trust the host the user registered when it's the canonical dealer
    host (or its www form) — for those, staying faithful to what they typed is
    fine. Any other host (e.g. the marketing domain easyvehicles.com.au, which
    only 301-redirects to the canonical host at the *root* — deeper paths like
    /stock return an empty page) is normalised to CANONICAL_BASE_URL so the
    stock index and detail pages actually resolve. Also falls back to the
    canonical base if the URL is unparseable or host-less."""
    try:
        parsed = urlparse(profile_url)
        host = (parsed.netloc or "").lower()
        if parsed.scheme and host in (HOST, f"www.{HOST}"):
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

    The site is fully server-rendered HTML, so a plain requests +
    BeautifulSoup scrape works — no Playwright needed. It *does* however
    rate-limit repeated hits from one IP behind a /security.php bot challenge
    served with HTTP 200; see the fetch layer above, which detects that and
    retries through ZenRows. Detail pages
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
            html, status = _fetch(page_url)
            if status == FETCH_BLOCKED:
                # Abandon the whole run rather than hand back what we scraped so
                # far. A short list is worse than an empty one: the caller's
                # reconcile step deletes/marks-sold every listing missing from
                # the batch, and its cascade guard only trips below 50% of the
                # existing count — so a block on page 2 of 2 could silently bin
                # the listings that live on that page. Returning [] makes the
                # caller bail with "No listings found" and leave the DB alone;
                # the next scheduled run retries.
                logger.error(
                    f"Bot-protection block on stock index {page_url} — abandoning "
                    "discovery for this run to avoid a partial-list reconcile"
                )
                return []
            if html is None:
                # Genuine error (404 = we walked past the last page). Keep the
                # links gathered so far, as before.
                logger.error(f"Failed to fetch stock index {page_url}")
                break

            # Match detail links only: exactly /buy/<slug>/<token> (two path
            # segments). Excludes /buy/<slug>/securepay.virtualyard.com.au/...
            # finance/enquiry links, which have extra path segments.
            page_hrefs = re.findall(
                r"""href=['"]((?:https?://[^'"/]+)?/buy/[^'"/]+/[A-Za-z0-9_\-]+)['"]""",
                html,
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

    # Recognises a real full-size gallery photo URL (either host), excluding the
    # theme no-photo placeholder and site chrome. Query strings (e.g. ?w=2048 on
    # the VirtualYard-hosted variants) are tolerated.
    _IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp)(?:$|\?)", re.IGNORECASE)

    @staticmethod
    def _is_gallery_image(url):
        if not url:
            return False
        if "storage.googleapis.com/au-assets/" not in url and \
                "virtualyard.com.au/photos/" not in url:
            return False
        return bool(EasyVehiclesAustraliaAdapter._IMG_EXT_RE.search(url))

    @staticmethod
    def _url_is_live(url):
        """True unless the URL definitively answers with a non-200.

        A transport error or a HEAD-hostile status (405/501) returns True: we
        can't prove the URL is dead, and keeping the primary preserves existing
        behaviour rather than churning every photo onto the fallback host
        because of one flaky request.
        """
        try:
            response = requests.head(
                url, headers={"User-Agent": USER_AGENT},
                timeout=_IMAGE_CHECK_TIMEOUT, allow_redirects=True,
            )
        except Exception as exc:
            logger.warning(f"Could not verify gallery image {url}: {exc}")
            return True
        if response.status_code in (405, 501):
            return True
        return response.status_code == 200

    def _parse_images(self, html):
        """Collect exactly one URL per real gallery photo.

        The VirtualYard lightSlider gallery renders EACH photo several times at
        different sizes, and — crucially — every rendition is a *different*
        signed URL with its own opaque token:

          * the fullscreen image on the slide's own ``<li data-src=...>``
          * a carousel-sized copy on the inner ``<img src>/<img data-src>``
          * a thumbnail on ``data-thumb``
          * plus lightSlider's loop ``<li class="clone">`` copies

        The previous implementation regex-scanned the whole page for
        au-assets URLs and de-duplicated by *exact string*. Because the
        full-size and carousel-sized renditions of one photo are distinct
        strings, that dedup couldn't pair them, so every photo was collected
        twice (25 photos → 50 URLs) and published 2× on Facebook — the
        "duplicate images" bug.

        Fix: parse the gallery structurally and take a single canonical URL per
        real slide — the fullscreen ``<li data-src>`` (falling back to the
        inner ``<img>`` if a slide lacks it) — skipping ``li.clone`` loop
        copies. Selecting only the ``vehicle-photo-carousel`` list also keeps us
        out of the "Recent vehicles" sidebar for free (no page-cut needed on
        this path). If the gallery markup can't be found (template change), fall
        back to the old whole-page scan so we degrade to "some duplicates"
        rather than "no images at all".

        Dead-primary handling: a slide's ``data-src`` (the storage.googleapis.com
        copy) is frequently a 404 — 6 of 20 photos on one reported listing, 11 of
        21 on another. The dealer's own page still looks complete because each
        slide carries a ``data-src-error`` twin on virtualyard.com.au that the
        markup's ``onerror`` swaps in; we stored only the dead primary, so the
        extension fetched a 404 per affected photo, dropped it, and tripped its
        PARTIAL_IMAGE_UPLOAD guard. So — when EASYVEHICLES_VERIFY_IMAGE_URLS is
        on — verify each primary and substitute the slide's own fallback if it
        isn't serving. It defaults to OFF because those 404s were subsequently
        measured to be transient and the ingest task's own retries recover them;
        see the setting's comment for the numbers."""
        soup = BeautifulSoup(html, "html.parser")
        gallery = []
        seen = set()
        verify = getattr(settings, "EASYVEHICLES_VERIFY_IMAGE_URLS", True)

        carousel = soup.find("ul", class_="vehicle-photo-carousel")
        if carousel:
            for li in carousel.find_all("li", recursive=False):
                if "clone" in (li.get("class") or []):
                    continue  # lightSlider loop duplicate
                # Prefer the slide's own fullscreen image; fall back to the
                # inner <img>'s data-src / src if the slide lacks data-src.
                url = li.get("data-src")
                if not self._is_gallery_image(url):
                    img = li.find("img")
                    if img is not None:
                        url = img.get("data-src") or img.get("src")
                if not self._is_gallery_image(url):
                    continue
                # Only pay for the check when there's something to switch to,
                # and only accept the fallback if it's actually serving —
                # otherwise keep the primary and let the pipeline's retries and
                # the proxy deal with it, exactly as before.
                fallback = li.get("data-src-error")
                if (
                    verify
                    and self._is_gallery_image(fallback)
                    and fallback != url
                    and not self._url_is_live(url)
                ):
                    if self._url_is_live(fallback):
                        logger.info(
                            f"Gallery image {url} is not serving — using the "
                            f"slide's fallback {fallback}"
                        )
                        url = fallback
                    else:
                        logger.warning(
                            f"Gallery image {url} is not serving and its "
                            f"fallback {fallback} isn't either — keeping the primary"
                        )
                if url not in seen:
                    seen.add(url)
                    gallery.append(url)
            if gallery:
                return gallery

        # --- Fallback: gallery markup not found (e.g. template change). ---
        # Old behaviour: whole-page scan, cut at the related-vehicles section so
        # a sidebar car's photos can't attach to this listing. Exact-string
        # dedup only — may keep same-photo size variants, but that's strictly
        # better than returning no images.
        lowered = html.lower()
        cut = len(html)
        for marker in ("recent vehicles", "similar vehicles",
                       "you may also like", "recommended for you"):
            idx = lowered.find(marker)
            if idx != -1:
                cut = min(cut, idx)
        scan = html[:cut]
        for host_re in (
            r"https://storage\.googleapis\.com/au-assets/[A-Za-z0-9_\-./]+\.(?:jpe?g|png|webp)",
            r"https://virtualyard\.com\.au/photos/[A-Za-z0-9_\-./]+\.(?:jpe?g|png|webp)",
        ):
            for m in re.finditer(host_re, scan):
                url = m.group(0)
                if url not in seen:
                    seen.add(url)
                    gallery.append(url)
            if gallery:
                break
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
        html, status = _fetch(stock_url)
        if html is None:
            # Blocked or errored. Returning None makes the orchestrator skip
            # this listing entirely — no create, no update — so a transient
            # block can never overwrite good data with blanks. The listing is
            # still counted as "seen" by the caller, so it won't be reconciled
            # away either; the next run picks it up.
            logger.error(
                f"Skipping EasyVehicles listing {listing_id} — fetch {status} for {stock_url}"
            )
            return None

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

        # Last line of defence against writing an empty listing. Even with the
        # block detection above, any future change that leaves the specs table
        # unparseable would otherwise produce a row with every field None — the
        # exact hollow listings this guard exists to prevent. Year+make is the
        # minimum identity a listing needs to be publishable; without it we'd
        # rather have no row than a blank one, so skip and retry next run.
        # (Deliberately not gated on `model`: the make-normalizer legitimately
        # empties it for two-token brands such as MINI Cooper, and that listing
        # is otherwise complete.)
        if not year or not make:
            logger.error(
                f"Discarding EasyVehicles listing {listing_id} — no vehicle data "
                f"parsed (year={year!r} make={make!r} model={model!r}). "
                f"Page fetched OK ({len(html)} bytes) but specs were unreadable."
            )
            return None

        # Non-fatal completeness warning: worth surfacing because a listing
        # published without these looks broken to buyers, but not worth dropping
        # an otherwise-identifiable vehicle over.
        missing = [
            name for name, value in (("price", price), ("images", images))
            if not value
        ]
        if missing:
            logger.warning(
                f"EasyVehicles listing {listing_id} parsed with missing "
                f"{', '.join(missing)} — saving anyway"
            )

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
        # Easy Vehicles Australia (Teixeira Group) trades from a single physical
        # location — 5 Old Aberdeen Pl, West Perth WA 6005 (per their /contact
        # page). Per-listing location isn't exposed on the detail pages, so
        # (mirroring the DNA / Buckingham adapters) we return the dealership's
        # own suburb/state here. discover_and_save_dealer_location() stamps this
        # onto User.dealership_suburb/_state at signup, and the listing
        # serializer builds each row's `location` from it. A reseller feeding
        # off this site who isn't in West Perth can be overridden per-user in
        # the admin.
        return {
            "suburb": "West Perth",
            "state": "WA",
            "address": "5 Old Aberdeen Pl, West Perth WA 6005",
        }
