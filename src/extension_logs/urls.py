from django.urls import path

from .views import ExtensionLogCreateView
from .admin_views import (
    get_extension_logs,
    get_dealer_meta,
    send_extension_command,
    backend_health,
)

app_name = 'extension_logs'

urlpatterns = [
    path('', ExtensionLogCreateView.as_view(), name='create'),
    # Admin-only diagnostics + remote control (consumed by the debugging MCP server)
    path('admin/', get_extension_logs, name='admin_logs'),
    path('dealer-meta/', get_dealer_meta, name='dealer_meta'),
    path('command/', send_extension_command, name='command'),
    path('health/', backend_health, name='health'),
]
