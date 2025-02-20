from django.contrib import admin
from .models import Project, Employee, ProjectAssignment, Attendance,Team, TeamMemberStatus, Leave, LeaveBalance, Notification


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'manager', 'purchase_and_expenses', 'invoice_amount', 'status')
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
    list_display = ('user', 'is_employee', 'is_manager', 'rank', 'salary', 'phone_number', 'date_of_join')
    search_fields = ('user__username', 'rank', 'phone_number')
    list_filter = ('is_employee', 'is_manager')


@admin.register(ProjectAssignment)
class ProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = ('project', 'employee', 'time_start', 'time_stop')
    search_fields = ('project__name', 'employee__user__username')
    list_filter = ('project__name', 'employee__rank')


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'login_time', 'log_out_time', 'total_hours_of_work', 'total_travel_time', 'status')
    search_fields = ('employee__user__username', 'status')
    list_filter = ('status',)
    readonly_fields = ('total_hours_of_work', 'total_travel_time')


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'manager', 'project')  # Show basic fields
    filter_horizontal = ('employees',)  # Allow selecting employees easily
    search_fields = ('name', 'manager__user__username')  # Basic search functionality
    list_filter = ('manager', 'project')  # Simple filters for manager and project


@admin.register(TeamMemberStatus)
class TeamMemberStatusAdmin(admin.ModelAdmin):
    list_display = ('team', 'employee', 'status', 'notes', 'last_updated')  # Added 'notes'
    list_filter = ('status', 'team__project', 'team', 'last_updated')
    search_fields = ('employee__name', 'team__name', 'team__project__name')
    ordering = ('team', 'employee')
    readonly_fields = ('last_updated',)  # Make the field read-only in the admin interface

    def get_readonly_fields(self, request, obj=None):
        """
        Add `last_updated` as read-only. Also, make `team` and `employee` read-only for existing objects.
        """
        if obj:  # If the object exists
            return ('team', 'employee', 'last_updated')
        return ('last_updated',)

    fieldsets = (
        (None, {
            'fields': ('team', 'employee', 'status', 'notes')  # Added 'notes' field
        }),
    )


class LeaveAdmin(admin.ModelAdmin):
    list_display = ('user', 'leave_type', 'from_date', 'to_date', 'no_of_days', 'approval_status')
    list_filter = ('approval_status', 'leave_type')

admin.site.register(Leave, LeaveAdmin)

@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("user", "annual_leave", "sick_leave", "casual_leave")
    search_fields = ("user__username",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'message', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at')
    search_fields = ('recipient__username', 'message')
    ordering = ('-created_at',)
    actions = ['mark_as_read']

    def mark_as_read(self, request, queryset):
        queryset.update(is_read=True)
    mark_as_read.short_description = "Mark selected notifications as read"