# apps/pdf_generator/managers.py
from django.db import models
from django.utils import timezone
from datetime import timedelta

class PDFTemplateManager(models.Manager):
    """Custom manager for PDF templates"""
    
    def active(self):
        """Get only active templates"""
        return self.filter(is_active=True)
    
    def free_templates(self):
        """Get free templates"""
        return self.active().filter(is_premium=False)
    
    def premium_templates(self):
        """Get premium templates"""
        return self.active().filter(is_premium=True)
    
    def by_type(self, template_type):
        """Get templates by type"""
        return self.active().filter(template_type=template_type)
    
    def popular(self, limit=10):
        """Get most popular templates"""
        return self.active().order_by('-usage_count')[:limit]

class PDFDocumentManager(models.Manager):
    """Custom manager for PDF documents"""
    
    def completed(self):
        """Get completed documents"""
        return self.filter(status='completed')
    
    def pending(self):
        """Get pending documents"""
        return self.filter(status='pending')
    
    def processing(self):
        """Get documents being processed"""
        return self.filter(status='processing')
    
    def failed(self):
        """Get failed documents"""
        return self.filter(status='failed')
    
    def for_user(self, user):
        """Get documents for specific user"""
        return self.filter(user=user)
    
    def recent(self, days=30):
        """Get documents from last N days"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since)
    
    def by_idea(self, idea):
        """Get documents for specific idea"""
        return self.filter(idea=idea)
    
    def public(self):
        """Get public documents"""
        return self.completed().filter(is_public=True)
    
    def most_downloaded(self, limit=10):
        """Get most downloaded documents"""
        return self.completed().order_by('-download_count')[:limit]
    
    def can_retry(self, max_retries=3):
        """Get documents that can be retried"""
        return self.failed().filter(retry_count__lt=max_retries)
    
    def stuck_processing(self, hours=2):
        """Get documents stuck in processing state"""
        stuck_time = timezone.now() - timedelta(hours=hours)
        return self.processing().filter(generation_started_at__lt=stuck_time)
    
    def user_daily_count(self, user, date=None):
        """Get user's PDF count for a specific date"""
        if date is None:
            date = timezone.now().date()
        
        return self.filter(
            user=user,
            created_at__date=date
        ).count()
    
    def user_can_generate(self, user, daily_limit=None):
        """Check if user can generate more PDFs today"""
        if user.has_active_subscription():
            return True  # Premium users have unlimited PDFs
        
        if daily_limit is None:
            from django.conf import settings
            daily_limit = getattr(settings, 'FREE_USER_DAILY_PDF_LIMIT', 2)
        
        today_count = self.user_daily_count(user)
        return today_count < daily_limit