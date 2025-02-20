from django.apps import AppConfig


class AdminConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Admin'

    def ready(self):
        import Admin.signals  # Import signals when the app is ready

        