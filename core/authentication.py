# apps/core/authentication.py
import jwt
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework import authentication, exceptions
from django.utils import timezone
from datetime import datetime, timedelta

User = get_user_model()

class JWTAuthentication(authentication.BaseAuthentication):
    """
    Custom JWT authentication
    """
    def authenticate(self, request):
        token = self.get_token_from_request(request)
        if not token:
            return None
        
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=['HS256']
            )
            user = User.objects.get(id=payload['user_id'])
            
            # Check if token is expired
            if payload['exp'] < timezone.now().timestamp():
                raise exceptions.AuthenticationFailed('Token expired')
            
            return (user, token)
        
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('Token expired')
        except jwt.InvalidTokenError:
            raise exceptions.AuthenticationFailed('Invalid token')
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed('User not found')
    
    def get_token_from_request(self, request):
        """Extract token from Authorization header"""
        authorization_header = request.META.get('HTTP_AUTHORIZATION')
        if not authorization_header:
            return None
        
        try:
            prefix, token = authorization_header.split(' ')
            if prefix.lower() != 'bearer':
                return None
            return token
        except ValueError:
            return None

def generate_jwt_token(user):
    """Generate JWT token for user"""
    payload = {
        'user_id': str(user.id),
        'email': user.email,
        'iat': timezone.now(),
        'exp': timezone.now() + timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME)
    }
    
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm='HS256')

def generate_refresh_token(user):
    """Generate refresh token for user"""
    payload = {
        'user_id': str(user.id),
        'type': 'refresh',
        'iat': timezone.now(),
        'exp': timezone.now() + timedelta(seconds=settings.JWT_REFRESH_TOKEN_LIFETIME)
    }
    
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm='HS256')