from django.urls import path
from .views import admstrn_add_project, admstrn_project_list_view, admstrn_edit_project, admstrn_delete_project,admstrn_project_summary_view, dashboard, attendance,admstrn_log_in, admstrn_log_off, admstrn_render_attendance_page, admstrn_update_travel_time, admstrn_apply_leave, admstrn_leave_status, admstrn_leave_records, admstrn_upload_medical_certificate, admstrn_fetch_notifications, admstrn_mark_notifications_as_read

urlpatterns = [
    path('dashboard/', dashboard, name='admstrn-dashboard'),
    path('add-project/', admstrn_add_project, name='admstrn_add_project'),
    path('projects/', admstrn_project_list_view, name='admstrn_project_list_view'),
    path('project/<int:project_id>/', admstrn_project_summary_view, name='admstrn_project_summary_view'),
    path('project/edit/<int:project_id>/', admstrn_edit_project, name='admstrn_edit_project'),
    path('project/delete/<int:project_id>/', admstrn_delete_project, name='admstrn_delete_project'),

    path('attendance-status/', attendance, name='admstrn_attendance_status'),
    path('attendance/log-in/', admstrn_log_in, name='admstrn_log_in'),
    path('attendance/log-off/<int:attendance_id>/', admstrn_log_off, name='admstrn_log_off'),
    path('attendance/', admstrn_render_attendance_page, name='admstrn_attendance_dashboard'),

    path('travel-time/edit/<int:attendance_id>/', admstrn_update_travel_time, name='admstrn_update_travel_time'),

    path('apply-leave/', admstrn_apply_leave, name='admstrn_apply_leave'),
    path("my-leave/", admstrn_leave_status, name="admstrn_leave_status"),
    path("leave-records/", admstrn_leave_records, name="admstrn_leave_records"),
    path('upload_medical_certificate/<int:leave_id>/', admstrn_upload_medical_certificate, name='admstrn_upload_medical_certificate'),

    path('manager-fetch-notifications/', admstrn_fetch_notifications, name='admstrn_fetch_notifications'),
    path('manager-mark-notifications-as-read', admstrn_mark_notifications_as_read, name='admstrn_mark_notifications_as_read'),
    
    ]