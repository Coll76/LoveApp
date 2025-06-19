# apps/advertisements/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator, URLValidator
from django.utils import timezone
from core.models import BaseModel, SoftDeleteModel
from decimal import Decimal
import uuid
from .managers import (
    AdCampaignManager, 
    AdImpressionManager, 
    AdClickManager,
    AdProviderManager
)

User = get_user_model()

class AdProvider(BaseModel):
    """
    Advertisement providers (Google AdSense, Facebook Ads, etc.)
    """
    PROVIDER_CHOICES = [
        ('google_adsense', 'Google AdSense'),
        ('facebook_ads', 'Facebook Ads'),
        ('amazon_associates', 'Amazon Associates'),
        ('custom', 'Custom Provider'),
    ]
    
    name = models.CharField(max_length=100, unique=True)
    provider_type = models.CharField(max_length=50, choices=PROVIDER_CHOICES)
    api_key = models.CharField(max_length=500, blank=True)
    publisher_id = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(default=1)  # Lower number = higher priority
    revenue_share = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=Decimal('70.00'),
        validators=[MinValueValidator(Decimal('0.00')), MaxValueValidator(Decimal('100.00'))]
    )
    configuration = models.JSONField(default=dict)  # Provider-specific settings
    
    objects = AdProviderManager()
    
    class Meta:
        db_table = 'ad_providers'
        verbose_name = 'Ad Provider'
        verbose_name_plural = 'Ad Providers'
        ordering = ['priority', 'name']
    
    def __str__(self):
        return f"{self.name} ({self.get_provider_type_display()})"

class AdCategory(BaseModel):
    """
    Advertisement categories for better targeting
    """
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'ad_categories'
        verbose_name = 'Ad Category'
        verbose_name_plural = 'Ad Categories'
        ordering = ['name']
    
    def __str__(self):
        return self.name

class AdPlacement(BaseModel):
    """
    Define where ads can be placed in the application
    """
    PLACEMENT_TYPE_CHOICES = [
        ('banner', 'Banner'),
        ('interstitial', 'Interstitial'),
        ('native', 'Native'),
        ('video', 'Video'),
        ('popup', 'Popup'),
        ('sidebar', 'Sidebar'),
    ]
    
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    placement_type = models.CharField(max_length=20, choices=PLACEMENT_TYPE_CHOICES)
    page_location = models.CharField(max_length=200)  # e.g., 'home_page', 'idea_results'
    width = models.IntegerField(null=True, blank=True)
    height = models.IntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    max_ads_per_view = models.IntegerField(default=1)
    description = models.TextField(blank=True)
    
    class Meta:
        db_table = 'ad_placements'
        verbose_name = 'Ad Placement'
        verbose_name_plural = 'Ad Placements'
    
    def __str__(self):
        return f"{self.name} ({self.placement_type})"

