from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
import logging

logger = logging.getLogger(__name__)

def custom_exception_handler(exc, context):
    """
    Custom exception handler for DRF
    """
    response = exception_handler(exc, context)
    
    if response is not None:
        custom_response_data = {
            'error': {
                'status_code': response.status_code,
                'message': 'An error occurred',
                'details': response.data
            }
        }
        
        # Log the exception
        logger.error(f"API Exception: {exc}", exc_info=True)
        
        response.data = custom_response_data
    
    return response

class APIException(Exception):
    """Base API exception class"""
    def __init__(self, message, status_code=status.HTTP_400_BAD_REQUEST):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class PaymentException(APIException):
    """Payment-related exceptions"""
    pass

class SubscriptionException(APIException):
    """Subscription-related exceptions"""
    pass

class UsageLimitException(APIException):
    """Usage limit exceptions"""
    def __init__(self, message="Daily usage limit exceeded"):
        super().__init__(message, status.HTTP_429_TOO_MANY_REQUESTS)

class ValidationError(APIException):
    """Validation error exception"""
    def __init__(self, message="Validation failed"):
        super().__init__(message, status.HTTP_400_BAD_REQUEST)

class ServiceUnavailableError(APIException):
    """Service unavailable exception"""
    def __init__(self, message="Service temporarily unavailable"):
        super().__init__(message, status.HTTP_503_SERVICE_UNAVAILABLE)