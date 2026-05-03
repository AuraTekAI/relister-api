from django.urls import path

from .views import ExtensionLogCreateView

app_name = 'extension_logs'

urlpatterns = [
    path('', ExtensionLogCreateView.as_view(), name='create'),
]
