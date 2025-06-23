# apps/pdf_generator/serializers.py
import re
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers
from rest_framework.exceptions import ValidationError as DRFValidationError

from ideas.models import GeneratedIdea
from ideas.serializers import GeneratedIdeaSerializer  # Fixed import
from users.serializers import UserPublicSerializer
from .models import (
    PDFDocument, PDFTemplate, PDFCustomization, 
    PDFGenerationQueue, PDFUsageStats
)


class PDFTemplateSerializer(serializers.ModelSerializer):
    """
    Serializer for PDF templates with access control
    """
    
    preview_url = serializers.SerializerMethodField()
    is_accessible = serializers.SerializerMethodField()
    usage_count_display = serializers.SerializerMethodField()
    
    class Meta:
        model = PDFTemplate
        fields = [
            'id', 'name', 'slug', 'template_type', 'format', 'description', 
            'is_premium', 'is_accessible', 'preview_url',
            'usage_count', 'usage_count_display', 'sort_order',
            'created_at', 'is_active'
        ]
        read_only_fields = ['id', 'usage_count', 'created_at']
    
    def get_preview_url(self, obj) -> Optional[str]:
        """Get template preview image URL"""
        if obj.preview_image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.preview_image.url)
            return obj.preview_image.url
        return None
    
    def get_is_accessible(self, obj) -> bool:
        """Check if user can access this template"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return not obj.is_premium
        
        # Check if user has premium subscription (assuming this method exists)
        if hasattr(request.user, 'has_active_subscription'):
            return not obj.is_premium or request.user.has_active_subscription()
        else:
            # Fallback: allow access to non-premium templates
            return not obj.is_premium
    
    def get_usage_count_display(self, obj) -> str:
        """Format usage count for display"""
        count = obj.usage_count
        if count >= 1000000:
            return f"{count/1000000:.1f}M"
        elif count >= 1000:
            return f"{count/1000:.1f}K"
        return str(count)


class PDFTemplateDetailSerializer(PDFTemplateSerializer):
    """
    Detailed serializer for PDF templates including HTML sample
    """
    
    html_sample = serializers.SerializerMethodField()
    css_sample = serializers.SerializerMethodField()
    
    class Meta(PDFTemplateSerializer.Meta):
        fields = PDFTemplateSerializer.Meta.fields + [
            'html_sample', 'css_sample'
        ]
    
    def get_html_sample(self, obj) -> str:
        """Get truncated HTML template sample"""
        if obj.html_template:
            # Remove sensitive template logic and return first 500 chars
            html = obj.html_template[:500]
            if len(obj.html_template) > 500:
                html += "..."
            return html
        return ""
    
    def get_css_sample(self, obj) -> str:
        """Get CSS template sample"""
        if obj.css_styles:
            css = obj.css_styles[:300]
            if len(obj.css_styles) > 300:
                css += "..."
            return css
        return ""


class PDFCustomizationSerializer(serializers.ModelSerializer):
    """
    Serializer for PDF customization preferences
    """
    
    class Meta:
        model = PDFCustomization
        fields = [
            'id', 'color_scheme', 'primary_color', 'secondary_color', 
            'accent_color', 'font_family', 'font_size',
            'include_cover_page', 'include_table_of_contents', 
            'include_footer', 'include_page_numbers',
            'custom_logo', 'watermark_text',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_primary_color(self, value):
        """Validate hex color format"""
        if value and not re.match(r'^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$', value):
            raise serializers.ValidationError("Invalid hex color format")
        return value
    
    def validate_secondary_color(self, value):
        """Validate hex color format"""
        if value and not re.match(r'^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$', value):
            raise serializers.ValidationError("Invalid hex color format")
        return value
    
    def validate_accent_color(self, value):
        """Validate hex color format"""
        if value and not re.match(r'^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$', value):
            raise serializers.ValidationError("Invalid hex color format")
        return value
    
    def validate_font_size(self, value):
        """Validate font size range"""
        if value and (value < 8 or value > 20):
            raise serializers.ValidationError("Font size must be between 8 and 20")
        return value
    
    def validate_watermark_text(self, value):
        """Validate watermark text length"""
        if value and len(value) > 100:
            raise serializers.ValidationError("Watermark text cannot exceed 100 characters")
        return value


class PDFDocumentSerializer(serializers.ModelSerializer):
    """
    Basic serializer for PDF documents
    """
    
    user = UserPublicSerializer(read_only=True)
    idea = GeneratedIdeaSerializer(read_only=True)
    template = PDFTemplateSerializer(read_only=True)
    file_size_display = serializers.SerializerMethodField()
    generation_time_display = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    can_download = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    
    class Meta:
        model = PDFDocument
        fields = [
            'id', 'title', 'filename', 'user', 'idea', 'template',
            'status', 'status_display', 'file_size', 'file_size_display',
            'generation_time', 'generation_time_display', 'download_count',
            'share_count', 'is_public', 'can_download', 'can_edit',
            'page_count', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'user', 'filename', 'file_size', 'generation_time', 
            'download_count', 'share_count', 'page_count', 'created_at', 'updated_at'
        ]
    
    def get_file_size_display(self, obj) -> str:
        """Format file size for display"""
        if not obj.file_size:
            return "N/A"
        
        size = obj.file_size
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        elif size >= 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size} bytes"
    
    def get_generation_time_display(self, obj) -> str:
        """Format generation time for display"""
        if not obj.generation_time:
            return "N/A"
        
        seconds = int(obj.generation_time)
        if seconds >= 60:
            minutes = seconds // 60
            remaining_seconds = seconds % 60
            return f"{minutes}m {remaining_seconds}s"
        return f"{seconds}s"
    
    def get_status_display(self, obj) -> str:
        """Get user-friendly status display"""
        status_map = {
            'pending': 'Generating...',
            'processing': 'Processing...',
            'completed': 'Ready',
            'failed': 'Failed'
        }
        return status_map.get(obj.status, obj.status.title())
    
    def get_can_download(self, obj) -> bool:
        """Check if current user can download this PDF"""
        request = self.context.get('request')
        if not request:
            return False
        
        # Owner can always download
        if request.user == obj.user:
            return obj.status == 'completed'
        
        # Public PDFs can be downloaded by anyone
        return obj.is_public and obj.status == 'completed'
    
    def get_can_edit(self, obj) -> bool:
        """Check if current user can edit this PDF"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        return request.user == obj.user


