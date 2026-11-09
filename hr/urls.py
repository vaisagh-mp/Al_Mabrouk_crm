from django.urls import path
from . import views
from Admin import timesheet_views
from Admin import document_views
from .views import hr_create_employee,hr_employee_list,hr_attendance_list_view, hr_employee_leave_list,hr_employee_manage_leave, hr_attendance_detail, hr_edit_attendance, hr_delete_attendance, hr_employee_profile, hr_edit_employee, hr_delete_employee, hr_deactivate_employee, hr_reactivate_employee, hr_apply_leave, hr_upload_medical_certificate, hr_leave_status, hr_leave_records,hr_fetch_notifications, hr_mark_notifications_as_read, dashboard,attendance, hr_log_in, hr_log_off, hr_render_attendance_page, hr_update_travel_time, hr_profile, hr_update_profile, hr_employee_analytics, hr_get_presence_data, hr_set_day_status


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
    path('attendance/day-status/<int:employee_id>/<str:date>/', hr_set_day_status, name='hr_set_day_status'),
    path('attendance/employee/<int:employee_id>/<str:month>/', views.hr_employee_attendance_month_view, name='hr_employee_attendance_month'),

    path('attendance-status/', attendance, name='hr_attendance_status'),
    path('attendance/log-in/', hr_log_in, name='hr_log_in'),
    path('attendance/log-off/<int:attendance_id>/', hr_log_off, name='hr_log_off'),
    path('attendance/', hr_render_attendance_page, name='hr_attendance_dashboard'),

    path('travel-time/edit/<int:attendance_id>/', hr_update_travel_time, name='hr_update_travel_time'),

    path('employee/<int:employee_id>/', hr_employee_profile, name='hr_employee_profile'),
    path('employees/<int:employee_id>/', hr_employee_profile, name='hr_employee_profile_canonical'),
    path('employee/edit/<int:employee_id>/', hr_edit_employee, name='hr_edit_employee'),
    path('employee/delete/<int:employee_id>/', hr_delete_employee, name='hr_delete_employee'),
    path('employee/<int:employee_id>/deactivate/', hr_deactivate_employee, name='hr_deactivate_employee'),
    path('employee/<int:employee_id>/reactivate/', hr_reactivate_employee, name='hr_reactivate_employee'),

    path('apply-leave/', hr_apply_leave, name='hr_apply_leave'),
    path("my-leave/", hr_leave_status, name="hr_leave_status"),
    path("leave-records/", hr_leave_records, name="hr_leave_records"),
    path('upload_medical_certificate/<int:leave_id>/', hr_upload_medical_certificate, name='hr_upload_medical_certificate'),

    path('manager-fetch-notifications/', hr_fetch_notifications, name='hr_fetch_notifications'),
    path('manager-mark-notifications-as-read', hr_mark_notifications_as_read, name='hr_mark_notifications_as_read'),

    path('profile/', hr_profile, name='hr_profile_view'),
    path("update-profile/", hr_update_profile, name="hr_update_profile"),
    path('analytics/employees/', hr_employee_analytics, name='hr_employee_analytics'),
    path('get-presence-data/', hr_get_presence_data, name='hr_get_presence_data'),

    # Settings: Departments and Branches
    path('settings/departments/', views.hr_departments_list, name='hr_departments'),
    path('settings/departments/add/', views.hr_department_create, name='hr_department_create'),
    path('settings/departments/<int:pk>/edit/', views.hr_department_edit, name='hr_department_edit'),
    path('settings/departments/<int:pk>/toggle/', views.hr_department_toggle_active, name='hr_department_toggle_active'),
    path('settings/departments/<int:pk>/delete/', views.hr_department_delete, name='hr_department_delete'),

    path('settings/branches/', views.hr_branches_list, name='hr_branches'),
    path('settings/branches/add/', views.hr_branch_create, name='hr_branch_create'),
    path('settings/branches/<int:pk>/edit/', views.hr_branch_edit, name='hr_branch_edit'),
    path('settings/branches/<int:pk>/toggle/', views.hr_branch_toggle_active, name='hr_branch_toggle_active'),
    path('settings/branches/<int:pk>/delete/', views.hr_branch_delete, name='hr_branch_delete'),

    # Leave Management (V2)
    path('import-opening-balances/', views.hr_import_opening_balances, name='hr_import_opening_balances'),
    path('leave-year-reviews/', views.hr_leave_year_reviews, name='hr_leave_year_reviews'),
    path('leave-statement/', views.hr_leave_statement, name='hr_leave_statement'),

    # Shifts & Holidays
    path('shifts/schedules/', views.hr_duty_schedules_list, name='hr_duty_schedules'),
    path('shifts/schedules/add/', views.hr_duty_schedule_create, name='hr_duty_schedule_create'),
    path('shifts/schedules/<int:pk>/edit/', views.hr_duty_schedule_edit, name='hr_duty_schedule_edit'),
    path('shifts/schedules/<int:pk>/toggle/', views.hr_duty_schedule_toggle_active, name='hr_duty_schedule_toggle_active'),
    path('shifts/schedules/<int:pk>/delete/', views.hr_duty_schedule_delete, name='hr_duty_schedule_delete'),

    path('shifts/rest-days/', views.hr_weekly_rest_days_list, name='hr_weekly_rest_days'),
    path('shifts/rest-days/<int:employee_id>/history/', views.hr_weekly_rest_day_history, name='hr_weekly_rest_day_history'),

    path('shifts/roster/', views.hr_roster_view, name='hr_roster'),
    path('shifts/roster/toggle/', views.hr_roster_toggle, name='hr_roster_toggle'),
    path('shifts/roster/fill-pattern/', views.hr_roster_fill_pattern, name='hr_roster_fill_pattern'),

    path('shifts/holidays/', views.hr_public_holidays_list, name='hr_public_holidays'),
    path('shifts/holidays/add/', views.hr_holiday_create, name='hr_holiday_create'),
    path('shifts/holidays/<int:pk>/edit/', views.hr_holiday_edit, name='hr_holiday_edit'),
    path('shifts/holidays/<int:pk>/delete/', views.hr_holiday_delete, name='hr_holiday_delete'),

    path('timesheet-approvals/', timesheet_views.timesheet_approvals_view, {'role': 'hr'}, name='timesheet_approvals_hr'),
    path('ot-approval/', timesheet_views.ot_approval_view, {'role': 'hr'}, name='ot_approval_hr'),

    path('documents/', document_views.documents_list_view, name='hr_documents_list'),
    path('documents/download/<str:category>/<int:doc_id>/', document_views.download_private_document_view, name='hr_document_download'),
    path('documents/upload/<str:category>/', document_views.upload_document_view, name='hr_document_upload'),
    path('documents/upload/<str:category>/<int:employee_id>/', document_views.upload_document_view, name='hr_document_upload_employee'),
    path('documents/renew/<str:category>/<int:doc_id>/', document_views.renew_document_view, name='hr_document_renew'),
    path('documents/edit/<str:category>/<int:doc_id>/', document_views.edit_document_view, name='hr_document_edit'),
    path('documents/import/', document_views.document_import_view, name='hr_document_import'),
    path('settings/document-types/', document_views.document_types_view, name='hr_settings_document_types'),
]