class AdCampaign(BaseModel, SoftDeleteModel):
    """
    Advertisement campaigns
    """
    CAMPAIGN_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('paused', 'Paused'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    
    CAMPAIGN_TYPE_CHOICES = [
        ('cpc', 'Cost Per Click'),
        ('cpm', 'Cost Per Mille'),
        ('cpa', 'Cost Per Action'),
        ('rewarded', 'Rewarded Video'),
    ]
    
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    advertiser = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ad_campaigns')
    provider = models.ForeignKey(AdProvider, on_delete=models.CASCADE)
    category = models.ForeignKey(AdCategory, on_delete=models.SET_NULL, null=True, blank=True)
    placement = models.ForeignKey(AdPlacement, on_delete=models.CASCADE)
    
    # Campaign details
    title = models.CharField(max_length=200)
    description = models.TextField()
    call_to_action = models.CharField(max_length=100, blank=True)
    target_url = models.URLField(validators=[URLValidator()])
    
    # Media
    image_url = models.URLField(blank=True)
    video_url = models.URLField(blank=True)
    
    # Campaign settings
    campaign_type = models.CharField(max_length=20, choices=CAMPAIGN_TYPE_CHOICES, default='cpc')
    status = models.CharField(max_length=20, choices=CAMPAIGN_STATUS_CHOICES, default='draft')
    budget = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    spent_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    
    # Pricing
    bid_amount = models.DecimalField(max_digits=8, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])
    
    # Scheduling
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    
    # Targeting
    target_countries = models.JSONField(default=list)
    target_age_min = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(13), MaxValueValidator(100)])
    target_age_max = models.IntegerField(null=True, blank=True, validators=[MinValueValidator(13), MaxValueValidator(100)])
    target_gender = models.CharField(max_length=20, blank=True)
    target_interests = models.JSONField(default=list)
    
    # Performance tracking
    total_impressions = models.BigIntegerField(default=0)
    total_clicks = models.BigIntegerField(default=0)
    total_conversions = models.BigIntegerField(default=0)
    
    objects = AdCampaignManager()
    
    class Meta:
        db_table = 'ad_campaigns'
        verbose_name = 'Ad Campaign'
        verbose_name_plural = 'Ad Campaigns'
        indexes = [
            models.Index(fields=['status', 'start_date']),
            models.Index(fields=['advertiser', 'status']),
            models.Index(fields=['placement', 'status']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.status}"
    
    @property
    def click_through_rate(self):
        """Calculate CTR"""
        if self.total_impressions > 0:
            return (self.total_clicks / self.total_impressions) * 100
        return 0
    
    @property
    def conversion_rate(self):
        """Calculate conversion rate"""
        if self.total_clicks > 0:
            return (self.total_conversions / self.total_clicks) * 100
        return 0
    
    @property
    def is_active(self):
        """Check if campaign is currently active"""
        now = timezone.now()
        return (
            self.status == 'active' and
            self.start_date <= now and
            (self.end_date is None or self.end_date >= now) and
            self.spent_amount < self.budget
        )
    
    def increment_impressions(self, count=1):
        """Increment impression count"""
        self.total_impressions += count
        self.save(update_fields=['total_impressions'])
    
    def increment_clicks(self, count=1):
        """Increment click count"""
        self.total_clicks += count
        self.save(update_fields=['total_clicks'])
    
    def increment_conversions(self, count=1):
        """Increment conversion count"""
        self.total_conversions += count
        self.save(update_fields=['total_conversions'])

class AdImpression(BaseModel):
    """
    Track ad impressions for analytics and billing
    """
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='impressions')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    session_id = models.CharField(max_length=100, blank=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    referrer = models.URLField(blank=True)
    page_url = models.URLField()
    
    # Targeting verification
    user_country = models.CharField(max_length=2, blank=True)
    user_age = models.IntegerField(null=True, blank=True)
    user_gender = models.CharField(max_length=20, blank=True)
    
    # Technical details
    viewport_width = models.IntegerField(null=True, blank=True)
    viewport_height = models.IntegerField(null=True, blank=True)
    device_type = models.CharField(max_length=50, blank=True)  # mobile, desktop, tablet
    
    # Billing
    cost = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0.0000'))
    
    objects = AdImpressionManager()
    
    class Meta:
        db_table = 'ad_impressions'
        verbose_name = 'Ad Impression'
        verbose_name_plural = 'Ad Impressions'
        indexes = [
            models.Index(fields=['campaign', 'created_at']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['ip_address', 'created_at']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Impression for {self.campaign.name} at {self.created_at}"

class AdClick(BaseModel):
    """
    Track ad clicks for analytics and billing
    """
    impression = models.OneToOneField(AdImpression, on_delete=models.CASCADE, related_name='click')
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='clicks')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    # Click details
    click_position_x = models.IntegerField(null=True, blank=True)
    click_position_y = models.IntegerField(null=True, blank=True)
    time_to_click = models.IntegerField(null=True, blank=True)  # Milliseconds from impression
    
    # Billing
    cost = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal('0.0000'))
    
    # Fraud detection
    is_valid = models.BooleanField(default=True)
    fraud_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    
    objects = AdClickManager()
    
    class Meta:
        db_table = 'ad_clicks'
        verbose_name = 'Ad Click'
        verbose_name_plural = 'Ad Clicks'
        indexes = [
            models.Index(fields=['campaign', 'created_at']),
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['is_valid', 'created_at']),
        ]
    
    def __str__(self):
        return f"Click for {self.campaign.name} at {self.created_at}"

