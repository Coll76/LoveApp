# apps/pdf_generator/throttles.py
import logging
from typing import Optional, Dict, Any

from django.core.cache import cache
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime, timedelta

from rest_framework.throttling import UserRateThrottle, AnonRateThrottle, BaseThrottle
from rest_framework.request import Request
from rest_framework.views import APIView

from .models import PDFDocument, PDFUsageStats

User = get_user_model()
logger = logging.getLogger(__name__)


class PDFGenerationThrottle(BaseThrottle):
    """
    Throttle PDF generation based on user subscription tier and limits.
    
    Implements both daily and hourly limits with different rates for:
    - Free users: 3 PDFs per day, 1 per hour
    - Premium users: 50 PDFs per day, 10 per hour
    - Enterprise users: 200 PDFs per day, 30 per hour
    """
    
    scope = 'pdf_generation'
    cache_format = 'throttle_pdf_gen_{scope}_{ident}'
    
    def __init__(self):
        super().__init__()
        self.daily_limits = getattr(settings, 'PDF_GENERATION_DAILY_LIMITS', {
            'free': 3,
            'premium': 50,
            'enterprise': 200,
            'admin': 1000
        })
        self.hourly_limits = getattr(settings, 'PDF_GENERATION_HOURLY_LIMITS', {
            'free': 1,
            'premium': 10,
            'enterprise': 30,
            'admin': 100
        })
    
    def get_cache_key(self, request: Request, view: APIView, period: str) -> str:
        """Generate cache key for user and time period"""
        if request.user.is_authenticated:
            ident = request.user.pk
        else:
            ident = self.get_ident(request)
        
        return self.cache_format.format(scope=f"{self.scope}_{period}", ident=ident)
    
    def get_user_tier(self, user) -> str:
        """Determine user's subscription tier"""
        if not user.is_authenticated:
            return 'anonymous'
        
        if user.is_superuser or user.is_staff:
            return 'admin'
        
        # Check for active subscription
        if hasattr(user, 'has_active_subscription') and user.has_active_subscription():
            # Determine subscription type
            if hasattr(user, 'subscription'):
                subscription_type = getattr(user.subscription, 'plan_type', 'premium')
                if subscription_type in ['enterprise', 'business']:
                    return 'enterprise'
                return 'premium'
            return 'premium'
        
        return 'free'
    
    def get_rate_limits(self, user) -> Dict[str, int]:
        """Get rate limits for user tier"""
        tier = self.get_user_tier(user)
        
        return {
            'daily': self.daily_limits.get(tier, self.daily_limits['free']),
            'hourly': self.hourly_limits.get(tier, self.hourly_limits['free'])
        }
    
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Check if request should be allowed"""
        # Allow non-authenticated users to fail at permission level
        if not request.user.is_authenticated:
            return False
        
        # Get user limits
        limits = self.get_rate_limits(request.user)
        
        # Check daily limit
        daily_key = self.get_cache_key(request, view, 'daily')
        daily_count = cache.get(daily_key, 0)
        
        if daily_count >= limits['daily']:
            logger.warning(f"User {request.user.id} exceeded daily PDF generation limit: {daily_count}/{limits['daily']}")
            return False
        
        # Check hourly limit
        hourly_key = self.get_cache_key(request, view, 'hourly')
        hourly_count = cache.get(hourly_key, 0)
        
        if hourly_count >= limits['hourly']:
            logger.warning(f"User {request.user.id} exceeded hourly PDF generation limit: {hourly_count}/{limits['hourly']}")
            return False
        
        # Also check database count for accuracy (cache might be cleared)
        if not self._check_database_limits(request.user, limits):
            return False
        
        # Increment counters
        self._increment_counters(daily_key, hourly_key)
        
        return True
    
    def _check_database_limits(self, user, limits: Dict[str, int]) -> bool:
        """Verify limits against database records"""
        now = timezone.now()
        
        # Check daily limit from database
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count = PDFDocument.objects.filter(
            user=user,
            created_at__gte=today_start
        ).count()
        
        if daily_count >= limits['daily']:
            return False
        
        # Check hourly limit from database
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        hourly_count = PDFDocument.objects.filter(
            user=user,
            created_at__gte=hour_start
        ).count()
        
        if hourly_count >= limits['hourly']:
            return False
        
        return True
    
    def _increment_counters(self, daily_key: str, hourly_key: str):
        """Increment cache counters atomically"""
        # Increment daily counter (expires at end of day)
        try:
            cache.get_or_set(daily_key, 0, timeout=self._seconds_until_end_of_day())
            cache.set(daily_key, cache.get(daily_key, 0) + 1, timeout=self._seconds_until_end_of_day())
        except Exception as e:
            logger.error(f"Error incrementing daily counter: {e}")
        
        # Increment hourly counter (expires at end of hour)
        try:
            cache.get_or_set(hourly_key, 0, timeout=3600)  # 1 hour
            cache.set(hourly_key, cache.get(hourly_key, 0) + 1, timeout=3600)
        except Exception as e:
            logger.error(f"Error incrementing hourly counter: {e}")
    
    def _seconds_until_end_of_day(self) -> int:
        """Calculate seconds until end of current day"""
        now = timezone.now()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return int((end_of_day - now).total_seconds())
    
    def wait(self) -> Optional[float]:
        """Return time to wait before next request (in seconds)"""
        # Return time until next hour (for hourly limit reset)
        now = timezone.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        return (next_hour - now).total_seconds()


class PDFDownloadThrottle(UserRateThrottle):
    """
    Throttle PDF downloads to prevent abuse.
    
    Limits:
    - Authenticated users: 100 downloads per hour
    - Anonymous users: 10 downloads per hour
    """
    
    scope = 'pdf_download'
    rate = '100/hour'
    
    def get_rate(self) -> str:
        """Return rate based on user authentication status"""
        if not getattr(self, 'request', None):
            return self.rate
        
        if hasattr(self.request, 'user') and self.request.user.is_authenticated:
            # Premium users get higher limits
            if hasattr(self.request.user, 'has_active_subscription') and self.request.user.has_active_subscription():
                return '500/hour'
            return '100/hour'
        
        return '10/hour'  # Anonymous users
    
    def get_cache_key(self, request: Request, view: APIView) -> str:
        """Override to use user-specific or IP-based key"""
        if request.user.is_authenticated:
            ident = request.user.pk
        else:
            ident = self.get_ident(request)
        
        return self.cache_format.format(scope=self.scope, ident=ident)


class PDFPublicDownloadThrottle(BaseThrottle):
    """
    Throttle public PDF downloads with IP-based limiting.
    
    More restrictive than authenticated downloads to prevent abuse.
    """
    
    scope = 'pdf_public_download'
    cache_format = 'throttle_{scope}_{ident}'
    
    def __init__(self):
        super().__init__()
        self.rate_limit = getattr(settings, 'PDF_PUBLIC_DOWNLOAD_RATE_LIMIT', 20)  # per hour
        self.rate_period = 3600  # 1 hour in seconds
    
    def get_cache_key(self, request: Request, view: APIView) -> str:
        """Generate cache key based on IP address"""
        ident = self.get_ident(request)
        return self.cache_format.format(scope=self.scope, ident=ident)
    
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Check if public download should be allowed"""
        cache_key = self.get_cache_key(request, view)
        
        # Get current count
        current_count = cache.get(cache_key, 0)
        
        if current_count >= self.rate_limit:
            logger.warning(f"IP {self.get_ident(request)} exceeded public PDF download limit: {current_count}/{self.rate_limit}")
            return False
        
        # Increment counter
        cache.set(cache_key, current_count + 1, timeout=self.rate_period)
        
        return True
    
    def wait(self) -> Optional[float]:
        """Return time to wait before next request"""
        return self.rate_period  # Wait for rate period to reset


