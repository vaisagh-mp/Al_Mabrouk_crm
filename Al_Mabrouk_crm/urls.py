from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include
from Admin.leave_views import calculate_leave_days_api

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/leave/calculate-days/', calculate_leave_days_api, name='api_calculate_leave_days'),
    path('', include('Authentication.urls')),  # Include the authentication app URLs
    path('admin-panel/', include('Admin.urls')),  
    path('employee/', include('employee_data.urls')),  
    path('manager/', include('manager.urls')),
    path('administration/', include('administration.urls')),
    path('hr/', include('hr.urls')),
    path('service-logs/', include('service_logs.urls')),
    path('finance/', include('finance.urls')),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
