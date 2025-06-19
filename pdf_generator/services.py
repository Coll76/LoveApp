# apps/pdf_generator/services.py
import os
import uuid
import logging
from datetime import datetime, timedelta
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError
from django.template import Template, Context
from django.db import transaction
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration
import qrcode
from io import BytesIO
import base64
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Any, Optional, Tuple, List
import PyPDF2
import hashlib
import json
from celery import shared_task

from .models import (
    PDFDocument, PDFTemplate, PDFCustomization, 
    PDFGenerationQueue, PDFUsageStats
)
from ideas.models import GeneratedIdea
from users.models import User

logger = logging.getLogger(__name__)

class PDFGenerationError(Exception):
    """Custom exception for PDF generation errors"""
    pass

class PDFGeneratorService:
    """Main service for PDF generation"""
    
    def __init__(self):
        self.font_config = FontConfiguration()
        self.base_media_path = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Ensure required directories exist"""
        directories = [
            self.base_media_path,
            os.path.join(self.base_media_path, 'temp'),
            os.path.join(self.base_media_path, 'completed'),
            os.path.join(self.base_media_path, 'previews'),
        ]
        
        for directory in directories:
            os.makedirs(directory, exist_ok=True)
    
    def generate_pdf(
        self, 
        user: User, 
        idea: GeneratedIdea, 
        template: PDFTemplate = None,
        custom_options: Dict[str, Any] = None
    ) -> PDFDocument:
        """
        Generate a PDF document for a date idea
        
        Args:
            user: User requesting the PDF
            idea: GeneratedIdea to create PDF for
            template: PDFTemplate to use (optional)
            custom_options: Custom styling options (optional)
        
        Returns:
            PDFDocument instance
        
        Raises:
            PDFGenerationError: If generation fails
            ValidationError: If validation fails
        """
        try:
            # Validate inputs
            self._validate_generation_request(user, idea, template)
            
            # Get or create default template
            if not template:
                template = self._get_default_template(user)
            
            # Create PDF document record
            pdf_doc = self._create_pdf_document(user, idea, template, custom_options)
            
            # Generate the actual PDF
            self._generate_pdf_file(pdf_doc)
            
            # Update usage statistics
            self._update_usage_stats(user, template)
            
            logger.info(f"PDF generated successfully: {pdf_doc.id}")
            return pdf_doc
            
        except Exception as e:
            logger.error(f"PDF generation failed: {str(e)}")
            if 'pdf_doc' in locals():
                pdf_doc.mark_as_failed(str(e))
            raise PDFGenerationError(f"Failed to generate PDF: {str(e)}")
    
    def _validate_generation_request(
        self, 
        user: User, 
        idea: GeneratedIdea, 
        template: PDFTemplate = None
    ):
        """Validate PDF generation request"""
        
        # Check if user can generate PDFs
        if not PDFDocument.objects.user_can_generate(user):
            raise ValidationError("Daily PDF generation limit exceeded")
        
        # Check if idea belongs to user
        if idea.request.user != user:
            raise ValidationError("Idea does not belong to user")
        
        # Check if template is available to user
        if template and template.is_premium and not user.has_active_subscription():
            raise ValidationError("Premium template requires active subscription")
    
    def _get_default_template(self, user: User) -> PDFTemplate:
        """Get default template based on user subscription"""
        templates = PDFTemplate.objects.active()
        
        if user.has_active_subscription():
            return templates.first()
        else:
            return templates.free_templates().first()
    
    def _create_pdf_document(
        self, 
        user: User, 
        idea: GeneratedIdea, 
        template: PDFTemplate,
        custom_options: Dict[str, Any] = None
    ) -> PDFDocument:
        """Create PDF document record"""
        
        filename = f"{idea.title[:50]}_{uuid.uuid4().hex[:8]}.pdf"
        filename = self._sanitize_filename(filename)
        
        pdf_doc = PDFDocument.objects.create(
            user=user,
            idea=idea,
            template=template,
            title=idea.title,
            filename=filename,
            custom_options=custom_options or {},
            status='pending'
        )
        
        # Generate public access token if needed
        if custom_options and custom_options.get('make_public', False):
            pdf_doc.is_public = True
            pdf_doc.generate_public_token()
            pdf_doc.save()
        
        return pdf_doc
    
    def _generate_pdf_file(self, pdf_doc: PDFDocument):
        """Generate the actual PDF file"""
        
        pdf_doc.mark_as_processing()
        
        try:
            # Prepare context data
            context = self._prepare_pdf_context(pdf_doc)
            
            # Render HTML template
            html_content = self._render_html_template(pdf_doc.template, context)
            
            # Generate PDF from HTML
            pdf_path = self._html_to_pdf(html_content, pdf_doc)
            
            # Get file info
            file_size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else None
            page_count = self._get_pdf_page_count(pdf_path)
            
            # Update document record
            pdf_doc.mark_as_completed(pdf_path, file_size, page_count)
            
            # Increment template usage
            if pdf_doc.template:
                pdf_doc.template.increment_usage()
            
        except Exception as e:
            logger.error(f"PDF file generation failed for {pdf_doc.id}: {str(e)}")
            pdf_doc.mark_as_failed(str(e))
            raise
    
    def _prepare_pdf_context(self, pdf_doc: PDFDocument) -> Dict[str, Any]:
        """Prepare context data for PDF template"""
        
        idea = pdf_doc.idea
        user = pdf_doc.user
        
        # Get user customization
        customization = getattr(user, 'pdf_customization', None)
        
        # Generate QR code for sharing
        qr_code_data = None
        if pdf_doc.custom_options.get('include_qr_code', True):
            qr_code_data = self._generate_qr_code(pdf_doc)
        
        context = {
            'pdf_document': pdf_doc,
            'idea': idea,
            'user': user,
            'customization': customization,
            'qr_code_data': qr_code_data,
            'generation_date': timezone.now(),
            'custom_options': pdf_doc.custom_options,
            
            # Idea details
            'title': idea.title,
            'description': idea.description,
            'detailed_plan': idea.detailed_plan,
            'estimated_cost': idea.estimated_cost,
            'duration': idea.duration,
            'location_suggestions': idea.location_suggestions,
            'preparation_tips': idea.preparation_tips,
            'alternatives': idea.alternatives,
            
            # Request details
            'occasion': idea.request.occasion,
            'budget': idea.request.get_budget_display(),
            'location_type': idea.request.get_location_type_display(),
            'location_city': idea.request.location_city,
            'special_requirements': idea.request.special_requirements,
            
            # Styling
            'primary_color': customization.primary_color if customization else '#007bff',
            'secondary_color': customization.secondary_color if customization else '#6c757d',
            'accent_color': customization.accent_color if customization else '#28a745',
            'font_family': customization.font_family if customization else 'default',
            'font_size': customization.font_size if customization else 12,
        }
        
        return context
    
    def _render_html_template(self, template: PDFTemplate, context: Dict[str, Any]) -> str:
        """Render HTML template with context"""
        
        try:
            # Use template's HTML if available, otherwise use default
            if template and template.html_template:
                # Use Django's template engine
                django_template = Template(template.html_template)
                html_content = django_template.render(Context(context))
            else:
                # Use default template
                html_content = render_to_string('pdf/date_plan_default.html', context)
            
            return html_content
            
        except Exception as e:
            logger.error(f"Template rendering failed: {str(e)}")
            raise PDFGenerationError(f"Template rendering failed: {str(e)}")
    
    def _html_to_pdf(self, html_content: str, pdf_doc: PDFDocument) -> str:
        """Convert HTML to PDF using WeasyPrint"""
        
        try:
            # Prepare CSS
            css_content = self._prepare_css(pdf_doc)
            
            # Create PDF path
            pdf_path = os.path.join(
                self.base_media_path, 
                'completed', 
                pdf_doc.filename
            )
            
            # Generate PDF
            html_doc = HTML(string=html_content, base_url=settings.MEDIA_ROOT)
            
            if css_content:
                css_doc = CSS(string=css_content, font_config=self.font_config)
                html_doc.write_pdf(pdf_path, stylesheets=[css_doc], font_config=self.font_config)
            else:
                html_doc.write_pdf(pdf_path, font_config=self.font_config)
            
            return pdf_path
            
        except Exception as e:
            logger.error(f"HTML to PDF conversion failed: {str(e)}")
            raise PDFGenerationError(f"PDF conversion failed: {str(e)}")
    
    def _prepare_css(self, pdf_doc: PDFDocument) -> str:
        """Prepare CSS styles for PDF"""
        
        css_content = ""
        
        # Add template CSS
        if pdf_doc.template and pdf_doc.template.css_styles:
            css_content += pdf_doc.template.css_styles
        
        # Get user customization
        customization = getattr(pdf_doc.user, 'pdf_customization', None)
        primary_color = customization.primary_color if customization else '#007bff'
        secondary_color = customization.secondary_color if customization else '#6c757d'
        font_size = customization.font_size if customization else 12
        
        # Add default CSS with customization
        css_content += f"""
        @page {{
            size: A4;
            margin: 2cm;
            @bottom-right {{
                content: "Page " counter(page) " of " counter(pages);
                font-size: 10px;
                color: #666;
            }}
        }}
        
        body {{
            font-family: 'DejaVu Sans', sans-serif;
            font-size: {font_size}px;
            line-height: 1.6;
            color: #333;
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 2px solid {primary_color};
            padding-bottom: 20px;
        }}
        
        .title {{
            font-size: 24px;
            font-weight: bold;
            color: {primary_color};
            margin-bottom: 10px;
        }}
        
        .section {{
            margin-bottom: 20px;
            page-break-inside: avoid;
        }}
        
        .section-title {{
            font-size: 16px;
            font-weight: bold;
            color: {primary_color};
            margin-bottom: 10px;
            border-bottom: 1px solid #ddd;
            padding-bottom: 5px;
        }}
        
        .qr-code {{
            text-align: center;
            margin-top: 30px;
        }}
        
        .footer {{
            margin-top: 30px;
            text-align: center;
            font-size: 10px;
            color: #666;
            border-top: 1px solid #ddd;
            padding-top: 10px;
        }}
        
        .cost-section {{
            background-color: #f8f9fa;
            padding: 15px;
            border-left: 4px solid {primary_color};
            margin: 20px 0;
        }}
        
        .location-section {{
            border: 1px solid #ddd;
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
        }}
        
        .preparation-tips {{
            background-color: #fff3cd;
            border: 1px solid #ffeaa7;
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
        }}
        
        .alternatives {{
            background-color: #e8f5e8;
            border: 1px solid #c3e6cb;
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
        }}
        """
        
        return css_content
    
    def _generate_qr_code(self, pdf_doc: PDFDocument) -> str:
        """Generate QR code for PDF sharing"""
        
        try:
            # Create sharing URL
            if pdf_doc.is_public and pdf_doc.public_access_token:
                share_url = f"{settings.FRONTEND_URL}/shared/pdf/{pdf_doc.public_access_token}"
            else:
                share_url = f"{settings.FRONTEND_URL}/pdf/{pdf_doc.id}"
            
            # Generate QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(share_url)
            qr.make(fit=True)
            
            # Create QR code image
            qr_img = qr.make_image(fill_color="black", back_color="white")
            
            # Convert to base64
            buffer = BytesIO()
            qr_img.save(buffer, format='PNG')
            buffer.seek(0)
            qr_data = base64.b64encode(buffer.getvalue()).decode()
            
            return f"data:image/png;base64,{qr_data}"
            
        except Exception as e:
            logger.error(f"QR code generation failed: {str(e)}")
            return None
    
    def _get_pdf_page_count(self, pdf_path: str) -> Optional[int]:
        """Get page count of PDF file"""
        
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                return len(pdf_reader.pages)
        except Exception as e:
            logger.error(f"Failed to get page count: {str(e)}")
            return None
    
    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename for safe storage"""
        
        # Remove invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Limit length
        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[:251] + ext
        
        return filename
    
    def _update_usage_stats(self, user: User, template: PDFTemplate):
        """Update daily usage statistics"""
        
        today = timezone.now().date()
        
        try:
            stats, created = PDFUsageStats.objects.get_or_create(
                date=today,
                defaults={
                    'total_pdfs_generated': 0,
                    'successful_generations': 0,
                    'failed_generations': 0,
                    'total_users': 0,
                    'free_tier_pdfs': 0,
                    'premium_pdfs': 0,
                }
            )
            
            stats.total_pdfs_generated += 1
            stats.successful_generations += 1
            
            # Track user tier
            if user.has_active_subscription():
                stats.premium_pdfs += 1
            else:
                stats.free_tier_pdfs += 1
            
            # Update total users (approximate)
            today_users = PDFDocument.objects.filter(
                created_at__date=today
            ).values_list('user', flat=True).distinct().count()
            stats.total_users = today_users
            
            stats.save()
            
        except Exception as e:
            logger.error(f"Failed to update usage stats: {str(e)}")


