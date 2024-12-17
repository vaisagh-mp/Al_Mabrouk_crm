from django.urls import path
from .views import create_employee, edit_employee, delete_employee, employee_list, attendance_list_view, dashboard

urlpatterns = [
    path('dashboard/', dashboard, name='dashboard'),
    path('employees/', employee_list, name='employee_list'),
    path('employee/create/', create_employee, name='create-employee'),
    path('employee/attendance/', attendance_list_view, name='attendance_list_view'),
    path('employee/edit/<int:employee_id>/', edit_employee, name='edit-employee'),
    path('employee/delete/<int:employee_id>/', delete_employee, name='delete-employee'),
]
