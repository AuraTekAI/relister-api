"""
ASGI config for relister project.

Exposes the ASGI callable as ``application``. HTTP is served by Django's normal
handler; websockets are routed through Channels (JWT-authenticated) for the
real-time extension control channel.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "relister.settings")

# Initialise Django (populates the app registry) BEFORE importing anything that
# touches models/consumers.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from extension_logs.routing import websocket_urlpatterns  # noqa: E402
from extension_logs.ws_auth import JWTAuthMiddleware  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # Auth is the JWT `?token=` on the URL; origin validation is intentionally not
    # enforced so the extension (chrome-extension:// origin) and the cross-origin
    # webapp can both connect.
    "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
})
