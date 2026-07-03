from django.contrib import admin

from .models import TeamAlertLog


@admin.register(TeamAlertLog)
class TeamAlertLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'alert_type', 'active_listings', 'inactive_listings', 'sent_at')
    search_fields = ('user__email', 'alert_type')
    list_filter = ('alert_type', 'sent_at')
    readonly_fields = ('user', 'alert_type', 'active_listings', 'inactive_listings', 'sent_at')
    date_hierarchy = 'sent_at'
    ordering = ('-sent_at',)

    def has_add_permission(self, request):
        return False
