from django.urls import path
from . import views
from . import payroll_views
from Admin import reports_views

urlpatterns = [
    # Dashboard & Phase placeholders
    path('', views.fin_dashboard, name='fin_dashboard'),
    path('dashboard/', views.fin_dashboard, name='fin_dashboard_alt'),
    path('pnl/', views.fin_pnl, name='fin_pnl'),
    path('pnl/export/', views.fin_pnl_export_excel, name='fin_pnl_export_excel'),
    path('projects/', views.fin_projects, name='fin_projects'),
    path('projects/export/', views.fin_projects_export_excel, name='fin_projects_export_excel'),
    path('projects/<int:project_id>/', views.fin_project_detail, name='fin_project_detail'),

    # Phase B: Invoices (Invoice Costing)
    path('invoices/', views.fin_invoices, name='fin_invoices'),
    path('invoices/add/', views.fin_invoice_add, name='fin_invoice_add'),
    path('invoices/<int:invoice_id>/edit/', views.fin_invoice_edit, name='fin_invoice_edit'),
    path('invoices/<int:invoice_id>/record-payment/', views.fin_invoice_record_payment, name='fin_invoice_record_payment'),
    path('invoices/<int:invoice_id>/apply-credit/', views.fin_invoice_apply_credit, name='fin_invoice_apply_credit'),
    path('invoices/<int:invoice_id>/delete/', views.fin_invoice_delete, name='fin_invoice_delete'),
    path('invoices/export/', views.fin_invoices_export_excel, name='fin_invoices_export_excel'),

    # Phase B: Jobs P&L
    path('jobs-pnl/', views.fin_jobs_pnl, name='fin_jobs_pnl'),
    path('jobs-pnl/<int:invoice_id>/costs/', views.fin_job_costs, name='fin_job_costs'),
    path('jobs-pnl/cost/<int:cost_id>/delete/', views.fin_job_cost_delete, name='fin_job_cost_delete'),
    path('jobs-pnl/export/', views.fin_jobs_pnl_export_excel, name='fin_jobs_pnl_export_excel'),

    # Phase B: Spares Accounting
    path('spares/', views.fin_spares, name='fin_spares'),
    path('spares/add/', views.fin_spare_add, name='fin_spare_add'),
    path('spares/<int:spare_id>/edit/', views.fin_spare_edit, name='fin_spare_edit'),
    path('spares/<int:spare_id>/delete/', views.fin_spare_delete, name='fin_spare_delete'),
    path('spares/export/', views.fin_spares_export_excel, name='fin_spares_export_excel'),

    # Receivables
    path('receivables/', views.fin_receivables, name='fin_receivables'),
    path('receivables/export/', views.fin_receivables_export_excel, name='fin_receivables_export_excel'),
    path('receivables/<int:invoice_id>/record-payment/', views.fin_receivables_record_payment, name='fin_receivables_record_payment'),
    path('employee-cost/', views.fin_employee_cost, name='fin_employee_cost'),
    path('employee-cost/export/', views.fin_employee_cost_export_excel, name='fin_employee_cost_export_excel'),


    # Payroll
    path('payroll/', views.fin_payroll, name='fin_payroll'),
    path('payroll/list/', views.fin_payroll, name='payroll_list'),
    path('payroll/generate/', views.fin_payroll_generate, name='fin_payroll_generate'),
    path('payroll/<int:payroll_id>/edit/', views.fin_payroll_edit, name='fin_payroll_edit'),
    path('payroll/<int:payroll_id>/add-line/', payroll_views.fin_payroll_add_line, name='payroll_add_line'),
    path('payroll/lines/<int:line_id>/delete/', payroll_views.fin_payroll_delete_line, name='payroll_delete_line'),
    path('payroll/<int:payroll_id>/revert/', views.fin_payroll_revert, name='fin_payroll_revert'),
    path('payroll/<int:payroll_id>/delete/', views.fin_payroll_delete, name='fin_payroll_delete'),
    path('payroll/<int:payroll_id>/approve/', views.fin_payroll_approve, name='payroll_approve'),
    path('payroll/approve-all/', views.fin_payroll_approve_all, name='fin_payroll_approve_all'),
    path('payroll/mark-paid/', views.fin_payroll_mark_paid, name='fin_payroll_mark_paid'),
    path('payroll/export/', views.fin_payroll_export_excel, name='fin_payroll_export_excel'),

    # Phase 7: Payroll & Salary (Structures, Increments, Advances, Payslips)
    path('salary-structures/', payroll_views.salary_structures_list, name='salary_structures_list'),
    path('increments/', payroll_views.salary_increments_list, name='salary_increments_list'),
    path('increments/propose/', payroll_views.salary_increment_propose, name='salary_increment_propose'),
    path('increments/<int:pk>/<str:action>/', payroll_views.salary_increment_action, name='salary_increment_action'),
    path('advances/', payroll_views.salary_advances_list, name='salary_advances_list'),
    path('advances/request/', payroll_views.salary_advance_request, name='salary_advance_request'),
    path('advances/<int:pk>/', payroll_views.salary_advance_detail, name='salary_advance_detail'),
    path('advances/<int:pk>/<str:action>/', payroll_views.salary_advance_action, name='salary_advance_action'),
    path('advances/instalment/<int:pk>/skip/', payroll_views.salary_advance_skip_instalment, name='salary_advance_skip_instalment'),
    path('payslips/', payroll_views.payslips_list, name='payslips_list'),
    path('payslips/generate-month/', payroll_views.payslip_generate_month, name='payslip_generate_month'),
    path('payslips/<int:pk>/regenerate/', payroll_views.payslip_regenerate, name='payslip_regenerate'),
    path('payslips/<int:pk>/download/', payroll_views.payslip_download, name='payslip_download'),
    path('payslips/download-zip/', payroll_views.payslips_download_zip, name='payslips_download_zip'),
    path('my-payslips/', payroll_views.my_payslips, name='my_payslips'),

    # Expenses
    path('expenses/', views.fin_expenses, name='fin_expenses'),
    path('expenses/add/', views.fin_expense_add, name='fin_expense_add'),
    path('expenses/<int:expense_id>/edit/', views.fin_expense_edit, name='fin_expense_edit'),
    path('expenses/<int:expense_id>/delete/', views.fin_expense_delete, name='fin_expense_delete'),
    path('expenses/export/', views.fin_expense_export_excel, name='fin_expense_export_excel'),

    # Project invoice edit
    path('projects/<int:project_id>/invoice/edit/', views.fin_project_invoice_edit, name='fin_project_invoice_edit'),

    # Settings / Categories
    path('import/', views.fin_import, name='fin_import'),
    path('settings/categories/', views.fin_categories, name='fin_categories'),
    path('settings/categories/add/', views.fin_category_add, name='fin_category_add'),
    path('settings/categories/<int:category_id>/edit/', views.fin_category_edit, name='fin_category_edit'),
    path('settings/categories/<int:category_id>/toggle/', views.fin_category_toggle, name='fin_category_toggle'),
    path('settings/categories/<int:category_id>/delete/', views.fin_category_delete, name='fin_category_delete'),

    # Settings / Exchange Rates
    path('settings/exchange-rates/', views.fin_exchange_rates, name='fin_exchange_rates'),
    path('settings/exchange-rates/add/', views.fin_exchange_rate_add, name='fin_exchange_rate_add'),
    path('settings/exchange-rates/<int:rate_id>/edit/', views.fin_exchange_rate_edit, name='fin_exchange_rate_edit'),
    path('settings/exchange-rates/<int:rate_id>/toggle/', views.fin_exchange_rate_toggle, name='fin_exchange_rate_toggle'),
    path('settings/exchange-rates/<int:rate_id>/delete/', views.fin_exchange_rate_delete, name='fin_exchange_rate_delete'),

    # API: Exchange Rate pre-fill
    path('api/exchange-rate/', views.fin_api_exchange_rate, name='fin_api_exchange_rate'),

    # Attendance & Leave for Accountant
    path('attendance/', views.fin_render_attendance_page, name='fin_attendance_dashboard'),
    path('attendance/log-in/', views.fin_log_in, name='fin_log_in'),
    path('attendance/log-off/<int:attendance_id>/', views.fin_log_off, name='fin_log_off'),
    path('attendance-status/', views.fin_attendance_status, name='fin_attendance_status'),
    path('travel-time/edit/<int:attendance_id>/', views.fin_update_travel_time, name='fin_update_travel_time'),
    path('apply-leave/', views.fin_apply_leave, name='fin_apply_leave'),
    path('my-leave/', views.fin_leave_status, name='fin_leave_status'),
    path('leave-records/', views.fin_leave_records, name='fin_leave_records'),
    path('upload-medical-certificate/<int:leave_id>/', views.fin_upload_medical_certificate, name='fin_upload_medical_certificate'),

    # Notifications & Profile
    path('notifications/fetch/', views.fin_fetch_notifications, name='fin_fetch_notifications'),
    path('notifications/mark-as-read/', views.fin_mark_notifications_as_read, name='fin_mark_notifications_as_read'),
    path('profile/', views.fin_profile_view, name='fin_profile_view'),
    path('update-profile/', views.fin_update_profile, name='fin_update_profile'),
    path('reports/', reports_views.reports_center_view, {'role': 'accountant'}, name='fin_reports_center'),
]
