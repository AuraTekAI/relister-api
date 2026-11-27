from django.urls import path

from .account_events import facebook_account_event, failure_event
from .views import TeamAlertView, infrastructure_health

app_name = 'team_alerts'

urlpatterns = [
    path('', TeamAlertView.as_view(), name='create'),
    # External uptime cron → backend: synchronous DB/Redis check (works without Celery).
    path('health/', infrastructure_health, name='infra_health'),
    # Extension → backend: dealer Facebook account restricted / checkpoint / logged out.
    path('fb-account-events/', facebook_account_event, name='fb_account_event'),
    # Extension → backend: one failed delete (publish/form-fill come from /publish-failures/).
    path('failure-events/', failure_event, name='failure_event'),
]
