# apps/core/middleware.py
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
import json
import logging

logger = logging.getLogger(__name__)

class RateLimitMiddleware(MiddlewareMixin):
    """
    Simple rate limiting middleware
    """
    def process_request(self, request):
        if request.path.startswith('/api/'):
            # Get client IP
            from .utils import get_client_ip
            client_ip = get_client_ip(request)
            
            # Create rate limit key
            rate_limit_key = f"rate_limit:{client_ip}"
            
            # Get current count
            current_count = cache.get(rate_limit_key, 0)
            
            # Set rate limit (100 requests per hour for anonymous users)
            if not request.user.is_authenticated and current_count >= 100:
                return JsonResponse({
                    'error': 'Rate limit exceeded. Please try again later.'
                }, status=429)
            
            # Increment counter
            cache.set(rate_limit_key, current_count + 1, 3600)  # 1 hour
        
        return None

class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Add security headers to responses
    """
    def process_response(self, request, response):
        # Add security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['X-XSS-Protection'] = '1; mode=block'
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        
        # Add CSP header for API endpoints
        if request.path.startswith('/api/'):
            response['Content-Security-Policy'] = "default-src 'none'"
        
        return response