class PDFQueueService:
    """Service for managing PDF generation queue"""
    
    def add_to_queue(
        self, 
        pdf_document: PDFDocument, 
        priority: str = 'normal'
    ) -> PDFGenerationQueue:
        """Add PDF generation to queue"""
        
        queue_item = PDFGenerationQueue.objects.create(
            user=pdf_document.user,
            pdf_document=pdf_document,
            priority=priority,
            status='pending'
        )
        
        # Calculate estimated completion time
        self._calculate_estimated_completion(queue_item)
        
        return queue_item
    
    def process_queue(self, max_items: int = 10):
        """Process pending items in the queue"""
        
        pending_items = PDFGenerationQueue.objects.filter(
            status='pending'
        ).order_by('-priority', 'created_at')[:max_items]
        
        for item in pending_items:
            try:
                self._process_queue_item(item)
            except Exception as e:
                logger.error(f"Failed to process queue item {item.id}: {str(e)}")
                item.status = 'failed'
                item.save()
    
    def _process_queue_item(self, queue_item: PDFGenerationQueue):
        """Process individual queue item"""
        
        queue_item.status = 'processing'
        queue_item.processing_started_at = timezone.now()
        queue_item.save()
        
        try:
            # Generate PDF
            pdf_service = PDFGeneratorService()
            pdf_service._generate_pdf_file(queue_item.pdf_document)
            
            queue_item.status = 'completed'
            queue_item.processing_completed_at = timezone.now()
            queue_item.save()
            
        except Exception as e:
            queue_item.status = 'failed'
            queue_item.save()
            raise
    
    def _calculate_estimated_completion(self, queue_item: PDFGenerationQueue):
        """Calculate estimated completion time"""
        
        # Count items ahead in queue
        ahead_count = PDFGenerationQueue.objects.filter(
            status='pending',
            created_at__lt=queue_item.created_at
        ).count()
        
        # Estimate 30 seconds per PDF
        estimated_seconds = ahead_count * 30
        estimated_completion = timezone.now() + timedelta(seconds=estimated_seconds)
        
        queue_item.estimated_completion_time = estimated_completion
        queue_item.queue_position = ahead_count + 1
        queue_item.save()


