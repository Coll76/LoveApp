# apps/ideas/validators.py
import re
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Any, Union
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils.html import strip_tags
from django.utils.text import slugify

logger = logging.getLogger(__name__)

# Constants for validation
MAX_PROMPT_LENGTH = 10000
MIN_PROMPT_LENGTH = 10
MAX_INTERESTS_LENGTH = 1000
MIN_INTERESTS_LENGTH = 5
MAX_CITY_NAME_LENGTH = 100
MIN_CITY_NAME_LENGTH = 2
MAX_REQUIREMENTS_LENGTH = 2000
MAX_COMMENT_LENGTH = 1000
MIN_RATING = 1
MAX_RATING = 5

# Profanity and spam patterns (basic implementation)
PROFANITY_PATTERNS = [
    r'\b(spam|scam|fake|bot|test)\b',
    r'\b(fuck|shit|damn|hell|bitch)\b',
    r'\b(xxx|porn|sex|adult)\b',
]

SPAM_PATTERNS = [
    r'(https?://\S+){3,}',  # Multiple URLs
    r'(.)\1{10,}',  # Repeated characters
    r'\b(buy now|click here|free money|make money)\b',
    r'[A-Z]{5,}\s[A-Z]{5,}',  # All caps words
]

# Suspicious IP patterns
SUSPICIOUS_IP_PATTERNS = [
    r'^10\.',  # Private network
    r'^192\.168\.',  # Private network
    r'^172\.(1[6-9]|2[0-9]|3[01])\.',  # Private network
    r'^127\.',  # Localhost
]

# Valid location types and other choices (should match model choices)
VALID_BUDGET_CHOICES = ['low', 'moderate', 'high', 'luxury']
VALID_LOCATION_TYPES = ['indoor', 'outdoor', 'home', 'restaurant', 'activity', 'travel', 'any']
VALID_DURATION_CHOICES = ['quick', 'half_day', 'full_day', 'weekend', 'week_plus']
VALID_PERSONALITY_TYPES = ['adventurous', 'romantic', 'intellectual', 'active', 'relaxed', 'creative']
VALID_FEEDBACK_TYPES = ['rating', 'comment', 'report', 'suggestion']
VALID_REPORT_REASONS = ['inappropriate', 'spam', 'offensive', 'copyright', 'other']


class ValidationError(Exception):
    """Custom validation error for ideas app"""
    def __init__(self, message: str, code: str = None):
        self.message = message
        self.code = code
        super().__init__(self.message)


