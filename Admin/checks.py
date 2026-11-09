from django.conf import settings
from django.core.checks import Error, register, Tags


@register(Tags.compatibility)
def check_timesheet_approval_settings(app_configs, **kwargs):
    errors = []
    if getattr(settings, 'HR_TIMESHEET_APPROVAL', False) and getattr(settings, 'HR_TIMESHEET_APPROVAL_FROM', None) is None:
        errors.append(
            Error(
                "Set HR_TIMESHEET_APPROVAL_FROM to the go-live date.",
                hint="When HR_TIMESHEET_APPROVAL is True, HR_TIMESHEET_APPROVAL_FROM must be configured with an effective date.",
                id="hr.E002",
            )
        )
    return errors

