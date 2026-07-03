from django.urls import path

from .views import TeamAlertView

app_name = 'team_alerts'

urlpatterns = [
    path('', TeamAlertView.as_view(), name='create'),
]
