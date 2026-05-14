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