class PDFTemplateService:
    """Service for managing PDF templates"""
    
    def create_template(
        self, 
        name: str,
        template_type: str,
        html_template: str,
        css_styles: str = '',
        **kwargs
    ) -> PDFTemplate:
        """Create new PDF template"""
        
        template = PDFTemplate.objects.create(
            name=name,
            template_type=template_type,
            html_template=html_template,
            css_styles=css_styles,
            **kwargs
        )
        
        # Generate preview
        self._generate_template_preview(template)
        
        return template
    
    def _generate_template_preview(self, template: PDFTemplate):
        """Generate preview image for template"""
        
        try:
            # Create sample context
            sample_context = self._get_sample_context()
            
            # Render template
            django_template = Template(template.html_template)
            html_content = django_template.render(Context(sample_context))
            
            # Convert to image (simplified - you might want to use a proper HTML to image service)
            preview_path = os.path.join(
                settings.MEDIA_ROOT, 
                'pdf_templates', 
                'previews', 
                f"{template.slug}_preview.png"
            )
            
            # This is a placeholder - implement actual HTML to image conversion
            # You might use services like htmlcsstoimage.com API or wkhtmltoimage
            
            template.preview_image = f"pdf_templates/previews/{template.slug}_preview.png"
            template.save()
            
        except Exception as e:
            logger.error(f"Failed to generate template preview: {str(e)}")
    
    def _get_sample_context(self) -> Dict[str, Any]:
        """Get sample context for template preview"""
        
        return {
            'title': 'Sample Date Idea',
            'description': 'A romantic evening under the stars',
            'detailed_plan': 'Start with dinner at a cozy restaurant, then move to a scenic viewpoint for stargazing.',
            'estimated_cost': '$50-100',
            'duration': '3-4 hours',
            'location_suggestions': 'Downtown area with good restaurants and nearby parks',
            'preparation_tips': 'Check weather forecast and bring a blanket',
            'alternatives': 'Indoor movie night if weather is bad',
            'primary_color': '#007bff',
            'secondary_color': '#6c757d',
            'accent_color': '#28a745',
        }


