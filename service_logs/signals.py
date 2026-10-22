from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from Admin.models import Attendance
from service_logs.models import EmployeeServiceLog
from service_logs.ot import recalculate_day_ot


@receiver(post_delete, sender=EmployeeServiceLog)
def on_service_log_deleted(sender, instance, **kwargs):
    if instance.employee_id and instance.date:
        recalculate_day_ot(instance.employee_id, instance.date)


@receiver(pre_save, sender=Attendance)
def attendance_pre_save(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = Attendance.objects.only('login_time', 'employee_id').get(pk=instance.pk)
            instance._old_login_time = old.login_time
            instance._old_employee_id = old.employee_id
        except Attendance.DoesNotExist:
            instance._old_login_time = None
            instance._old_employee_id = None
    else:
        instance._old_login_time = None
        instance._old_employee_id = None


@receiver(post_save, sender=Attendance)
def attendance_post_save(sender, instance, **kwargs):
    tz = timezone.get_current_timezone()

    curr_date = None
    if instance.login_time and instance.employee_id:
        curr_date = timezone.localtime(instance.login_time, tz).date()
        if EmployeeServiceLog.objects.filter(employee_id=instance.employee_id, date=curr_date).exists():
            recalculate_day_ot(instance.employee_id, curr_date)

    old_time = getattr(instance, '_old_login_time', None)
    old_emp_id = getattr(instance, '_old_employee_id', instance.employee_id)
    if old_time and old_emp_id:
        old_date = timezone.localtime(old_time, tz).date()
        if old_date != curr_date or old_emp_id != instance.employee_id:
            if EmployeeServiceLog.objects.filter(employee_id=old_emp_id, date=old_date).exists():
                recalculate_day_ot(old_emp_id, old_date)


@receiver(post_delete, sender=Attendance)
def attendance_post_delete(sender, instance, **kwargs):
    if instance.login_time and instance.employee_id:
        tz = timezone.get_current_timezone()
        local_date = timezone.localtime(instance.login_time, tz).date()
        if EmployeeServiceLog.objects.filter(employee_id=instance.employee_id, date=local_date).exists():
            recalculate_day_ot(instance.employee_id, local_date)
