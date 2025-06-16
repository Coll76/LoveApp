# apps/users/views.py
from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from core.authentication import generate_jwt_token, generate_refresh_token
from core.utils import generate_random_string
from .models import UserProfile, UserPreferences, UserUsageLimit, EmailVerification
from .serializers import (
    UserRegistrationSerializer, UserLoginSerializer, UserSerializer,
    UserProfileSerializer, UserPreferencesSerializer, UserUsageLimitSerializer,
    ChangePasswordSerializer
)
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

class UserRegistrationView(generics.CreateAPIView):
    """User registration endpoint"""
    queryset = User.objects.all()
    serializer_class = UserRegistrationSerializer
    permission_classes = [permissions.AllowAny]
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # Generate email verification token
        verification_token = generate_random_string(32)
        EmailVerification.objects.create(
            user=user,
            token=verification_token,
            expires_at=timezone.now() + timedelta(hours=24)
        )
        
        # TODO: Send verification email
        logger.info(f"User registered: {user.email}")
        
        return Response({
            'message': 'Registration successful. Please verify your email.',
            'user_id': user.id,
            'email': user.email
        }, status=status.HTTP_201_CREATED)

class UserLoginView(generics.GenericAPIView):
    """User login endpoint"""
    serializer_class = UserLoginSerializer
    permission_classes = [permissions.AllowAny]
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        
        # Update last login IP
        from core.utils import get_client_ip
        user.last_login_ip = get_client_ip(request)
        user.last_login = timezone.now()
        user.save()
        
        # Generate tokens
        access_token = generate_jwt_token(user)
        refresh_token = generate_refresh_token(user)
        
        logger.info(f"User logged in: {user.email}")
        
        return Response({
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': UserSerializer(user).data
        })

class UserProfileView(generics.RetrieveUpdateAPIView):
    """User profile management"""
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_object(self):
        return self.request.user

class UserProfileDetailView(generics.RetrieveUpdateAPIView):
    """User profile detail management"""
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_object(self):
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

class UserPreferencesView(generics.RetrieveUpdateAPIView):
    """User preferences management"""
    serializer_class = UserPreferencesSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_object(self):
        preferences, created = UserPreferences.objects.get_or_create(user=self.request.user)
        return preferences

class UserUsageLimitView(generics.RetrieveAPIView):
    """User usage limits view"""
    serializer_class = UserUsageLimitSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_object(self):
        return UserUsageLimit.get_or_create_today(self.request.user)

class ChangePasswordView(generics.GenericAPIView):
    """Change user password"""
    serializer_class = ChangePasswordSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        user = request.user
        user.set_password(serializer.validated_data['new_password'])
        user.save()
        
        logger.info(f"Password changed for user: {user.email}")
        
        return Response({'message': 'Password changed successfully'})

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def verify_email(request):
    """Verify user email with token"""
    token = request.data.get('token')
    if not token:
        return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        verification = EmailVerification.objects.get(token=token, is_used=False)
        if verification.is_expired():
            return Response({'error': 'Token has expired'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Mark user as verified
        user = verification.user
        user.is_email_verified = True
        user.save()
        
        # Mark token as used
        verification.mark_as_used()
        
        logger.info(f"Email verified for user: {user.email}")
        
        return Response({'message': 'Email verified successfully'})
    
    except EmailVerification.DoesNotExist:
        return Response({'error': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def resend_verification_email(request):
    """Resend verification email"""
    email = request.data.get('email')
    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        user = User.objects.get(email=email)
        if user.is_email_verified:
            return Response({'error': 'Email is already verified'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Invalidate old tokens
        EmailVerification.objects.filter(user=user, is_used=False).update(is_used=True)
        
        # Create new token
        verification_token = generate_random_string(32)
        EmailVerification.objects.create(
            user=user,
            token=verification_token,
            expires_at=timezone.now() + timedelta(hours=24)
        )
        
        # TODO: Send verification email
        logger.info(f"Verification email resent to: {email}")
        
        return Response({'message': 'Verification email sent'})
    
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