class PDFAnalyticsService:
    """Service for PDF analytics and reporting"""
    
    def get_user_stats(self, user: User) -> Dict[str, Any]:
        """Get PDF statistics for a user"""
        
        user_docs = PDFDocument.objects.for_user(user)
        
        stats = {
            'total_pdfs': user_docs.count(),
            'completed_pdfs': user_docs.completed().count(),
            'pending_pdfs': user_docs.pending().count(),
            'failed_pdfs': user_docs.failed().count(),
            'total_downloads': sum(doc.download_count for doc in user_docs),
            'total_shares': sum(doc.share_count for doc in user_docs),
            'most_used_template': self._get_most_used_template(user),
            'average_generation_time': self._get_average_generation_time(user),
            'recent_activity': self._get_recent_activity(user),
        }
        
        return stats
    
    def get_platform_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get platform-wide PDF statistics"""
        
        since = timezone.now() - timedelta(days=days)
        recent_docs = PDFDocument.objects.recent(days)
        
        stats = {
            'total_pdfs_period': recent_docs.count(),
            'successful_pdfs': recent_docs.completed().count(),
            'failed_pdfs': recent_docs.failed().count(),
            'total_users_period': recent_docs.values('user').distinct().count(),
            'most_popular_templates': self._get_popular_templates(),
            'average_generation_time': self._get_platform_average_generation_time(days),
            'daily_breakdown': self._get_daily_breakdown(days),
            'template_usage': self._get_template_usage_stats(),
        }
        
        return stats
    
    def _get_most_used_template(self, user: User) -> Optional[str]:
        """Get user's most used template"""
        
        from django.db.models import Count
        
        result = PDFDocument.objects.for_user(user).values(
            'template__name'
        ).annotate(
            count=Count('template')
        ).order_by('-count').first()
        
        return result['template__name'] if result else None
    
    def _get_average_generation_time(self, user: User) -> float:
        """Get average generation time for user's PDFs"""
        
        from django.db.models import Avg
        
        result = PDFDocument.objects.for_user(user).completed().aggregate(
            avg_time=Avg('generation_time')
        )
        
        return result['avg_time'] or 0.0
    
    def _get_recent_activity(self, user: User, limit: int = 10) -> List[Dict]:
        """Get recent PDF activity for user"""
        
        recent_docs = PDFDocument.objects.for_user(user).order_by(
            '-created_at'
        )[:limit]
        
        activity = []
        for doc in recent_docs:
            activity.append({
                'title': doc.title,
                'status': doc.status,
                'created_at': doc.created_at,
                'template': doc.template.name if doc.template else None,
                'download_count': doc.download_count,
            })
        
        return activity
    
    def _get_popular_templates(self, limit: int = 10) -> List[Dict]:
        """Get most popular templates"""
        
        templates = PDFTemplate.objects.popular(limit)
        
        return [
            {
                'name': template.name,
                'type': template.template_type,
                'usage_count': template.usage_count,
                'is_premium': template.is_premium,
            }
            for template in templates
        ]
    
    def _get_platform_average_generation_time(self, days: int) -> float:
        """Get platform average generation time"""
        
        from django.db.models import Avg
        
        result = PDFDocument.objects.recent(days).completed().aggregate(
            avg_time=Avg('generation_time')
        )
        
        return result['avg_time'] or 0.0
    
    def _get_daily_breakdown(self, days: int) -> List[Dict]:
        """Get daily PDF generation breakdown"""
        
        from django.db.models import Count
        from django.db.models.functions import TruncDate
        
        since = timezone.now() - timedelta(days=days)
        
        daily_stats = PDFDocument.objects.filter(
            created_at__gte=since
        ).extra(
            select={'day': 'date(created_at)'}
        ).values('day').annotate(
            total=Count('id'),
            completed=Count('id', filter=models.Q(status='completed')),
            failed=Count('id', filter=models.Q(status='failed'))
        ).order_by('day')
        
        return list(daily_stats)
    
    def _get_template_usage_stats(self) -> Dict[str, int]:
        """Get template usage statistics"""
        
        from django.db.models import Count
        
        usage = PDFTemplate.objects.values('template_type').annotate(
            count=Count('pdfdocument')
        )
        
        return {item['template_type']: item['count'] for item in usage}


