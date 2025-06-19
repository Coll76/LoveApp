# apps/pdf_generator/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db.models import Count, Sum, Avg
from django.utils import timezone
from django.contrib.admin import SimpleListFilter
from django.http import HttpResponse
import csv
import os

from .models import (
    PDFTemplate, 
    PDFDocument, 
    PDFGenerationQueue, 
    PDFUsageStats, 
    PDFCustomization
)


class StatusFilter(SimpleListFilter):
    """Custom filter for PDF document status"""
    title = 'Status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return (
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        )

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class CreatedDateFilter(SimpleListFilter):
    """Filter for creation date ranges"""
    title = 'Created Date'
    parameter_name = 'created_date'

    def lookups(self, request, model_admin):
        return (
            ('today', 'Today'),
            ('week', 'This Week'),
            ('month', 'This Month'),
            ('year', 'This Year'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == 'today':
            return queryset.filter(created_at__date=now.date())
        elif self.value() == 'week':
            start_week = now - timezone.timedelta(days=7)
            return queryset.filter(created_at__gte=start_week)
        elif self.value() == 'month':
            start_month = now.replace(day=1)
            return queryset.filter(created_at__gte=start_month)
        elif self.value() == 'year':
            start_year = now.replace(month=1, day=1)
            return queryset.filter(created_at__gte=start_year)
        return queryset


@admin.register(PDFTemplate)
class PDFTemplateAdmin(admin.ModelAdmin):
    """Admin configuration for PDF Templates"""
    list_display = [
        'name', 
        'template_type', 
        'format', 
        'is_premium', 
        'is_active', 
        'usage_count',
        'sort_order',
        'preview_thumbnail',
        'created_at'
    ]
    list_filter = [
        'template_type', 
        'format', 
        'is_premium', 
        'is_active',
        'created_at'
    ]
    search_fields = ['name', 'slug', 'description']
    readonly_fields = ['usage_count', 'created_at', 'updated_at']
    prepopulated_fields = {'slug': ('name',)}
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'template_type', 'format', 'description')
        }),
        ('Template Content', {
            'fields': ('html_template', 'css_styles'),
            'classes': ('collapse',)
        }),
        ('Settings', {
            'fields': ('is_premium', 'is_active', 'sort_order')
        }),
        ('Preview & Analytics', {
            'fields': ('preview_image', 'usage_count'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['make_active', 'make_inactive', 'reset_usage_count', 'export_templates']
    
    def preview_thumbnail(self, obj):
        """Display preview image thumbnail"""
        if obj.preview_image:
            return format_html(
                '<img src="{}" width="50" height="50" style="object-fit: cover; border-radius: 4px;" />',
                obj.preview_image.url
            )
        return "No preview"
    preview_thumbnail.short_description = "Preview"
    
    def make_active(self, request, queryset):
        """Bulk action to activate templates"""
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} templates were successfully activated.')
    make_active.short_description = "Mark selected templates as active"
    
    def make_inactive(self, request, queryset):
        """Bulk action to deactivate templates"""
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} templates were successfully deactivated.')
    make_inactive.short_description = "Mark selected templates as inactive"
    
    def reset_usage_count(self, request, queryset):
        """Bulk action to reset usage count"""
        updated = queryset.update(usage_count=0)
        self.message_user(request, f'Usage count reset for {updated} templates.')
    reset_usage_count.short_description = "Reset usage count"
    
    def export_templates(self, request, queryset):
        """Export selected templates to CSV"""
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="pdf_templates.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Name', 'Type', 'Format', 'Premium', 'Active', 'Usage Count'])
        
        for template in queryset:
            writer.writerow([
                template.name,
                template.template_type,
                template.format,
                template.is_premium,
                template.is_active,
                template.usage_count
            ])
        
        return response
    export_templates.short_description = "Export selected templates to CSV"


@admin.register(PDFDocument)
class PDFDocumentAdmin(admin.ModelAdmin):
    """Admin configuration for PDF Documents"""
    list_display = [
        'title',
        'user_email', 
        'status',
        'template_name',
        'file_size_display',
        'page_count',
        'download_count',
        'generation_time_display',
        'created_at'
    ]
    list_filter = [
        StatusFilter,
        CreatedDateFilter,
        'template__template_type',
        'is_public',
        'include_qr_code',
        'include_watermark'
    ]
    search_fields = [
        'title', 
        'user__email', 
        'user__first_name', 
        'user__last_name',
        'filename'
    ]
    readonly_fields = [
        'user', 
        'idea', 
        'filename', 
        'file_path', 
        'file_size',
        'page_count',
        'generation_started_at',
        'generation_completed_at',
        'generation_time',
        'download_count',
        'last_downloaded_at',
        'share_count',
        'public_access_token',
        'created_at',
        'updated_at',
        'file_link'
    ]
    
    fieldsets = (
        ('Document Info', {
            'fields': ('title', 'user', 'idea', 'template', 'status')
        }),
        ('File Details', {
            'fields': ('filename', 'file_path', 'file_size', 'page_count', 'file_link'),
            'classes': ('collapse',)
        }),
        ('Generation Details', {
            'fields': (
                'generation_started_at', 
                'generation_completed_at', 
                'generation_time',
                'error_message',
                'retry_count'
            ),
            'classes': ('collapse',)
        }),
        ('Customization', {
            'fields': ('custom_options', 'include_qr_code', 'include_watermark'),
            'classes': ('collapse',)
        }),
        ('Access & Sharing', {
            'fields': (
                'is_public', 
                'public_access_token',
                'download_count',
                'last_downloaded_at',
                'share_count'
            ),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    actions = [
        'retry_failed_documents', 
        'mark_as_completed', 
        'export_documents',
        'cleanup_failed_documents'
    ]
    
    def user_email(self, obj):
        """Display user email with link to user admin"""
        if obj.user:
            url = reverse("admin:auth_user_change", args=[obj.user.pk])
            return format_html('<a href="{}">{}</a>', url, obj.user.email)
        return "No user"
    user_email.short_description = "User"
    user_email.admin_order_field = 'user__email'
    
    def template_name(self, obj):
        """Display template name with link"""
        if obj.template:
            url = reverse("admin:pdf_generator_pdftemplate_change", args=[obj.template.pk])
            return format_html('<a href="{}">{}</a>', url, obj.template.name)
        return "No template"
    template_name.short_description = "Template"
    template_name.admin_order_field = 'template__name'
    
    def file_size_display(self, obj):
        """Display file size in human readable format"""
        if obj.file_size:
            size = obj.file_size
            for unit in ['B', 'KB', 'MB', 'GB']:
                if size < 1024.0:
                    return f"{size:.1f} {unit}"
                size /= 1024.0
            return f"{size:.1f} TB"
        return "Unknown"
    file_size_display.short_description = "File Size"
    file_size_display.admin_order_field = 'file_size'
    
    def generation_time_display(self, obj):
        """Display generation time in seconds"""
        if obj.generation_time:
            return f"{obj.generation_time:.2f}s"
        return "N/A"
    generation_time_display.short_description = "Gen Time"
    generation_time_display.admin_order_field = 'generation_time'
    
    def file_link(self, obj):
        """Display download link if file exists"""
        if obj.file_path and os.path.exists(obj.file_path):
            download_url = reverse('pdf_generator:download-pdf', args=[obj.pk])
            return format_html('<a href="{}" target="_blank">Download PDF</a>', download_url)
        return "File not available"
    file_link.short_description = "Download"
    
    def retry_failed_documents(self, request, queryset):
        """Retry failed document generation"""
        failed_docs = queryset.filter(status='failed')
        count = failed_docs.count()
        
        for doc in failed_docs:
            if doc.can_retry():
                doc.status = 'pending'
                doc.error_message = ''
                doc.save(update_fields=['status', 'error_message'])
        
        self.message_user(request, f'{count} failed documents queued for retry.')
    retry_failed_documents.short_description = "Retry failed documents"
    
    def mark_as_completed(self, request, queryset):
        """Mark selected documents as completed (admin override)"""
        updated = queryset.filter(status__in=['pending', 'processing']).update(
            status='completed',
            generation_completed_at=timezone.now()
        )
        self.message_user(request, f'{updated} documents marked as completed.')
    mark_as_completed.short_description = "Mark as completed (override)"
    
    def export_documents(self, request, queryset):
        """Export document data to CSV"""
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="pdf_documents.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            'Title', 'User', 'Status', 'Template', 'File Size', 
            'Page Count', 'Downloads', 'Generation Time', 'Created'
        ])
        
        for doc in queryset:
            writer.writerow([
                doc.title,
                doc.user.email if doc.user else '',
                doc.status,
                doc.template.name if doc.template else '',
                doc.file_size or 0,
                doc.page_count or 0,
                doc.download_count,
                doc.generation_time or 0,
                doc.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        return response
    export_documents.short_description = "Export to CSV"
    
    def cleanup_failed_documents(self, request, queryset):
        """Clean up old failed documents"""
        old_failed = queryset.filter(
            status='failed',
            created_at__lt=timezone.now() - timezone.timedelta(days=7)
        )
        count = old_failed.count()
        old_failed.delete()
        self.message_user(request, f'{count} old failed documents cleaned up.')
    cleanup_failed_documents.short_description = "Clean up old failed documents"


@admin.register(PDFGenerationQueue)
class PDFGenerationQueueAdmin(admin.ModelAdmin):
    """Admin configuration for PDF Generation Queue"""
    list_display = [
        'pdf_document_title',
        'user_email',
        'priority',
        'status',
        'queue_position',
        'assigned_worker',
        'processing_started_at',
        'wait_time_display',
        'created_at'
    ]
    list_filter = [
        'priority',
        'status',
        'processing_started_at',
        'created_at'
    ]
    search_fields = [
        'pdf_document__title',
        'user__email',
        'assigned_worker'
    ]
    readonly_fields = [
        'user',
        'pdf_document',
        'processing_started_at',
        'processing_completed_at',
        'wait_time',
        'created_at',
        'updated_at'
    ]
    
    actions = ['cancel_queue_items', 'reset_to_pending', 'export_queue']
    
    def pdf_document_title(self, obj):
        """Display PDF document title with link"""
        if obj.pdf_document:
            url = reverse("admin:pdf_generator_pdfdocument_change", args=[obj.pdf_document.pk])
            return format_html('<a href="{}">{}</a>', url, obj.pdf_document.title)
        return "No document"
    pdf_document_title.short_description = "PDF Document"
    
    def user_email(self, obj):
        """Display user email"""
        return obj.user.email if obj.user else "No user"
    user_email.short_description = "User"
    user_email.admin_order_field = 'user__email'
    
    def wait_time_display(self, obj):
        """Display wait time in human readable format"""
        if obj.wait_time:
            return f"{obj.wait_time:.1f}s"
        return "N/A"
    wait_time_display.short_description = "Wait Time"
    wait_time_display.admin_order_field = 'wait_time'
    
    def cancel_queue_items(self, request, queryset):
        """Cancel selected queue items"""
        updated = queryset.filter(status='pending').update(status='cancelled')
        self.message_user(request, f'{updated} queue items cancelled.')
    cancel_queue_items.short_description = "Cancel selected items"
    
    def reset_to_pending(self, request, queryset):
        """Reset failed items to pending"""
        updated = queryset.filter(status='failed').update(
            status='pending',
            assigned_worker='',
            processing_started_at=None
        )
        self.message_user(request, f'{updated} items reset to pending.')
    reset_to_pending.short_description = "Reset to pending"
    
    def export_queue(self, request, queryset):
        """Export queue data to CSV"""
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="pdf_queue.csv"'
        
        writer = csv.writer(response)
        writer.writerow(['Document', 'User', 'Priority', 'Status', 'Position', 'Worker', 'Created'])
        
        for item in queryset:
            writer.writerow([
                item.pdf_document.title if item.pdf_document else '',
                item.user.email if item.user else '',
                item.priority,
                item.status,
                item.queue_position or '',
                item.assigned_worker or '',
                item.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        return response
    export_queue.short_description = "Export queue to CSV"


@admin.register(PDFUsageStats)
class PDFUsageStatsAdmin(admin.ModelAdmin):
    """Admin configuration for PDF Usage Statistics"""
    list_display = [
        'date',
        'total_pdfs_generated',
        'successful_generations',
        'failed_generations',
        'success_rate_display',
        'total_users',
        'premium_pdfs',
        'total_downloads',
        'avg_generation_time_display'
    ]
    list_filter = ['date']
    readonly_fields = [
        'date',
        'total_pdfs_generated',
        'successful_generations',
        'failed_generations',
        'total_users',
        'free_tier_pdfs',
        'premium_pdfs',
        'total_downloads',
        'total_shares',
        'average_generation_time',
        'total_file_size',
        'created_at',
        'updated_at'
    ]
    
    def success_rate_display(self, obj):
        """Calculate and display success rate"""
        if obj.total_pdfs_generated > 0:
            rate = (obj.successful_generations / obj.total_pdfs_generated) * 100
            return f"{rate:.1f}%"
        return "0%"
    success_rate_display.short_description = "Success Rate"
    
    def avg_generation_time_display(self, obj):
        """Display average generation time"""
        if obj.average_generation_time:
            return f"{obj.average_generation_time:.2f}s"
        return "N/A"
    avg_generation_time_display.short_description = "Avg Gen Time"
    avg_generation_time_display.admin_order_field = 'average_generation_time'


@admin.register(PDFCustomization)
class PDFCustomizationAdmin(admin.ModelAdmin):
    """Admin configuration for PDF Customizations"""
    list_display = [
        'user_email',
        'color_scheme',
        'font_family',
        'font_size',
        'include_cover_page',
        'include_table_of_contents',
        'has_custom_logo',
        'created_at'
    ]
    list_filter = [
        'color_scheme',
        'font_family',
        'font_size',
        'include_cover_page',
        'include_table_of_contents',
        'created_at'
    ]
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    readonly_fields = ['user', 'created_at', 'updated_at']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Color Preferences', {
            'fields': ('color_scheme', 'primary_color', 'secondary_color', 'accent_color')
        }),
        ('Typography', {
            'fields': ('font_family', 'font_size')
        }),
        ('Layout Options', {
            'fields': (
                'include_cover_page',
                'include_table_of_contents',
                'include_footer',
                'include_page_numbers'
            )
        }),
        ('Branding', {
            'fields': ('custom_logo', 'watermark_text'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def user_email(self, obj):
        """Display user email with link"""
        if obj.user:
            url = reverse("admin:auth_user_change", args=[obj.user.pk])
            return format_html('<a href="{}">{}</a>', url, obj.user.email)
        return "No user"
    user_email.short_description = "User"
    user_email.admin_order_field = 'user__email'
    
    def has_custom_logo(self, obj):
        """Check if user has custom logo"""
        return bool(obj.custom_logo)
    has_custom_logo.boolean = True
    has_custom_logo.short_description = "Custom Logo"


# Register admin site customizations
admin.site.site_header = "PDF Generator Administration"
admin.site.site_title = "PDF Generator Admin"
admin.site.index_title = "Welcome to PDF Generator Administration"