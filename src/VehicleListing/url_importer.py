from enum import Enum, auto
class ImportFromSourceOption(Enum):
    UNKNOWN = auto()
    FACEBOOK = auto()
    FACEBOOK_PROFILE = auto()
    GUMTREE_PROFILE = auto()
    GUMTREE = auto()
    CARSALES = auto()

class ImportFromUrl:
    """Import from URL Starts With"""
    EXPECTED_FACEBOOK_URL_STARTS_WITH = "https://www.facebook.com/marketplace/item/"
    EXPECTED_FACEBOOK_URL_STARTS_WITH_WEB = "https://web.facebook.com/marketplace/item/"
    EXPECTED_FACEBOOK_PROFILE_URL_STARTS_WITH = "https://www.facebook.com/marketplace/profile/"
    EXPECTED_FACEBOOK_PROFILE_URL_STARTS_WITH_WEB = "https://web.facebook.com/marketplace/profile/"
    EXPECTED_GUMTREE_PROFILE_URL_STARTS_WITH_WEB = "https://www.gumtree.com.au/web/s-user"
    EXPECTED_GUMTREE_URL_STARTS_WITH = "https://www.gumtree.com.au/"

    def __init__(self, url):
        self.url = url

    def get_import_source_from_url(self):
        """Get import source from URL"""
        if not self.url:
            return ImportFromSourceOption.UNKNOWN

        trimmed_url = self.url.strip()
        if trimmed_url.startswith(self.EXPECTED_FACEBOOK_URL_STARTS_WITH):
            return ImportFromSourceOption.FACEBOOK
        if trimmed_url.startswith(self.EXPECTED_FACEBOOK_URL_STARTS_WITH_WEB):
            return ImportFromSourceOption.FACEBOOK
        if trimmed_url.startswith(self.EXPECTED_FACEBOOK_PROFILE_URL_STARTS_WITH):
            return ImportFromSourceOption.FACEBOOK_PROFILE
        if trimmed_url.startswith(self.EXPECTED_FACEBOOK_PROFILE_URL_STARTS_WITH_WEB):
            return ImportFromSourceOption.FACEBOOK_PROFILE
        if trimmed_url.startswith(self.EXPECTED_GUMTREE_PROFILE_URL_STARTS_WITH_WEB):
            return ImportFromSourceOption.GUMTREE_PROFILE

        if trimmed_url.startswith(self.EXPECTED_GUMTREE_URL_STARTS_WITH):
            return ImportFromSourceOption.GUMTREE
        

        return ImportFromSourceOption.UNKNOWN

    def validate(self):
        """Validate URL"""
        if not self.url:
            return False, "URL is required"

        source = self.get_import_source_from_url()
        if source == ImportFromSourceOption.UNKNOWN:
            return False, "Invalid URL"

        if source == ImportFromSourceOption.CARSALES:
            return False, "Only Facebook and Gumtree URLs are supported"

        if self._has_multiple_urls():
            return False, "Only 1 URL is supported at a time"

        return True, None

    def _has_multiple_urls(self):
        """Check if URL has multiple URLs"""
        return self.url.lower().count("http") > 1

    def print_url_type(self):
        """Print URL type"""
        source = self.get_import_source_from_url()
        if source == ImportFromSourceOption.FACEBOOK:
            return "Facebook"
        elif source == ImportFromSourceOption.FACEBOOK_PROFILE:
            return "Facebook Profile"
        elif source == ImportFromSourceOption.GUMTREE_PROFILE:
            return "Gumtree Profile"
        elif source == ImportFromSourceOption.GUMTREE:
            return "Gumtree"
        else:
            return "Unknown"