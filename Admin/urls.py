from django.urls import path
from .views import create_employee, edit_employee, delete_employee, employee_list, attendance_list_view, dashboard, project_summary_view, add_project, project_list_view, project_assignment_list, project_assignment_create, project_assignment_update, project_assignment_delete, manage_attendance, employee_profile
urlpatterns = [
    path('dashboard/', dashboard, name='admin-dashboard'),
    
    path('employees/', employee_list, name='employee_list'),
    path('employee/create/', create_employee, name='create-employee'),
    path('employee/attendance/', attendance_list_view, name='attendance_list_view'),
    path('employee/manage-attendance/', manage_attendance, name='manage_attendance'),
    path('employee/edit/<int:employee_id>/', edit_employee, name='edit-employee'),
    path('employee/delete/<int:employee_id>/', delete_employee, name='delete-employee'),
    path('employee/<int:employee_id>/', employee_profile, name='employee_profile'),

    path('projects/', project_list_view, name='project-list'),
    path('project/<int:project_id>/', project_summary_view, name='project-summary'),
    path('add-project/', add_project, name='add_project'),

    path('project-assignments/', project_assignment_list, name='project-assignment-list'),
    path('project-assignments/create/', project_assignment_create, name='project-assignment-create'),
    path('project-assignments/<int:pk>/edit/', project_assignment_update, name='project-assignment-update'),
    path('project-assignments/<int:pk>/delete/', project_assignment_delete, name='project-assignment-delete'),
]
