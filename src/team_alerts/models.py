from django.conf import settings
from django.db import models


class TeamAlertLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='team_alert_logs',
    )
    alert_type = models.CharField(max_length=100)
    active_listings = models.IntegerField()
    inactive_listings = models.IntegerField()
    old_listings_count = models.IntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f'{self.user.email} | {self.alert_type} | {self.sent_at:%Y-%m-%d}'
