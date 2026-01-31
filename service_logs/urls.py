from django.urls import path
from . import views

urlpatterns = [
    path('', views.service_log_list, name='service_log_list'),
    path('create/', views.service_log_create, name='service_log_create'),
    path("edit/<int:log_id>/", views.service_log_edit, name="service_log_edit"),
    path("delete/<int:pk>/", views.service_log_delete, name="service_log_delete"),

    # ADMIN ONLY
    path('utilization/', views.service_log_utilization, name='service_log_utilization'),
    # path("analytics/", views.analytics_dashboard, name="analytics_dashboard"),
    path("analytics/projects/", views.analytics_project, name="analytics_project"),
    path("analytics/project/<int:project_id>/", views.project_drilldown, name="project_drilldown"),
    path("analytics/employees/", views.analytics_employee, name="analytics_employee"),
]
