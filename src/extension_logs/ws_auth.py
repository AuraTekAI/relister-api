"""
JWT auth middleware for Channels websockets.

The webapp and the extension both authenticate with the same SimpleJWT access
token used for the REST API, passed as a `?token=` query-string parameter on the
websocket URL (browsers can't set Authorization headers on a WebSocket). The
token is the security boundary for the control channel, so origin validation is
intentionally not enforced here (the extension connects from a
`chrome-extension://` origin and the webapp from a different host than the API).
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _get_user(token: str):
    from django.contrib.auth import get_user_model
    from rest_framework_simplejwt.tokens import AccessToken

    User = get_user_model()
    try:
        access = AccessToken(token)
        user_id = access.get("user_id")
        return User.objects.get(id=user_id)
    except Exception:
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs((scope.get("query_string") or b"").decode())
        token = (query.get("token") or [None])[0]
        scope["user"] = await _get_user(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
