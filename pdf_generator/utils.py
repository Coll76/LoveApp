# apps/pdf_generator/utils.py
import hashlib
import logging
import os
import secrets
import mimetypes
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union, List, Tuple
from urllib.parse import urlparse
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.http import HttpRequest
from django.utils import timezone
from django.utils.text import slugify
from django.utils.html import strip_tags
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.urls import reverse

from PIL import Image, ImageDraw, ImageFont
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
from qrcode.image.styles.colormasks import SquareGradiantColorMask

logger = logging.getLogger(__name__)


def get_client_ip(request: HttpRequest) -> str:
    """
    Get client IP address from request, considering proxy headers
    
    Args:
        request: Django HttpRequest object
        
    Returns:
        str: Client IP address
    """
    # Check for IP in forwarded headers (for load balancers/proxies)
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        # Take the first IP in the chain
        ip = x_forwarded_for.split(',')[0].strip()
        if _is_valid_ip(ip):
            return ip
    
    # Check for real IP header (common in some proxy setups)
    x_real_ip = request.META.get('HTTP_X_REAL_IP')
    if x_real_ip and _is_valid_ip(x_real_ip):
        return x_real_ip
    
    # Fall back to REMOTE_ADDR
    remote_addr = request.META.get('REMOTE_ADDR', '')
    if _is_valid_ip(remote_addr):
        return remote_addr
    
    return '127.0.0.1'  # Default fallback


def _is_valid_ip(ip: str) -> bool:
    """
    Validate IP address format
    
    Args:
        ip: IP address string
        
    Returns:
        bool: True if valid IP format
    """
    import ipaddress
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def log_pdf_access(pdf_doc, user=None, action='view', ip_address=None, 
                   user_agent=None, referer=None, additional_data=None):
    """
    Log PDF access for analytics and security
    
    Args:
        pdf_doc: PDFDocument instance
        user: User instance (can be None for anonymous access)
        action: Type of access ('view', 'download', 'share', 'public_download')
        ip_address: Client IP address
        user_agent: User agent string
        referer: Referer URL
        additional_data: Dict of additional data to log
    """
    try:
        from .models import PDFAccessLog  # Import here to avoid circular imports
        
        access_log = PDFAccessLog.objects.create(
            pdf_document=pdf_doc,
            user=user,
            action=action,
            ip_address=ip_address or '127.0.0.1',
            user_agent=user_agent or '',
            referer=referer or '',
            additional_data=additional_data or {},
            timestamp=timezone.now()
        )
        
        # Update document access statistics
        if action == 'download':
            pdf_doc.last_downloaded_at = timezone.now()
        elif action in ['view', 'public_download']:
            pdf_doc.last_accessed_at = timezone.now()
        
        pdf_doc.save(update_fields=['last_accessed_at', 'last_downloaded_at'])
        
        logger.info(f"PDF access logged: {action} on document {pdf_doc.id} by {user.id if user else 'anonymous'}")
        
    except Exception as e:
        # Don't let logging failures break the main functionality
        logger.error(f"Failed to log PDF access: {str(e)}")


def generate_secure_token(length: int = 32) -> str:
    """
    Generate cryptographically secure random token
    
    Args:
        length: Token length in characters
        
    Returns:
        str: Secure random token
    """
    return secrets.token_urlsafe(length)


def generate_pdf_filename(title: str, user_id: int, timestamp: datetime = None) -> str:
    """
    Generate standardized PDF filename
    
    Args:
        title: PDF title
        user_id: User ID
        timestamp: Creation timestamp (defaults to now)
        
    Returns:
        str: Sanitized filename
    """
    if timestamp is None:
        timestamp = timezone.now()
    
    # Sanitize title
    safe_title = slugify(title)[:50]  # Limit length
    
    # Add timestamp and user ID for uniqueness
    timestamp_str = timestamp.strftime('%Y%m%d_%H%M%S')
    
    # Generate short hash for additional uniqueness
    hash_input = f"{title}_{user_id}_{timestamp.isoformat()}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    
    filename = f"{safe_title}_{timestamp_str}_{short_hash}.pdf"
    
    # Ensure filename is not too long (max 255 chars for most filesystems)
    if len(filename) > 200:
        filename = f"{safe_title[:30]}_{timestamp_str}_{short_hash}.pdf"
    
    return filename


