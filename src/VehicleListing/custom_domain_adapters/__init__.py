from urllib.parse import urlparse

from .base import DomainAdapter
from .buckinghamautos import BuckinghamAutosAdapter
from .dnacarsales import DNACarSalesAdapter
from .generic_jsonld import GenericJsonLdAdapter

_REGISTRY: dict[str, DomainAdapter] = {}


def register(adapter: DomainAdapter) -> None:
    _REGISTRY[adapter.HOST.lower()] = adapter


def _host_of(url: str) -> str:
    if not url:
        return ""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def resolve_for_url(url: str) -> DomainAdapter | None:
    """Return an adapter for the URL.

    Hand-written site-specific adapters take precedence; everything else falls
    through to a per-URL `GenericJsonLdAdapter` instance. The per-URL instance
    is important: the orchestrator uses `adapter.HOST` as the listing's
    `seller_profile_id`, so we want each dealership host to get its own
    identity even though they share the generic implementation.
    """
    host = _host_of(url)
    if not host:
        return None
    specific = _REGISTRY.get(host)
    if specific:
        return specific
    return GenericJsonLdAdapter(host=host)


def resolve_for_host(host: str) -> DomainAdapter | None:
    if not host:
        return None
    specific = _REGISTRY.get(host.lower())
    if specific:
        return specific
    return GenericJsonLdAdapter(host=host.lower())


def supported_hosts() -> list[str]:
    """Hosts with a hand-written specific adapter.

    The generic JSON-LD fallback accepts any URL whose page exposes
    schema.org/Vehicle data, so this list is no longer a gate — it's
    informational (which sites are first-class) for diagnostics and admin.
    """
    return sorted(_REGISTRY.keys())


# Gumtree image hosts are CORS-friendly and the extension expects raw URLs
# (it normalizes gumtreeau-res.cloudinary.com → images.gumtree.com.au itself).
# Listed here so /old-listings/ — which mixes Gumtree + custom-domain rows —
# leaves Gumtree URLs untouched while still proxying DNA's same-origin images.
GUMTREE_SAFE_HOSTS: set[str] = {
    "images.gumtree.com.au",
    "gumtreeau-res.cloudinary.com",
}


def any_needs_image_proxy(url: str) -> bool:
    """Decide whether the API should rewrite an image URL through its proxy.

    Resolution order:
      1. If the host is a known CORS-friendly Gumtree image host, never proxy.
      2. If any specifically-registered adapter affirms `needs_image_proxy`,
         proxy. (DNA's same-origin images.)
      3. If the image URL's host is in any specific adapter's `KNOWN_HOSTS`
         (its dealership host or any CDN it owns), trust that adapter's
         decision — i.e. don't proxy. (Buckingham's Cloudfront CDN.)
      4. Otherwise, the host is unknown — proxy by default to avoid CORS
         issues for generic-scraped sites.
    """
    if not url:
        return False
    host = _host_of(url)
    if host in GUMTREE_SAFE_HOSTS:
        return False
    if any(adapter.needs_image_proxy(url) for adapter in _REGISTRY.values()):
        return True
    for adapter in _REGISTRY.values():
        if host in adapter.KNOWN_HOSTS:
            return False
    return True


register(DNACarSalesAdapter())
register(BuckinghamAutosAdapter())

__all__ = [
    "DomainAdapter",
    "register",
    "resolve_for_url",
    "resolve_for_host",
    "supported_hosts",
    "any_needs_image_proxy",
]
