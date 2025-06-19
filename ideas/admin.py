# apps/ideas/admin.py
from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.urls import reverse
from django.db.models import Count, Avg
from django.utils import timezone
from django.contrib.admin import SimpleListFilter
import json

from .models import (
    IdeaCategory,
    IdeaTemplate,
    IdeaRequest,
    GeneratedIdea,
    IdeaFeedback,
    IdeaBookmark,
    IdeaUsageStats,
    AIModelConfiguration,
)


class StatusFilter(SimpleListFilter):
    """Custom filter for request status"""
    title = 'Status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return [
            ('pending', 'Pending'),
            ('processing', 'Processing'),
            ('completed', 'Completed'),
            ('failed', 'Failed'),
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(status=self.value())
        return queryset


class BudgetFilter(SimpleListFilter):
    """Custom filter for budget ranges"""
    title = 'Budget Range'
    parameter_name = 'budget'

    def lookups(self, request, model_admin):
        return [
            ('low', 'Low Budget ($0-$50)'),
            ('moderate', 'Moderate Budget ($50-$150)'),
            ('high', 'High Budget ($150+)'),
            ('unlimited', 'Unlimited Budget'),
        ]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(budget=self.value())
        return queryset


class RatingFilter(SimpleListFilter):
    """Custom filter for user ratings"""
    title = 'User Rating'
    parameter_name = 'rating_range'

    def lookups(self, request, model_admin):
        return [
            ('5', '5 Stars'),
            ('4-5', '4-5 Stars'),
            ('3-4', '3-4 Stars'),
            ('1-3', '1-3 Stars'),
            ('unrated', 'Unrated'),
        ]

    def queryset(self, request, queryset):
        if self.value() == '5':
            return queryset.filter(user_rating__gte=5.0)
        elif self.value() == '4-5':
            return queryset.filter(user_rating__gte=4.0, user_rating__lt=5.0)
        elif self.value() == '3-4':
            return queryset.filter(user_rating__gte=3.0, user_rating__lt=4.0)
        elif self.value() == '1-3':
            return queryset.filter(user_rating__gte=1.0, user_rating__lt=3.0)
        elif self.value() == 'unrated':
            return queryset.filter(user_rating__isnull=True)
        return queryset


@admin.register(IdeaCategory)
class IdeaCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'icon', 'is_active', 'sort_order', 'template_count', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('is_active', 'sort_order')
    ordering = ('sort_order', 'name')
    
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'description', 'icon')
        }),
        ('Settings', {
            'fields': ('is_active', 'sort_order')
        }),
    )
    
    def template_count(self, obj):
        """Display count of templates in this category"""
        count = obj.templates.count()
        if count > 0:
            url = reverse('admin:ideas_ideatemplate_changelist') + f'?category__id__exact={obj.id}'
            return format_html('<a href="{}">{} templates</a>', url, count)
        return '0 templates'
    template_count.short_description = 'Templates'


