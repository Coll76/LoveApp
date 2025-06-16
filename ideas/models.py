# apps/ideas/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from core.models import BaseModel, SoftDeleteModel
from decimal import Decimal
import uuid
from .managers import IdeaRequestManager, GeneratedIdeaManager, IdeaTemplateManager

User = get_user_model()

class IdeaCategory(BaseModel):
    """
    Categories for different types of date ideas
    """
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)  # Icon class or emoji
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'idea_categories'
        verbose_name = 'Idea Category'
        verbose_name_plural = 'Idea Categories'
        ordering = ['sort_order', 'name']
    
    def __str__(self):
        return self.name

class IdeaTemplate(BaseModel):
    """
    Reusable templates for idea generation prompts
    """
    TEMPLATE_TYPE_CHOICES = [
        ('romantic', 'Romantic'),
        ('adventurous', 'Adventurous'),
        ('casual', 'Casual'),
        ('creative', 'Creative'),
        ('budget_friendly', 'Budget Friendly'),
        ('luxurious', 'Luxurious'),
        ('indoor', 'Indoor'),
        ('outdoor', 'Outdoor'),
        ('cultural', 'Cultural'),
        ('active', 'Active'),
        ('relaxed', 'Relaxed'),
    ]
    
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    template_type = models.CharField(max_length=50, choices=TEMPLATE_TYPE_CHOICES)
    category = models.ForeignKey(IdeaCategory, on_delete=models.CASCADE, related_name='templates')
    prompt_template = models.TextField(help_text="Use {variables} for dynamic content")
    description = models.TextField(blank=True)
    is_premium = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    usage_count = models.IntegerField(default=0)
    average_rating = models.DecimalField(
        max_digits=3, 
        decimal_places=2, 
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('5.00'))]
    )
    
    objects = IdeaTemplateManager()
    
    class Meta:
        db_table = 'idea_templates'
        verbose_name = 'Idea Template'
        verbose_name_plural = 'Idea Templates'
        indexes = [
            models.Index(fields=['template_type', 'is_active']),
            models.Index(fields=['category', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.template_type})"
    
    def increment_usage(self):
        """Increment usage count"""
        self.usage_count += 1
        self.save(update_fields=['usage_count'])

class IdeaRequest(BaseModel, SoftDeleteModel):
    """
    User's request for date ideas with their specific parameters
    """
    BUDGET_CHOICES = [
        ('low', 'Low Budget ($0-$50)'),
        ('moderate', 'Moderate Budget ($50-$150)'),
        ('high', 'High Budget ($150+)'),
        ('unlimited', 'Unlimited Budget'),
    ]
    
    LOCATION_TYPE_CHOICES = [
        ('indoor', 'Indoor'),
        ('outdoor', 'Outdoor'),
        ('mixed', 'Mixed'),
        ('any', 'Any Location'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='idea_requests')
    title = models.CharField(max_length=200, blank=True)
    
    # User input parameters
    occasion = models.CharField(max_length=200, blank=True)  # Anniversary, first date, etc.
    partner_interests = models.TextField(blank=True)  # What they like
    user_interests = models.TextField(blank=True)  # What user likes
    personality_type = models.CharField(max_length=200, blank=True)  # Introvert, extrovert, etc.
    budget = models.CharField(max_length=20, choices=BUDGET_CHOICES, default='moderate')
    location_type = models.CharField(max_length=20, choices=LOCATION_TYPE_CHOICES, default='any')
    location_city = models.CharField(max_length=100, blank=True)
    duration = models.CharField(max_length=100, blank=True)  # Half day, full day, evening
    special_requirements = models.TextField(blank=True)  # Dietary restrictions, accessibility
    custom_prompt = models.TextField(blank=True)  # User's own custom request
    
    # AI generation parameters
    ai_model = models.CharField(max_length=50, default='gpt-3.5-turbo')
    temperature = models.FloatField(default=0.7, validators=[MinValueValidator(0.0), MaxValueValidator(2.0)])
    max_tokens = models.IntegerField(default=1500)
    
    # Processing info
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.IntegerField(default=0)
    
    # Metadata
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    session_id = models.CharField(max_length=100, blank=True)
    
    objects = IdeaRequestManager()
    
    class Meta:
        db_table = 'idea_requests'
        verbose_name = 'Idea Request'
        verbose_name_plural = 'Idea Requests'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]
    
    def __str__(self):
        return f"Idea Request {self.id} by {self.user.email}"
    
    def mark_as_processing(self):
        """Mark request as processing"""
        self.status = 'processing'
        self.processing_started_at = timezone.now()
        self.save(update_fields=['status', 'processing_started_at'])
    
    def mark_as_completed(self):
        """Mark request as completed"""
        self.status = 'completed'
        self.processing_completed_at = timezone.now()
        self.save(update_fields=['status', 'processing_completed_at'])
    
    def mark_as_failed(self, error_message=''):
        """Mark request as failed"""
        self.status = 'failed'
        self.error_message = error_message
        self.retry_count += 1
        self.save(update_fields=['status', 'error_message', 'retry_count'])
    
    def can_retry(self, max_retries=3):
        """Check if request can be retried"""
        return self.status == 'failed' and self.retry_count < max_retries
    
    def get_processing_time(self):
        """Get processing time in seconds"""
        if self.processing_started_at and self.processing_completed_at:
            delta = self.processing_completed_at - self.processing_started_at
            return delta.total_seconds()
        return None

class GeneratedIdea(BaseModel):
    """
    AI-generated date ideas
    """
    request = models.ForeignKey(IdeaRequest, on_delete=models.CASCADE, related_name='generated_ideas')
    template_used = models.ForeignKey(IdeaTemplate, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Generated content
    title = models.CharField(max_length=300)
    description = models.TextField()
    detailed_plan = models.TextField(blank=True)
    estimated_cost = models.CharField(max_length=100, blank=True)
    duration = models.CharField(max_length=100, blank=True)
    location_suggestions = models.JSONField(default=list)  # List of specific locations
    preparation_tips = models.TextField(blank=True)
    alternatives = models.TextField(blank=True)  # Alternative suggestions
    
    # AI metadata
    ai_model_used = models.CharField(max_length=50)
    prompt_used = models.TextField()
    ai_response_raw = models.TextField()  # Raw AI response for debugging
    generation_tokens = models.IntegerField(null=True, blank=True)
    
    # Engagement metrics
    view_count = models.IntegerField(default=0)
    like_count = models.IntegerField(default=0)
    share_count = models.IntegerField(default=0)
    pdf_download_count = models.IntegerField(default=0)
    
    # Quality scores
    content_quality_score = models.FloatField(null=True, blank=True)  # Internal quality assessment
    user_rating = models.DecimalField(
        max_digits=3, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(Decimal('1.00')), MaxValueValidator(Decimal('5.00'))]
    )
    
    objects = GeneratedIdeaManager()
    
    class Meta:
        db_table = 'generated_ideas'
        verbose_name = 'Generated Idea'
        verbose_name_plural = 'Generated Ideas'
        indexes = [
            models.Index(fields=['request', 'created_at']),
            models.Index(fields=['user_rating']),
            models.Index(fields=['view_count']),
        ]
    
    def __str__(self):
        return f"{self.title} (Request: {self.request.id})"
    
    def increment_view_count(self):
        """Increment view count"""
        self.view_count += 1
        self.save(update_fields=['view_count'])
    
    def increment_like_count(self):
        """Increment like count"""
        self.like_count += 1
        self.save(update_fields=['like_count'])
    
    def increment_share_count(self):
        """Increment share count"""
        self.share_count += 1
        self.save(update_fields=['share_count'])
    
    def increment_pdf_download_count(self):
        """Increment PDF download count"""
        self.pdf_download_count += 1
        self.save(update_fields=['pdf_download_count'])

class IdeaFeedback(BaseModel):
    """
    User feedback and ratings for generated ideas
    """
    FEEDBACK_TYPE_CHOICES = [
        ('rating', 'Rating'),
        ('like', 'Like'),
        ('dislike', 'Dislike'),
        ('report', 'Report'),
        ('comment', 'Comment'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='idea_feedback')
    idea = models.ForeignKey(GeneratedIdea, on_delete=models.CASCADE, related_name='feedback')
    feedback_type = models.CharField(max_length=20, choices=FEEDBACK_TYPE_CHOICES)
    
    # Rating specific
    rating = models.IntegerField(
        null=True, 
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    
    # Comment specific
    comment = models.TextField(blank=True)
    
    # Report specific
    report_reason = models.CharField(max_length=200, blank=True)
    
    # Metadata
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    
    class Meta:
        db_table = 'idea_feedback'
        verbose_name = 'Idea Feedback'
        verbose_name_plural = 'Idea Feedback'
        unique_together = ['user', 'idea', 'feedback_type']  # Prevent duplicate feedback
        indexes = [
            models.Index(fields=['idea', 'feedback_type']),
            models.Index(fields=['user', 'feedback_type']),
        ]
    
    def __str__(self):
        return f"{self.feedback_type} by {self.user.email} for {self.idea.title}"

class IdeaBookmark(BaseModel):
    """
    User bookmarks for saving favorite ideas
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookmarked_ideas')
    idea = models.ForeignKey(GeneratedIdea, on_delete=models.CASCADE, related_name='bookmarks')
    notes = models.TextField(blank=True)  # User's personal notes
    
    class Meta:
        db_table = 'idea_bookmarks'
        verbose_name = 'Idea Bookmark'
        verbose_name_plural = 'Idea Bookmarks'
        unique_together = ['user', 'idea']
    
    def __str__(self):
        return f"{self.user.email} bookmarked {self.idea.title}"

class IdeaUsageStats(BaseModel):
    """
    Daily usage statistics for analytics
    """
    date = models.DateField(unique=True)
    total_requests = models.IntegerField(default=0)
    successful_generations = models.IntegerField(default=0)
    failed_generations = models.IntegerField(default=0)
    total_users = models.IntegerField(default=0)
    free_tier_requests = models.IntegerField(default=0)
    premium_requests = models.IntegerField(default=0)
    average_rating = models.DecimalField(
        max_digits=3, 
        decimal_places=2, 
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('5.00'))]
    )
    total_tokens_used = models.IntegerField(default=0)
    
    class Meta:
        db_table = 'idea_usage_stats'
        verbose_name = 'Idea Usage Stats'
        verbose_name_plural = 'Idea Usage Stats'
        ordering = ['-date']
    
    def __str__(self):
        return f"Usage stats for {self.date}"

class AIModelConfiguration(BaseModel):
    """
    Configuration for different AI models
    """
    name = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=50)  # openai, deepseek, etc.
    model_id = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    is_premium_only = models.BooleanField(default=False)
    max_tokens = models.IntegerField(default=1500)
    temperature = models.FloatField(default=0.7)
    cost_per_1k_tokens = models.DecimalField(max_digits=8, decimal_places=6)
    priority = models.IntegerField(default=1)  # Lower number = higher priority
    
    class Meta:
        db_table = 'ai_model_configurations'
        verbose_name = 'AI Model Configuration'
        verbose_name_plural = 'AI Model Configurations'
        ordering = ['priority']
    
    def __str__(self):
        return f"{self.name} ({self.provider})"