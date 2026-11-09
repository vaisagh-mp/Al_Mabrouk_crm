from django.urls import path
from django.views.generic import TemplateView
from django.contrib.auth import views as auth_views
from .views import *
from . import timesheet_views
from . import document_views
urlpatterns = [
    path('dashboard/', dashboard, name='admin-dashboard'),
    path('search/', search_redirect_view, name='search_redirect'),
    
    path('employees/', employee_list, name='employee_list'),
    path('employee_list/', employee_list, name='employee_list_legacy'),
    path('managers/', manager_list, name='manager_list'),
    path('employee/create/', create_employee, name='create-employee'),
    # path('employee/edit/<int:employee_id>/', edit_employee, name='edit-employee'),
    # path('employee/delete/<int:employee_id>/', delete_employee, name='delete-employee'),
    # path('user/edit/<int:user_id>/', admin_edit_user, name='admin_edit_user'),

    path('team/create/', admin_team_create, name='admin-team-create'),
    path('teams/', team_list, name='admin-team-list'),
    path('team/update/<int:pk>/', admin_team_update, name='admin-team-update'),
    path('teams/<int:team_id>/delete/', admin_team_delete, name='admin-team-delete'),

    path('employee/<int:employee_id>/', employee_profile, name='employee_profile'),
    path('employee_profile/<int:employee_id>/', employee_profile, name='admin_employee_profile'),
    path('manager/<int:manager_id>/', manager_profile, name='manager_profile'),
    path('employee/edit/<int:employee_id>/', admin_edit_employee, name='admin_edit_employee'),
    path('manager/edit/<int:manager_id>/', admin_edit_manager, name='admin_edit_manager'),
    path('employee/delete/<int:employee_id>/', admin_delete_employee, name='admin_delete_employee'),
    path('manager/delete/<int:manager_id>/', admin_delete_manager, name='admin_delete_manager'),
    path('employee/<int:employee_id>/deactivate/', admin_deactivate_employee, name='admin_deactivate_employee'),
    path('employee/<int:employee_id>/reactivate/', admin_reactivate_employee, name='admin_reactivate_employee'),
    path('manager/<int:manager_id>/deactivate/', admin_deactivate_manager, name='admin_deactivate_manager'),
    path('manager/<int:manager_id>/reactivate/', admin_reactivate_manager, name='admin_reactivate_manager'),

    path('employee/attendance/', attendance_list_view, name='attendance_list_adminview'),
    path('manager/attendance/', manager_attendance_list_view, name='manager_attendance_list_view'),
    path('attendance/<int:pk>/', attendance_detail, name='attendance_detail'),
    path('employee/manage-attendance/', manage_attendance, name='manage_attendance'),
    path('manager/manage-attendance/', manage_manager_attendance, name='manage_manager_attendance'),
    path('attendance/edit/<int:attendance_id>/', edit_attendance, name='edit_attendance'),
    path('attendance/delete/<int:attendance_id>/', delete_attendance, name='delete_attendance'),
    path('attendance/day-status/<int:employee_id>/<str:date>/', admin_set_day_status, name='admin_set_day_status'),
    path('attendance/employee/<int:employee_id>/<str:month>/', employee_attendance_month_view, name='employee_attendance_month'),

    path('projects/', project_list_view, name='project-list'),
    path('project/<int:project_id>/', admin_project_summary_view, name='project-summary'),
    path('add-project/', add_project, name='add_project'),
    path('project/edit/<int:project_id>/', edit_project, name='edit_project'),
    path('project/delete/<int:project_id>/', delete_project, name='delete_project'),
    path('manage-project-status/', admin_manage_project_status, name='admin_manage_project_status'),
    path('project/<int:project_id>/attachments/', admin_project_attachments_view, name='admin_project_attachments_view'),
    path('attachment/<int:attachment_id>/delete/', admin_delete_project_attachment, name='admin_delete_project_attachment'),
    
    path('update-status/<int:project_id>/', update_team_admin_status, name='update_team_admin_status'),

    path('work-order/create/<int:project_id>/', create_work_order_view_admin, name='admin-create-work-order'),
    path('work-order/view/<int:pk>/', admin_view_work_order, name='admin_view_work_order'),
    path('work-order/update/<int:pk>/', admin_update_work_order_view, name='admin-update-work-order'),



    path('project-assignments/', project_assignment_list, name='project-assignment-list'),
    path('project-assignments/create/', project_assignment_create, name='project-assignment-create'),
    path('project-assignments/<int:pk>/edit/', project_assignment_update, name='project-assignment-update'),
    path('project-assignments/<int:pk>/delete/', project_assignment_delete, name='project-assignment-delete'),

    path('employee-leave-list/', employee_leave_list, name='employee_leave_list'),
    path('manager-leave-list/', manager_leave_list, name='manager_leave_list'),
    path('employee-manage-leave/', employee_manage_leave, name='employee_manage_leave'),
    path('manager-manage-leave/', manager_manage_leave, name='manager_manage_leave'),
    path('import-opening-balances/', admin_import_opening_balances, name='admin_import_opening_balances'),
    path('leave-year-reviews/', admin_leave_year_reviews, name='admin_leave_year_reviews'),
    path('leave-statement/', admin_leave_statement, name='admin_leave_statement'),
    path('leave_statement/', admin_leave_statement, name='leave_statement'),

    path('vessel-list', vessel_list, name='vessel_list'),
    path('vessel-create/', vessel_create, name='vessel_create'),
    path('vessel-update/<int:pk>/',vessel_update, name='vessel_update'),
    path('delete/<int:pk>/', vessel_delete, name='vessel_delete'),

    path('fetch-notifications/', fetch_notifications, name='fetch-notifications'),
    path('mark-notifications-as-read/', mark_notifications_as_read, name='mark-notifications-as-read'),

    path("admin-notifications/", admin_notifications, name="admin-notifications"),
    path("admin-mark-all-notifications/", admin_mark_all_notifications, name="admin-mark-all-notifications"),
    path("admin-mark-single-notification/<int:notification_id>/", admin_mark_single_notification, name="admin-mark-single-notification"),


    path('change-password/', change_password, name='change_password'),
    path('change-password/done/', TemplateView.as_view(template_name='password_change_done.html'),  name='password_change_done'),

    path('timesheet-approvals/', timesheet_views.timesheet_approvals_view, {'role': 'admin'}, name='timesheet_approvals_admin'),
    path('ot-approval/', timesheet_views.ot_approval_view, {'role': 'admin'}, name='ot_approval_admin'),
    path('timesheets/monthly-pdf/<int:employee_id>/<str:month_str>/', timesheet_views.employee_monthly_timesheet_pdf, name='employee_monthly_timesheet_pdf'),

    path('documents/', document_views.documents_list_view, name='admin_documents_list'),
    path('documents/download/<str:category>/<int:doc_id>/', document_views.download_private_document_view, name='document_download'),
    path('documents/upload/<str:category>/', document_views.upload_document_view, name='document_upload'),
    path('documents/upload/<str:category>/<int:employee_id>/', document_views.upload_document_view, name='document_upload_employee'),
    path('documents/renew/<str:category>/<int:doc_id>/', document_views.renew_document_view, name='document_renew'),
    path('documents/edit/<str:category>/<int:doc_id>/', document_views.edit_document_view, name='document_edit'),
    path('documents/import/', document_views.document_import_view, name='document_import'),
    path('documents/types/', document_views.document_types_view, name='hr_document_types'),
]
