# apps/core/permissions.py
from rest_framework import permissions
from django.contrib.auth import get_user_model

User = get_user_model()

class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object to edit it.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions for any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only to the owner
        return obj.user == request.user

class IsPremiumUser(permissions.BasePermission):
    """
    Permission class to check if user has premium subscription
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        # Check if user has active premium subscription
        return hasattr(request.user, 'subscription') and \
               request.user.subscription.is_active_premium()

class HasDailyIdeasLeft(permissions.BasePermission):
    """
    Permission to check if user has daily ideas left
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        
        # Premium users have unlimited ideas
        if hasattr(request.user, 'subscription') and \
           request.user.subscription.is_active_premium():
            return True
        
        # Check daily limit for free users
        from users.models import UserUsageLimit
        usage = UserUsageLimit.objects.get_or_create_today(request.user)
        return usage.can_generate_idea()