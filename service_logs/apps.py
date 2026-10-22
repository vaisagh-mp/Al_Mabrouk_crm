from django.apps import AppConfig


class ServiceLogsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'service_logs'

    def ready(self):
        import service_logs.signals  # noqa