class PDFOptimizationService:
    """Service for PDF optimization and compression"""
    
    def optimize_pdf(self, pdf_path: str, compression_level: str = 'medium') -> str:
        """Optimize PDF file size"""
        
        try:
            optimized_path = pdf_path.replace('.pdf', '_optimized.pdf')
            
            # Use PyPDF2 for basic optimization
            with open(pdf_path, 'rb') as input_file:
                pdf_reader = PyPDF2.PdfReader(input_file)
                pdf_writer = PyPDF2.PdfWriter()
                
                for page in pdf_reader.pages:
                    pdf_writer.add_page(page)
                
                # Compress
                if compression_level == 'high':
                    pdf_writer.compress_identical_objects()
                
                with open(optimized_path, 'wb') as output_file:
                    pdf_writer.write(output_file)
            
            # Replace original with optimized
            os.replace(optimized_path, pdf_path)
            
            return pdf_path
            
        except Exception as e:
            logger.error(f"PDF optimization failed: {str(e)}")
            return pdf_path  # Return original path if optimization fails
    
    def compress_images_in_pdf(self, pdf_path: str) -> str:
        """Compress images within PDF"""
        
        # This would require more sophisticated PDF processing
        # For now, return the original path
        return pdf_path


# Celery tasks for async PDF generation
@shared_task(bind=True, max_retries=3)
def generate_pdf_async(self, user_id: int, idea_id: int, template_id: int = None, custom_options: Dict = None):
    """Async task for PDF generation"""
    
    try:
        user = User.objects.get(id=user_id)
        idea = GeneratedIdea.objects.get(id=idea_id)
        template = PDFTemplate.objects.get(id=template_id) if template_id else None
        
        pdf_service = PDFGeneratorService()
        pdf_doc = pdf_service.generate_pdf(user, idea, template, custom_options)
        
        return {
            'success': True,
            'pdf_id': str(pdf_doc.id),
            'filename': pdf_doc.filename
        }
        
    except Exception as e:
        logger.error(f"Async PDF generation failed: {str(e)}")
        
        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=60 * (2 ** self.request.retries))
        
        return {
            'success': False,
            'error': str(e)
        }


@shared_task
def process_pdf_queue():
    """Process PDF generation queue"""
    
    queue_service = PDFQueueService()
    queue_service.process_queue()


# Complete the missing methods from apps/pdf_generator/services.py