def validate_text_content(text: str, field_name: str = "text", 
                         min_length: int = 1, max_length: int = 1000,
                         allow_html: bool = False, check_profanity: bool = True) -> str:
    """
    Validate and sanitize text content
    
    Args:
        text: Text to validate
        field_name: Name of the field for error messages
        min_length: Minimum allowed length
        max_length: Maximum allowed length
        allow_html: Whether to allow HTML tags
        check_profanity: Whether to check for profanity
        
    Returns:
        Cleaned text
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(text, str):
        raise ValidationError(f"{field_name} must be a string")
    
    # Strip whitespace
    text = text.strip()
    
    # Check length
    if len(text) < min_length:
        raise ValidationError(
            f"{field_name} must be at least {min_length} characters long"
        )
    
    if len(text) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )
    
    # Remove HTML tags if not allowed
    if not allow_html:
        text = strip_tags(text)
    
    # Check for profanity
    if check_profanity and _contains_profanity(text):
        logger.warning(f"Profanity detected in {field_name}: {text[:50]}...")
        raise ValidationError(f"{field_name} contains inappropriate content")
    
    # Check for spam patterns
    if _contains_spam_patterns(text):
        logger.warning(f"Spam patterns detected in {field_name}: {text[:50]}...")
        raise ValidationError(f"{field_name} appears to be spam")
    
    return text


def validate_idea_request_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Comprehensive validation for idea request data
    
    Args:
        data: Dictionary containing idea request data
        
    Returns:
        Validated and cleaned data
        
    Raises:
        ValidationError: If validation fails
    """
    validated_data = {}
    
    # Validate title (optional)
    if 'title' in data and data['title']:
        validated_data['title'] = validate_text_content(
            data['title'], 
            field_name="title",
            min_length=5,
            max_length=200,
            check_profanity=True
        )
    
    # Validate occasion (optional)
    if 'occasion' in data and data['occasion']:
        validated_data['occasion'] = validate_text_content(
            data['occasion'],
            field_name="occasion",
            min_length=3,
            max_length=100
        )
    
    # Validate interests
    if 'partner_interests' in data and data['partner_interests']:
        validated_data['partner_interests'] = validate_interests(
            data['partner_interests'], 
            "partner_interests"
        )
    
    if 'user_interests' in data and data['user_interests']:
        validated_data['user_interests'] = validate_interests(
            data['user_interests'], 
            "user_interests"
        )
    
    # Validate personality type
    if 'personality_type' in data and data['personality_type']:
        validated_data['personality_type'] = validate_choice(
            data['personality_type'],
            VALID_PERSONALITY_TYPES,
            "personality_type"
        )
    
    # Validate budget
    if 'budget' in data and data['budget']:
        validated_data['budget'] = validate_choice(
            data['budget'],
            VALID_BUDGET_CHOICES,
            "budget"
        )
    
    # Validate location
    if 'location_type' in data and data['location_type']:
        validated_data['location_type'] = validate_choice(
            data['location_type'],
            VALID_LOCATION_TYPES,
            "location_type"
        )
    
    if 'location_city' in data and data['location_city']:
        validated_data['location_city'] = validate_city_name(data['location_city'])
    
    # Validate duration
    if 'duration' in data and data['duration']:
        validated_data['duration'] = validate_choice(
            data['duration'],
            VALID_DURATION_CHOICES,
            "duration"
        )
    
    # Validate special requirements
    if 'special_requirements' in data and data['special_requirements']:
        validated_data['special_requirements'] = validate_text_content(
            data['special_requirements'],
            field_name="special_requirements",
            min_length=5,
            max_length=MAX_REQUIREMENTS_LENGTH
        )
    
    # Validate custom prompt
    if 'custom_prompt' in data and data['custom_prompt']:
        validated_data['custom_prompt'] = validate_ai_prompt(data['custom_prompt'])
    
    # Validate AI parameters
    if 'temperature' in data:
        validated_data['temperature'] = validate_temperature(data['temperature'])
    
    if 'max_tokens' in data:
        validated_data['max_tokens'] = validate_max_tokens(data['max_tokens'])
    
    if 'ai_model' in data and data['ai_model']:
        validated_data['ai_model'] = validate_ai_model(data['ai_model'])
    
    # Ensure at least some meaningful input is provided
    required_fields = ['partner_interests', 'user_interests', 'custom_prompt', 'occasion']
    if not any(validated_data.get(field) for field in required_fields):
        raise ValidationError(
            "Please provide at least one of: partner interests, your interests, "
            "occasion, or custom prompt"
        )
    
    return validated_data


def validate_interests(interests: str, field_name: str) -> str:
    """
    Validate interests field with specific rules
    
    Args:
        interests: Interests string to validate
        field_name: Name of the field for error messages
        
    Returns:
        Validated interests string
        
    Raises:
        ValidationError: If validation fails
    """
    interests = validate_text_content(
        interests,
        field_name=field_name,
        min_length=MIN_INTERESTS_LENGTH,
        max_length=MAX_INTERESTS_LENGTH,
        check_profanity=True
    )
    
    # Check for meaningful content (not just repeated words)
    words = interests.lower().split()
    if len(set(words)) < 3:
        raise ValidationError(
            f"{field_name} should contain at least 3 different words"
        )
    
    # Check for valid interest patterns
    if not _contains_valid_interests(interests):
        raise ValidationError(
            f"{field_name} should describe actual interests or activities"
        )
    
    return interests


