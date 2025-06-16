# apps/users/managers.py
from django.db import models
from django.utils import timezone

class UserUsageLimitManager(models.Manager):
    def get_today_usage(self, user):
        """Get today's usage for a user"""
        today = timezone.now().date()
        return self.filter(user=user, date=today).first()
    
    def reset_daily_limits(self):
        """Reset daily limits (called by Celery task)"""
        # This could be used for any cleanup if needed
        pass

