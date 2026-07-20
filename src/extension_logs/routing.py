from django.urls import re_path

from .consumers import ExtensionControlConsumer

websocket_urlpatterns = [
    re_path(r"^ws/extension/$", ExtensionControlConsumer.as_asgi()),
]
