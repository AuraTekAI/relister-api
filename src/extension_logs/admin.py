from django.contrib import admin

from .models import ExtensionLog


@admin.register(ExtensionLog)
class ExtensionLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'log_preview', 'created_at', 'updated_at')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'log')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('user', 'log', 'created_at', 'updated_at')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    def log_preview(self, obj):
        if not obj.log:
            return ''
        return obj.log if len(obj.log) <= 100 else obj.log[:97] + '...'
    log_preview.short_description = 'Log'

    def has_add_permission(self, request):
        return False
