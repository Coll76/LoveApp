# apps/core/response.py
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict
from django.db import models
from typing import Any, Dict, List, Optional, Union
import json
import logging

logger = logging.getLogger(__name__)


class APIResponse:
    """
    Standardized API response class for consistent JSON responses
    """
    
    @staticmethod
    def success(data: Any = None, message: str = "Success", status_code: int = 200, 
                pagination: Dict = None, meta: Dict = None) -> JsonResponse:
        """
        Return successful API response
        
        Args:
            data: Response data
            message: Success message
            status_code: HTTP status code
            pagination: Pagination information
            meta: Additional metadata
        """
        response_data = {
            'success': True,
            'message': message,
            'data': APIResponse._serialize_data(data) if data is not None else None
        }
        
        if pagination:
            response_data['pagination'] = pagination
            
        if meta:
            response_data['meta'] = meta
        
        return JsonResponse(
            response_data, 
            status=status_code,
            encoder=DjangoJSONEncoder,
            safe=False
        )
    
    @staticmethod
    def error(message: str = "An error occurred", status_code: int = 400, 
              errors: Dict = None, error_code: str = None) -> JsonResponse:
        """
        Return error API response
        
        Args:
            message: Error message
            status_code: HTTP status code
            errors: Detailed error information
            error_code: Custom error code
        """
        response_data = {
            'success': False,
            'message': message,
            'data': None
        }
        
        if errors:
            response_data['errors'] = errors
            
        if error_code:
            response_data['error_code'] = error_code
        
        return JsonResponse(
            response_data, 
            status=status_code,
            encoder=DjangoJSONEncoder
        )
    
    @staticmethod
    def validation_error(errors: Dict, message: str = "Validation failed") -> JsonResponse:
        """
        Return validation error response
        
        Args:
            errors: Validation errors dictionary
            message: Error message
        """
        return APIResponse.error(
            message=message,
            status_code=422,
            errors=errors,
            error_code="VALIDATION_ERROR"
        )
    
    @staticmethod
    def not_found(message: str = "Resource not found") -> JsonResponse:
        """Return 404 not found response"""
        return APIResponse.error(
            message=message,
            status_code=404,
            error_code="NOT_FOUND"
        )
    
    @staticmethod
    def unauthorized(message: str = "Authentication required") -> JsonResponse:
        """Return 401 unauthorized response"""
        return APIResponse.error(
            message=message,
            status_code=401,
            error_code="UNAUTHORIZED"
        )
    
    @staticmethod
    def forbidden(message: str = "Permission denied") -> JsonResponse:
        """Return 403 forbidden response"""
        return APIResponse.error(
            message=message,
            status_code=403,
            error_code="FORBIDDEN"
        )
    
    @staticmethod
    def rate_limited(message: str = "Rate limit exceeded") -> JsonResponse:
        """Return 429 rate limited response"""
        return APIResponse.error(
            message=message,
            status_code=429,
            error_code="RATE_LIMITED"
        )
    
    @staticmethod
    def server_error(message: str = "Internal server error") -> JsonResponse:
        """Return 500 server error response"""
        return APIResponse.error(
            message=message,
            status_code=500,
            error_code="INTERNAL_ERROR"
        )
    
    @staticmethod
    def created(data: Any = None, message: str = "Resource created successfully") -> JsonResponse:
        """Return 201 created response"""
        return APIResponse.success(
            data=data,
            message=message,
            status_code=201
        )
    
    @staticmethod
    def updated(data: Any = None, message: str = "Resource updated successfully") -> JsonResponse:
        """Return successful update response"""
        return APIResponse.success(
            data=data,
            message=message,
            status_code=200
        )
    
    @staticmethod
    def deleted(message: str = "Resource deleted successfully") -> JsonResponse:
        """Return successful deletion response"""
        return APIResponse.success(
            message=message,
            status_code=200
        )
    
    @staticmethod
    def paginated(data: List, pagination_info: Dict, message: str = "Success") -> JsonResponse:
        """
        Return paginated response
        
        Args:
            data: List of items
            pagination_info: Pagination metadata
            message: Success message
        """
        return APIResponse.success(
            data=data,
            message=message,
            pagination=pagination_info
        )
    
    @staticmethod
    def _serialize_data(data: Any) -> Any:
        """
        Serialize data for JSON response
        
        Args:
            data: Data to serialize
        """
        if data is None:
            return None
        
        if isinstance(data, models.Model):
            return APIResponse._serialize_model(data)
        
        if isinstance(data, models.QuerySet):
            return [APIResponse._serialize_model(item) for item in data]
        
        if isinstance(data, list):
            return [APIResponse._serialize_data(item) for item in data]
        
        if isinstance(data, dict):
            return {key: APIResponse._serialize_data(value) for key, value in data.items()}
        
        return data
    
    @staticmethod
    def _serialize_model(instance: models.Model) -> Dict:
        """
        Serialize Django model instance
        
        Args:
            instance: Model instance to serialize
        """
        try:
            data = model_to_dict(instance)
            
            # Handle UUID fields
            for field_name, field_value in data.items():
                if hasattr(field_value, 'hex'):  # UUID field
                    data[field_name] = str(field_value)
            
            return data
        except Exception as e:
            logger.error(f"Error serializing model {instance.__class__.__name__}: {str(e)}")
            return {'id': str(instance.pk) if instance.pk else None}


