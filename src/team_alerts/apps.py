from django.apps import AppConfig


class TeamAlertsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'team_alerts'

    def ready(self):
        # Wrap ZenRowsClient.get so a 402 (out of credits) from ANY scraper
        # alerts KAZ_AND_TEAM immediately. Responses are returned untouched.
        from .zenrows_monitor import install_zenrows_402_hook
        install_zenrows_402_hook()