def validate_ai_prompt(prompt: str) -> str:
    """
    Validate AI prompt with security checks
    
    Args:
        prompt: AI prompt to validate
        
    Returns:
        Validated prompt
        
    Raises:
        ValidationError: If validation fails
    """
    prompt = validate_text_content(
        prompt,
        field_name="custom_prompt",
        min_length=MIN_PROMPT_LENGTH,
        max_length=MAX_PROMPT_LENGTH,
        allow_html=False,
        check_profanity=True
    )
    
    # Check for prompt injection attempts
    if _contains_prompt_injection(prompt):
        logger.warning(f"Prompt injection attempt detected: {prompt[:100]}...")
        raise ValidationError("Prompt contains potentially harmful instructions")
    
    # Check for system command attempts
    if _contains_system_commands(prompt):
        logger.warning(f"System command attempt detected: {prompt[:100]}...")
        raise ValidationError("Prompt contains invalid system commands")
    
    return prompt


def validate_temperature(temperature: Union[str, float, int]) -> float:
    """
    Validate AI temperature parameter
    
    Args:
        temperature: Temperature value to validate
        
    Returns:
        Validated temperature as float
        
    Raises:
        ValidationError: If validation fails
    """
    try:
        temp = float(temperature)
    except (ValueError, TypeError):
        raise ValidationError("Temperature must be a number")
    
    if not 0.0 <= temp <= 2.0:
        raise ValidationError("Temperature must be between 0.0 and 2.0")
    
    return temp


def validate_max_tokens(max_tokens: Union[str, int]) -> int:
    """
    Validate max_tokens parameter
    
    Args:
        max_tokens: Max tokens value to validate
        
    Returns:
        Validated max_tokens as integer
        
    Raises:
        ValidationError: If validation fails
    """
    try:
        tokens = int(max_tokens)
    except (ValueError, TypeError):
        raise ValidationError("Max tokens must be an integer")
    
    if not 100 <= tokens <= 4000:
        raise ValidationError("Max tokens must be between 100 and 4000")
    
    return tokens


def validate_ai_model(model: str) -> str:
    """
    Validate AI model name
    
    Args:
        model: Model name to validate
        
    Returns:
        Validated model name
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(model, str):
        raise ValidationError("AI model must be a string")
    
    model = model.strip().lower()
    
    # Get available models from settings or default list
    available_models = getattr(settings, 'AVAILABLE_AI_MODELS', ['deepseek', 'openai'])
    
    if model not in available_models:
        raise ValidationError(f"Invalid AI model. Available models: {', '.join(available_models)}")
    
    return model


def validate_city_name(city: str) -> str:
    """
    Validate city name
    
    Args:
        city: City name to validate
        
    Returns:
        Validated city name
        
    Raises:
        ValidationError: If validation fails
    """
    city = validate_text_content(
        city,
        field_name="city",
        min_length=MIN_CITY_NAME_LENGTH,
        max_length=MAX_CITY_NAME_LENGTH,
        check_profanity=False
    )
    
    # Check for valid city name pattern
    if not re.match(r'^[a-zA-Z\s\-\.\']+$', city):
        raise ValidationError("City name contains invalid characters")
    
    return city.title()  # Capitalize properly


def validate_choice(value: str, valid_choices: List[str], field_name: str) -> str:
    """
    Validate choice field against valid options
    
    Args:
        value: Value to validate
        valid_choices: List of valid choices
        field_name: Name of the field for error messages
        
    Returns:
        Validated choice
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    
    value = value.strip().lower()
    
    if value not in valid_choices:
        raise ValidationError(
            f"Invalid {field_name}. Valid choices: {', '.join(valid_choices)}"
        )
    
    return value


