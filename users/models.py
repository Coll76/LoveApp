# apps/users/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.conf import settings
from core.models import BaseModel, SoftDeleteModel
from core.managers import CustomUserManager
import uuid
from .managers import UserUsageLimitManager
class User(AbstractUser):
    """
    Custom User model with email as username field
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = None  # Remove username field
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    is_email_verified = models.BooleanField(default=False)
    email_verification_token = models.CharField(max_length=255, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']
    
    objects = CustomUserManager()
    
    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
    
    def __str__(self):
        return self.email
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    def has_active_subscription(self):
        """Check if user has active premium subscription"""
        try:
            return self.subscription.is_active_premium()
        except AttributeError:
            return False
    
    def get_subscription_tier(self):
        """Get user subscription tier"""
        if self.has_active_subscription():
            return self.subscription.plan_name
        return 'free'

class UserProfile(BaseModel):
    """
    Extended user profile information
    """
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('other', 'Other'),
        ('prefer_not_to_say', 'Prefer not to say'),
    ]
    
    RELATIONSHIP_STATUS_CHOICES = [
        ('single', 'Single'),
        ('in_relationship', 'In a relationship'),
        ('married', 'Married'),
        ('divorced', 'Divorced'),
        ('widowed', 'Widowed'),
        ('complicated', 'It\'s complicated'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    bio = models.TextField(max_length=500, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, blank=True)
    relationship_status = models.CharField(
        max_length=20, 
        choices=RELATIONSHIP_STATUS_CHOICES, 
        default='single'
    )
    location = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, blank=True)  # ISO country code
    timezone = models.CharField(max_length=50, default='UTC')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    
    class Meta:
        db_table = 'user_profiles'
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'
    
    def __str__(self):
        return f"{self.user.email} Profile"
    
    @property
    def age(self):
        if self.birth_date:
            today = timezone.now().date()
            return today.year - self.birth_date.year - (
                (today.month, today.day) < (self.birth_date.month, self.birth_date.day)
            )
        return None

class UserPreferences(BaseModel):
    """
    User preferences for idea generation and app behavior
    """
    IDEA_STYLE_CHOICES = [
        ('romantic', 'Romantic'),
        ('adventurous', 'Adventurous'),
        ('casual', 'Casual'),
        ('creative', 'Creative'),
        ('budget_friendly', 'Budget Friendly'),
        ('luxurious', 'Luxurious'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    preferred_idea_styles = models.JSONField(default=list)  # List of IDEA_STYLE_CHOICES
    budget_range = models.CharField(max_length=50, default='moderate')  # low, moderate, high
    preferred_locations = models.JSONField(default=list)  # indoor, outdoor, restaurant, etc.
    notification_email = models.BooleanField(default=True)
    notification_marketing = models.BooleanField(default=False)
    language = models.CharField(max_length=10, default='en')
    currency = models.CharField(max_length=3, default='USD')
    
    class Meta:
        db_table = 'user_preferences'
        verbose_name = 'User Preferences'
        verbose_name_plural = 'User Preferences'
    
    def __str__(self):
        return f"{self.user.email} Preferences"

class UserUsageLimit(BaseModel):
    """
    Track daily usage limits for free users
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='usage_limits')
    date = models.DateField()
    ideas_generated = models.IntegerField(default=0)
    pdfs_generated = models.IntegerField(default=0)
    
    # Add the custom manager
    objects = UserUsageLimitManager()
    
    class Meta:
        db_table = 'user_usage_limits'
        verbose_name = 'User Usage Limit'
        verbose_name_plural = 'User Usage Limits'
        unique_together = ['user', 'date']
    
    def __str__(self):
        return f"{self.user.email} - {self.date}"
    
    def can_generate_idea(self):
        """Check if user can generate more ideas today"""
        # Premium users have unlimited ideas
        if self.user.has_active_subscription():
            return True
        
        # Free users limited to 5 ideas per day
        daily_limit = settings.SUBSCRIPTION_PLANS['free']['limitations']['daily_ideas']
        return self.ideas_generated < daily_limit
    
    def can_generate_pdf(self):
        """Check if user can generate PDFs"""
        # Only premium users can generate PDFs
        return self.user.has_active_subscription()
    
    def increment_ideas(self):
        """Increment ideas generated count"""
        self.ideas_generated += 1
        self.save()
    
    def increment_pdfs(self):
        """Increment PDFs generated count"""
        self.pdfs_generated += 1
        self.save()

class EmailVerification(BaseModel):
    """
    Email verification tokens
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=255, unique=True)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    
    class Meta:
        db_table = 'email_verifications'
        verbose_name = 'Email Verification'
        verbose_name_plural = 'Email Verifications'
    
    def __str__(self):
        return f"Email verification for {self.user.email}"
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def mark_as_used(self):
        self.is_used = True
        self.save()

class PasswordResetToken(BaseModel):
    """
    Password reset tokens
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=255, unique=True)
    is_used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    
    class Meta:
        db_table = 'password_reset_tokens'
        verbose_name = 'Password Reset Token'
        verbose_name_plural = 'Password Reset Tokens'
    
    def __str__(self):
        return f"Password reset for {self.user.email}"
    
    def is_expired(self):
        return timezone.now() > self.expires_at
    
    def mark_as_used(self):
        self.is_used = True
        self.save()