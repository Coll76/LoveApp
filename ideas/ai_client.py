# apps/ideas/ai_client.py
import logging
import json
import time
import requests
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.core.exceptions import ValidationError

from core.exceptions import ServiceUnavailableError, ValidationError as CustomValidationError

logger = logging.getLogger(__name__)


@dataclass
class AIResponse:
    """Data class for AI API responses"""
    content: str
    model: str
    usage: Dict[str, int]
    response_time: float
    timestamp: datetime
    raw_response: Dict[str, Any]


@dataclass
class AIModelConfig:
    """Configuration for AI models"""
    name: str
    api_endpoint: str
    api_key: str
    max_tokens: int
    temperature_range: tuple
    rate_limit_per_minute: int
    timeout_seconds: int
    supports_streaming: bool = False


class AIProviderError(Exception):
    """Custom exception for AI provider errors"""
    pass


class RateLimitError(AIProviderError):
    """Exception for rate limit exceeded"""
    pass


class AIClient:
    """
    AI Client for generating date ideas using DeepSeek and other providers
    Supports multiple AI providers with failover capabilities
    """
    
    def __init__(self):
        self.providers = self._initialize_providers()
        self.default_provider = getattr(settings, 'DEFAULT_AI_PROVIDER', 'deepseek')
        self.rate_limit_cache_prefix = 'ai_rate_limit'
        self.response_cache_prefix = 'ai_response'
        self.cache_timeout = getattr(settings, 'AI_RESPONSE_CACHE_TIMEOUT', 3600)
        
    def _initialize_providers(self) -> Dict[str, AIModelConfig]:
        """Initialize AI provider configurations"""
        providers = {}
        
        # DeepSeek Configuration
        deepseek_config = AIModelConfig(
            name='deepseek',
            api_endpoint=getattr(settings, 'DEEPSEEK_API_ENDPOINT', 'http://localhost:11434/api/generate'),
            api_key=getattr(settings, 'DEEPSEEK_API_KEY', ''),
            max_tokens=4000,
            temperature_range=(0.0, 2.0),
            rate_limit_per_minute=60,
            timeout_seconds=30,
            supports_streaming=True
        )
        providers['deepseek'] = deepseek_config
        
        # OpenAI Configuration (fallback)
        if hasattr(settings, 'OPENAI_API_KEY') and settings.OPENAI_API_KEY:
            openai_config = AIModelConfig(
                name='openai',
                api_endpoint='https://api.openai.com/v1/chat/completions',
                api_key=settings.OPENAI_API_KEY,
                max_tokens=4000,
                temperature_range=(0.0, 2.0),
                rate_limit_per_minute=60,
                timeout_seconds=30,
                supports_streaming=True
            )
            providers['openai'] = openai_config
        
        return providers
    
    def generate_completion(
        self,
        prompt: str,
        model: str = None,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        user_id: int = None,
        system_prompt: str = None,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Generate AI completion for given prompt
        
        Args:
            prompt: The input prompt
            model: AI model to use (defaults to configured default)
            temperature: Creativity level (0.0-2.0)
            max_tokens: Maximum tokens to generate
            user_id: User ID for rate limiting and analytics
            system_prompt: System prompt for context
            use_cache: Whether to use cached responses
            
        Returns:
            Dict containing AI response data
            
        Raises:
            ServiceUnavailableError: If AI service is unavailable
            RateLimitError: If rate limit is exceeded
            ValidationError: If input parameters are invalid
        """
        try:
            # Validate inputs
            self._validate_completion_request(prompt, temperature, max_tokens)
            
            # Use default model if not specified
            if not model:
                model = self.default_provider
            
            # Check if provider exists
            if model not in self.providers:
                logger.warning(f"Unknown AI provider: {model}, falling back to default")
                model = self.default_provider
            
            provider_config = self.providers[model]
            
            # Check rate limits
            if user_id:
                self._check_rate_limit(user_id, provider_config)
            
            # Check cache for similar requests
            if use_cache:
                cached_response = self._get_cached_response(prompt, model, temperature)
                if cached_response:
                    logger.info(f"Returning cached response for user {user_id}")
                    return cached_response
            
            # Generate completion based on provider
            if model == 'deepseek':
                response = self._generate_deepseek_completion(
                    prompt, temperature, max_tokens, system_prompt, provider_config
                )
            elif model == 'openai':
                response = self._generate_openai_completion(
                    prompt, temperature, max_tokens, system_prompt, provider_config
                )
            else:
                raise AIProviderError(f"Unsupported AI provider: {model}")
            
            # Cache the response
            if use_cache and response:
                self._cache_response(prompt, model, temperature, response)
            
            # Update rate limit counter
            if user_id:
                self._update_rate_limit_counter(user_id, provider_config)
            
            # Log successful generation
            self._log_generation_success(user_id, model, response)
            
            return response
            
        except RateLimitError:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            raise
        except AIProviderError as e:
            logger.error(f"AI Provider error: {str(e)}")
            # Try fallback provider
            if model != 'openai' and 'openai' in self.providers:
                logger.info("Attempting fallback to OpenAI")
                return self.generate_completion(
                    prompt, 'openai', temperature, max_tokens, user_id, system_prompt, use_cache
                )
            raise ServiceUnavailableError(f"AI service unavailable: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in AI completion: {str(e)}")
            raise ServiceUnavailableError(f"AI service error: {str(e)}")
    
    def _validate_completion_request(self, prompt: str, temperature: float, max_tokens: int) -> None:
        """Validate completion request parameters"""
        if not prompt or not prompt.strip():
            raise CustomValidationError("Prompt cannot be empty")
        
        if len(prompt) > 10000:
            raise CustomValidationError("Prompt too long (max 10000 characters)")
        
        if not (0.0 <= temperature <= 2.0):
            raise CustomValidationError("Temperature must be between 0.0 and 2.0")
        
        if not (100 <= max_tokens <= 4000):
            raise CustomValidationError("max_tokens must be between 100 and 4000")
    
    def _check_rate_limit(self, user_id: int, provider_config: AIModelConfig) -> None:
        """Check if user has exceeded rate limits"""
        cache_key = f"{self.rate_limit_cache_prefix}_{user_id}_{provider_config.name}"
        current_requests = cache.get(cache_key, 0)
        
        if current_requests >= provider_config.rate_limit_per_minute:
            raise RateLimitError("Rate limit exceeded. Please try again later.")
    
    def _update_rate_limit_counter(self, user_id: int, provider_config: AIModelConfig) -> None:
        """Update rate limit counter"""
        cache_key = f"{self.rate_limit_cache_prefix}_{user_id}_{provider_config.name}"
        current_requests = cache.get(cache_key, 0)
        cache.set(cache_key, current_requests + 1, 60)  # 1 minute timeout
    
    def _get_cached_response(self, prompt: str, model: str, temperature: float) -> Optional[Dict]:
        """Get cached response if available"""
        cache_key = self._generate_cache_key(prompt, model, temperature)
        return cache.get(cache_key)
    
    def _cache_response(self, prompt: str, model: str, temperature: float, response: Dict) -> None:
        """Cache AI response"""
        cache_key = self._generate_cache_key(prompt, model, temperature)
        cache.set(cache_key, response, self.cache_timeout)
    
    def _generate_cache_key(self, prompt: str, model: str, temperature: float) -> str:
        """Generate cache key for response caching"""
        import hashlib
        content = f"{prompt}_{model}_{temperature}"
        hash_key = hashlib.md5(content.encode()).hexdigest()
        return f"{self.response_cache_prefix}_{hash_key}"
    
    def _generate_deepseek_completion(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str],
        config: AIModelConfig
    ) -> Dict[str, Any]:
        """Generate completion using DeepSeek (Ollama) API"""
        start_time = time.time()
        
        # Prepare the full prompt
        full_prompt = prompt
        if system_prompt:
            full_prompt = f"System: {system_prompt}\n\nUser: {prompt}"
        
        # Prepare request payload for Ollama API
        payload = {
            'model': 'deepseek-r1:7b',  # DeepSeek model name in Ollama
            'prompt': full_prompt,
            'stream': False,
            'options': {
                'temperature': temperature,
                'num_predict': max_tokens,
                'top_p': 0.9,
                'repeat_penalty': 1.1,
            }
        }
        
        headers = {
            'Content-Type': 'application/json',
        }
        
        # Add API key if required
        if config.api_key:
            headers['Authorization'] = f'Bearer {config.api_key}'
        
        try:
            response = requests.post(
                config.api_endpoint,
                json=payload,
                headers=headers,
                timeout=config.timeout_seconds
            )
            
            response.raise_for_status()
            response_data = response.json()
            
            response_time = time.time() - start_time
            
            # Parse Ollama response format
            content = response_data.get('response', '')
            
            # Extract usage information
            usage = {
                'prompt_tokens': response_data.get('prompt_eval_count', 0),
                'completion_tokens': response_data.get('eval_count', 0),
                'total_tokens': response_data.get('prompt_eval_count', 0) + response_data.get('eval_count', 0)
            }
            
            return {
                'content': content,
                'model': config.name,
                'usage': usage,
                'response_time': response_time,
                'timestamp': timezone.now(),
                'raw_response': response_data
            }
            
        except requests.exceptions.Timeout:
            raise AIProviderError("DeepSeek API request timed out")
        except requests.exceptions.ConnectionError:
            raise AIProviderError("Failed to connect to DeepSeek API")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                raise RateLimitError("DeepSeek API rate limit exceeded")
            elif e.response.status_code >= 500:
                raise AIProviderError("DeepSeek API server error")
            else:
                raise AIProviderError(f"DeepSeek API error: {e.response.status_code}")
        except json.JSONDecodeError:
            raise AIProviderError("Invalid JSON response from DeepSeek API")
        except Exception as e:
            raise AIProviderError(f"Unexpected DeepSeek API error: {str(e)}")
    
    def _generate_openai_completion(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        system_prompt: Optional[str],
        config: AIModelConfig
    ) -> Dict[str, Any]:
        """Generate completion using OpenAI API"""
        start_time = time.time()
        
        # Prepare messages for chat completion
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            'model': 'gpt-3.5-turbo',
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'top_p': 1,
            'frequency_penalty': 0,
            'presence_penalty': 0
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {config.api_key}'
        }
        
        try:
            response = requests.post(
                config.api_endpoint,
                json=payload,
                headers=headers,
                timeout=config.timeout_seconds
            )
            
            response.raise_for_status()
            response_data = response.json()
            
            response_time = time.time() - start_time
            
            # Parse OpenAI response format
            content = response_data['choices'][0]['message']['content']
            usage = response_data.get('usage', {})
            
            return {
                'content': content,
                'model': config.name,
                'usage': usage,
                'response_time': response_time,
                'timestamp': timezone.now(),
                'raw_response': response_data
            }
            
        except requests.exceptions.Timeout:
            raise AIProviderError("OpenAI API request timed out")
        except requests.exceptions.ConnectionError:
            raise AIProviderError("Failed to connect to OpenAI API")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                raise RateLimitError("OpenAI API rate limit exceeded")
            elif e.response.status_code >= 500:
                raise AIProviderError("OpenAI API server error")
            else:
                raise AIProviderError(f"OpenAI API error: {e.response.status_code}")
        except (KeyError, IndexError):
            raise AIProviderError("Invalid response format from OpenAI API")
        except json.JSONDecodeError:
            raise AIProviderError("Invalid JSON response from OpenAI API")
        except Exception as e:
            raise AIProviderError(f"Unexpected OpenAI API error: {str(e)}")
    
    def _log_generation_success(self, user_id: int, model: str, response: Dict) -> None:
        """Log successful AI generation"""
        logger.info(
            f"AI generation successful - User: {user_id}, Model: {model}, "
            f"Tokens: {response.get('usage', {}).get('total_tokens', 0)}, "
            f"Response time: {response.get('response_time', 0):.2f}s"
        )
    
    def get_available_models(self) -> List[Dict[str, Any]]:
        """Get list of available AI models"""
        models = []
        for name, config in self.providers.items():
            models.append({
                'name': name,
                'display_name': name.title(),
                'max_tokens': config.max_tokens,
                'temperature_range': config.temperature_range,
                'rate_limit_per_minute': config.rate_limit_per_minute,
                'supports_streaming': config.supports_streaming
            })
        return models
    
    def health_check(self) -> Dict[str, Any]:
        """Check health status of AI providers"""
        health_status = {}
        
        for name, config in self.providers.items():
            try:
                # Simple health check request
                test_response = self.generate_completion(
                    prompt="Hello, are you working?",
                    model=name,
                    temperature=0.1,
                    max_tokens=50,
                    use_cache=False
                )
                
                health_status[name] = {
                    'status': 'healthy',
                    'response_time': test_response.get('response_time', 0),
                    'last_checked': timezone.now().isoformat()
                }
                
            except Exception as e:
                health_status[name] = {
                    'status': 'unhealthy',
                    'error': str(e),
                    'last_checked': timezone.now().isoformat()
                }
        
        return health_status
    
    def clear_user_cache(self, user_id: int) -> None:
        """Clear cached responses for a specific user"""
        # This is a simplified implementation
        # In production, you might want to use cache tags or a more sophisticated approach
        cache_pattern = f"{self.rate_limit_cache_prefix}_{user_id}_*"
        # Note: Django's cache doesn't support pattern deletion by default
        # You might need to implement this differently based on your cache backend
        logger.info(f"Cache clearing requested for user {user_id}")
    
    def get_usage_stats(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """Get AI usage statistics for a user"""
        # This would typically query a database table where you store usage metrics
        # For now, returning a placeholder structure
        return {
            'user_id': user_id,
            'period_days': days,
            'total_requests': 0,
            'total_tokens': 0,
            'average_response_time': 0.0,
            'most_used_model': self.default_provider,
            'error_rate': 0.0
        }


# Utility functions for AI client
def get_ai_client() -> AIClient:
    """Get singleton AI client instance"""
    if not hasattr(get_ai_client, '_instance'):
        get_ai_client._instance = AIClient()
    return get_ai_client._instance


def validate_ai_response(response: Dict[str, Any]) -> bool:
    """Validate AI response structure"""
    required_fields = ['content', 'model', 'usage', 'timestamp']
    return all(field in response for field in required_fields)


def sanitize_ai_content(content: str) -> str:
    """Sanitize AI-generated content"""
    # Remove potentially harmful content
    import re
    
    # Remove HTML tags
    content = re.sub(r'<[^>]+>', '', content)
    
    # Remove excessive whitespace
    content = re.sub(r'\s+', ' ', content).strip()
    
    # Remove potential markdown links that might be malicious
    content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
    
    return content