def validate_rating(rating: Union[str, int, float]) -> int:
    """
    Validate rating value
    
    Args:
        rating: Rating value to validate
        
    Returns:
        Validated rating as integer
        
    Raises:
        ValidationError: If validation fails
    """
    try:
        rating_int = int(rating)
    except (ValueError, TypeError):
        raise ValidationError("Rating must be a number")
    
    if not MIN_RATING <= rating_int <= MAX_RATING:
        raise ValidationError(f"Rating must be between {MIN_RATING} and {MAX_RATING}")
    
    return rating_int


def validate_feedback_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate feedback data
    
    Args:
        data: Feedback data to validate
        
    Returns:
        Validated feedback data
        
    Raises:
        ValidationError: If validation fails
    """
    validated_data = {}
    
    # Validate feedback type
    if 'feedback_type' not in data:
        raise ValidationError("Feedback type is required")
    
    feedback_type = validate_choice(
        data['feedback_type'],
        VALID_FEEDBACK_TYPES,
        "feedback_type"
    )
    validated_data['feedback_type'] = feedback_type
    
    # Validate based on feedback type
    if feedback_type == 'rating':
        if 'rating' not in data:
            raise ValidationError("Rating is required for rating feedback")
        validated_data['rating'] = validate_rating(data['rating'])
    
    elif feedback_type == 'comment':
        if 'comment' not in data or not data['comment']:
            raise ValidationError("Comment is required for comment feedback")
        validated_data['comment'] = validate_text_content(
            data['comment'],
            field_name="comment",
            min_length=10,
            max_length=MAX_COMMENT_LENGTH,
            check_profanity=True
        )
    
    elif feedback_type == 'report':
        if 'report_reason' not in data:
            raise ValidationError("Report reason is required for report feedback")
        validated_data['report_reason'] = validate_choice(
            data['report_reason'],
            VALID_REPORT_REASONS,
            "report_reason"
        )
        
        # Optional comment for reports
        if 'comment' in data and data['comment']:
            validated_data['comment'] = validate_text_content(
                data['comment'],
                field_name="report_comment",
                min_length=5,
                max_length=MAX_COMMENT_LENGTH,
                check_profanity=False  # Don't check profanity in reports
            )
    
    return validated_data


def validate_ip_address(ip_address: str) -> bool:
    """
    Validate and check IP address for suspicious patterns
    
    Args:
        ip_address: IP address to validate
        
    Returns:
        True if IP is valid and not suspicious
        
    Raises:
        ValidationError: If IP is invalid or suspicious
    """
    if not ip_address:
        return True  # Allow empty IP
    
    # Basic IP format validation
    ip_pattern = r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    if not re.match(ip_pattern, ip_address):
        raise ValidationError("Invalid IP address format")
    
    # Check for suspicious patterns
    for pattern in SUSPICIOUS_IP_PATTERNS:
        if re.match(pattern, ip_address):
            logger.warning(f"Suspicious IP detected: {ip_address}")
            # Don't raise error for private IPs in development
            if not getattr(settings, 'DEBUG', False):
                raise ValidationError("Request from suspicious IP address")
    
    return True


def validate_search_query(query: str) -> str:
    """
    Validate search query
    
    Args:
        query: Search query to validate
        
    Returns:
        Validated search query
        
    Raises:
        ValidationError: If validation fails
    """
    query = validate_text_content(
        query,
        field_name="search_query",
        min_length=3,
        max_length=200,
        check_profanity=True
    )
    
    # Remove potentially dangerous characters
    query = re.sub(r'[<>"\']', '', query)
    
    return query


def validate_bulk_operation_data(data: List[Dict[str, Any]], max_items: int = 50) -> List[Dict[str, Any]]:
    """
    Validate bulk operation data
    
    Args:
        data: List of items to validate
        max_items: Maximum number of items allowed
        
    Returns:
        Validated data list
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(data, list):
        raise ValidationError("Bulk data must be a list")
    
    if len(data) == 0:
        raise ValidationError("Bulk data cannot be empty")
    
    if len(data) > max_items:
        raise ValidationError(f"Bulk operation limited to {max_items} items")
    
    validated_items = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValidationError(f"Item {i} must be a dictionary")
        
        # Validate required fields
        if 'id' not in item:
            raise ValidationError(f"Item {i} missing required 'id' field")
        
        try:
            item['id'] = int(item['id'])
        except (ValueError, TypeError):
            raise ValidationError(f"Item {i} has invalid 'id' field")
        
        validated_items.append(item)
    
    return validated_items


