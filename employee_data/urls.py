from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('submit-attendance/', views.submit_attendance_request, name='submit_attendance_request'),
    path('update-project-status/<int:project_id>/', views.update_project_status, name='update_project_status'),
]