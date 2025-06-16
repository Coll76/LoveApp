# apps/core/decorators.py
from functools import wraps
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import json
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


def api_view(allowed_methods=None):
    """
    Decorator for API views that handles common API patterns
    """
    if allowed_methods is None:
        allowed_methods = ['GET']
    
    def decorator(view_func):
        @wraps(view_func)
        @csrf_exempt
        @require_http_methods(allowed_methods)
        def wrapped_view(request, *args, **kwargs):
            try:
                # Handle JSON parsing for POST/PUT/PATCH requests
                if request.method in ['POST', 'PUT', 'PATCH']:
                    if hasattr(request, '_body') and request._body:
                        try:
                            request.json = json.loads(request.body.decode('utf-8'))
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            return JsonResponse({
                                'success': False,
                                'error': 'Invalid JSON in request body'
                            }, status=400)
                    else:
                        request.json = {}
                
                # Set content type for API responses
                response = view_func(request, *args, **kwargs)
                if hasattr(response, 'content_type'):
                    response['Content-Type'] = 'application/json'
                
                return response
                
            except Exception as e:
                logger.error(f"API view error in {view_func.__name__}: {str(e)}")
                return JsonResponse({
                    'success': False,
                    'error': 'Internal server error'
                }, status=500)
        
        return wrapped_view
    return decorator


def permission_required(permission_string, raise_exception=True):
    """
    Decorator to check if user has specific permission
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if raise_exception:
                    return JsonResponse({
                        'success': False,
                        'error': 'Authentication required'
                    }, status=401)
                raise PermissionDenied("Authentication required")
            
            # Check if user has the required permission
            if not request.user.has_perm(permission_string):
                if raise_exception:
                    return JsonResponse({
                        'success': False,
                        'error': 'Permission denied'
                    }, status=403)
                raise PermissionDenied(f"Permission '{permission_string}' required")
            
            return view_func(request, *args, **kwargs)
        
        return wrapped_view
    return decorator


def staff_required(view_func):
    """
    Decorator to require staff status
    """
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({
                'success': False,
                'error': 'Authentication required'
            }, status=401)
        
        if not request.user.is_staff:
            return JsonResponse({
                'success': False,
                'error': 'Staff access required'
            }, status=403)
        
        return view_func(request, *args, **kwargs)
    
    return wrapped_view


def premium_required(view_func):
    """
    Decorator to require premium subscription
    """
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({
                'success': False,
                'error': 'Authentication required'
            }, status=401)
        
        # Check if user has active premium subscription
        subscription = getattr(request.user, 'subscription', None)
        if not subscription or not subscription.is_active_premium():
            return JsonResponse({
                'success': False,
                'error': 'Premium subscription required'
            }, status=403)
        
        return view_func(request, *args, **kwargs)
    
    return wrapped_view


def rate_limit(max_requests=60, window=3600, key_func=None):
    """
    Simple rate limiting decorator
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            from django.core.cache import cache
            from .utils import get_client_ip
            
            # Generate rate limit key
            if key_func:
                rate_key = key_func(request)
            elif request.user.is_authenticated:
                rate_key = f"rate_limit:user:{request.user.id}"
            else:
                rate_key = f"rate_limit:ip:{get_client_ip(request)}"
            
            # Check current count
            current_count = cache.get(rate_key, 0)
            
            if current_count >= max_requests:
                return JsonResponse({
                    'success': False,
                    'error': 'Rate limit exceeded. Please try again later.'
                }, status=429)
            
            # Increment counter
            cache.set(rate_key, current_count + 1, window)
            
            response = view_func(request, *args, **kwargs)
            
            # Add rate limit headers
            if hasattr(response, '__setitem__'):
                response['X-RateLimit-Limit'] = str(max_requests)
                response['X-RateLimit-Remaining'] = str(max_requests - current_count - 1)
                response['X-RateLimit-Reset'] = str(window)
            
            return response
        
        return wrapped_view
    return decorator


def validate_json(*required_fields):
    """
    Decorator to validate JSON request data
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    data = json.loads(request.body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    return JsonResponse({
                        'success': False,
                        'error': 'Invalid JSON format'
                    }, status=400)
                
                # Check required fields
                missing_fields = []
                for field in required_fields:
                    if field not in data or data[field] is None:
                        missing_fields.append(field)
                
                if missing_fields:
                    return JsonResponse({
                        'success': False,
                        'error': f'Missing required fields: {", ".join(missing_fields)}'
                    }, status=400)
                
                # Add parsed data to request
                request.json = data
            
            return view_func(request, *args, **kwargs)
        
        return wrapped_view
    return decorator


def cache_response(timeout=300, key_prefix=None):
    """
    Decorator to cache view responses
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            from django.core.cache import cache
            
            # Generate cache key
            if key_prefix:
                cache_key = f"{key_prefix}:{request.path}:{request.GET.urlencode()}"
            else:
                cache_key = f"view_cache:{view_func.__name__}:{request.path}:{request.GET.urlencode()}"
            
            # Add user to cache key for authenticated requests
            if request.user.is_authenticated:
                cache_key += f":user:{request.user.id}"
            
            # Try to get from cache
            cached_response = cache.get(cache_key)
            if cached_response:
                return cached_response
            
            # Generate response and cache it
            response = view_func(request, *args, **kwargs)
            
            # Only cache successful responses
            if hasattr(response, 'status_code') and response.status_code == 200:
                cache.set(cache_key, response, timeout)
            
            return response
        
        return wrapped_view
    return decorator


def handle_exceptions(*exception_classes):
    """
    Decorator to handle specific exceptions and return JSON responses
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            try:
                return view_func(request, *args, **kwargs)
            except Exception as e:
                # Check if it's one of the specified exception classes
                for exc_class in exception_classes:
                    if isinstance(e, exc_class):
                        logger.error(f"Handled exception in {view_func.__name__}: {str(e)}")
                        return JsonResponse({
                            'success': False,
                            'error': str(e)
                        }, status=getattr(e, 'status_code', 400))
                
                # Re-raise if not handled
                raise
        
        return wrapped_view
    return decorator


def subscription_required(plan_types=None):
    """
    Decorator to require specific subscription plans
    """
    if plan_types is None:
        plan_types = ['premium_monthly', 'premium_yearly']
    
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({
                    'success': False,
                    'error': 'Authentication required'
                }, status=401)
            
            subscription = getattr(request.user, 'subscription', None)
            if not subscription:
                return JsonResponse({
                    'success': False,
                    'error': 'Subscription required'
                }, status=403)
            
            if not subscription.is_active_premium() or subscription.plan_name not in plan_types:
                return JsonResponse({
                    'success': False,
                    'error': f'Required subscription plan: {", ".join(plan_types)}'
                }, status=403)
            
            return view_func(request, *args, **kwargs)
        
        return wrapped_view
    return decorator