# Helper functions

def _contains_profanity(text: str) -> bool:
    """Check if text contains profanity"""
    text_lower = text.lower()
    for pattern in PROFANITY_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            return True
    return False


def _contains_spam_patterns(text: str) -> bool:
    """Check if text contains spam patterns"""
    for pattern in SPAM_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _contains_valid_interests(interests: str) -> bool:
    """Check if interests contain valid activity/interest keywords"""
    # Basic validation - could be enhanced with ML or more sophisticated rules
    valid_keywords = [
        'music', 'movie', 'book', 'sport', 'food', 'travel', 'art', 'dance',
        'cook', 'read', 'watch', 'play', 'listen', 'walk', 'run', 'swim',
        'hiking', 'gaming', 'photography', 'painting', 'writing', 'learning'
    ]
    
    interests_lower = interests.lower()
    return any(keyword in interests_lower for keyword in valid_keywords)


def _contains_prompt_injection(prompt: str) -> bool:
    """Check for prompt injection attempts"""
    injection_patterns = [
        r'ignore\s+(previous|above|all)\s+instructions',
        r'system\s*:\s*',
        r'assistant\s*:\s*',
        r'human\s*:\s*',
        r'ai\s*:\s*',
        r'pretend\s+to\s+be',
        r'act\s+as\s+if',
        r'forget\s+everything',
        r'new\s+instructions',
        r'override\s+instructions',
    ]
    
    prompt_lower = prompt.lower()
    for pattern in injection_patterns:
        if re.search(pattern, prompt_lower):
            return True
    
    return False


def _contains_system_commands(prompt: str) -> bool:
    """Check for system command attempts"""
    command_patterns = [
        r'\$\s*\w+',  # Shell variables
        r';\s*rm\s+',  # Dangerous commands
        r';\s*del\s+',
        r';\s*sudo\s+',
        r'exec\s*\(',
        r'eval\s*\(',
        r'__import__',
        r'subprocess',
        r'os\.system',
    ]
    
    for pattern in command_patterns:
        if re.search(pattern, prompt, re.IGNORECASE):
            return True
    
    return False


def validate_json_data(data: str, max_size: int = 10000) -> Dict[str, Any]:
    """
    Validate and parse JSON data
    
    Args:
        data: JSON string to validate
        max_size: Maximum size in characters
        
    Returns:
        Parsed JSON data
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(data, str):
        raise ValidationError("JSON data must be a string")
    
    if len(data) > max_size:
        raise ValidationError(f"JSON data too large (max {max_size} characters)")
    
    try:
        parsed_data = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Invalid JSON format: {str(e)}")
    
    return parsed_data


def validate_url(url: str, allowed_domains: List[str] = None) -> str:
    """
    Validate URL format and domain
    
    Args:
        url: URL to validate
        allowed_domains: List of allowed domains (optional)
        
    Returns:
        Validated URL
        
    Raises:
        ValidationError: If validation fails
    """
    if not isinstance(url, str):
        raise ValidationError("URL must be a string")
    
    url = url.strip()
    
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValidationError("Invalid URL format")
    
    if not parsed.scheme or not parsed.netloc:
        raise ValidationError("URL must include protocol and domain")
    
    if parsed.scheme not in ['http', 'https']:
        raise ValidationError("URL must use HTTP or HTTPS protocol")
    
    if allowed_domains:
        domain = parsed.netloc.lower()
        if not any(domain.endswith(allowed_domain) for allowed_domain in allowed_domains):
            raise ValidationError(f"URL domain not allowed. Allowed domains: {', '.join(allowed_domains)}")
    
    return url