class PDFAPIThrottle(UserRateThrottle):
    """
    General API throttle for PDF-related endpoints.
    
    Prevents abuse of PDF management APIs.
    """
    
    scope = 'pdf_api'
    
    def get_rate(self) -> str:
        """Return rate based on user tier"""
        if not hasattr(self, 'request') or not self.request.user.is_authenticated:
            return '50/hour'  # Anonymous/unauthenticated
        
        user = self.request.user
        
        if user.is_superuser or user.is_staff:
            return '2000/hour'  # Admin users
        
        # Check subscription status
        if hasattr(user, 'has_active_subscription') and user.has_active_subscription():
            return '1000/hour'  # Premium users
        
        return '200/hour'  # Free users


class PDFBulkOperationThrottle(BaseThrottle):
    """
    Throttle bulk operations like batch PDF generation or mass downloads.
    
    Very restrictive to prevent system overload.
    """
    
    scope = 'pdf_bulk_operation'
    cache_format = 'throttle_{scope}_{ident}'
    
    def __init__(self):
        super().__init__()
        self.limits = getattr(settings, 'PDF_BULK_OPERATION_LIMITS', {
            'free': {'count': 2, 'period': 3600 * 24},      # 2 per day
            'premium': {'count': 10, 'period': 3600 * 6},   # 10 per 6 hours
            'enterprise': {'count': 50, 'period': 3600 * 2}, # 50 per 2 hours
            'admin': {'count': 1000, 'period': 3600}        # 1000 per hour
        })
    
    def get_user_tier(self, user) -> str:
        """Determine user tier for bulk operations"""
        if not user.is_authenticated:
            return 'free'
        
        if user.is_superuser or user.is_staff:
            return 'admin'
        
        if hasattr(user, 'has_active_subscription') and user.has_active_subscription():
            if hasattr(user, 'subscription'):
                subscription_type = getattr(user.subscription, 'plan_type', 'premium')
                if subscription_type in ['enterprise', 'business']:
                    return 'enterprise'
            return 'premium'
        
        return 'free'
    
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Check if bulk operation should be allowed"""
        if not request.user.is_authenticated:
            return False
        
        tier = self.get_user_tier(request.user)
        limit_config = self.limits[tier]
        
        cache_key = f"{self.cache_format.format(scope=self.scope, ident=request.user.pk)}_{tier}"
        
        current_count = cache.get(cache_key, 0)
        
        if current_count >= limit_config['count']:
            logger.warning(f"User {request.user.id} exceeded bulk operation limit: {current_count}/{limit_config['count']}")
            return False
        
        # Increment counter
        cache.set(cache_key, current_count + 1, timeout=limit_config['period'])
        
        return True
    
    def wait(self) -> Optional[float]:
        """Return time to wait before next bulk operation"""
        if hasattr(self, 'request') and self.request.user.is_authenticated:
            tier = self.get_user_tier(self.request.user)
            return self.limits[tier]['period']
        
        return self.limits['free']['period']


class PDFQueueThrottle(BaseThrottle):
    """
    Throttle PDF queue operations to prevent queue spam.
    
    Limits how often users can check queue status or manipulate queue items.
    """
    
    scope = 'pdf_queue'
    cache_format = 'throttle_{scope}_{ident}'
    rate_limit = 60  # requests per hour
    rate_period = 3600  # 1 hour
    
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Check if queue operation should be allowed"""
        if not request.user.is_authenticated:
            return False
        
        cache_key = self.cache_format.format(scope=self.scope, ident=request.user.pk)
        current_count = cache.get(cache_key, 0)
        
        if current_count >= self.rate_limit:
            return False
        
        cache.set(cache_key, current_count + 1, timeout=self.rate_period)
        return True
    
    def wait(self) -> Optional[float]:
        """Return time to wait before next queue operation"""
        return self.rate_period