@shared_task
def cleanup_old_pdfs():
    """Clean up old PDF files"""
    
    # Delete PDFs older than 30 days for free users
    cutoff_date = timezone.now() - timedelta(days=30)
    
    # Get expired PDFs for free users
    expired_free_pdfs = PDFDocument.objects.filter(
        created_at__lt=cutoff_date,
        user__subscription__isnull=True  # Free users
    ).exclude(status='failed')
    
    # Delete PDFs older than 90 days for premium users
    premium_cutoff = timezone.now() - timedelta(days=90)
    expired_premium_pdfs = PDFDocument.objects.filter(
        created_at__lt=premium_cutoff,
        user__subscription__isnull=False  # Premium users
    ).exclude(status='failed')
    
    cleanup_count = 0
    
    for pdf_doc in expired_free_pdfs:
        try:
            # Delete physical file
            if pdf_doc.file_path and os.path.exists(pdf_doc.file_path):
                os.remove(pdf_doc.file_path)
            
            # Soft delete the record
            pdf_doc.soft_delete()
            cleanup_count += 1
            
        except Exception as e:
            logger.error(f"Failed to cleanup PDF {pdf_doc.id}: {str(e)}")
    
    for pdf_doc in expired_premium_pdfs:
        try:
            # Delete physical file
            if pdf_doc.file_path and os.path.exists(pdf_doc.file_path):
                os.remove(pdf_doc.file_path)
            
            # Soft delete the record
            pdf_doc.soft_delete()
            cleanup_count += 1
            
        except Exception as e:
            logger.error(f"Failed to cleanup PDF {pdf_doc.id}: {str(e)}")
    
    # Clean up failed PDFs older than 7 days
    failed_cutoff = timezone.now() - timedelta(days=7)
    failed_pdfs = PDFDocument.objects.filter(
        status='failed',
        created_at__lt=failed_cutoff
    )
    
    for pdf_doc in failed_pdfs:
        try:
            # Delete any partial files
            if pdf_doc.file_path and os.path.exists(pdf_doc.file_path):
                os.remove(pdf_doc.file_path)
            
            # Hard delete failed records
            pdf_doc.delete()
            cleanup_count += 1
            
        except Exception as e:
            logger.error(f"Failed to cleanup failed PDF {pdf_doc.id}: {str(e)}")
    
    logger.info(f"Cleaned up {cleanup_count} old PDF files")
    return cleanup_count


@shared_task
def cleanup_temp_files():
    """Clean up temporary files"""
    
    temp_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs', 'temp')
    if not os.path.exists(temp_dir):
        return 0
    
    # Delete temp files older than 1 hour
    cutoff_time = timezone.now() - timedelta(hours=1)
    cleanup_count = 0
    
    try:
        for filename in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, filename)
            
            if os.path.isfile(filepath):
                # Get file modification time
                file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
                file_time = timezone.make_aware(file_time)
                
                if file_time < cutoff_time:
                    os.remove(filepath)
                    cleanup_count += 1
                    
    except Exception as e:
        logger.error(f"Failed to cleanup temp files: {str(e)}")
    
    logger.info(f"Cleaned up {cleanup_count} temporary files")
    return cleanup_count


@shared_task
def update_pdf_statistics():
    """Update daily PDF statistics"""
    
    today = timezone.now().date()
    
    try:
        # Get or create today's stats
        stats, created = PDFUsageStats.objects.get_or_create(
            date=today,
            defaults={
                'total_pdfs_generated': 0,
                'successful_generations': 0,
                'failed_generations': 0,
                'total_users': 0,
                'free_tier_pdfs': 0,
                'premium_pdfs': 0,
                'total_downloads': 0,
                'total_shares': 0,
                'average_generation_time': 0.0,
                'total_file_size': 0,
            }
        )
        
        # Calculate today's stats
        today_pdfs = PDFDocument.objects.filter(created_at__date=today)
        
        # Update basic counts
        stats.total_pdfs_generated = today_pdfs.count()
        stats.successful_generations = today_pdfs.filter(status='completed').count()
        stats.failed_generations = today_pdfs.filter(status='failed').count()
        
        # Count unique users
        stats.total_users = today_pdfs.values('user').distinct().count()
        
        # Count by user tier
        stats.free_tier_pdfs = today_pdfs.filter(
            user__subscription__isnull=True
        ).count()
        stats.premium_pdfs = today_pdfs.filter(
            user__subscription__isnull=False
        ).count()
        
        # Calculate totals
        stats.total_downloads = sum(
            today_pdfs.values_list('download_count', flat=True)
        )
        stats.total_shares = sum(
            today_pdfs.values_list('share_count', flat=True)
        )
        
        # Calculate average generation time
        completed_pdfs = today_pdfs.filter(
            status='completed',
            generation_time__isnull=False
        )
        
        if completed_pdfs.exists():
            from django.db.models import Avg
            avg_time = completed_pdfs.aggregate(
                avg=Avg('generation_time')
            )['avg']
            stats.average_generation_time = avg_time or 0.0
        
        # Calculate total file size
        stats.total_file_size = sum(
            size for size in today_pdfs.values_list('file_size', flat=True) 
            if size is not None
        )
        
        stats.save()
        
        logger.info(f"Updated PDF statistics for {today}")
        
    except Exception as e:
        logger.error(f"Failed to update PDF statistics: {str(e)}")


