# apps/pdf_generator/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from core.models import BaseModel, SoftDeleteModel
from ideas.models import GeneratedIdea
import uuid
import os
from .managers import PDFDocumentManager, PDFTemplateManager

User = get_user_model()

class PDFTemplate(BaseModel):
    """
    PDF templates for different types of date plans
    """
    TEMPLATE_TYPE_CHOICES = [
        ('classic', 'Classic'),
        ('modern', 'Modern'),
        ('romantic', 'Romantic'),
        ('minimalist', 'Minimalist'),
        ('colorful', 'Colorful'),
        ('elegant', 'Elegant'),
    ]
    
    TEMPLATE_FORMAT_CHOICES = [
        ('A4', 'A4 Portrait'),
        ('A4_landscape', 'A4 Landscape'),
        ('letter', 'Letter Portrait'),
        ('letter_landscape', 'Letter Landscape'),
    ]
    
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    template_type = models.CharField(max_length=20, choices=TEMPLATE_TYPE_CHOICES)
    format = models.CharField(max_length=20, choices=TEMPLATE_FORMAT_CHOICES, default='A4')
    description = models.TextField(blank=True)
    html_template = models.TextField(help_text="HTML template with placeholders")
    css_styles = models.TextField(blank=True, help_text="Custom CSS styles")
    is_premium = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    preview_image = models.ImageField(upload_to='pdf_templates/previews/', null=True, blank=True)
    usage_count = models.IntegerField(default=0)
    sort_order = models.IntegerField(default=0)
    
    objects = PDFTemplateManager()
    
    class Meta:
        db_table = 'pdf_templates'
        verbose_name = 'PDF Template'
        verbose_name_plural = 'PDF Templates'
        ordering = ['sort_order', 'name']
        indexes = [
            models.Index(fields=['template_type', 'is_active']),
            models.Index(fields=['is_premium', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.template_type})"
    
    def increment_usage(self):
        """Increment usage count"""
        self.usage_count += 1
        self.save(update_fields=['usage_count'])

class PDFDocument(BaseModel, SoftDeleteModel):
    """
    Generated PDF documents
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pdf_documents')
    idea = models.ForeignKey(GeneratedIdea, on_delete=models.CASCADE, related_name='pdf_documents')
    template = models.ForeignKey(PDFTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Document details
    title = models.CharField(max_length=300)
    filename = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_size = models.BigIntegerField(null=True, blank=True)  # Size in bytes
    page_count = models.IntegerField(null=True, blank=True)
    
    # Generation details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    generation_started_at = models.DateTimeField(null=True, blank=True)
    generation_completed_at = models.DateTimeField(null=True, blank=True)
    generation_time = models.FloatField(null=True, blank=True)  # Time in seconds
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    
    # Customization options
    custom_options = models.JSONField(default=dict)  # Custom styling, colors, etc.
    include_qr_code = models.BooleanField(default=True)
    include_watermark = models.BooleanField(default=False)
    
    # Access and analytics
    download_count = models.IntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)
    share_count = models.IntegerField(default=0)
    is_public = models.BooleanField(default=False)
    public_access_token = models.CharField(max_length=100, blank=True, unique=True)
    
    # Metadata
    metadata = models.JSONField(default=dict)
    
    objects = PDFDocumentManager()
    
    class Meta:
        db_table = 'pdf_documents'
        verbose_name = 'PDF Document'
        verbose_name_plural = 'PDF Documents'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['idea', 'created_at']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['public_access_token']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.user.email}"
    
    def get_file_url(self):
        """Get the URL for downloading the PDF"""
        if self.file_path and os.path.exists(self.file_path):
            # Return the media URL path
            return f"/media/pdfs/{self.filename}"
        return None
    
    def mark_as_processing(self):
        """Mark document as processing"""
        self.status = 'processing'
        self.generation_started_at = timezone.now()
        self.save(update_fields=['status', 'generation_started_at'])
    
    def mark_as_completed(self, file_path, file_size=None, page_count=None):
        """Mark document as completed"""
        self.status = 'completed'
        self.generation_completed_at = timezone.now()
        self.file_path = file_path
        self.file_size = file_size
        self.page_count = page_count
        
        if self.generation_started_at:
            delta = self.generation_completed_at - self.generation_started_at
            self.generation_time = delta.total_seconds()
        
        self.save(update_fields=[
            'status', 'generation_completed_at', 'file_path', 
            'file_size', 'page_count', 'generation_time'
        ])
    
    def mark_as_failed(self, error_message=''):
        """Mark document generation as failed"""
        self.status = 'failed'
        self.error_message = error_message
        self.retry_count += 1
        self.save(update_fields=['status', 'error_message', 'retry_count'])
    
    def can_retry(self, max_retries=3):
        """Check if generation can be retried"""
        return self.status == 'failed' and self.retry_count < max_retries
    
    def increment_download_count(self):
        """Increment download count"""
        self.download_count += 1
        self.last_downloaded_at = timezone.now()
        self.save(update_fields=['download_count', 'last_downloaded_at'])
    
    def increment_share_count(self):
        """Increment share count"""
        self.share_count += 1
        self.save(update_fields=['share_count'])
    
    def generate_public_token(self):
        """Generate public access token for sharing"""
        if not self.public_access_token:
            self.public_access_token = str(uuid.uuid4())
            self.save(update_fields=['public_access_token'])
        return self.public_access_token

class PDFGenerationQueue(BaseModel):
    """
    Queue for PDF generation tasks
    """
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    pdf_document = models.OneToOneField(PDFDocument, on_delete=models.CASCADE, related_name='queue_item')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    
    # Processing details
    assigned_worker = models.CharField(max_length=100, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    estimated_completion_time = models.DateTimeField(null=True, blank=True)
    
    # Queue position and timing
    queue_position = models.IntegerField(null=True, blank=True)
    wait_time = models.FloatField(null=True, blank=True)  # Time in queue (seconds)
    
    class Meta:
        db_table = 'pdf_generation_queue'
        verbose_name = 'PDF Generation Queue Item'
        verbose_name_plural = 'PDF Generation Queue Items'
        ordering = ['-priority', 'created_at']
        indexes = [
            models.Index(fields=['status', 'priority']),
            models.Index(fields=['user', 'status']),
        ]
    
    def __str__(self):
        return f"Queue Item: {self.pdf_document.title} - {self.status}"

class PDFUsageStats(BaseModel):
    """
    Daily PDF generation statistics
    """
    date = models.DateField(unique=True)
    total_pdfs_generated = models.IntegerField(default=0)
    successful_generations = models.IntegerField(default=0)
    failed_generations = models.IntegerField(default=0)
    total_users = models.IntegerField(default=0)
    free_tier_pdfs = models.IntegerField(default=0)
    premium_pdfs = models.IntegerField(default=0)
    total_downloads = models.IntegerField(default=0)
    total_shares = models.IntegerField(default=0)
    average_generation_time = models.FloatField(default=0.0)
    total_file_size = models.BigIntegerField(default=0)  # Total bytes
    
    class Meta:
        db_table = 'pdf_usage_stats'
        verbose_name = 'PDF Usage Stats'
        verbose_name_plural = 'PDF Usage Stats'
        ordering = ['-date']
    
    def __str__(self):
        return f"PDF stats for {self.date}"

class PDFCustomization(BaseModel):
    """
    User's custom PDF styling preferences
    """
    COLOR_SCHEME_CHOICES = [
        ('default', 'Default'),
        ('romantic', 'Romantic Pink'),
        ('elegant', 'Elegant Black'),
        ('nature', 'Nature Green'),
        ('ocean', 'Ocean Blue'),
        ('sunset', 'Sunset Orange'),
        ('custom', 'Custom Colors'),
    ]
    
    FONT_CHOICES = [
        ('default', 'Default'),
        ('serif', 'Serif'),
        ('sans_serif', 'Sans Serif'),
        ('script', 'Script'),
        ('modern', 'Modern'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='pdf_customization')
    
    # Color preferences
    color_scheme = models.CharField(max_length=20, choices=COLOR_SCHEME_CHOICES, default='default')
    primary_color = models.CharField(max_length=7, default='#007bff')  # Hex color
    secondary_color = models.CharField(max_length=7, default='#6c757d')
    accent_color = models.CharField(max_length=7, default='#28a745')
    
    # Typography preferences
    font_family = models.CharField(max_length=20, choices=FONT_CHOICES, default='default')
    font_size = models.IntegerField(default=12, validators=[MinValueValidator(8), MaxValueValidator(20)])
    
    # Layout preferences
    include_cover_page = models.BooleanField(default=True)
    include_table_of_contents = models.BooleanField(default=True)
    include_footer = models.BooleanField(default=True)
    include_page_numbers = models.BooleanField(default=True)
    
    # Branding
    custom_logo = models.ImageField(upload_to='pdf_customization/logos/', null=True, blank=True)
    watermark_text = models.CharField(max_length=100, blank=True)
    
    class Meta:
        db_table = 'pdf_customizations'
        verbose_name = 'PDF Customization'
        verbose_name_plural = 'PDF Customizations'
    
    def __str__(self):
        return f"PDF customization for {self.user.email}"