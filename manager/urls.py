from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='manager-dashboard'),
    path('manage-attendance/', views.manage_attendance_requests, name='manage_attendance_requests'),
    path('manage-leave/', views.manage_leave, name='manage_leave'),
    path('leave-list/', views.leave_list, name='leave_list'),
    path('project-assignments/create/', views.project_assignment_create, name='project-assign'),
    path('update-project-status/<int:project_id>/', views.update_project_status, name='update_project_status'),

    path('manager/profile/', views.manager_profile, name='manager_profile'),
    path("manager/profile/update/", views.update_manager_profile, name="update_manager_profile"),

    path('teams/', views.team_list, name='team-list'),
    path('team/<int:pk>/', views.team_detail, name='team-detail'),
    path('team/create/', views.team_create, name='team-create'),
    path('team/<int:pk>/edit/', views.team_update, name='team-update'),
    path('team/delete/<int:team_id>/', views.team_delete, name='team-delete'),

    path('employees/', views.manager_employee_list, name='manager_employee_list'),
    path('employee/attendance/', views.attendance_list, name='attendance_list'),
    path('employee/attendance/<int:pk>/', views.attendance_detail, name='attendance_detail_view'),
    path('attendance/edit/<int:attendance_id>/', views.manager_edit_attendance, name='manager_edit_attendance'),
    path('travel-time/edit/<int:attendance_id>/', views.manager_update_travel_time, name='manager_update_travel_time'),
    path('attendance-status/', views.attendance, name='attendance_status'),
    path('attendance/delete/<int:attendance_id>/', views.manager_delete_attendance, name='manager_delete_attendance'),
    path('employee/manage-attendance/', views.manage_attendance, name='pending_attendance'),
    path('employee/employee-profile/<int:employee_id>/', views.employee_profile, name='employee_profile_view'),
    path('projects/', views.project_list_view, name='project_list'),
    path('project/<int:project_id>/', views.project_summary_view, name='project-summary-view'),
    path('update-status/<int:project_id>/', views.update_team_manager_status, name='update_team_manager_status'),
    path('manage-project-status/', views.manage_project_status, name='manage_project_status'),
    path('project/<int:project_id>/attachments/', views.project_attachments_view, name='project_attachments_view'),
    path('attachment/<int:attachment_id>/delete/', views.delete_project_attachment, name='delete-project-attachment'),


    path('attendance/', views.manager_render_attendance_page, name='manager_attendance_dashboard'),

    path('apply-leave/', views.manager_apply_leave, name='manager_apply_leave'),
    path("my-leave/", views.manager_leave_status, name="manager_leave_status"),
    path('attendance/log-in/', views.manager_log_in, name='manager_log_in'),
    path('attendance/log-off/<int:attendance_id>/', views.manager_log_off, name='manager_log_off'),
    path("leave-records/", views.manager_leave_records, name="manager_leave_records"),
    path('upload_medical_certificate/<int:leave_id>/', views.manager_upload_medical_certificate, name='manager_upload_medical_certificate'),

    
    path('manager-fetch-notifications/', views.manager_fetch_notifications, name='manager-fetch-notifications'),
    path('manager-mark-notifications-as-read', views.manager_mark_notifications_as_read, name='manager-mark-notifications-as-read'),
]
