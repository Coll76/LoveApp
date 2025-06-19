# apps/pdf_generator/filters.py
"""
PDF Generator Filters

This module provides comprehensive filtering capabilities for PDF-related models
with proper security, performance optimizations, and extensive filter options.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import django_filters
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Count, Avg, Sum, Max, Min
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ideas.models import GeneratedIdea
from .models import (
    PDFDocument, PDFTemplate, PDFCustomization, 
    PDFGenerationQueue, PDFUsageStats
)

logger = logging.getLogger(__name__)
User = get_user_model()


class BaseSecureFilter(django_filters.FilterSet):
    """
    Base filter class with security and performance optimizations
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = getattr(self, 'request', None)
        
        # Add common security validations
        if self.request and hasattr(self.request, 'user'):
            self._apply_user_security()
    
    def _apply_user_security(self):
        """Apply user-based security filters"""
        if not self.request.user.is_authenticated:
            # For anonymous users, only show public content
            if hasattr(self.queryset.model, 'is_public'):
                self.queryset = self.queryset.filter(is_public=True)
    
    def filter_queryset(self, queryset):
        """Override to add performance optimizations"""
        # Apply base filtering
        queryset = super().filter_queryset(queryset)
        
        # Add select_related and prefetch_related optimizations
        queryset = self._optimize_queryset(queryset)
        
        return queryset
    
    def _optimize_queryset(self, queryset):
        """Add query optimizations based on model"""
        # Override in subclasses for model-specific optimizations
        return queryset