@shared_task
def process_stuck_pdfs():
    """Process PDFs that are stuck in processing state"""
    
    # Get PDFs stuck in processing for more than 2 hours
    stuck_pdfs = PDFDocument.objects.stuck_processing(hours=2)
    
    processed_count = 0
    
    for pdf_doc in stuck_pdfs:
        try:
            # Reset to pending status for retry
            pdf_doc.status = 'pending'
            pdf_doc.generation_started_at = None
            pdf_doc.retry_count += 1
            pdf_doc.save(update_fields=[
                'status', 'generation_started_at', 'retry_count'
            ])
            
            # Add back to queue if retries are available
            if pdf_doc.can_retry():
                queue_service = PDFQueueService()
                queue_service.add_to_queue(pdf_doc, priority='high')
                processed_count += 1
            else:
                # Mark as failed if max retries exceeded
                pdf_doc.mark_as_failed("Max retries exceeded - stuck in processing")
                
        except Exception as e:
            logger.error(f"Failed to process stuck PDF {pdf_doc.id}: {str(e)}")
    
    logger.info(f"Processed {processed_count} stuck PDF documents")
    return processed_count


@shared_task
def generate_template_previews():
    """Generate preview images for templates that don't have them"""
    
    templates_without_previews = PDFTemplate.objects.active().filter(
        preview_image__isnull=True
    )
    
    template_service = PDFTemplateService()
    generated_count = 0
    
    for template in templates_without_previews:
        try:
            template_service._generate_template_preview(template)
            generated_count += 1
            
        except Exception as e:
            logger.error(f"Failed to generate preview for template {template.id}: {str(e)}")
    
    logger.info(f"Generated {generated_count} template previews")
    return generated_count


