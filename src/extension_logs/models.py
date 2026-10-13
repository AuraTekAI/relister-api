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


class DailyActiveUser(models.Model):
    """
    One row per user per day (Australia/Perth date): was the extension used that
    day, when it was first/last seen, and how many actions it performed.
    Written by extension_logs.activity.record_activity — never edit counts by hand.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='daily_activity',
    )
    date = models.DateField()
    active = models.BooleanField(default=False)
    first_seen = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    action_count = models.PositiveIntegerField(default=0)  # total of the per-action counts below
    listed_count = models.PositiveIntegerField(default=0)
    relisted_count = models.PositiveIntegerField(default=0)
    deleted_count = models.PositiveIntegerField(default=0)
    other_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'daily_active_user'
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='unique_daily_active_user_per_day'),
        ]

    def __str__(self):
        return f'{self.user_id} @ {self.date} active={self.active}'