class DateRangeFilter(django_filters.FilterSet):
    """
    Mixin for common date range filtering
    """
    
    date_from = django_filters.DateFilter(
        field_name='created_at',
        lookup_expr='gte',
        widget=forms.DateInput(attrs={'type': 'date'}),
        label=_('Created from')
    )
    
    date_to = django_filters.DateFilter(
        field_name='created_at',
        lookup_expr='lte',
        widget=forms.DateInput(attrs={'type': 'date'}),
        label=_('Created to')
    )
    
    # Predefined date ranges
    date_range = django_filters.ChoiceFilter(
        method='filter_by_date_range',
        choices=[
            ('today', _('Today')),
            ('yesterday', _('Yesterday')),
            ('this_week', _('This Week')),
            ('last_week', _('Last Week')),
            ('this_month', _('This Month')),
            ('last_month', _('Last Month')),
            ('this_year', _('This Year')),
            ('last_30_days', _('Last 30 Days')),
            ('last_90_days', _('Last 90 Days')),
        ],
        label=_('Date Range'),
        empty_label=_('All Time')
    )
    
    def filter_by_date_range(self, queryset, name, value):
        """Filter by predefined date ranges"""
        now = timezone.now()
        today = now.date()
        
        date_ranges = {
            'today': (
                timezone.make_aware(datetime.combine(today, datetime.min.time())),
                timezone.make_aware(datetime.combine(today, datetime.max.time()))
            ),
            'yesterday': (
                timezone.make_aware(datetime.combine(today - timedelta(days=1), datetime.min.time())),
                timezone.make_aware(datetime.combine(today - timedelta(days=1), datetime.max.time()))
            ),
            'this_week': (
                now - timedelta(days=now.weekday()),
                now
            ),
            'last_week': (
                now - timedelta(days=now.weekday() + 7),
                now - timedelta(days=now.weekday())
            ),
            'this_month': (
                now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                now
            ),
            'last_month': (
                (now.replace(day=1) - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            ),
            'this_year': (
                now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
                now
            ),
            'last_30_days': (
                now - timedelta(days=30),
                now
            ),
            'last_90_days': (
                now - timedelta(days=90),
                now
            ),
        }
        
        if value in date_ranges:
            start_date, end_date = date_ranges[value]
            return queryset.filter(created_at__range=(start_date, end_date))
        
        return queryset


class PDFDocumentFilter(BaseSecureFilter, DateRangeFilter):
    """
    Comprehensive filter for PDF documents with security and performance optimizations
    """
    
    # Basic text search
    search = django_filters.CharFilter(
        method='filter_search',
        label=_('Search'),
        widget=forms.TextInput(attrs={
            'placeholder': _('Search by title, idea title, or description...'),
            'class': 'form-control'
        })
    )
    
    # Status filtering
    status = django_filters.MultipleChoiceFilter(
        choices=[
            ('pending', _('Pending')),
            ('processing', _('Processing')),
            ('completed', _('Completed')),
            ('failed', _('Failed')),
            ('queued', _('Queued')),
        ],
        widget=forms.CheckboxSelectMultiple,
        label=_('Status')
    )
    
    # Template filtering
    template = django_filters.ModelChoiceFilter(
        queryset=PDFTemplate.objects.active(),
        empty_label=_('All Templates'),
        label=_('Template')
    )
    
    template_category = django_filters.CharFilter(
        field_name='template__category',
        lookup_expr='iexact',
        label=_('Template Category')
    )
    
    # Idea-related filtering
    idea = django_filters.ModelChoiceFilter(
        queryset=GeneratedIdea.objects.none(),  # Will be populated in __init__
        empty_label=_('All Ideas'),
        label=_('Idea')
    )
    
    idea_category = django_filters.CharFilter(
        field_name='idea__category',
        lookup_expr='icontains',
        label=_('Idea Category')
    )
    
    # File properties
    file_size_min = django_filters.NumberFilter(
        field_name='file_size',
        lookup_expr='gte',
        label=_('Minimum File Size (bytes)')
    )
    
    file_size_max = django_filters.NumberFilter(
        field_name='file_size',
        lookup_expr='lte',
        label=_('Maximum File Size (bytes)')
    )
    
    # Engagement metrics
    download_count_min = django_filters.NumberFilter(
        field_name='download_count',
        lookup_expr='gte',
        label=_('Minimum Downloads')
    )
    
    share_count_min = django_filters.NumberFilter(
        field_name='share_count',
        lookup_expr='gte',
        label=_('Minimum Shares')
    )
    
    # Boolean filters
    is_public = django_filters.BooleanFilter(
        label=_('Public PDFs Only')
    )
    
    has_watermark = django_filters.BooleanFilter(
        field_name='include_watermark',
        label=_('Has Watermark')
    )
    
    has_qr_code = django_filters.BooleanFilter(
        field_name='include_qr_code',
        label=_('Has QR Code')
    )
    
    # Advanced filters
    generation_time_min = django_filters.NumberFilter(
        field_name='generation_time',
        lookup_expr='gte',
        label=_('Minimum Generation Time (seconds)')
    )
    
    generation_time_max = django_filters.NumberFilter(
        field_name='generation_time',
        lookup_expr='lte',
        label=_('Maximum Generation Time (seconds)')
    )
    
    # Ordering
    ordering = django_filters.OrderingFilter(
        fields=[
            ('created_at', 'created_at'),
            ('updated_at', 'updated_at'),
            ('title', 'title'),
            ('download_count', 'download_count'),
            ('share_count', 'share_count'),
            ('file_size', 'file_size'),
            ('generation_time', 'generation_time'),
        ],
        field_labels={
            'created_at': _('Date Created'),
            'updated_at': _('Date Updated'),
            'title': _('Title'),
            'download_count': _('Downloads'),
            'share_count': _('Shares'),
            'file_size': _('File Size'),
            'generation_time': _('Generation Time'),
        }
    )
    
    class Meta:
        model = PDFDocument
        fields = {
            'title': ['icontains', 'exact'],
            'created_at': ['gte', 'lte', 'exact'],
            'updated_at': ['gte', 'lte'],
            'status': ['exact', 'in'],
            'is_public': ['exact'],
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Populate idea queryset based on user
        if self.request and self.request.user.is_authenticated:
            self.filters['idea'].queryset = GeneratedIdea.objects.filter(
                request__user=self.request.user
            ).select_related('request')
            
            # For premium users, show additional filters
            if self.request.user.has_active_subscription():
                self._add_premium_filters()
    
    def _add_premium_filters(self):
        """Add premium-only filters"""
        # Add analytics-based filters for premium users
        self.filters['high_engagement'] = django_filters.BooleanFilter(
            method='filter_high_engagement',
            label=_('High Engagement PDFs')
        )
        
        self.filters['trending'] = django_filters.BooleanFilter(
            method='filter_trending',
            label=_('Trending PDFs')
        )
    
    def filter_search(self, queryset, name, value):
        """Comprehensive search across multiple fields"""
        if not value:
            return queryset
        
        # Sanitize search input
        value = value.strip()[:100]  # Limit length for security
        
        search_query = Q()
        
        # Search in PDF title and description
        search_query |= Q(title__icontains=value)
        
        # Search in related idea
        search_query |= Q(idea__title__icontains=value)
        search_query |= Q(idea__description__icontains=value)
        
        # Search in template name
        search_query |= Q(template__name__icontains=value)
        
        # For exact ID matches (if numeric)
        if value.isdigit():
            search_query |= Q(id=int(value))
            search_query |= Q(idea__id=int(value))
        
        return queryset.filter(search_query).distinct()
    
    def filter_high_engagement(self, queryset, name, value):
        """Filter PDFs with high engagement (premium feature)"""
        if not value:
            return queryset
        
        # Define high engagement criteria
        avg_downloads = queryset.aggregate(avg_downloads=Avg('download_count'))['avg_downloads'] or 0
        avg_shares = queryset.aggregate(avg_shares=Avg('share_count'))['avg_shares'] or 0
        
        return queryset.filter(
            Q(download_count__gt=avg_downloads * 1.5) |
            Q(share_count__gt=avg_shares * 1.5)
        )
    
    def filter_trending(self, queryset, name, value):
        """Filter trending PDFs (premium feature)"""
        if not value:
            return queryset
        
        # PDFs with recent activity (downloads/shares in last 7 days)
        recent_date = timezone.now() - timedelta(days=7)
        
        return queryset.filter(
            Q(updated_at__gte=recent_date) &
            (Q(download_count__gt=0) | Q(share_count__gt=0))
        ).order_by('-updated_at')
    
    def _optimize_queryset(self, queryset):
        """Add PDF-specific query optimizations"""
        return queryset.select_related(
            'user', 'idea', 'template', 'idea__request'
        ).prefetch_related(
            'idea__request__user'
        )


class PDFTemplateFilter(BaseSecureFilter):
    """
    Filter for PDF templates with category and feature-based filtering
    """
    
    # Text search
    search = django_filters.CharFilter(
        method='filter_search',
        label=_('Search Templates'),
        widget=forms.TextInput(attrs={
            'placeholder': _('Search by name or description...'),
            'class': 'form-control'
        })
    )
    
    # Category filtering
    category = django_filters.CharFilter(
        lookup_expr='iexact',
        label=_('Category')
    )
    
    categories = django_filters.MultipleChoiceFilter(
        field_name='category',
        choices=[],  # Will be populated dynamically
        widget=forms.CheckboxSelectMultiple,
        label=_('Categories')
    )
    
    # Premium/Free filtering
    is_premium = django_filters.BooleanFilter(
        label=_('Premium Templates Only')
    )
    
    is_free = django_filters.BooleanFilter(
        method='filter_free_templates',
        label=_('Free Templates Only')
    )
    
    # Usage-based filtering
    popular = django_filters.BooleanFilter(
        method='filter_popular',
        label=_('Popular Templates')
    )
    
    usage_count_min = django_filters.NumberFilter(
        field_name='usage_count',
        lookup_expr='gte',
        label=_('Minimum Usage Count')
    )
    
    # Feature-based filtering
    supports_custom_colors = django_filters.BooleanFilter(
        label=_('Supports Custom Colors')
    )
    
    supports_custom_fonts = django_filters.BooleanFilter(
        label=_('Supports Custom Fonts')
    )
    
    # Ordering
    ordering = django_filters.OrderingFilter(
        fields=[
            ('name', 'name'),
            ('usage_count', 'usage_count'),
            ('created_at', 'created_at'),
            ('sort_order', 'sort_order'),
        ],
        field_labels={
            'name': _('Name'),
            'usage_count': _('Popularity'),
            'created_at': _('Date Added'),
            'sort_order': _('Default Order'),
        }
    )
    
    class Meta:
        model = PDFTemplate
        fields = {
            'name': ['icontains', 'exact'],
            'category': ['exact', 'icontains'],
            'is_premium': ['exact'],
            'is_active': ['exact'],
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Populate category choices dynamically
        categories = PDFTemplate.objects.active().values_list(
            'category', flat=True
        ).distinct().order_by('category')
        
        category_choices = [(cat, cat.title()) for cat in categories if cat]
        self.filters['categories'].extra['choices'] = category_choices
        
        # Filter premium templates for non-subscribers
        if self.request and self.request.user.is_authenticated:
            if not self.request.user.has_active_subscription():
                self.queryset = self.queryset.filter(is_premium=False)
    
    def filter_search(self, queryset, name, value):
        """Search in template name and description"""
        if not value:
            return queryset
        
        value = value.strip()[:100]
        
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        ).distinct()
    
    def filter_free_templates(self, queryset, name, value):
        """Filter only free templates"""
        if value:
            return queryset.filter(is_premium=False)
        return queryset
    
    def filter_popular(self, queryset, name, value):
        """Filter popular templates based on usage"""
        if not value:
            return queryset
        
        # Get templates with above-average usage
        avg_usage = queryset.aggregate(avg_usage=Avg('usage_count'))['avg_usage'] or 0
        
        return queryset.filter(usage_count__gt=avg_usage).order_by('-usage_count')
    
    def _optimize_queryset(self, queryset):
        """Optimize template queries"""
        return queryset.select_related().prefetch_related()


class PDFGenerationQueueFilter(BaseSecureFilter, DateRangeFilter):
    """
    Filter for PDF generation queue with status and priority filtering
    """
    
    # Queue status
    status = django_filters.MultipleChoiceFilter(
        choices=[
            ('pending', _('Pending')),
            ('processing', _('Processing')),
            ('completed', _('Completed')),
            ('failed', _('Failed')),
            ('cancelled', _('Cancelled')),
        ],
        widget=forms.CheckboxSelectMultiple,
        label=_('Queue Status')
    )
    
    # Priority filtering
    priority = django_filters.MultipleChoiceFilter(
        choices=[
            ('low', _('Low')),
            ('normal', _('Normal')),
            ('high', _('High')),
            ('urgent', _('Urgent')),
        ],
        widget=forms.CheckboxSelectMultiple,
        label=_('Priority')
    )
    
    # User filtering (admin only)
    user = django_filters.ModelChoiceFilter(
        queryset=User.objects.none(),  # Populated for admins only
        empty_label=_('All Users'),
        label=_('User')
    )
    
    # Processing time filters
    processing_time_min = django_filters.NumberFilter(
        field_name='processing_time',
        lookup_expr='gte',
        label=_('Minimum Processing Time (seconds)')
    )
    
    processing_time_max = django_filters.NumberFilter(
        field_name='processing_time',
        lookup_expr='lte',
        label=_('Maximum Processing Time (seconds)')
    )
    
    # Queue position
    queue_position_max = django_filters.NumberFilter(
        field_name='queue_position',
        lookup_expr='lte',
        label=_('Maximum Queue Position')
    )
    
    # Ordering
    ordering = django_filters.OrderingFilter(
        fields=[
            ('created_at', 'created_at'),
            ('queue_position', 'queue_position'),
            ('priority', 'priority'),
            ('processing_time', 'processing_time'),
        ],
        field_labels={
            'created_at': _('Date Added'),
            'queue_position': _('Queue Position'),
            'priority': _('Priority'),
            'processing_time': _('Processing Time'),
        }
    )
    
    class Meta:
        model = PDFGenerationQueue
        fields = {
            'status': ['exact', 'in'],
            'priority': ['exact', 'in'],
            'created_at': ['gte', 'lte'],
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Only show user filter to admin users
        if (self.request and self.request.user.is_authenticated and 
            self.request.user.is_staff):
            self.filters['user'].queryset = User.objects.filter(
                is_active=True
            ).order_by('email')
        else:
            # Remove user filter for non-admin users
            del self.filters['user']
    
    def _optimize_queryset(self, queryset):
        """Optimize queue queries"""
        return queryset.select_related(
            'user', 'pdf_document', 'pdf_document__idea'
        )


class PDFUsageStatsFilter(BaseSecureFilter, DateRangeFilter):
    """
    Filter for PDF usage statistics with aggregation support
    """
    
    # Action type filtering
    action_type = django_filters.MultipleChoiceFilter(
        choices=[
            ('generate', _('Generate')),
            ('download', _('Download')),
            ('share', _('Share')),
            ('view', _('View')),
            ('delete', _('Delete')),
        ],
        widget=forms.CheckboxSelectMultiple,
        label=_('Action Type')
    )
    
    # PDF document filtering
    pdf_document = django_filters.ModelChoiceFilter(
        queryset=PDFDocument.objects.none(),  # Populated based on user
        empty_label=_('All Documents'),
        label=_('PDF Document')
    )
    
    # User filtering (admin only)
    user = django_filters.ModelChoiceFilter(
        queryset=User.objects.none(),
        empty_label=_('All Users'),
        label=_('User')
    )
    
    # IP address filtering (admin/security)
    ip_address = django_filters.CharFilter(
        lookup_expr='exact',
        label=_('IP Address')
    )
    
    # Aggregation period
    group_by = django_filters.ChoiceFilter(
        method='filter_group_by',
        choices=[
            ('hour', _('By Hour')),
            ('day', _('By Day')),
            ('week', _('By Week')),
            ('month', _('By Month')),
        ],
        label=_('Group By'),
        empty_label=_('No Grouping')
    )
    
    class Meta:
        model = PDFUsageStats
        fields = {
            'action_type': ['exact', 'in'],
            'created_at': ['gte', 'lte'],
            'ip_address': ['exact'],
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        if self.request and self.request.user.is_authenticated:
            # Populate PDF document choices for user
            self.filters['pdf_document'].queryset = PDFDocument.objects.filter(
                user=self.request.user
            ).order_by('-created_at')
            
            # Only show user filter to admin users
            if self.request.user.is_staff:
                self.filters['user'].queryset = User.objects.filter(
                    is_active=True
                ).order_by('email')
            else:
                del self.filters['user']
                # Non-admin users can only see their own stats
                self.queryset = self.queryset.filter(user=self.request.user)
    
    def filter_group_by(self, queryset, name, value):
        """Group statistics by time period"""
        # This would typically be handled in the view with aggregation
        # Return queryset as-is, let the view handle grouping
        return queryset
    
    def _optimize_queryset(self, queryset):
        """Optimize usage stats queries"""
        return queryset.select_related(
            'user', 'pdf_document', 'pdf_document__idea'
        )


# Custom filter widgets for better UX
class DateRangeWidget(forms.MultiWidget):
    """Custom widget for date range selection"""
    
    def __init__(self, attrs=None):
        widgets = [
            forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        ]
        super().__init__(widgets, attrs)
    
    def decompress(self, value):
        if value:
            return [value.start, value.end]
        return [None, None]


class NumberRangeWidget(forms.MultiWidget):
    """Custom widget for number range selection"""
    
    def __init__(self, attrs=None):
        widgets = [
            forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Min'}),
            forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Max'}),
        ]
        super().__init__(widgets, attrs)
    
    def decompress(self, value):
        if value:
            return [value[0], value[1]]
        return [None, None]


# Filter validation utilities
def validate_filter_params(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize filter parameters
    
    Args:
        request_data: Raw request data
        
    Returns:
        Cleaned and validated filter data
        
    Raises:
        ValidationError: If validation fails
    """
    
    cleaned_data = {}
    
    # Validate date parameters
    for field in ['date_from', 'date_to']:
        if field in request_data:
            try:
                date_value = datetime.strptime(request_data[field], '%Y-%m-%d').date()
                cleaned_data[field] = date_value
            except ValueError:
                raise ValidationError(f"Invalid date format for {field}")
    
    # Validate numeric parameters
    numeric_fields = [
        'file_size_min', 'file_size_max', 'download_count_min', 
        'share_count_min', 'generation_time_min', 'generation_time_max'
    ]
    
    for field in numeric_fields:
        if field in request_data:
            try:
                numeric_value = int(request_data[field])
                if numeric_value < 0:
                    raise ValidationError(f"{field} must be non-negative")
                cleaned_data[field] = numeric_value
            except (ValueError, TypeError):
                raise ValidationError(f"Invalid numeric value for {field}")
    
    # Validate choice fields
    choice_fields = {
        'status': ['pending', 'processing', 'completed', 'failed', 'queued'],
        'priority': ['low', 'normal', 'high', 'urgent'],
        'action_type': ['generate', 'download', 'share', 'view', 'delete'],
    }
    
    for field, valid_choices in choice_fields.items():
        if field in request_data:
            values = request_data.getlist(field) if hasattr(request_data, 'getlist') else [request_data[field]]
            for value in values:
                if value not in valid_choices:
                    raise ValidationError(f"Invalid choice '{value}' for {field}")
            cleaned_data[field] = values
    
    # Validate search terms (prevent injection)
    if 'search' in request_data:
        search_term = str(request_data['search']).strip()[:100]  # Limit length
        # Remove potentially dangerous characters
        import re
        search_term = re.sub(r'[<>"\';]', '', search_term)
        cleaned_data['search'] = search_term
    
    return cleaned_data


def get_filter_statistics(filtered_queryset) -> Dict[str, Any]:
    """
    Generate statistics for filtered results
    
    Args:
        filtered_queryset: Filtered queryset
        
    Returns:
        Dictionary with statistics
    """
    
    stats = {
        'total_count': filtered_queryset.count(),
    }
    
    # Add model-specific statistics
    model = filtered_queryset.model
    
    if model == PDFDocument:
        stats.update({
            'completed_count': filtered_queryset.filter(status='completed').count(),
            'pending_count': filtered_queryset.filter(status='pending').count(),
            'total_downloads': filtered_queryset.aggregate(
                total=Sum('download_count')
            )['total'] or 0,
            'avg_file_size': filtered_queryset.aggregate(
                avg=Avg('file_size')
            )['avg'] or 0,
        })
    
    elif model == PDFTemplate:
        stats.update({
            'premium_count': filtered_queryset.filter(is_premium=True).count(),
            'free_count': filtered_queryset.filter(is_premium=False).count(),
            'total_usage': filtered_queryset.aggregate(
                total=Sum('usage_count')
            )['total'] or 0,
        })
    
    elif model == PDFGenerationQueue:
        stats.update({
            'pending_count': filtered_queryset.filter(status='pending').count(),
            'processing_count': filtered_queryset.filter(status='processing').count(),
            'avg_processing_time': filtered_queryset.filter(
                processing_time__isnull=False
            ).aggregate(avg=Avg('processing_time'))['avg'] or 0,
        })
    
    return stats