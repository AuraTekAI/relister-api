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


class PushSubscription(models.Model):
    """
    A browser Web Push subscription for a dealer's extension. One dealer may have
    several (multiple browsers / machines). `install_type` records whether the
    extension is a CRX ('normal') or a Load-unpacked dev copy ('development'),
    which the admin dashboard uses to flag dealers that can't auto-update.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='push_subscriptions',
    )
    endpoint = models.TextField(unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    install_type = models.CharField(max_length=20, null=True, blank=True)
    extension_version = models.CharField(max_length=32, null=True, blank=True)
    user_agent = models.CharField(max_length=300, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        who = self.user.email if self.user_id else 'anonymous'
        return f'push[{who}] {self.endpoint[:40]}…'
