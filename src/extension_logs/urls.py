from django.urls import path

from .views import ExtensionLogCreateView, ExtensionActivityView
from .admin_views import (
    get_extension_logs,
    get_recent_error_logs,
    get_dealer_meta,
    send_extension_command,
    backend_health,
    get_dealer_activity,
)

app_name = 'extension_logs'

urlpatterns = [
    path('', ExtensionLogCreateView.as_view(), name='create'),
    # Daily usage tracking (extension → own activity)
    path('activity/', ExtensionActivityView.as_view(), name='activity'),
    # Admin-only diagnostics + remote control (consumed by the debugging MCP server)
    path('admin/', get_extension_logs, name='admin_logs'),
    path('errors/', get_recent_error_logs, name='recent_error_logs'),
    path('dealer-meta/', get_dealer_meta, name='dealer_meta'),
    path('command/', send_extension_command, name='command'),
    path('health/', backend_health, name='health'),
    path('admin/activity/', get_dealer_activity, name='admin_activity'),
]
