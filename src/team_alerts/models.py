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


class FailureEvent(models.Model):
    """One row per failed publish / delete / form-fill attempt — the timeline
    the failure-spike monitor (team_alerts/failure_monitor.py) counts.

    PublishFailure keeps only the LATEST failure per (dealer, listing) and is
    deleted on success, so it cannot answer "how many failures in the last
    hour vs. normal?". Publish and form-fill rows are written server-side by
    the /publish-failures/ endpoint; delete rows are reported by the extension
    via /api/team-alerts/failure-events/.
    """
    KIND_CHOICES = [
        ('publish', 'Publish failed'),
        ('delete', 'Delete failed'),
        ('form_fill', 'Facebook form fill failed'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='failure_events')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    listing_id = models.BigIntegerField(null=True, blank=True)
    reason = models.TextField(blank=True, null=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['kind', 'occurred_at'], name='ta_failevent_kind_at_idx')]
        ordering = ['-occurred_at']

    def __str__(self):
        return f'{self.user_id} {self.kind} @ {self.occurred_at:%Y-%m-%d %H:%M}'


class FacebookAccountEvent(models.Model):
    """A dealer's Facebook account changed state, as reported by the extension
    (dealer = user, issue_type, exact detected_at from the browser).

    `detected` opens an issue; `resolved` closes it. Kaz is emailed ONCE per
    open issue (alerted=True on the event that triggered the email); further
    `detected` reports for the same open issue are stored but not emailed.
    """
    ISSUE_CHOICES = [
        ('restricted', 'Account restricted / locked'),
        ('checkpoint', 'Security checkpoint / verification wall'),
        ('logged_out', 'Logged out of Facebook'),
    ]
    STATUS_CHOICES = [('detected', 'Detected'), ('resolved', 'Resolved')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='fb_account_events')
    issue_type = models.CharField(max_length=20, choices=ISSUE_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='detected')
    detail = models.CharField(max_length=500, blank=True, null=True)
    fb_user_id = models.CharField(max_length=40, blank=True, null=True)
    extension_version = models.CharField(max_length=32, blank=True, null=True)
    # Exact time the extension observed it (browser clock); created_at is server time.
    detected_at = models.DateTimeField()
    alerted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'issue_type', 'detected_at'], name='ta_fbacct_user_type_at_idx')]
        ordering = ['-detected_at']

    def __str__(self):
        return f'{self.user_id} {self.issue_type} {self.status} @ {self.detected_at:%Y-%m-%d %H:%M}'
