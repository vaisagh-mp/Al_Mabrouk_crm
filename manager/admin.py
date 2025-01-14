from django.contrib import admin
from .models import Manager

class ManagerAdmin(admin.ModelAdmin):
    list_display = ('user', 'department', 'hire_date')
    search_fields = ('user__username', 'department')

admin.site.register(Manager, ManagerAdmin)

