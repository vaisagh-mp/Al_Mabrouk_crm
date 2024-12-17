from django.urls import path
from .views import employee_dashboard

urlpatterns = [
    path('dashboard/', employee_dashboard, name='employee_dashboard'),
]