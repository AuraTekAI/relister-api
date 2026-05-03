from django.conf import settings
from django.db import models


class ExtensionLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='extension_logs',
    )
    log = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.user.email if self.user_id else 'anonymous'
        return f'{who} @ {self.created_at:%Y-%m-%d %H:%M:%S}'
