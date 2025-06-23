# apps/core/utils.py
import hashlib
import hmac
import secrets
import string
from typing import Optional, Any
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

def generate_random_string(length: int = 32) -> str:
    """Generate a random string of specified length"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_tx_ref() -> str:
    """Generate a unique transaction reference"""
    timestamp = str(int(timezone.now().timestamp()))
    random_str = generate_random_string(8)
    return f"lc_{timestamp}_{random_str}"

def verify_webhook_signature(payload: str, signature: str, secret: str) -> bool:
    """Verify webhook signature using HMAC-SHA256"""
    try:
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature)
    except Exception as e:
        logger.error(f"Error verifying webhook signature: {e}")
        return False

def get_client_ip(request) -> str:
    """Get client IP address from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')

def cache_key(prefix: str, *args) -> str:
    """Generate cache key with prefix and arguments"""
    key_parts = [str(arg) for arg in args]
    return f"{prefix}:{'_'.join(key_parts)}"

def get_or_set_cache(key: str, callable_func, timeout: int = 300) -> Any:
    """Get from cache or set using callable function"""
    result = cache.get(key)
    if result is None:
        result = callable_func()
        cache.set(key, result, timeout)
    return result

def detect_currency_from_country(country_code: str) -> str:
    """Detect currency based on country code"""
    currency_map = {
        'US': 'USD', 'CA': 'CAD', 'GB': 'GBP', 'AU': 'AUD',
        'KE': 'KES', 'NG': 'NGN', 'GH': 'GHS', 'UG': 'UGX',
        'TZ': 'TZS', 'ZA': 'ZAR', 'DE': 'EUR', 'FR': 'EUR',
        'IT': 'EUR', 'ES': 'EUR', 'NL': 'EUR', 'BE': 'EUR',
    }
    return currency_map.get(country_code.upper(), settings.DEFAULT_CURRENCY)

def get_user_country_currency(user) -> tuple[str, str]:
    """
    Get user's country and currency based on their profile or location
    Returns (country_code, currency_code)
    """
    try:
        # Try to get from user profile first
        if hasattr(user, 'profile') and user.profile:
            profile = user.profile
            if hasattr(profile, 'country') and profile.country:
                country_code = profile.country
                currency = detect_currency_from_country(country_code)
                return country_code, currency
            
            # If no country in profile, try to get from location
            if hasattr(profile, 'location') and profile.location:
                # You might want to implement location-to-country mapping here
                # For now, return default
                pass
        
        # Default fallback
        default_country = getattr(settings, 'DEFAULT_COUNTRY', 'US')
        default_currency = getattr(settings, 'DEFAULT_CURRENCY', 'USD')
        return default_country, default_currency
        
    except Exception as e:
        logger.error(f"Error getting user country/currency: {e}")
        # Return safe defaults
        return 'US', 'USD'

def get_subscription_price(plan_type: str, currency: str) -> float:
    """Get subscription price for specific plan and currency"""
    plans = settings.SUBSCRIPTION_PLANS
    if plan_type not in plans:
        raise ValueError(f"Invalid plan type: {plan_type}")
    
    plan = plans[plan_type]
    if plan_type == 'free':
        return 0.0
    
    pricing = plan.get('regional_pricing', {})
    return pricing.get(currency, pricing.get('USD', 0.0))