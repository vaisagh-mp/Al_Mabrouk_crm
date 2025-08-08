from django.contrib import admin
from .models import Project, Employee, ProjectAssignment, Attendance,Team, TeamMemberStatus, Leave, LeaveBalance, Notification, ActivityLog,ProjectAttachment, Holiday, WorkOrder, WorkOrderDetail, Spare, Tool, Document, Vessel, WorkOrderImage


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'manager', 'purchase_and_expenses', 'invoice_amount', 'status', 'created_at')
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
    list_display = ('user', 'is_employee', 'is_manager', 'is_administration', 'is_hr', 'rank', 'phone_number', 'date_of_join')
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
    list_display = (
        'team', 'employee', 'status', 'notes', 'manager_approval_status', 'last_updated'
    )
    list_filter = (
        'status', 'manager_approval_status', 'team__project', 'team', 'last_updated'
    )
    search_fields = (
        'employee__user__username', 'team__name', 'team__project__name'
    )
    ordering = ('team', 'employee')
    readonly_fields = ('last_updated',)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('team', 'employee', 'last_updated')
        return ('last_updated',)

    fieldsets = (
        (None, {
            'fields': (
                'team', 'employee', 'status', 'notes', 
                'manager_approval_status', 'rejection_reason'  # Add here
            )
        }),
    )


class LeaveAdmin(admin.ModelAdmin):
    list_display = ('user', 'leave_type', 'from_date', 'to_date', 'no_of_days', 'approval_status')
    list_filter = ('approval_status', 'leave_type')

admin.site.register(Leave, LeaveAdmin)

@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ("user", "annual_leave", "sick_leave")
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


admin.site.register(ActivityLog)

@admin.register(ProjectAttachment)
class ProjectAttachmentAdmin(admin.ModelAdmin):
    list_display = ('project', 'file', 'uploaded_by', 'uploaded_at')
    list_filter = ('uploaded_at', 'uploaded_by')
    search_fields = ('project__name', 'file')


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ('date', 'description')
    list_filter = ('date',)
    ordering = ('-date',)


class SpareInline(admin.TabularInline):
    model = Spare
    extra = 1

class ToolInline(admin.TabularInline):
    model = Tool
    extra = 1

class DocumentInline(admin.TabularInline):
    model = Document
    extra = 1

class WorkOrderDetailInline(admin.StackedInline):
    model = WorkOrderDetail
    can_delete = False
    max_num = 1
    extra = 0

@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ['work_order_number', 'client', 'vessel', 'assigned_date', 'get_assigned_users']
    def get_assigned_users(self, obj):
        return ", ".join([user.get_full_name() or user.username for user in obj.job_assigned_to.all()])
    get_assigned_users.short_description = 'Assigned Engineers'
    
    search_fields = ['work_order_number', 'client', 'vessel', 'imo_no']
    list_filter = ['assigned_date', 'client']
    inlines = [WorkOrderDetailInline, SpareInline, ToolInline, DocumentInline]

@admin.register(Spare)
class SpareAdmin(admin.ModelAdmin):
    list_display = ['work_order', 'name', 'unit', 'quantity']
    search_fields = ['name']

@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ['work_order', 'name', 'quantity']
    search_fields = ['name']

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ['work_order', 'name', 'status']
    search_fields = ['name']

@admin.register(WorkOrderDetail)
class WorkOrderDetailAdmin(admin.ModelAdmin):
    list_display = ['work_order', 'start_date', 'completion_date', 'estimated_hours']


@admin.register(Vessel)
class VesselAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(WorkOrderImage)
class WorkOrderImageAdmin(admin.ModelAdmin):
    list_display = ('id', 'work_order', 'image_preview')
    readonly_fields = ('image_preview',)

    def image_preview(self, obj):
        if obj.image:
            return f'<img src="{obj.image.url}" style="max-height: 100px;" />'
        return "-"
    image_preview.allow_tags = True
    image_preview.short_description = "Preview"