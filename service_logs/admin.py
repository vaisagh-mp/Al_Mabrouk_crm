from django.contrib import admin
from .models import EmployeeServiceLog


@admin.register(EmployeeServiceLog)
class EmployeeServiceLogAdmin(admin.ModelAdmin):
    list_display = (
        'date',
        'employee',
        'project',
        'service_type',
        'total_hours',
        'ot_hours',
        'ot_type',
        'is_holiday',
        'normal_cost',
        'ot_cost',
        'created_at',
    )

    list_filter = (
        'service_type',
        'ot_type',
        'is_holiday',
        'location_type',
        'date',
        'employee',
    )

    search_fields = (
        'employee__user__username',
        'employee__user__first_name',
        'employee__user__last_name',
        'project__name',
        'vessel_name',
        'port',
        'service_reference',
    )

    ordering = ('-date',)

    date_hierarchy = 'date'

    readonly_fields = (
        'total_hours',
        'ot_hours',
        'ot_type',
        'normal_cost',
        'ot_cost',
        'created_at',
    )

    fieldsets = (
        ('Employee & Project', {
            'fields': ('employee', 'project')
        }),
        ('Service Details', {
            'fields': (
                'date',
                'service_type',
                'service_reference',
                'vessel_name',
                'port',
                'location_type',
            )
        }),
        ('Time Details', {
            'fields': (
                'start_time',
                'end_time',
            )
        }),
        ('Calculated Values (Auto)', {
            'fields': (
                'total_hours',
                'ot_hours',
                'ot_type',
                'is_holiday',
                'normal_cost',
                'ot_cost',
            )
        }),
        ('System', {
            'fields': ('created_at',)
        }),
    )

    def has_change_permission(self, request, obj=None):
        """
        Prevent accidental modification of calculated fields
        but allow correcting time/date if needed.
        """
        return super().has_change_permission(request, obj)

    def has_add_permission(self, request):
        return True

    def has_delete_permission(self, request, obj=None):
        return True