class PDFShareThrottle(BaseThrottle):
    """
    Throttle PDF sharing operations to prevent spam.
    
    Limits how many PDFs a user can share publicly per day.
    """
    
    scope = 'pdf_share'
    cache_format = 'throttle_{scope}_{ident}'
    
    def __init__(self):
        super().__init__()
        self.daily_limits = getattr(settings, 'PDF_SHARE_DAILY_LIMITS', {
            'free': 5,
            'premium': 50,
            'enterprise': 200,
            'admin': 1000
        })
    
    def get_user_tier(self, user) -> str:
        """Get user tier for sharing limits"""
        if not user.is_authenticated:
            return 'free'
        
        if user.is_superuser or user.is_staff:
            return 'admin'
        
        if hasattr(user, 'has_active_subscription') and user.has_active_subscription():
            if hasattr(user, 'subscription'):
                subscription_type = getattr(user.subscription, 'plan_type', 'premium')
                if subscription_type in ['enterprise', 'business']:
                    return 'enterprise'
            return 'premium'
        
        return 'free'
    
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Check if sharing should be allowed"""
        if not request.user.is_authenticated:
            return False
        
        tier = self.get_user_tier(request.user)
        daily_limit = self.daily_limits[tier]
        
        cache_key = f"{self.cache_format.format(scope=self.scope, ident=request.user.pk)}_daily"
        current_count = cache.get(cache_key, 0)
        
        if current_count >= daily_limit:
            logger.warning(f"User {request.user.id} exceeded daily PDF sharing limit: {current_count}/{daily_limit}")
            return False
        
        # Increment counter with end-of-day expiry
        timeout = self._seconds_until_end_of_day()
        cache.set(cache_key, current_count + 1, timeout=timeout)
        
        return True
    
    def _seconds_until_end_of_day(self) -> int:
        """Calculate seconds until end of current day"""
        now = timezone.now()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return int((end_of_day - now).total_seconds())
    
    def wait(self) -> Optional[float]:
        """Return time to wait before next share operation"""
        return self._seconds_until_end_of_day()


# Throttle class mapping for easy reference
THROTTLE_CLASSES = {
    'pdf_generation': PDFGenerationThrottle,
    'pdf_download': PDFDownloadThrottle,
    'pdf_public_download': PDFPublicDownloadThrottle,
    'pdf_api': PDFAPIThrottle,
    'pdf_bulk_operation': PDFBulkOperationThrottle,
    'pdf_queue': PDFQueueThrottle,
    'pdf_share': PDFShareThrottle,
}


# Utility functions for throttle management
def get_user_throttle_status(user) -> Dict[str, Any]:
    """
    Get current throttle status for a user across all PDF operations.
    
    Returns dictionary with current usage and limits for each throttle type.
    """
    if not user.is_authenticated:
        return {}
    
    status = {}
    
    # PDF Generation status
    pdf_gen_throttle = PDFGenerationThrottle()
    limits = pdf_gen_throttle.get_rate_limits(user)
    
    # Get current counts from cache
    daily_key = f"throttle_pdf_gen_pdf_generation_daily_{user.pk}"
    hourly_key = f"throttle_pdf_gen_pdf_generation_hourly_{user.pk}"
    
    status['pdf_generation'] = {
        'daily': {
            'limit': limits['daily'],
            'used': cache.get(daily_key, 0),
            'remaining': max(0, limits['daily'] - cache.get(daily_key, 0))
        },
        'hourly': {
            'limit': limits['hourly'],
            'used': cache.get(hourly_key, 0),
            'remaining': max(0, limits['hourly'] - cache.get(hourly_key, 0))
        }
    }
    
    # PDF Sharing status
    pdf_share_throttle = PDFShareThrottle()
    share_tier = pdf_share_throttle.get_user_tier(user)
    share_limit = pdf_share_throttle.daily_limits[share_tier]
    share_key = f"throttle_pdf_share_{user.pk}_daily"
    
    status['pdf_sharing'] = {
        'daily': {
            'limit': share_limit,
            'used': cache.get(share_key, 0),
            'remaining': max(0, share_limit - cache.get(share_key, 0))
        }
    }
    
    return status


def reset_user_throttles(user, throttle_types: Optional[list] = None):
    """
    Reset throttle counters for a user.
    
    Args:
        user: User instance
        throttle_types: List of throttle types to reset, or None for all
    """
    if not user.is_authenticated:
        return
    
    if throttle_types is None:
        throttle_types = ['pdf_generation', 'pdf_download', 'pdf_share', 'pdf_queue']
    
    keys_to_delete = []
    
    for throttle_type in throttle_types:
        if throttle_type == 'pdf_generation':
            keys_to_delete.extend([
                f"throttle_pdf_gen_pdf_generation_daily_{user.pk}",
                f"throttle_pdf_gen_pdf_generation_hourly_{user.pk}"
            ])
        elif throttle_type == 'pdf_download':
            keys_to_delete.append(f"throttle_pdf_download_{user.pk}")
        elif throttle_type == 'pdf_share':
            keys_to_delete.append(f"throttle_pdf_share_{user.pk}_daily")
        elif throttle_type == 'pdf_queue':
            keys_to_delete.append(f"throttle_pdf_queue_{user.pk}")
    
    # Delete cache keys
    for key in keys_to_delete:
        try:
            cache.delete(key)
        except Exception as e:
            logger.error(f"Error deleting throttle cache key {key}: {e}")
    
    logger.info(f"Reset throttles for user {user.id}: {throttle_types}")