class PDFValidationService:
    """Service for validating PDF-related data"""
    
    @staticmethod
    def validate_custom_options(options: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and sanitize custom options"""
        
        if not isinstance(options, dict):
            raise ValidationError("Custom options must be a dictionary")
        
        # Define allowed options and their validators
        allowed_options = {
            'include_qr_code': bool,
            'include_watermark': bool,
            'make_public': bool,
            'color_scheme': str,
            'font_size': int,
            'include_cover_page': bool,
            'include_footer': bool,
            'custom_title': str,
            'custom_footer_text': str,
        }
        
        validated_options = {}
        
        for key, value in options.items():
            if key not in allowed_options:
                continue  # Skip unknown options
            
            expected_type = allowed_options[key]
            
            try:
                if expected_type == bool:
                    validated_options[key] = bool(value)
                elif expected_type == int:
                    validated_options[key] = int(value)
                    # Validate font size range
                    if key == 'font_size' and not (8 <= validated_options[key] <= 20):
                        validated_options[key] = 12  # Default font size
                elif expected_type == str:
                    validated_options[key] = str(value)[:200]  # Limit string length
                    
            except (ValueError, TypeError):
                # Skip invalid values
                continue
        
        return validated_options
    
    @staticmethod
    def validate_pdf_permissions(user: User, pdf_doc: PDFDocument) -> bool:
        """Validate user permissions for PDF operations"""
        
        # Check if user owns the PDF
        if pdf_doc.user != user:
            # Check if PDF is public
            if not pdf_doc.is_public:
                return False
        
        return True
    
    @staticmethod
    def validate_template_access(user: User, template: PDFTemplate) -> bool:
        """Validate user access to template"""
        
        if not template.is_active:
            return False
        
        # Check premium template access
        if template.is_premium and not user.has_active_subscription():
            return False
        
        return True


class PDFCacheService:
    """Service for caching PDF-related data"""
    
    def __init__(self):
        from django.core.cache import cache
        self.cache = cache
        self.cache_timeout = 3600  # 1 hour
    
    def get_user_pdf_count(self, user: User, date=None) -> int:
        """Get cached user PDF count"""
        
        if date is None:
            date = timezone.now().date()
        
        cache_key = f"user_pdf_count_{user.id}_{date}"
        count = self.cache.get(cache_key)
        
        if count is None:
            count = PDFDocument.objects.user_daily_count(user, date)
            self.cache.set(cache_key, count, self.cache_timeout)
        
        return count
    
    def invalidate_user_pdf_count(self, user: User, date=None):
        """Invalidate user PDF count cache"""
        
        if date is None:
            date = timezone.now().date()
        
        cache_key = f"user_pdf_count_{user.id}_{date}"
        self.cache.delete(cache_key)
    
    def get_template_stats(self, template: PDFTemplate) -> Dict[str, Any]:
        """Get cached template statistics"""
        
        cache_key = f"template_stats_{template.id}"
        stats = self.cache.get(cache_key)
        
        if stats is None:
            stats = {
                'usage_count': template.usage_count,
                'recent_usage': PDFDocument.objects.filter(
                    template=template,
                    created_at__gte=timezone.now() - timedelta(days=30)
                ).count(),
                'success_rate': self._calculate_template_success_rate(template),
            }
            self.cache.set(cache_key, stats, self.cache_timeout)
        
        return stats
    
    def _calculate_template_success_rate(self, template: PDFTemplate) -> float:
        """Calculate template success rate"""
        
        total_docs = PDFDocument.objects.filter(template=template).count()
        if total_docs == 0:
            return 100.0
        
        successful_docs = PDFDocument.objects.filter(
            template=template,
            status='completed'
        ).count()
        
        return (successful_docs / total_docs) * 100
    
    def invalidate_template_stats(self, template: PDFTemplate):
        """Invalidate template statistics cache"""
        
        cache_key = f"template_stats_{template.id}"
        self.cache.delete(cache_key)


class PDFSecurityService:
    """Service for PDF security and access control"""
    
    @staticmethod
    def generate_download_token(pdf_doc: PDFDocument) -> str:
        """Generate secure download token"""
        
        import secrets
        from django.utils import timezone
        
        # Generate secure token
        token = secrets.token_urlsafe(32)
        
        # Store token with expiration (you might want to create a model for this)
        # For now, we'll use a simple approach with cache
        from django.core.cache import cache
        
        cache_key = f"pdf_download_token_{token}"
        cache.set(cache_key, {
            'pdf_id': str(pdf_doc.id),
            'user_id': str(pdf_doc.user.id),
            'expires_at': timezone.now() + timedelta(hours=1)
        }, timeout=3600)  # 1 hour
        
        return token
    
    @staticmethod
    def validate_download_token(token: str) -> Optional[PDFDocument]:
        """Validate download token and return PDF document"""
        
        from django.core.cache import cache
        
        cache_key = f"pdf_download_token_{token}"
        token_data = cache.get(cache_key)
        
        if not token_data:
            return None
        
        # Check expiration
        if timezone.now() > token_data['expires_at']:
            cache.delete(cache_key)
            return None
        
        try:
            pdf_doc = PDFDocument.objects.get(id=token_data['pdf_id'])
            return pdf_doc
        except PDFDocument.DoesNotExist:
            cache.delete(cache_key)
            return None
    
    @staticmethod
    def check_rate_limit(user: User, action: str = 'download') -> bool:
        """Check if user has exceeded rate limits"""
        
        from django.core.cache import cache
        
        # Different limits for different actions
        limits = {
            'download': 100,  # 100 downloads per hour
            'generate': 20,   # 20 generations per hour
            'share': 50,      # 50 shares per hour
        }
        
        limit = limits.get(action, 10)
        cache_key = f"rate_limit_{user.id}_{action}"
        
        current_count = cache.get(cache_key, 0)
        
        if current_count >= limit:
            return False
        
        # Increment counter
        cache.set(cache_key, current_count + 1, timeout=3600)  # 1 hour
        return True
    
    @staticmethod
    def log_pdf_access(pdf_doc: PDFDocument, user: User, action: str, ip_address: str = None):
        """Log PDF access for security auditing"""
        
        # You might want to create a PDFAccessLog model for this
        logger.info(f"PDF Access - User: {user.email}, PDF: {pdf_doc.id}, Action: {action}, IP: {ip_address}")
        
        # For now, we'll just log it. In production, you'd want to store this in a database
        # for proper auditing and compliance


class PDFBackupService:
    """Service for backing up PDF files"""
    
    def __init__(self):
        self.backup_enabled = getattr(settings, 'PDF_BACKUP_ENABLED', False)
        self.backup_storage = getattr(settings, 'PDF_BACKUP_STORAGE', None)
    
    def backup_pdf(self, pdf_doc: PDFDocument) -> bool:
        """Backup PDF file to remote storage"""
        
        if not self.backup_enabled or not pdf_doc.file_path:
            return False
        
        try:
            # This is a placeholder for cloud storage backup
            # You would implement actual backup logic here using services like:
            # - AWS S3
            # - Google Cloud Storage
            # - Azure Blob Storage
            
            logger.info(f"PDF backup would be performed for {pdf_doc.id}")
            return True
            
        except Exception as e:
            logger.error(f"PDF backup failed for {pdf_doc.id}: {str(e)}")
            return False
    
    def restore_pdf(self, pdf_doc: PDFDocument) -> bool:
        """Restore PDF file from backup"""
        
        if not self.backup_enabled:
            return False
        
        try:
            # Placeholder for restore logic
            logger.info(f"PDF restore would be performed for {pdf_doc.id}")
            return True
            
        except Exception as e:
            logger.error(f"PDF restore failed for {pdf_doc.id}: {str(e)}")
            return False


# Additional utility functions
def get_pdf_mime_type(file_path: str) -> str:
    """Get MIME type for PDF file"""
    return 'application/pdf'


def calculate_pdf_hash(file_path: str) -> str:
    """Calculate hash for PDF file integrity checking"""
    
    import hashlib
    
    hash_sha256 = hashlib.sha256()
    
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        
        return hash_sha256.hexdigest()
        
    except Exception as e:
        logger.error(f"Failed to calculate PDF hash: {str(e)}")
        return ""


def sanitize_pdf_filename(filename: str) -> str:
    """Sanitize filename for PDF files"""
    
    import re
    
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    
    # Limit length
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:251] + ext
    
    # Ensure PDF extension
    if not filename.lower().endswith('.pdf'):
        filename += '.pdf'
    
    return filename