from django.contrib import admin
from .models import Project, Employee, Attendance

class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('user', 'rank', 'work_days', 'holidays', 'overseas_days', 'project', 'project_purchase_cost', 'project_invoice')
    search_fields = ('user', 'rank', 'project__name')
    ordering = ('user',)

class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'login_time', 'log_out_time', 'total_hours_of_work', 'status')
    list_filter = ('status', 'employee')
    search_fields = ('employee__name',)
    readonly_fields = ('total_hours_of_work',)

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'code')
    search_fields = ('name', 'code')
    ordering = ('name',)
    
admin.site.register(Employee, EmployeeAdmin)
admin.site.register(Attendance, AttendanceAdmin)