@admin.register(IdeaTemplate)
class IdeaTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'template_type', 'category', 'is_premium', 'is_active', 'usage_count', 'average_rating', 'created_at')
    list_filter = ('template_type', 'category', 'is_premium', 'is_active', 'created_at')
    search_fields = ('name', 'description', 'prompt_template')
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('is_active', 'is_premium')
    readonly_fields = ('usage_count', 'average_rating')
    ordering = ('-usage_count', 'name')
    
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'template_type', 'category')
        }),
        ('Content', {
            'fields': ('description', 'prompt_template')
        }),
        ('Settings', {
            'fields': ('is_premium', 'is_active')
        }),
        ('Statistics', {
            'fields': ('usage_count', 'average_rating'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('category')


@admin.register(IdeaRequest)
class IdeaRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'user_email', 'status', 'budget', 'location_type', 'ai_model', 'created_at', 'processing_time_display')
    list_filter = (StatusFilter, BudgetFilter, 'location_type', 'ai_model', 'created_at')
    search_fields = ('user__email', 'title', 'occasion', 'location_city')
    readonly_fields = ('processing_started_at', 'processing_completed_at', 'retry_count', 'session_id', 'ip_address', 'user_agent')
    date_hierarchy = 'created_at'
    actions = ['mark_as_pending', 'mark_as_failed']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'title', 'status')
        }),
        ('Request Details', {
            'fields': ('occasion', 'partner_interests', 'user_interests', 'personality_type', 'duration', 'special_requirements')
        }),
        ('Preferences', {
            'fields': ('budget', 'location_type', 'location_city')
        }),
        ('AI Configuration', {
            'fields': ('ai_model', 'temperature', 'max_tokens'),
            'classes': ('collapse',)
        }),
        ('Processing Info', {
            'fields': ('processing_started_at', 'processing_completed_at', 'error_message', 'retry_count'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('ip_address', 'user_agent', 'session_id'),
            'classes': ('collapse',)
        }),
    )
    
    def user_email(self, obj):
        """Display user email with link to user admin"""
        if obj.user:
            url = reverse('admin:auth_user_change', args=[obj.user.pk])
            return format_html('<a href="{}">{}</a>', url, obj.user.email)
        return '-'
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'
    
    def processing_time_display(self, obj):
        """Display processing time in human readable format"""
        time = obj.get_processing_time()
        if time is not None:
            if time < 60:
                return f"{time:.1f}s"
            else:
                return f"{time/60:.1f}m"
        return '-'
    processing_time_display.short_description = 'Processing Time'
    
    def mark_as_pending(self, request, queryset):
        """Action to mark requests as pending"""
        count = queryset.update(status='pending')
        self.message_user(request, f'{count} requests marked as pending.')
    mark_as_pending.short_description = 'Mark selected requests as pending'
    
    def mark_as_failed(self, request, queryset):
        """Action to mark requests as failed"""
        count = queryset.update(status='failed')
        self.message_user(request, f'{count} requests marked as failed.')
    mark_as_failed.short_description = 'Mark selected requests as failed'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


class IdeaFeedbackInline(admin.TabularInline):
    model = IdeaFeedback
    extra = 0
    readonly_fields = ('user', 'feedback_type', 'rating', 'comment', 'created_at')
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(GeneratedIdea)
class GeneratedIdeaAdmin(admin.ModelAdmin):
    list_display = ('title', 'request_id', 'user_email', 'ai_model_used', 'user_rating', 'view_count', 'like_count', 'created_at')
    list_filter = (RatingFilter, 'ai_model_used', 'created_at')
    search_fields = ('title', 'description', 'request__user__email')
    readonly_fields = ('view_count', 'like_count', 'share_count', 'pdf_download_count', 'generation_tokens', 'ai_response_raw')
    date_hierarchy = 'created_at'
    inlines = [IdeaFeedbackInline]
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('request', 'template_used', 'title')
        }),
        ('Generated Content', {
            'fields': ('description', 'detailed_plan', 'estimated_cost', 'duration', 'location_suggestions_display', 'preparation_tips', 'alternatives')
        }),
        ('AI Details', {
            'fields': ('ai_model_used', 'prompt_used', 'generation_tokens'),
            'classes': ('collapse',)
        }),
        ('Quality & Engagement', {
            'fields': ('user_rating', 'content_quality_score', 'view_count', 'like_count', 'share_count', 'pdf_download_count'),
            'classes': ('collapse',)
        }),
        ('Raw AI Response', {
            'fields': ('ai_response_raw',),
            'classes': ('collapse',)
        }),
    )
    
    def user_email(self, obj):
        """Display user email from related request"""
        if obj.request and obj.request.user:
            return obj.request.user.email
        return '-'
    user_email.short_description = 'User'
    user_email.admin_order_field = 'request__user__email'
    
    def request_id(self, obj):
        """Display request ID with link"""
        if obj.request:
            url = reverse('admin:ideas_idearequest_change', args=[obj.request.pk])
            return format_html('<a href="{}">{}</a>', url, obj.request.id)
        return '-'
    request_id.short_description = 'Request'
    request_id.admin_order_field = 'request__id'
    
    def location_suggestions_display(self, obj):
        """Display location suggestions in a readable format"""
        if obj.location_suggestions:
            locations = obj.location_suggestions
            if isinstance(locations, list) and locations:
                return mark_safe('<br>'.join([f"• {loc}" for loc in locations[:5]]))
        return '-'
    location_suggestions_display.short_description = 'Location Suggestions'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('request', 'request__user', 'template_used')