class PDFDocumentDetailSerializer(PDFDocumentSerializer):
    """
    Detailed serializer for PDF documents with additional fields
    """
    
    public_share_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    queue_info = serializers.SerializerMethodField()
    
    class Meta(PDFDocumentSerializer.Meta):
        fields = PDFDocumentSerializer.Meta.fields + [
            'file_path', 'custom_options', 'error_message',
            'include_qr_code', 'include_watermark',
            'public_share_url', 'download_url', 'queue_info',
            'generation_started_at', 'generation_completed_at',
            'retry_count', 'last_downloaded_at', 'public_access_token'
        ]
    
    def get_public_share_url(self, obj) -> Optional[str]:
        """Get public sharing URL if available"""
        if obj.is_public and obj.public_access_token:
            return f"{getattr(settings, 'FRONTEND_URL', '')}/shared/pdf/{obj.public_access_token}"
        return None
    
    def get_download_url(self, obj) -> Optional[str]:
        """Get download URL for the PDF"""
        if obj.status != 'completed':
            return None
        
        request = self.context.get('request')
        if not request:
            return None
        
        # Check permissions
        if request.user == obj.user or obj.is_public:
            if obj.is_public and obj.public_access_token:
                return request.build_absolute_uri(
                    f"/api/pdf/public/{obj.public_access_token}/download/"
                )
            else:
                return request.build_absolute_uri(
                    f"/api/pdf/documents/{obj.id}/download/"
                )
        return None
    
    def get_queue_info(self, obj) -> Optional[Dict[str, Any]]:
        """Get queue information if PDF is queued"""
        try:
            if hasattr(obj, 'queue_item') and obj.queue_item:
                queue_item = obj.queue_item
                return {
                    'queue_position': queue_item.queue_position,
                    'priority': queue_item.priority,
                    'estimated_completion_time': queue_item.estimated_completion_time,
                    'status': queue_item.status
                }
        except Exception:
            pass
        return None