class AdConversion(BaseModel):
    """
    Track ad conversions for performance measurement
    """
    CONVERSION_TYPE_CHOICES = [
        ('signup', 'User Signup'),
        ('subscription', 'Subscription Purchase'),
        ('idea_generation', 'Idea Generation'),
        ('pdf_download', 'PDF Download'),
        ('custom', 'Custom Action'),
    ]
    
    click = models.OneToOneField(AdClick, on_delete=models.CASCADE, related_name='conversion')
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='conversions')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    conversion_type = models.CharField(max_length=30, choices=CONVERSION_TYPE_CHOICES)
    conversion_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    
    # Attribution
    time_to_conversion = models.IntegerField()  # Seconds from click to conversion
    
    class Meta:
        db_table = 'ad_conversions'
        verbose_name = 'Ad Conversion'
        verbose_name_plural = 'Ad Conversions'
        indexes = [
            models.Index(fields=['campaign', 'conversion_type']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Conversion: {self.conversion_type} for {self.campaign.name}"

class AdRevenue(BaseModel):
    """
    Track revenue generated from advertisements
    """
    REVENUE_TYPE_CHOICES = [
        ('impression', 'Impression Revenue'),
        ('click', 'Click Revenue'),
        ('conversion', 'Conversion Revenue'),
    ]
    
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='revenues')
    provider = models.ForeignKey(AdProvider, on_delete=models.CASCADE)
    
    revenue_type = models.CharField(max_length=20, choices=REVENUE_TYPE_CHOICES)
    gross_revenue = models.DecimalField(max_digits=10, decimal_places=2)
    net_revenue = models.DecimalField(max_digits=10, decimal_places=2)  # After provider cut
    currency = models.CharField(max_length=3, default='USD')
    
    # Attribution
    impressions_count = models.IntegerField(default=0)
    clicks_count = models.IntegerField(default=0)
    conversions_count = models.IntegerField(default=0)
    
    # Time period
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    
    class Meta:
        db_table = 'ad_revenues'
        verbose_name = 'Ad Revenue'
        verbose_name_plural = 'Ad Revenues'
        indexes = [
            models.Index(fields=['campaign', 'period_start']),
            models.Index(fields=['provider', 'period_start']),
        ]
    
    def __str__(self):
        return f"Revenue: {self.net_revenue} {self.currency} for {self.campaign.name}"

class AdBlocker(BaseModel):
    """
    Track users with ad blockers for analytics
    """
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    session_id = models.CharField(max_length=100, blank=True)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True)
    page_url = models.URLField()
    
    # Ad blocker details
    blocker_detected = models.BooleanField(default=True)
    blocker_type = models.CharField(max_length=100, blank=True)  # uBlock, AdBlock, etc.
    
    class Meta:
        db_table = 'ad_blockers'
        verbose_name = 'Ad Blocker Detection'
        verbose_name_plural = 'Ad Blocker Detections'
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['user', 'created_at']),
        ]
    
    def __str__(self):
        return f"Ad blocker detected at {self.created_at}"

class RewardedAdView(BaseModel):
    """
    Track rewarded ad views for unlocking premium features
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rewarded_ad_views')
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='rewarded_views')
    
    # Reward details
    reward_type = models.CharField(max_length=50)  # 'extra_ideas', 'pdf_unlock', etc.
    reward_amount = models.IntegerField(default=1)
    reward_granted = models.BooleanField(default=False)
    
    # View verification
    view_duration = models.IntegerField()  # Seconds watched
    minimum_duration = models.IntegerField()  # Required seconds to get reward
    completed = models.BooleanField(default=False)
    
    # Session info
    session_id = models.CharField(max_length=100, blank=True)
    ip_address = models.GenericIPAddressField()
    
    class Meta:
        db_table = 'rewarded_ad_views'
        verbose_name = 'Rewarded Ad View'
        verbose_name_plural = 'Rewarded Ad Views'
        indexes = [
            models.Index(fields=['user', 'reward_granted']),
            models.Index(fields=['campaign', 'completed']),
        ]
    
    def __str__(self):
        return f"Rewarded ad view by {self.user.email} - {self.reward_type}"
    
    def grant_reward(self):
        """Grant reward to user if completed"""
        if self.completed and not self.reward_granted:
            self.reward_granted = True
            self.save()
            return True
        return False

class AdFrequencyCap(BaseModel):
    """
    Control ad frequency to prevent user fatigue
    """
    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='frequency_caps')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    
    # Frequency limits
    impressions_today = models.IntegerField(default=0)
    clicks_today = models.IntegerField(default=0)
    last_impression = models.DateTimeField(null=True, blank=True)
    last_click = models.DateTimeField(null=True, blank=True)
    
    # Caps
    max_impressions_per_day = models.IntegerField(default=10)
    max_clicks_per_day = models.IntegerField(default=3)
    min_time_between_impressions = models.IntegerField(default=300)  # Seconds
    
    class Meta:
        db_table = 'ad_frequency_caps'
        verbose_name = 'Ad Frequency Cap'
        verbose_name_plural = 'Ad Frequency Caps'
        unique_together = [
            ['campaign', 'user'],
            ['campaign', 'ip_address'],
        ]
    
    def __str__(self):
        identifier = self.user.email if self.user else self.ip_address
        return f"Frequency cap for {self.campaign.name} - {identifier}"
    
    def can_show_ad(self):
        """Check if ad can be shown based on frequency caps"""
        now = timezone.now()
        
        # Check daily impression limit
        if self.impressions_today >= self.max_impressions_per_day:
            return False
        
        # Check time between impressions
        if (self.last_impression and 
            (now - self.last_impression).seconds < self.min_time_between_impressions):
            return False
        
        return True
    
    def record_impression(self):
        """Record new impression"""
        now = timezone.now()
        
        # Reset daily counters if it's a new day
        if (self.last_impression and 
            self.last_impression.date() < now.date()):
            self.impressions_today = 0
            self.clicks_today = 0
        
        self.impressions_today += 1
        self.last_impression = now
        self.save()
    
    def record_click(self):
        """Record new click"""
        now = timezone.now()
        self.clicks_today += 1
        self.last_click = now
        self.save()