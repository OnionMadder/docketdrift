from django.apps import AppConfig


class OpinionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'opinions'

    def ready(self):
        # Register the system checks defined in opinions.checks. Importing
        # the module triggers the @register decorators at the bottom of
        # the file. Without this hook the checks would never load.
        from opinions import checks  # noqa: F401