class PaginatedResponse:
    """
    Helper class for creating paginated responses
    """
    
    def __init__(self, queryset, page: int = 1, per_page: int = 20, max_per_page: int = 100):
        self.queryset = queryset
        self.page = max(1, page)
        self.per_page = min(max_per_page, max(1, per_page))
        self.max_per_page = max_per_page
        
        self.total_count = queryset.count()
        self.total_pages = (self.total_count + self.per_page - 1) // self.per_page
        
        # Calculate offset
        offset = (self.page - 1) * self.per_page
        self.items = queryset[offset:offset + self.per_page]
        
        self.has_next = self.page < self.total_pages
        self.has_previous = self.page > 1
    
    def get_pagination_info(self) -> Dict:
        """Get pagination metadata"""
        return {
            'page': self.page,
            'per_page': self.per_page,
            'total_count': self.total_count,
            'total_pages': self.total_pages,
            'has_next': self.has_next,
            'has_previous': self.has_previous,
            'next_page': self.page + 1 if self.has_next else None,
            'previous_page': self.page - 1 if self.has_previous else None
        }
    
    def to_response(self, message: str = "Success") -> JsonResponse:
        """Convert to API response"""
        return APIResponse.paginated(
            data=list(self.items),
            pagination_info=self.get_pagination_info(),
            message=message
        )


class ErrorResponse:
    """
    Helper class for handling different types of errors
    """
    
    @staticmethod
    def from_exception(exception: Exception) -> JsonResponse:
        """
        Create error response from exception
        
        Args:
            exception: The exception that occurred
        """
        if hasattr(exception, 'status_code'):
            status_code = exception.status_code
        else:
            status_code = 500
        
        if hasattr(exception, 'error_code'):
            error_code = exception.error_code
        else:
            error_code = exception.__class__.__name__.upper()
        
        return APIResponse.error(
            message=str(exception),
            status_code=status_code,
            error_code=error_code
        )
    
    @staticmethod
    def from_form_errors(form) -> JsonResponse:
        """
        Create validation error response from Django form errors
        
        Args:
            form: Django form with errors
        """
        errors = {}
        
        for field, field_errors in form.errors.items():
            errors[field] = [str(error) for error in field_errors]
        
        return APIResponse.validation_error(errors=errors)
    
    @staticmethod
    def from_serializer_errors(serializer) -> JsonResponse:
        """
        Create validation error response from DRF serializer errors
        
        Args:
            serializer: DRF serializer with errors
        """
        errors = {}
        
        for field, field_errors in serializer.errors.items():
            if isinstance(field_errors, list):
                errors[field] = [str(error) for error in field_errors]
            else:
                errors[field] = [str(field_errors)]
        
        return APIResponse.validation_error(errors=errors)


def api_response_middleware(get_response):
    """
    Middleware to handle API response formatting
    """
    def middleware(request):
        response = get_response(request)
        
        # Add CORS headers for API endpoints
        if request.path.startswith('/api/'):
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        
        return response
    
    return middleware


def format_validation_errors(errors: Dict) -> Dict:
    """
    Format validation errors for consistent API responses
    
    Args:
        errors: Raw validation errors
    """
    formatted_errors = {}
    
    for field, field_errors in errors.items():
        if isinstance(field_errors, list):
            formatted_errors[field] = field_errors
        elif isinstance(field_errors, dict):
            formatted_errors[field] = format_validation_errors(field_errors)
        else:
            formatted_errors[field] = [str(field_errors)]
    
    return formatted_errors


def create_api_response(success: bool = True, data: Any = None, message: str = None, 
                       status_code: int = None, **kwargs) -> JsonResponse:
    """
    Generic function to create API responses
    
    Args:
        success: Whether the request was successful
        data: Response data
        message: Response message
        status_code: HTTP status code
        **kwargs: Additional response data
    """
    if success:
        return APIResponse.success(
            data=data,
            message=message or "Success",
            status_code=status_code or 200,
            **kwargs
        )
    else:
        return APIResponse.error(
            message=message or "An error occurred",
            status_code=status_code or 400,
            **kwargs
        )


# Convenience functions for common responses
def success_response(data=None, message="Success", **kwargs):
    """Shortcut for success response"""
    return APIResponse.success(data=data, message=message, **kwargs)


def error_response(message="An error occurred", status_code=400, **kwargs):
    """Shortcut for error response"""
    return APIResponse.error(message=message, status_code=status_code, **kwargs)


def validation_error_response(errors, message="Validation failed"):
    """Shortcut for validation error response"""
    return APIResponse.validation_error(errors=errors, message=message)