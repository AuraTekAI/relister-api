import re
from django.db import models


FILENAME_RE = re.compile(r'^(?P<base>.+)_v(?P<version>\d+)\.zip$')


class ZipFile(models.Model):
    filename = models.CharField(max_length=255)
    base_name = models.CharField(max_length=255, unique=True)
    version = models.PositiveIntegerField()
    s3_key = models.CharField(max_length=512)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.filename

    @classmethod
    def parse_filename(cls, filename):
        """Return (base_name, version) or raise ValueError."""
        match = FILENAME_RE.match(filename)
        if not match:
            raise ValueError(
                "Invalid filename format. Use: <name>_v<number>.zip "
                "(example: assets_v1.zip)"
            )
        return match.group('base'), int(match.group('version'))
