from django.urls import path
from . import views

urlpatterns = [
    path('dashboard/', views.dashboard, name='manager-dashboard'),
    path('manage-attendance/', views.manage_attendance_requests, name='manage_attendance_requests'),
    path('project-assignments/create/', views.project_assignment_create, name='project-assign'),
]
