# advertisements/ad_providers.py
import logging
import requests
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal
from datetime import datetime, timedelta
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
import json
import hashlib
import hmac
import base64
from urllib.parse import urlencode, parse_qs

logger = logging.getLogger(__name__)


class BaseAdProvider(ABC):
    """Abstract base class for all ad providers"""
    
    def __init__(self, provider_config: Dict[str, Any]):
        self.config = provider_config
        self.api_key = provider_config.get('api_key')
        self.secret_key = provider_config.get('secret_key')
        self.base_url = provider_config.get('base_url')
        self.timeout = provider_config.get('timeout', 30)
        
    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name"""
        pass
    
    @abstractmethod
    def validate_config(self) -> bool:
        """Validate provider configuration"""
        pass
    
    @abstractmethod
    def get_ads(self, targeting_criteria: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch ads from the provider"""
        pass
    
    def track_impression(self, campaign, impression) -> bool:
        """Track impression with the provider (optional)"""
        return True
    
    def track_click(self, campaign, click) -> bool:
        """Track click with the provider (optional)"""
        return True
    
    def track_conversion(self, campaign, conversion) -> bool:
        """Track conversion with the provider (optional)"""
        return True
    
    def get_revenue_data(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Get revenue data from provider (optional)"""
        return []
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                     headers: Optional[Dict] = None) -> requests.Response:
        """Make HTTP request to provider API"""
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        
        default_headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'LovecraftAds/1.0'
        }
        
        if headers:
            default_headers.update(headers)
            
        try:
            response = requests.request(
                method=method,
                url=url,
                json=data if method.upper() in ['POST', 'PUT', 'PATCH'] else None,
                params=data if method.upper() == 'GET' else None,
                headers=default_headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response
            
        except requests.RequestException as e:
            logger.error(f"Error making request to {self.get_provider_name()}: {str(e)}")
            raise
    
    def _generate_cache_key(self, key_parts: List[str]) -> str:
        """Generate cache key for provider data"""
        key_string = ":".join([self.get_provider_name().lower()] + key_parts)
        return f"ad_provider:{hashlib.md5(key_string.encode()).hexdigest()}"


class GoogleAdSenseProvider(BaseAdProvider):
    """Google AdSense provider implementation"""
    
    def __init__(self, provider_config: Dict[str, Any]):
        super().__init__(provider_config)
        self.publisher_id = provider_config.get('publisher_id')
        self.client_id = provider_config.get('client_id')
        self.client_secret = provider_config.get('client_secret')
        self.refresh_token = provider_config.get('refresh_token')
        self.access_token = None
        self.base_url = 'https://www.googleapis.com/adsense/v2'
    
    def get_provider_name(self) -> str:
        return "Google AdSense"
    
    def validate_config(self) -> bool:
        """Validate Google AdSense configuration"""
        required_fields = ['publisher_id', 'client_id', 'client_secret']
        for field in required_fields:
            if not self.config.get(field):
                logger.error(f"Missing required field for Google AdSense: {field}")
                return False
        return True
    
    def _get_access_token(self) -> str:
        """Get or refresh access token"""
        cache_key = f"google_adsense_token:{self.publisher_id}"
        token = cache.get(cache_key)
        
        if token:
            return token
            
        try:
            # Refresh token
            token_url = "https://oauth2.googleapis.com/token"
            data = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token'
            }
            
            response = requests.post(token_url, data=data, timeout=self.timeout)
            response.raise_for_status()
            
            token_data = response.json()
            access_token = token_data['access_token']
            expires_in = token_data.get('expires_in', 3600)
            
            # Cache token for slightly less than expiry time
            cache.set(cache_key, access_token, expires_in - 60)
            
            return access_token
            
        except Exception as e:
            logger.error(f"Error refreshing Google AdSense token: {str(e)}")
            raise
    
    def get_ads(self, targeting_criteria: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch ads from Google AdSense"""
        try:
            # For AdSense, we typically don't fetch ads programmatically
            # Instead, we use ad units that are rendered client-side
            # This method returns ad unit configurations
            
            ad_units = self._get_ad_units()
            
            # Filter and rank ad units based on targeting criteria
            relevant_ads = []
            for ad_unit in ad_units[:limit]:
                relevant_ads.append({
                    'id': ad_unit['name'],
                    'title': ad_unit.get('displayName', 'AdSense Ad'),
                    'description': 'Google AdSense Advertisement',
                    'ad_unit_id': ad_unit['name'],
                    'ad_code': self._generate_ad_code(ad_unit),
                    'size': ad_unit.get('contentAdsSettings', {}).get('size', 'RESPONSIVE'),
                    'type': ad_unit.get('contentAdsSettings', {}).get('type', 'DISPLAY')
                })
            
            return relevant_ads
            
        except Exception as e:
            logger.error(f"Error fetching Google AdSense ads: {str(e)}")
            return []
    
    def _get_ad_units(self) -> List[Dict[str, Any]]:
        """Get ad units from Google AdSense"""
        try:
            access_token = self._get_access_token()
            headers = {'Authorization': f'Bearer {access_token}'}
            
            endpoint = f"accounts/{self.publisher_id}/adunits"
            response = self._make_request('GET', endpoint, headers=headers)
            
            data = response.json()
            return data.get('adUnits', [])
            
        except Exception as e:
            logger.error(f"Error fetching AdSense ad units: {str(e)}")
            return []
    
    def _generate_ad_code(self, ad_unit: Dict[str, Any]) -> str:
        """Generate ad code for AdSense ad unit"""
        ad_unit_id = ad_unit['name'].split('/')[-1]
        
        return f"""
        <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={self.client_id}"></script>
        <ins class="adsbygoogle"
             style="display:block"
             data-ad-client="{self.client_id}"
             data-ad-slot="{ad_unit_id}"
             data-ad-format="auto"
             data-full-width-responsive="true"></ins>
        <script>
             (adsbygoogle = window.adsbygoogle || []).push({{}});
        </script>
        """
    
    def get_revenue_data(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Get revenue data from Google AdSense"""
        try:
            access_token = self._get_access_token()
            headers = {'Authorization': f'Bearer {access_token}'}
            
            # Format dates for AdSense API
            start_date_str = start_date.strftime('%Y-%m-%d')
            end_date_str = end_date.strftime('%Y-%m-%d')
            
            params = {
                'dateRange': 'CUSTOM',
                'startDate.year': start_date.year,
                'startDate.month': start_date.month,
                'startDate.day': start_date.day,
                'endDate.year': end_date.year,
                'endDate.month': end_date.month,
                'endDate.day': end_date.day,
                'metrics': ['ESTIMATED_EARNINGS', 'PAGE_VIEWS', 'CLICKS'],
                'dimensions': ['DATE']
            }
            
            endpoint = f"accounts/{self.publisher_id}/reports:generate"
            response = self._make_request('GET', endpoint, data=params, headers=headers)
            
            data = response.json()
            revenue_data = []
            
            for row in data.get('rows', []):
                revenue_data.append({
                    'date': row['cells'][0]['value'],
                    'revenue': Decimal(row['cells'][1]['value']),
                    'impressions': int(row['cells'][2]['value']),
                    'clicks': int(row['cells'][3]['value']),
                    'currency': 'USD'
                })
            
            return revenue_data
            
        except Exception as e:
            logger.error(f"Error fetching AdSense revenue data: {str(e)}")
            return []


class FacebookAudienceNetworkProvider(BaseAdProvider):
    """Facebook Audience Network provider implementation"""
    
    def __init__(self, provider_config: Dict[str, Any]):
        super().__init__(provider_config)
        self.app_id = provider_config.get('app_id')
        self.placement_ids = provider_config.get('placement_ids', [])
        self.base_url = 'https://graph.facebook.com/v18.0'
    
    def get_provider_name(self) -> str:
        return "Facebook Audience Network"
    
    def validate_config(self) -> bool:
        """Validate Facebook Audience Network configuration"""
        required_fields = ['api_key', 'app_id']
        for field in required_fields:
            if not self.config.get(field):
                logger.error(f"Missing required field for Facebook Audience Network: {field}")
                return False
        return True
    
    def get_ads(self, targeting_criteria: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch ads from Facebook Audience Network"""
        try:
            # Facebook Audience Network typically uses client-side SDK
            # This method returns placement configurations
            
            relevant_ads = []
            for placement_id in self.placement_ids[:limit]:
                relevant_ads.append({
                    'id': placement_id,
                    'title': 'Facebook Audience Network Ad',
                    'description': 'Facebook Audience Network Advertisement',
                    'placement_id': placement_id,
                    'ad_format': 'banner',  # or 'interstitial', 'native', 'rewarded_video'
                    'sdk_config': {
                        'app_id': self.app_id,
                        'placement_id': placement_id
                    }
                })
            
            return relevant_ads
            
        except Exception as e:
            logger.error(f"Error fetching Facebook Audience Network ads: {str(e)}")
            return []
    
    def get_revenue_data(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """Get revenue data from Facebook Audience Network"""
        try:
            params = {
                'access_token': self.api_key,
                'time_range': json.dumps({
                    'since': start_date.strftime('%Y-%m-%d'),
                    'until': end_date.strftime('%Y-%m-%d')
                }),
                'breakdowns': ['placement_id'],
                'metrics': ['fb_ad_network_revenue', 'fb_ad_network_request', 'fb_ad_network_filled_request']
            }
            
            endpoint = f"{self.app_id}/app_insights"
            response = self._make_request('GET', endpoint, data=params)
            
            data = response.json()
            revenue_data = []
            
            for item in data.get('data', []):
                revenue_data.append({
                    'placement_id': item.get('placement_id'),
                    'revenue': Decimal(str(item.get('fb_ad_network_revenue', 0))),
                    'requests': item.get('fb_ad_network_request', 0),
                    'filled_requests': item.get('fb_ad_network_filled_request', 0),
                    'currency': 'USD'
                })
            
            return revenue_data
            
        except Exception as e:
            logger.error(f"Error fetching Facebook Audience Network revenue: {str(e)}")
            return []


class CustomNetworkProvider(BaseAdProvider):
    """Custom ad network provider for direct campaigns"""
    
    def get_provider_name(self) -> str:
        return "Custom Network"
    
    def validate_config(self) -> bool:
        """Validate custom network configuration"""
        return True  # Minimal validation for custom networks
    
    def get_ads(self, targeting_criteria: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Return locally configured ads"""
        # This would typically return ads from your local database
        # that are configured as direct/custom campaigns
        return []


class AdMobProvider(BaseAdProvider):
    """Google AdMob provider for mobile apps"""
    
    def __init__(self, provider_config: Dict[str, Any]):
        super().__init__(provider_config)
        self.app_id = provider_config.get('app_id')
        self.ad_unit_ids = provider_config.get('ad_unit_ids', {})
        self.base_url = 'https://admob.googleapis.com/v1'
    
    def get_provider_name(self) -> str:
        return "Google AdMob"
    
    def validate_config(self) -> bool:
        """Validate AdMob configuration"""
        required_fields = ['app_id', 'api_key']
        for field in required_fields:
            if not self.config.get(field):
                logger.error(f"Missing required field for AdMob: {field}")
                return False
        return True
    
    def get_ads(self, targeting_criteria: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch ads from AdMob"""
        try:
            relevant_ads = []
            ad_formats = ['banner', 'interstitial', 'rewarded', 'native']
            
            for ad_format in ad_formats[:limit]:
                ad_unit_id = self.ad_unit_ids.get(ad_format)
                if ad_unit_id:
                    relevant_ads.append({
                        'id': ad_unit_id,
                        'title': f'AdMob {ad_format.title()} Ad',
                        'description': f'Google AdMob {ad_format} Advertisement',
                        'ad_unit_id': ad_unit_id,
                        'ad_format': ad_format,
                        'app_id': self.app_id
                    })
            
            return relevant_ads
            
        except Exception as e:
            logger.error(f"Error fetching AdMob ads: {str(e)}")
            return []


class UnityAdsProvider(BaseAdProvider):
    """Unity Ads provider for gaming apps"""
    
    def __init__(self, provider_config: Dict[str, Any]):
        super().__init__(provider_config)
        self.game_id = provider_config.get('game_id')
        self.placement_ids = provider_config.get('placement_ids', {})
        self.base_url = 'https://gameads-admin.applifier.com/stats/monetization-api'
    
    def get_provider_name(self) -> str:
        return "Unity Ads"
    
    def validate_config(self) -> bool:
        """Validate Unity Ads configuration"""
        required_fields = ['game_id', 'api_key']
        for field in required_fields:
            if not self.config.get(field):
                logger.error(f"Missing required field for Unity Ads: {field}")
                return False
        return True
    
    def get_ads(self, targeting_criteria: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        """Fetch ads from Unity Ads"""
        try:
            relevant_ads = []
            
            for placement_name, placement_id in list(self.placement_ids.items())[:limit]:
                relevant_ads.append({
                    'id': placement_id,
                    'title': f'Unity Ads {placement_name}',
                    'description': f'Unity Ads {placement_name} Advertisement',
                    'placement_id': placement_id,
                    'game_id': self.game_id,
                    'ad_format': placement_name
                })
            
            return relevant_ads
            
        except Exception as e:
            logger.error(f"Error fetching Unity Ads: {str(e)}")
            return []


class AdProviderRegistry:
    """Registry for managing ad providers"""
    
    _providers = {
        'google_adsense': GoogleAdSenseProvider,
        'facebook_audience_network': FacebookAudienceNetworkProvider,
        'google_admob': AdMobProvider,
        'unity_ads': UnityAdsProvider,
        'custom_network': CustomNetworkProvider,
    }
    
    _instances = {}
    
    @classmethod
    def register_provider(cls, provider_type: str, provider_class: type):
        """Register a new ad provider"""
        if not issubclass(provider_class, BaseAdProvider):
            raise ValueError("Provider class must inherit from BaseAdProvider")
        
        cls._providers[provider_type] = provider_class
        logger.info(f"Registered ad provider: {provider_type}")
    
    @classmethod
    def get_provider(cls, provider_type: str) -> Optional[BaseAdProvider]:
        """Get ad provider instance"""
        if provider_type not in cls._providers:
            logger.error(f"Unknown ad provider type: {provider_type}")
            return None
        
        # Return cached instance if available
        if provider_type in cls._instances:
            return cls._instances[provider_type]
        
        # Get provider configuration from settings
        provider_config = cls._get_provider_config(provider_type)
        if not provider_config:
            logger.error(f"No configuration found for provider: {provider_type}")
            return None
        
        try:
            # Create and validate provider instance
            provider_class = cls._providers[provider_type]
            provider_instance = provider_class(provider_config)
            
            if not provider_instance.validate_config():
                logger.error(f"Invalid configuration for provider: {provider_type}")
                return None
            
            # Cache the instance
            cls._instances[provider_type] = provider_instance
            return provider_instance
            
        except Exception as e:
            logger.error(f"Error creating provider instance for {provider_type}: {str(e)}")
            return None
    
    @classmethod
    def get_available_providers(cls) -> List[str]:
        """Get list of available provider types"""
        return list(cls._providers.keys())
    
    @classmethod
    def clear_cache(cls):
        """Clear provider instance cache"""
        cls._instances.clear()
    
    @staticmethod
    def _get_provider_config(provider_type: str) -> Optional[Dict[str, Any]]:
        """Get provider configuration from Django settings"""
        ad_providers_config = getattr(settings, 'AD_PROVIDERS', {})
        return ad_providers_config.get(provider_type)


# Utility functions for ad provider management

def get_provider_health_status() -> Dict[str, Dict[str, Any]]:
    """Check health status of all configured ad providers"""
    registry = AdProviderRegistry()
    health_status = {}
    
    for provider_type in registry.get_available_providers():
        try:
            provider = registry.get_provider(provider_type)
            if provider:
                # Basic health check - attempt to validate config
                is_healthy = provider.validate_config()
                health_status[provider_type] = {
                    'status': 'healthy' if is_healthy else 'unhealthy',
                    'provider_name': provider.get_provider_name(),
                    'last_checked': timezone.now().isoformat()
                }
            else:
                health_status[provider_type] = {
                    'status': 'unavailable',
                    'provider_name': provider_type,
                    'last_checked': timezone.now().isoformat()
                }
        except Exception as e:
            health_status[provider_type] = {
                'status': 'error',
                'error': str(e),
                'last_checked': timezone.now().isoformat()
            }
    
    return health_status


def refresh_provider_cache():
    """Refresh all provider caches"""
    AdProviderRegistry.clear_cache()
    logger.info("Ad provider cache cleared")


def test_provider_connection(provider_type: str) -> Dict[str, Any]:
    """Test connection to a specific ad provider"""
    registry = AdProviderRegistry()
    
    try:
        provider = registry.get_provider(provider_type)
        if not provider:
            return {
                'status': 'error',
                'message': f'Provider {provider_type} not available'
            }
        
        # Test with dummy targeting criteria
        test_criteria = {
            'placement_slug': 'test',
            'country': 'US',
            'device_type': 'desktop'
        }
        
        ads = provider.get_ads(test_criteria, limit=1)
        
        return {
            'status': 'success',
            'provider_name': provider.get_provider_name(),
            'ads_returned': len(ads),
            'test_time': timezone.now().isoformat()
        }
        
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e),
            'test_time': timezone.now().isoformat()
        }