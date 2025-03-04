from django.urls import path
from .views import hr_create_employee,hr_employee_list,hr_attendance_list_view, hr_employee_leave_list,hr_employee_manage_leave, hr_attendance_detail, hr_edit_attendance, hr_delete_attendance, hr_employee_profile, hr_edit_employee, hr_delete_employee, hr_apply_leave, hr_upload_medical_certificate, hr_leave_status, hr_leave_records,hr_fetch_notifications, hr_mark_notifications_as_read, dashboard,attendance, hr_log_in, hr_log_off, hr_render_attendance_page, hr_update_travel_time, hr_profile, hr_update_profile

urlpatterns = [
    path('dashboard/', dashboard, name='hr-dashboard'),
    path('employee/create/', hr_create_employee, name='hr_create_employee'),
    path('employees/', hr_employee_list, name='hr_employee_list'),
    path('employee/attendance/', hr_attendance_list_view, name='hr_attendance_list_view'),
    path('employee-leave-list/', hr_employee_leave_list, name='hr_employee_leave_list'),
    path('employee-manage-leave/', hr_employee_manage_leave, name='hr_employee_manage_leave'),
    path('attendance/<int:pk>/', hr_attendance_detail, name='hr_attendance_detail'),
    path('attendance/edit/<int:attendance_id>/', hr_edit_attendance, name='hr_edit_attendance'),
    path('attendance/delete/<int:attendance_id>/', hr_delete_attendance, name='hr_delete_attendance'),

    path('attendance-status/', attendance, name='hr_attendance_status'),
    path('attendance/log-in/', hr_log_in, name='hr_log_in'),
    path('attendance/log-off/<int:attendance_id>/', hr_log_off, name='hr_log_off'),
    path('attendance/', hr_render_attendance_page, name='hr_attendance_dashboard'),

    path('travel-time/edit/<int:attendance_id>/', hr_update_travel_time, name='hr_update_travel_time'),

    path('employee/<int:employee_id>/', hr_employee_profile, name='hr_employee_profile'),
    path('employee/edit/<int:employee_id>/', hr_edit_employee, name='hr_edit_employee'),
    path('employee/delete/<int:employee_id>/', hr_delete_employee, name='hr_delete_employee'),

    path('apply-leave/', hr_apply_leave, name='hr_apply_leave'),
    path("my-leave/", hr_leave_status, name="hr_leave_status"),
    path("leave-records/", hr_leave_records, name="hr_leave_records"),
    path('upload_medical_certificate/<int:leave_id>/', hr_upload_medical_certificate, name='hr_upload_medical_certificate'),

    path('manager-fetch-notifications/', hr_fetch_notifications, name='hr_fetch_notifications'),
    path('manager-mark-notifications-as-read', hr_mark_notifications_as_read, name='hr_mark_notifications_as_read'),

    path('profile/', hr_profile, name='hr_profile_view'),
    path("update-profile/", hr_update_profile, name="hr_update_profile"),

    ]