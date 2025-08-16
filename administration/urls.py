from django.urls import path
from .views import *

urlpatterns = [
    path('dashboard/', dashboard, name='admstrn-dashboard'),

    path('search/', admstrn_search_redirect_view, name='admstrn_search_redirect_view'),

    path("get-presence-data/", adm_get_presence_data, name="adm_get_presence_data"),

    path('add-project/', admstrn_add_project, name='admstrn_add_project'),
    path('projects/', admstrn_project_list_view, name='admstrn_project_list_view'),
    path('project/<int:project_id>/', admstrn_project_summary_view, name='admstrn_project_summary_view'),
    path('project/edit/<int:project_id>/', admstrn_edit_project, name='admstrn_edit_project'),
    path('project/delete/<int:project_id>/', admstrn_delete_project, name='admstrn_delete_project'),
    path('project/<int:project_id>/attachments/', admstrn_project_attachments_view, name='admstrn_project_attachments_view'),
    path('attachment/<int:attachment_id>/delete/', admstrn_delete_project_attachment, name='admstrn_delete_project_attachment'),

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

    path('profile/', admstrn_profile, name='admstrn_profile_view'),
    path("update-profile/", admstrn_update_profile, name="admstrn_update_profile"),
    
    ]