
class ImportFromSourceOption:
    UNKNOWN = "Unknown"
    FACEBOOK = "Facebook"
    GUMTREE = "Gumtree"
    CARSALES = "CarSales"

class ImportFromUrl:
    EXPECTED_FACEBOOK_URL_STARTS_WITH = "https://www.facebook.com/marketplace/item/"
    EXPECTED_GUMTREE_URL_STARTS_WITH = "https://www.gumtree.com.au/"

    def __init__(self, url):
        self.url = url

    def get_import_source_from_url(self):
        if not self.url:
            return ImportFromSourceOption.UNKNOWN

        trimmed_url = self.url.strip()
        if trimmed_url.startswith(self.EXPECTED_FACEBOOK_URL_STARTS_WITH):
            return ImportFromSourceOption.FACEBOOK

        if trimmed_url.startswith(self.EXPECTED_GUMTREE_URL_STARTS_WITH):
            return ImportFromSourceOption.GUMTREE

        return ImportFromSourceOption.UNKNOWN

    def validate(self):
        if not self.url:
            return False, "URL is required"

        source = self.get_import_source_from_url()
        if source == ImportFromSourceOption.UNKNOWN:
            return False, "Invalid URL"

        if source == ImportFromSourceOption.CARSALES:
            return False, "Only Facebook and Gumtree URLs are supported"

        http_count = self.url.lower().count("http")
        if http_count > 1:
            return False, "Only 1 URL is supported at a time"

        return True, None


