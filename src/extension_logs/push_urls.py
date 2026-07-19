from django.urls import path

from .push_views import vapid_public_key, subscribe, unsubscribe, send_push

app_name = 'push'

urlpatterns = [
    path('vapid-key/', vapid_public_key, name='vapid_key'),
    path('subscribe/', subscribe, name='subscribe'),
    path('unsubscribe/', unsubscribe, name='unsubscribe'),
    path('send/', send_push, name='send'),  # admin
]
