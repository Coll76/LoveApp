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
import qrcode
from io import BytesIO
import base64
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Any, Optional, Tuple, List
import PyPDF2
import hashlib
import json
from celery import shared_task
import subprocess
import tempfile
import asyncio
from pathlib import Path
from django.db import models
# Try to import Playwright, fallback to wkhtmltopdf if not available
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

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
    """Main service for PDF generation with multiple backend support"""
    
    def __init__(self):
        self.base_media_path = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        self._ensure_directories()
        
        # Determine which PDF generation method to use
        self.pdf_method = self._detect_pdf_method()
        logger.info(f"Using PDF generation method: {self.pdf_method}")
    
    def _detect_pdf_method(self) -> str:
        """Detect which PDF generation method is available"""
        
        # Check for Playwright
        if PLAYWRIGHT_AVAILABLE:
            try:
                # Test if Playwright browsers are installed
                import asyncio
                async def test_playwright():
                    async with async_playwright() as p:
                        browser = await p.chromium.launch()
                        await browser.close()
                        return True
                
                # Run test in new event loop to avoid conflicts
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(test_playwright())
                    return "playwright"
                except Exception:
                    pass
                finally:
                    loop.close()
            except Exception:
                pass
        
        # Check for wkhtmltopdf
        if self._check_wkhtmltopdf():
            return "wkhtmltopdf"
        
        # Fallback to basic HTML rendering
        logger.warning("No PDF generation backend available, using basic HTML output")
        return "html"
    
    def _check_wkhtmltopdf(self) -> bool:
        """Check if wkhtmltopdf is available"""
        try:
            result = subprocess.run(
                ['wkhtmltopdf', '--version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
    
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
            
            # Generate PDF from HTML using appropriate method
            if self.pdf_method == "playwright":
                pdf_path = self._html_to_pdf_playwright(html_content, pdf_doc)
            elif self.pdf_method == "wkhtmltopdf":
                pdf_path = self._html_to_pdf_wkhtmltopdf(html_content, pdf_doc)
            else:
                # Fallback to HTML file (not ideal but works)
                pdf_path = self._html_to_file(html_content, pdf_doc)
            
            # Get file info
            file_size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else None
            page_count = self._get_pdf_page_count(pdf_path) if pdf_path.endswith('.pdf') else 1
            
            # Update document record
            pdf_doc.mark_as_completed(pdf_path, file_size, page_count)
            
            # Increment template usage
            if pdf_doc.template:
                pdf_doc.template.increment_usage()
            
        except Exception as e:
            logger.error(f"PDF file generation failed for {pdf_doc.id}: {str(e)}")
            pdf_doc.mark_as_failed(str(e))
            raise
    
    def _html_to_pdf_playwright(self, html_content: str, pdf_doc: PDFDocument) -> str:
        """Convert HTML to PDF using Playwright"""
        
        async def generate_pdf():
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                
                # Set content
                await page.set_content(html_content, wait_until='networkidle')
                
                # Generate PDF
                pdf_path = os.path.join(
                    self.base_media_path, 
                    'completed', 
                    pdf_doc.filename
                )
                
                await page.pdf(
                    path=pdf_path,
                    format='A4',
                    margin={
                        'top': '2cm',
                        'right': '2cm',
                        'bottom': '2cm',
                        'left': '2cm'
                    },
                    print_background=True,
                    display_header_footer=True,
                    header_template='<div style="font-size:10px; text-align:center; width:100%;">' +
                                  f'{pdf_doc.title}</div>',
                    footer_template='<div style="font-size:10px; text-align:center; width:100%;">' +
                                  'Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>'
                )
                
                await browser.close()
                return pdf_path
        
        # Run async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(generate_pdf())
        finally:
            loop.close()
    
    def _html_to_pdf_wkhtmltopdf(self, html_content: str, pdf_doc: PDFDocument) -> str:
        """Convert HTML to PDF using wkhtmltopdf"""
        
        # Create temporary HTML file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as temp_html:
            temp_html.write(html_content)
            temp_html_path = temp_html.name
        
        try:
            # Create PDF path
            pdf_path = os.path.join(
                self.base_media_path, 
                'completed', 
                pdf_doc.filename
            )
            
            # Build wkhtmltopdf command
            cmd = [
                'wkhtmltopdf',
                '--page-size', 'A4',
                '--margin-top', '2cm',
                '--margin-right', '2cm',
                '--margin-bottom', '2cm',
                '--margin-left', '2cm',
                '--encoding', 'UTF-8',
                '--enable-local-file-access',
                '--print-media-type',
                '--header-center', pdf_doc.title,
                '--header-font-size', '10',
                '--footer-center', 'Page [page] of [toPage]',
                '--footer-font-size', '10',
                temp_html_path,
                pdf_path
            ]
            
            # Execute command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60  # 60 second timeout
            )
            
            if result.returncode != 0:
                raise PDFGenerationError(f"wkhtmltopdf failed: {result.stderr}")
            
            return pdf_path
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_html_path)
            except OSError:
                pass
    
    def _html_to_file(self, html_content: str, pdf_doc: PDFDocument) -> str:
        """Fallback: Save as HTML file when PDF generation is not available"""
        
        # Change filename to HTML
        html_filename = pdf_doc.filename.replace('.pdf', '.html')
        html_path = os.path.join(
            self.base_media_path, 
            'completed', 
            html_filename
        )
        
        # Add some basic styling for better presentation
        styled_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{pdf_doc.title}</title>
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                .header {{ text-align: center; margin-bottom: 30px; border-bottom: 2px solid #007bff; padding-bottom: 20px; }}
                .title {{ font-size: 24px; font-weight: bold; color: #007bff; margin-bottom: 10px; }}
                .section {{ margin-bottom: 20px; }}
                .section-title {{ font-size: 16px; font-weight: bold; color: #007bff; margin-bottom: 10px; }}
                @media print {{ body {{ margin: 0; }} }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
        
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(styled_html)
        
        # Update filename in document record
        pdf_doc.filename = html_filename
        pdf_doc.save(update_fields=['filename'])
        
        return html_path
    
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
            'font_family': customization.font_family if customization else 'Arial, sans-serif',
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
            # Return a basic template as fallback
            return self._get_fallback_template(context)
    
    def _get_fallback_template(self, context: Dict[str, Any]) -> str:
        """Get fallback HTML template when rendering fails"""
        
        return f"""
        <div class="header">
            <div class="title">{context.get('title', 'Date Plan')}</div>
            <div>Generated on {context.get('generation_date', timezone.now()).strftime('%B %d, %Y')}</div>
        </div>
        
        <div class="section">
            <div class="section-title">Description</div>
            <p>{context.get('description', 'No description available')}</p>
        </div>
        
        <div class="section">
            <div class="section-title">Detailed Plan</div>
            <p>{context.get('detailed_plan', 'No detailed plan available')}</p>
        </div>
        
        <div class="section">
            <div class="section-title">Estimated Cost</div>
            <p>{context.get('estimated_cost', 'Not specified')}</p>
        </div>
        
        <div class="section">
            <div class="section-title">Duration</div>
            <p>{context.get('duration', 'Not specified')}</p>
        </div>
        
        {f'<div class="section"><div class="section-title">Location Suggestions</div><p>{context.get("location_suggestions")}</p></div>' if context.get('location_suggestions') else ''}
        
        {f'<div class="section"><div class="section-title">Preparation Tips</div><p>{context.get("preparation_tips")}</p></div>' if context.get('preparation_tips') else ''}
        
        {f'<div class="section"><div class="section-title">Alternatives</div><p>{context.get("alternatives")}</p></div>' if context.get('alternatives') else ''}
        
        {f'<div class="qr-code"><img src="{context.get("qr_code_data")}" alt="QR Code" style="width: 150px; height: 150px;"></div>' if context.get('qr_code_data') else ''}
        """
    
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

# Keep all the other service classes unchanged
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


# Installation helper functions
def install_playwright_browsers():
    """Install Playwright browsers"""
    try:
        subprocess.run(['playwright', 'install', 'chromium'], check=True)
        logger.info("Playwright browsers installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to install Playwright browsers: {e}")
        return False
    except FileNotFoundError:
        logger.error("Playwright not found. Install with: pip install playwright")
        return False


def check_pdf_dependencies():
    """Check and report PDF generation dependencies"""
    
    status = {
        'playwright': False,
        'wkhtmltopdf': False,
        'recommendations': []
    }
    
    # Check Playwright
    if PLAYWRIGHT_AVAILABLE:
        try:
            import asyncio
            async def test_playwright():
                async with async_playwright() as p:
                    browser = await p.chromium.launch()
                    await browser.close()
                    return True
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(test_playwright())
                status['playwright'] = True
            except Exception:
                status['recommendations'].append("Install Playwright browsers: playwright install chromium")
            finally:
                loop.close()
        except Exception:
            status['recommendations'].append("Install Playwright: pip install playwright")
    else:
        status['recommendations'].append("Install Playwright: pip install playwright")
    
    # Check wkhtmltopdf
    try:
        result = subprocess.run(['wkhtmltopdf', '--version'], capture_output=True, timeout=10)
        if result.returncode == 0:
            status['wkhtmltopdf'] = True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        status['recommendations'].append("Install wkhtmltopdf: https://wkhtmltopdf.org/downloads.html")
    
    return status