from django.urls import path
from .views import create_employee, employee_list, attendance_list_view, dashboard, admin_project_summary_view, add_project, project_list_view, project_assignment_list, project_assignment_create, project_assignment_update, project_assignment_delete, manage_attendance, employee_profile,attendance_detail, edit_project, delete_project, edit_attendance, delete_attendance, manager_list, manager_profile, manager_attendance_list_view, manage_manager_attendance, employee_leave_list, manager_leave_list, employee_manage_leave, manager_manage_leave, admin_edit_employee, admin_delete_employee, admin_edit_manager, admin_delete_manager


urlpatterns = [
    path('dashboard/', dashboard, name='admin-dashboard'),
    
    path('employees/', employee_list, name='employee_list'),
    path('managers/', manager_list, name='manager_list'),
    path('employee/create/', create_employee, name='create-employee'),
    # path('employee/edit/<int:employee_id>/', edit_employee, name='edit-employee'),
    # path('employee/delete/<int:employee_id>/', delete_employee, name='delete-employee'),
    path('employee/<int:employee_id>/', employee_profile, name='employee_profile'),
    path('manager/<int:manager_id>/', manager_profile, name='manager_profile'),
    path('employee/edit/<int:employee_id>/', admin_edit_employee, name='admin_edit_employee'),
    path('manager/edit/<int:manager_id>/', admin_edit_manager, name='admin_edit_manager'),
    path('employee/delete/<int:employee_id>/', admin_delete_employee, name='admin_delete_employee'),
    path('manager/delete/<int:manager_id>/', admin_delete_manager, name='admin_delete_manager'),

    path('employee/attendance/', attendance_list_view, name='attendance_list_adminview'),
    path('manager/attendance/', manager_attendance_list_view, name='manager_attendance_list_view'),
    path('attendance/<int:pk>/', attendance_detail, name='attendance_detail'),
    path('employee/manage-attendance/', manage_attendance, name='manage_attendance'),
    path('manager/manage-attendance/', manage_manager_attendance, name='manage_manager_attendance'),
    path('attendance/edit/<int:attendance_id>/', edit_attendance, name='edit_attendance'),
    path('attendance/delete/<int:attendance_id>/', delete_attendance, name='delete_attendance'),

    path('projects/', project_list_view, name='project-list'),
    path('project/<int:project_id>/', admin_project_summary_view, name='project-summary'),
    path('add-project/', add_project, name='add_project'),
    path('project/edit/<int:project_id>/', edit_project, name='edit_project'),
    path('project/delete/<int:project_id>/', delete_project, name='delete_project'),

    path('project-assignments/', project_assignment_list, name='project-assignment-list'),
    path('project-assignments/create/', project_assignment_create, name='project-assignment-create'),
    path('project-assignments/<int:pk>/edit/', project_assignment_update, name='project-assignment-update'),
    path('project-assignments/<int:pk>/delete/', project_assignment_delete, name='project-assignment-delete'),

    path('employee-leave-list/', employee_leave_list, name='employee_leave_list'),
    path('manager-leave-list/', manager_leave_list, name='manager_leave_list'),
    path('employee-manage-leave/', employee_manage_leave, name='employee_manage_leave'),
    path('manager-manage-leave/', manager_manage_leave, name='manager_manage_leave'),
]
