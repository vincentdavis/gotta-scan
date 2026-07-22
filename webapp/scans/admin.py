from django.contrib import admin

from .models import Scan


@admin.register(Scan)
class ScanAdmin(admin.ModelAdmin):
    list_display = ['scan_id', 'status', 'image_count', 'capture_mode', 'created_at', 'completed_at']
    list_filter = ['status', 'capture_mode']
    search_fields = ['scan_id']
    readonly_fields = ['created_at', 'updated_at', 'claimed_at', 'completed_at']
    actions = ['requeue']

    @admin.action(description='Requeue selected scans')
    def requeue(self, request, queryset):
        queryset.update(status=Scan.Status.QUEUED, claimed_at=None, error_message='')
