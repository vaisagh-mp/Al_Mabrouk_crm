from django.contrib import admin
from .models import Project, Employee, ProjectAssignment, Attendance


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'purchase_and_expenses', 'invoice_amount', 'currency_code', 'status')
    search_fields = ('name', 'code', 'status')
    list_filter = ('status',)
    readonly_fields = ('calculate_expenses', 'calculate_profit')

    def calculate_expenses(self, obj):
        """
        Display calculated total expenses in the admin interface.
        """
        return obj.calculate_expenses()
    calculate_expenses.short_description = "Total Expenses"

    def calculate_profit(self, obj):
        """
        Display calculated profit in the admin interface.
        """
        return obj.calculate_profit()
    calculate_profit.short_description = "Profit"


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('user', 'rank', 'salary', 'work_days', 'holidays', 'overseas_days')
    search_fields = ('user__username', 'rank')
    list_filter = ('rank',)
    readonly_fields = ('work_days',)


class ProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = ('project', 'employee_names', 'time_start', 'time_stop')
    search_fields = ('project__name', 'employee__user__username')
    list_filter = ('project__name', 'employee__rank')

    def employee_names(self, obj):
        # Returns a comma-separated list of employee usernames
        return ", ".join([employee.user.username for employee in obj.employee.all()])
    employee_names.short_description = 'Employees'
admin.site.register(ProjectAssignment, ProjectAssignmentAdmin)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'login_time', 'log_out_time', 'total_hours_of_work', 'status')
    search_fields = ('employee__user__username', 'status')
    list_filter = ('status',)
    readonly_fields = ('total_hours_of_work',)
