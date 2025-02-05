from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='manager-dashboard'),
    path('manage-attendance/', views.manage_attendance_requests, name='manage_attendance_requests'),
    path('project-assignments/create/', views.project_assignment_create, name='project-assign'),
    path('update-project-status/<int:project_id>/', views.update_project_status, name='update_project_status'),

    path('teams/', views.team_list, name='team-list'),
    path('team/<int:pk>/', views.team_detail, name='team-detail'),
    path('team/create/', views.team_create, name='team-create'),
    path('team/<int:pk>/edit/', views.team_update, name='team-update'),
    path('team/delete/<int:team_id>/', views.team_delete, name='team-delete'),

    path('employees/', views.manager_employee_list, name='manager_employee_list'),
    path('employee/attendance/', views.attendance_list, name='attendance_list'),
    path('employee/attendance/<int:pk>/', views.attendance_detail, name='attendance_detail_view'),
    path('attendance/edit/<int:attendance_id>/', views.manager_edit_attendance, name='manager_edit_attendance'),
    path('attendance/delete/<int:attendance_id>/', views.manager_delete_attendance, name='manager_delete_attendance'),
    path('employee/manage-attendance/', views.manage_attendance, name='pending_attendance'),
    path('employee/employee-profile/<int:employee_id>/', views.employee_profile, name='employee_profile_view'),
    path('projects/', views.project_list_view, name='project_list'),
    path('project/<int:project_id>/', views.project_summary_view, name='project-summary-view'),
    path('update-status/<int:project_id>/', views.update_team_manager_status, name='update_team_manager_status'),
]