def get_pdf_storage_path(user_id: int, filename: str) -> str:
    """
    Generate storage path for PDF files
    
    Args:
        user_id: User ID
        filename: PDF filename
        
    Returns:
        str: Storage path
    """
    # Organize by user ID and date for better file organization
    now = timezone.now()
    year = now.strftime('%Y')
    month = now.strftime('%m')
    
    # Hash user ID to create subdirectories (for better performance with many files)
    user_hash = hashlib.md5(str(user_id).encode()).hexdigest()[:2]
    
    return f"pdfs/{year}/{month}/{user_hash}/{user_id}/{filename}"


def validate_pdf_options(options: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate PDF generation options
    
    Args:
        options: Dictionary of PDF options
        
    Returns:
        Tuple[bool, List[str]]: (is_valid, list_of_errors)
    """
    errors = []
    
    # Validate page size
    valid_page_sizes = ['A4', 'A3', 'A5', 'Letter', 'Legal']
    page_size = options.get('page_size', 'A4')
    if page_size not in valid_page_sizes:
        errors.append(f"Invalid page size: {page_size}. Must be one of {valid_page_sizes}")
    
    # Validate orientation
    valid_orientations = ['portrait', 'landscape']
    orientation = options.get('orientation', 'portrait')
    if orientation not in valid_orientations:
        errors.append(f"Invalid orientation: {orientation}. Must be one of {valid_orientations}")
    
    # Validate margins
    margins = options.get('margins', {})
    if margins:
        for margin_type in ['top', 'bottom', 'left', 'right']:
            if margin_type in margins:
                try:
                    margin_value = float(margins[margin_type])
                    if margin_value < 0 or margin_value > 5:  # 5cm max margin
                        errors.append(f"Invalid {margin_type} margin: {margin_value}. Must be between 0 and 5")
                except (ValueError, TypeError):
                    errors.append(f"Invalid {margin_type} margin: must be a number")
    
    # Validate colors (hex format)
    color_fields = ['primary_color', 'secondary_color', 'accent_color']
    for field in color_fields:
        if field in options:
            color = options[field]
            if not _is_valid_hex_color(color):
                errors.append(f"Invalid {field}: {color}. Must be a valid hex color (e.g., #FF0000)")
    
    # Validate font size
    font_size = options.get('font_size')
    if font_size is not None:
        try:
            font_size = int(font_size)
            if font_size < 8 or font_size > 72:
                errors.append("Font size must be between 8 and 72")
        except (ValueError, TypeError):
            errors.append("Font size must be a number")
    
    # Validate boolean options
    boolean_fields = [
        'include_cover_page', 'include_table_of_contents', 'include_footer',
        'include_page_numbers', 'include_watermark', 'include_qr_code'
    ]
    for field in boolean_fields:
        if field in options and not isinstance(options[field], bool):
            errors.append(f"{field} must be a boolean value")
    
    return len(errors) == 0, errors


def _is_valid_hex_color(color: str) -> bool:
    """
    Validate hex color format
    
    Args:
        color: Color string
        
    Returns:
        bool: True if valid hex color
    """
    if not isinstance(color, str):
        return False
    
    # Remove # if present
    color = color.lstrip('#')
    
    # Check if it's 3 or 6 characters and all hex
    if len(color) in [3, 6]:
        try:
            int(color, 16)
            return True
        except ValueError:
            return False
    
    return False


def create_qr_code(data: str, size: int = 200, logo_path: str = None) -> bytes:
    """
    Generate QR code image
    
    Args:
        data: Data to encode in QR code
        size: QR code size in pixels
        logo_path: Optional path to logo to embed in center
        
    Returns:
        bytes: QR code image as bytes
    """
    try:
        # Create QR code instance
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,  # High error correction for logo
            box_size=10,
            border=4,
        )
        
        qr.add_data(data)
        qr.make(fit=True)
        
        # Create styled QR code image
        img = qr.make_image(
            image_factory=StyledPilImage,
            module_drawer=RoundedModuleDrawer(),
            color_mask=SquareGradiantColorMask()
        )
        
        # Resize to desired size
        img = img.resize((size, size), Image.Resampling.LANCZOS)
        
        # Add logo if provided
        if logo_path and os.path.exists(logo_path):
            try:
                logo = Image.open(logo_path)
                logo_size = int(size * 0.2)  # Logo should be 20% of QR code size
                logo = logo.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
                
                # Calculate position to center logo
                logo_pos = ((size - logo_size) // 2, (size - logo_size) // 2)
                
                # Create a white background for the logo
                logo_bg = Image.new('RGB', (logo_size + 10, logo_size + 10), 'white')
                logo_bg.paste(logo, (5, 5))
                
                # Paste logo with background onto QR code
                bg_pos = ((size - logo_size - 10) // 2, (size - logo_size - 10) // 2)
                img.paste(logo_bg, bg_pos)
                
            except Exception as e:
                logger.warning(f"Failed to add logo to QR code: {str(e)}")
        
        # Convert to bytes
        from io import BytesIO
        img_buffer = BytesIO()
        img.save(img_buffer, format='PNG')
        return img_buffer.getvalue()
        
    except Exception as e:
        logger.error(f"QR code generation failed: {str(e)}")
        raise


def create_watermark_image(text: str, width: int = 300, height: int = 100, 
                          opacity: int = 128) -> bytes:
    """
    Create watermark image
    
    Args:
        text: Watermark text
        width: Image width
        height: Image height
        opacity: Opacity level (0-255)
        
    Returns:
        bytes: Watermark image as bytes
    """
    try:
        # Create transparent image
        img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        
        # Try to load a good font
        try:
            font = ImageFont.truetype("arial.ttf", 20)
        except OSError:
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            except OSError:
                font = ImageFont.load_default()
        
        # Calculate text position (centered)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) // 2
        y = (height - text_height) // 2
        
        # Draw text with specified opacity
        draw.text((x, y), text, font=font, fill=(128, 128, 128, opacity))
        
        # Convert to bytes
        from io import BytesIO
        img_buffer = BytesIO()
        img.save(img_buffer, format='PNG')
        return img_buffer.getvalue()
        
    except Exception as e:
        logger.error(f"Watermark creation failed: {str(e)}")
        raise


def sanitize_html_content(html_content: str) -> str:
    """
    Sanitize HTML content for PDF generation
    
    Args:
        html_content: Raw HTML content
        
    Returns:
        str: Sanitized HTML content
    """
    try:
        import bleach
        from bleach.css_sanitizer import CSSSanitizer
        
        # Define allowed tags and attributes for PDF generation
        allowed_tags = [
            'p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'strong', 'em', 'b', 'i', 'u', 'br', 'hr',
            'ul', 'ol', 'li', 'table', 'tr', 'td', 'th', 'thead', 'tbody',
            'img', 'a', 'blockquote', 'pre', 'code'
        ]
        
        allowed_attributes = {
            '*': ['class', 'style', 'id'],
            'img': ['src', 'alt', 'width', 'height'],
            'a': ['href', 'title'],
            'table': ['border', 'cellpadding', 'cellspacing'],
        }
        
        # CSS sanitizer for style attributes
        css_sanitizer = CSSSanitizer(
            allowed_css_properties=[
                'color', 'background-color', 'font-size', 'font-weight',
                'text-align', 'margin', 'padding', 'border', 'width', 'height'
            ]
        )
        
        # Clean the HTML
        clean_html = bleach.clean(
            html_content,
            tags=allowed_tags,
            attributes=allowed_attributes,
            css_sanitizer=css_sanitizer,
            strip=True
        )
        
        return clean_html
        
    except ImportError:
        # Fallback if bleach is not installed
        logger.warning("bleach not installed, using basic HTML sanitization")
        return strip_tags(html_content)
    except Exception as e:
        logger.error(f"HTML sanitization failed: {str(e)}")
        return strip_tags(html_content)


def estimate_pdf_generation_time(idea_content_length: int, template_complexity: str = 'medium',
                                include_images: bool = False) -> int:
    """
    Estimate PDF generation time in seconds
    
    Args:
        idea_content_length: Length of idea content
        template_complexity: Template complexity ('simple', 'medium', 'complex')
        include_images: Whether PDF includes images
        
    Returns:
        int: Estimated generation time in seconds
    """
    base_time = 5  # Base 5 seconds
    
    # Add time based on content length
    content_time = min(idea_content_length // 1000, 30)  # Max 30 seconds for content
    
    # Add time based on template complexity
    complexity_multiplier = {
        'simple': 1.0,
        'medium': 1.5,
        'complex': 2.0
    }
    template_time = base_time * complexity_multiplier.get(template_complexity, 1.5)
    
    # Add time for images
    image_time = 10 if include_images else 0
    
    total_time = int(base_time + content_time + template_time + image_time)
    
    return max(total_time, 5)  # Minimum 5 seconds


def cleanup_old_pdf_files(days_old: int = 30) -> Dict[str, int]:
    """
    Clean up old PDF files to free storage space
    
    Args:
        days_old: Delete files older than this many days
        
    Returns:
        Dict[str, int]: Cleanup statistics
    """
    try:
        from .models import PDFDocument
        
        cutoff_date = timezone.now() - timedelta(days=days_old)
        
        # Find old PDF documents
        old_documents = PDFDocument.objects.filter(
            created_at__lt=cutoff_date,
            is_deleted=True  # Only clean up soft-deleted documents
        )
        
        stats = {
            'files_processed': 0,
            'files_deleted': 0,
            'space_freed': 0,
            'errors': 0
        }
        
        for doc in old_documents:
            stats['files_processed'] += 1
            
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    file_size = os.path.getsize(doc.file_path)
                    os.remove(doc.file_path)
                    stats['files_deleted'] += 1
                    stats['space_freed'] += file_size
                    
                    # Clear file path from database
                    doc.file_path = ''
                    doc.save(update_fields=['file_path'])
                    
                except OSError as e:
                    logger.error(f"Failed to delete file {doc.file_path}: {str(e)}")
                    stats['errors'] += 1
        
        logger.info(f"PDF cleanup completed: {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"PDF cleanup failed: {str(e)}")
        return {'error': str(e)}


def send_pdf_notification_email(user, pdf_document, notification_type: str):
    """
    Send email notification about PDF status
    
    Args:
        user: User instance
        pdf_document: PDFDocument instance
        notification_type: Type of notification ('completed', 'failed', 'shared')
    """
    try:
        if not user.email or not user.preferences.get('email_notifications', True):
            return
        
        # Email templates and subjects
        templates = {
            'completed': {
                'subject': 'Your PDF is Ready!',
                'template': 'pdf_generator/emails/pdf_completed.html'
            },
            'failed': {
                'subject': 'PDF Generation Failed',
                'template': 'pdf_generator/emails/pdf_failed.html'
            },
            'shared': {
                'subject': 'PDF Shared Successfully',
                'template': 'pdf_generator/emails/pdf_shared.html'
            }
        }
        
        if notification_type not in templates:
            logger.warning(f"Unknown notification type: {notification_type}")
            return
        
        template_info = templates[notification_type]
        
        # Prepare context
        context = {
            'user': user,
            'pdf_document': pdf_document,
            'site_url': settings.FRONTEND_URL,
            'download_url': f"{settings.FRONTEND_URL}/pdf/{pdf_document.id}/download",
        }
        
        # Add notification-specific context
        if notification_type == 'shared':
            context['share_url'] = f"{settings.FRONTEND_URL}/shared/pdf/{pdf_document.public_access_token}"
        
        # Render email content
        html_content = render_to_string(template_info['template'], context)
        
        # Send email
        send_mail(
            subject=template_info['subject'],
            message='',  # Plain text version (optional)
            html_message=html_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False
        )
        
        logger.info(f"PDF notification email sent to {user.email}: {notification_type}")
        
    except Exception as e:
        logger.error(f"Failed to send PDF notification email: {str(e)}")


def get_pdf_mime_type(file_path: str) -> str:
    """
    Get MIME type for PDF file
    
    Args:
        file_path: Path to PDF file
        
    Returns:
        str: MIME type
    """
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type if mime_type == 'application/pdf' else 'application/pdf'


def cache_pdf_metadata(pdf_document, duration: int = 3600):
    """
    Cache PDF metadata for faster access
    
    Args:
        pdf_document: PDFDocument instance
        duration: Cache duration in seconds
    """
    try:
        cache_key = f"pdf_metadata_{pdf_document.id}"
        metadata = {
            'id': pdf_document.id,
            'title': pdf_document.title,
            'status': pdf_document.status,
            'file_size': pdf_document.file_size,
            'created_at': pdf_document.created_at.isoformat(),
            'download_count': pdf_document.download_count,
            'is_public': pdf_document.is_public,
        }
        
        cache.set(cache_key, metadata, duration)
        
    except Exception as e:
        logger.error(f"Failed to cache PDF metadata: {str(e)}")


def get_cached_pdf_metadata(pdf_id: int) -> Optional[Dict[str, Any]]:
    """
    Retrieve cached PDF metadata
    
    Args:
        pdf_id: PDF document ID
        
    Returns:
        Optional[Dict[str, Any]]: Cached metadata or None
    """
    try:
        cache_key = f"pdf_metadata_{pdf_id}"
        return cache.get(cache_key)
    except Exception as e:
        logger.error(f"Failed to retrieve cached PDF metadata: {str(e)}")
        return None


def validate_file_size(file_size: int, max_size_mb: int = 50) -> bool:
    """
    Validate file size against limits
    
    Args:
        file_size: File size in bytes
        max_size_mb: Maximum allowed size in MB
        
    Returns:
        bool: True if file size is acceptable
    """
    max_size_bytes = max_size_mb * 1024 * 1024
    return file_size <= max_size_bytes


def generate_pdf_hash(file_path: str) -> str:
    """
    Generate SHA-256 hash of PDF file for integrity checking
    
    Args:
        file_path: Path to PDF file
        
    Returns:
        str: SHA-256 hash
    """
    try:
        hash_sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    except Exception as e:
        logger.error(f"Failed to generate PDF hash: {str(e)}")
        raise


# Rate limiting utilities
def get_user_rate_limit_key(user_id: int, action: str) -> str:
    """Generate cache key for user rate limiting"""
    return f"rate_limit_{action}_{user_id}"


def check_rate_limit(user_id: int, action: str, limit: int, window_seconds: int) -> bool:
    """
    Check if user has exceeded rate limit
    
    Args:
        user_id: User ID
        action: Action being rate limited
        limit: Maximum number of actions allowed
        window_seconds: Time window in seconds
        
    Returns:
        bool: True if within rate limit, False if exceeded
    """
    try:
        cache_key = get_user_rate_limit_key(user_id, action)
        current_count = cache.get(cache_key, 0)
        
        if current_count >= limit:
            return False
        
        # Increment counter
        cache.set(cache_key, current_count + 1, window_seconds)
        return True
        
    except Exception as e:
        logger.error(f"Rate limit check failed: {str(e)}")
        return True  # Allow action on error to prevent blocking users