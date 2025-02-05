from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.employee_dashboard, name='employee_dashboard'),

    path('submit-attendance/', views.submit_attendance_request, name='submit_attendance_request'),
    path('attendance-list/', views.attendance_list, name='attendance_list_view'),
    path('update-project-status/<int:project_id>/', views.update_project_status, name='update_project_status_emp'),
    path('projects/', views.projects, name='projects'),
    path('project/<int:project_id>/', views.project_details, name='project_details'),

    path('profile/', views.profile, name='profile_view'),
    path("update-profile/", views.update_profile, name="update_profile"),

    path('attendance/', views.render_attendance_page, name='attendance_dashboard'),
    path('attendance/log-in/', views.log_in, name='log_in'),
    path('attendance/log-off/<int:attendance_id>/', views.log_off, name='log_off'),
    path('update-status/<int:project_id>/', views.update_team_member_status, name='update_team_member_status'),
    
    # path('attendance/travel-in/', views.travel_in, name='travel_in'),
    # path('attendance/travel-out/', views.travel_out, name='travel_out'),

    path('apply-leave/', views.apply_leave, name='apply_leave'),
    path("my-leave/", views.my_leave_status, name="my_leave"),
    path("leave-records/", views.leave_records, name="leave_records"),
]