class PDFDocumentCreateSerializer(serializers.Serializer):
    """
    Serializer for creating new PDF documents
    """
    
    idea_id = serializers.UUIDField(required=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)
    custom_options = serializers.JSONField(required=False, default=dict)
    title = serializers.CharField(max_length=300, required=False)
    include_qr_code = serializers.BooleanField(default=True)
    include_watermark = serializers.BooleanField(default=False)
    is_public = serializers.BooleanField(default=False)
    
    def validate_idea_id(self, value):
        """Validate that idea exists and belongs to user"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required")
        
        try:
            idea = GeneratedIdea.objects.select_related('request').get(
                id=value,
                request__user=request.user
            )
        except GeneratedIdea.DoesNotExist:
            raise serializers.ValidationError("Idea not found or access denied")
        
        return value
    
    def validate_template_id(self, value):
        """Validate template exists and user has access"""
        if not value:
            return value
        
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required")
        
        try:
            template = PDFTemplate.objects.get(id=value, is_active=True)
        except PDFTemplate.DoesNotExist:
            raise serializers.ValidationError("Template not found or inactive")
        
        # Check premium access
        if template.is_premium and hasattr(request.user, 'has_active_subscription'):
            if not request.user.has_active_subscription():
                raise serializers.ValidationError(
                    "Premium template requires active subscription"
                )
        
        return value
    
    def validate_custom_options(self, value):
        """Validate custom options structure"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Custom options must be a dictionary")
        
        # Validate specific option keys and values
        allowed_keys = {
            'font_size', 'font_family', 'color_scheme', 'include_images',
            'include_charts', 'page_orientation', 'margin_size',
            'header_text', 'footer_text', 'async_generation',
            'compression_level', 'quality'
        }
        
        for key in value.keys():
            if key not in allowed_keys:
                raise serializers.ValidationError(f"Invalid option key: {key}")
        
        # Validate specific values
        if 'font_size' in value:
            font_size = value['font_size']
            if not isinstance(font_size, (int, float)) or font_size < 8 or font_size > 24:
                raise serializers.ValidationError(
                    "Font size must be a number between 8 and 24"
                )
        
        if 'page_orientation' in value:
            if value['page_orientation'] not in ['portrait', 'landscape']:
                raise serializers.ValidationError(
                    "Page orientation must be 'portrait' or 'landscape'"
                )
        
        if 'compression_level' in value:
            if value['compression_level'] not in ['low', 'medium', 'high']:
                raise serializers.ValidationError(
                    "Compression level must be 'low', 'medium', or 'high'"
                )
        
        return value
    
    def validate_title(self, value):
        """Validate PDF title"""
        if value:
            # Remove potentially dangerous characters
            cleaned_title = re.sub(r'[<>:"/\\|?*]', '', value)
            if len(cleaned_title.strip()) == 0:
                raise serializers.ValidationError("Title cannot be empty after cleaning")
            return cleaned_title.strip()
        return value
    
    def validate(self, attrs):
        """Cross-field validation"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required")
        
        # Check user's daily generation limit (basic implementation)
        daily_limit = getattr(request.user, 'daily_pdf_limit', 10)  # Default limit
        today_count = PDFDocument.objects.filter(
            user=request.user,
            created_at__date=timezone.now().date()
        ).count()
        
        if today_count >= daily_limit:
            raise serializers.ValidationError(
                f"Daily PDF generation limit ({daily_limit}) exceeded"
            )
        
        # Check monthly limit for non-premium users
        if hasattr(request.user, 'has_active_subscription') and not request.user.has_active_subscription():
            monthly_limit = getattr(request.user, 'monthly_pdf_limit', 50)  # Default limit
            current_month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_count = PDFDocument.objects.filter(
                user=request.user,
                created_at__gte=current_month
            ).count()
            
            if month_count >= monthly_limit:
                raise serializers.ValidationError(
                    f"Monthly PDF generation limit ({monthly_limit}) exceeded. "
                    "Consider upgrading to premium for unlimited generation."
                )
        
        return attrs


class PDFGenerationQueueSerializer(serializers.ModelSerializer):
    """
    Serializer for PDF generation queue items
    """
    
    pdf_document = PDFDocumentSerializer(read_only=True)
    estimated_wait_time = serializers.SerializerMethodField()
    
    class Meta:
        model = PDFGenerationQueue
        fields = [
            'id', 'pdf_document', 'status', 'priority',
            'queue_position', 'estimated_completion_time',
            'estimated_wait_time', 'processing_started_at',
            'processing_completed_at', 'wait_time',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_estimated_wait_time(self, obj) -> Optional[str]:
        """Calculate estimated wait time"""
        if obj.estimated_completion_time:
            now = timezone.now()
            if obj.estimated_completion_time > now:
                delta = obj.estimated_completion_time - now
                minutes = delta.total_seconds() / 60
                if minutes < 1:
                    return "Less than 1 minute"
                elif minutes < 60:
                    return f"{int(minutes)} minutes"
                else:
                    hours = minutes / 60
                    return f"{hours:.1f} hours"
        return None


class PDFUsageStatsSerializer(serializers.ModelSerializer):
    """
    Serializer for PDF usage statistics
    """
    
    class Meta:
        model = PDFUsageStats
        fields = [
            'id', 'date', 'total_pdfs_generated', 'successful_generations',
            'failed_generations', 'total_users', 'free_tier_pdfs',
            'premium_pdfs', 'total_downloads', 'total_shares',
            'average_generation_time', 'total_file_size', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class PDFAnalyticsSerializer(serializers.Serializer):
    """
    Serializer for PDF analytics data
    """
    
    total_generated = serializers.IntegerField()
    total_downloads = serializers.IntegerField()
    total_shares = serializers.IntegerField()
    success_rate = serializers.FloatField()
    average_generation_time = serializers.FloatField()
    storage_used = serializers.IntegerField()
    bandwidth_used = serializers.IntegerField()
    
    # Monthly breakdown
    monthly_stats = serializers.ListField(
        child=serializers.DictField()
    )
    
    # Template usage
    template_usage = serializers.ListField(
        child=serializers.DictField()
    )
    
    # Recent activity
    recent_pdfs = PDFDocumentSerializer(many=True, read_only=True)


class PDFShareSerializer(serializers.Serializer):
    """
    Serializer for PDF sharing responses
    """
    
    share_url = serializers.URLField()
    access_token = serializers.CharField()
    expires_at = serializers.DateTimeField(allow_null=True)
    message = serializers.CharField()


class PDFOptimizationSerializer(serializers.Serializer):
    """
    Serializer for PDF optimization requests and responses
    """
    
    compression_level = serializers.ChoiceField(
        choices=['low', 'medium', 'high'],
        default='medium'
    )
    
    # Response fields
    original_size = serializers.IntegerField(read_only=True)
    optimized_size = serializers.IntegerField(read_only=True)
    size_reduction_percent = serializers.FloatField(read_only=True)
    message = serializers.CharField(read_only=True)


class PDFRegenerateSerializer(serializers.Serializer):
    """
    Serializer for PDF regeneration requests
    """
    
    template_id = serializers.UUIDField(required=False, allow_null=True)
    custom_options = serializers.JSONField(required=False, default=dict)
    
    def validate_template_id(self, value):
        """Validate template exists and user has access"""
        if not value:
            return value
        
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Authentication required")
        
        try:
            template = PDFTemplate.objects.get(id=value, is_active=True)
        except PDFTemplate.DoesNotExist:
            raise serializers.ValidationError("Template not found or inactive")
        
        # Check premium access
        if template.is_premium and hasattr(request.user, 'has_active_subscription'):
            if not request.user.has_active_subscription():
                raise serializers.ValidationError(
                    "Premium template requires active subscription"
                )
        
        return value
    
    def validate_custom_options(self, value):
        """Validate custom options using the same logic as create serializer"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Custom options must be a dictionary")
        
        # Reuse validation logic from PDFDocumentCreateSerializer
        create_serializer = PDFDocumentCreateSerializer()
        return create_serializer.validate_custom_options(value)