@admin.register(IdeaFeedback)
class IdeaFeedbackAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'idea_title', 'feedback_type', 'rating', 'created_at')
    list_filter = ('feedback_type', 'rating', 'created_at')
    search_fields = ('user__email', 'idea__title', 'comment')
    readonly_fields = ('ip_address', 'user_agent')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'idea', 'feedback_type')
        }),
        ('Content', {
            'fields': ('rating', 'comment', 'report_reason')
        }),
        ('Metadata', {
            'fields': ('ip_address', 'user_agent'),
            'classes': ('collapse',)
        }),
    )
    
    def user_email(self, obj):
        """Display user email"""
        return obj.user.email if obj.user else '-'
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'
    
    def idea_title(self, obj):
        """Display idea title with link"""
        if obj.idea:
            url = reverse('admin:ideas_generatedidea_change', args=[obj.idea.pk])
            return format_html('<a href="{}">{}</a>', url, obj.idea.title[:50])
        return '-'
    idea_title.short_description = 'Idea'
    idea_title.admin_order_field = 'idea__title'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'idea')


@admin.register(IdeaBookmark)
class IdeaBookmarkAdmin(admin.ModelAdmin):
    list_display = ('user_email', 'idea_title', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('user__email', 'idea__title', 'notes')
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('user', 'idea')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
    )
    
    def user_email(self, obj):
        """Display user email"""
        return obj.user.email if obj.user else '-'
    user_email.short_description = 'User'
    user_email.admin_order_field = 'user__email'
    
    def idea_title(self, obj):
        """Display idea title with link"""
        if obj.idea:
            url = reverse('admin:ideas_generatedidea_change', args=[obj.idea.pk])
            return format_html('<a href="{}">{}</a>', url, obj.idea.title[:50])
        return '-'
    idea_title.short_description = 'Idea'
    idea_title.admin_order_field = 'idea__title'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user', 'idea')


@admin.register(IdeaUsageStats)
class IdeaUsageStatsAdmin(admin.ModelAdmin):
    list_display = ('date', 'total_requests', 'successful_generations', 'failed_generations', 'total_users', 'average_rating', 'total_tokens_used')
    list_filter = ('date',)
    date_hierarchy = 'date'
    ordering = ('-date',)
    
    fieldsets = (
        ('Date', {
            'fields': ('date',)
        }),
        ('Request Statistics', {
            'fields': ('total_requests', 'successful_generations', 'failed_generations')
        }),
        ('User Statistics', {
            'fields': ('total_users', 'free_tier_requests', 'premium_requests')
        }),
        ('Quality & Usage', {
            'fields': ('average_rating', 'total_tokens_used')
        }),
    )
    
    def has_add_permission(self, request):
        """Prevent manual addition of stats"""
        return False
    
    def has_delete_permission(self, request, obj=None):
        """Prevent deletion of stats"""
        return False


@admin.register(AIModelConfiguration)
class AIModelConfigurationAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'model_id', 'is_active', 'is_premium_only', 'cost_per_1k_tokens', 'priority')
    list_filter = ('provider', 'is_active', 'is_premium_only')
    search_fields = ('name', 'model_id')
    list_editable = ('is_active', 'is_premium_only', 'priority')
    ordering = ('priority', 'name')
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'provider', 'model_id')
        }),
        ('Configuration', {
            'fields': ('max_tokens', 'temperature', 'cost_per_1k_tokens')
        }),
        ('Settings', {
            'fields': ('is_active', 'is_premium_only', 'priority')
        }),
    )


# Custom admin site configuration
admin.site.site_header = "Date Ideas Admin"
admin.site.site_title = "Date Ideas Admin Portal"
admin.site.index_title = "Welcome to Date Ideas Administration"