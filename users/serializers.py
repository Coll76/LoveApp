# apps/users/serializers.py
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import User, UserProfile, UserPreferences, UserUsageLimit
from core.utils import generate_random_string

class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ('email', 'first_name', 'last_name', 'password', 'password_confirm')
    
    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError("Passwords don't match")
        return attrs
    
    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(**validated_data)
        
        # Create related objects
        UserProfile.objects.create(user=user)
        UserPreferences.objects.create(user=user)
        
        return user

class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    
    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')
        
        if email and password:
            user = authenticate(email=email, password=password)
            if not user:
                raise serializers.ValidationError('Invalid credentials')
            if not user.is_active:
                raise serializers.ValidationError('User account is disabled')
            attrs['user'] = user
        else:
            raise serializers.ValidationError('Must include email and password')
        
        return attrs

class UserProfileSerializer(serializers.ModelSerializer):
    age = serializers.ReadOnlyField()
    
    class Meta:
        model = UserProfile
        exclude = ('id', 'user', 'created_at', 'updated_at')

class UserPreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreferences
        exclude = ('id', 'user', 'created_at', 'updated_at')

class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    preferences = UserPreferencesSerializer(read_only=True)
    subscription_tier = serializers.ReadOnlyField(source='get_subscription_tier')
    
    class Meta:
        model = User
        fields = (
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'is_email_verified', 'date_joined', 'subscription_tier',
            'profile', 'preferences'
        )
        read_only_fields = ('id', 'email', 'date_joined', 'is_email_verified')

class UserUsageLimitSerializer(serializers.ModelSerializer):
    can_generate_idea = serializers.ReadOnlyField()
    can_generate_pdf = serializers.ReadOnlyField()
    daily_limit = serializers.SerializerMethodField()
    
    class Meta:
        model = UserUsageLimit
        fields = (
            'date', 'ideas_generated', 'pdfs_generated',
            'can_generate_idea', 'can_generate_pdf', 'daily_limit'
        )
    
    def get_daily_limit(self, obj):
        from django.conf import settings
        if obj.user.has_active_subscription():
            return 'unlimited'
        return settings.SUBSCRIPTION_PLANS['free']['limitations']['daily_ideas']

class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    new_password_confirm = serializers.CharField(write_only=True)
    
    def validate(self, attrs):
        if attrs['new_password'] != attrs['new_password_confirm']:
            raise serializers.ValidationError("New passwords don't match")
        return attrs
    
    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect")
        return value