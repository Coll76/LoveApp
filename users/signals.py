# apps/users/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import UserProfile, UserPreferences
import logging

User = get_user_model()
logger = logging.getLogger(__name__)

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create user profile when user is created"""
    if created:
        try:
            UserProfile.objects.get_or_create(user=instance)
            UserPreferences.objects.get_or_create(user=instance)
            logger.info(f"Created profile and preferences for user {instance.email}")
        except Exception as e:
            logger.error(f"Error creating user profile: {e}")

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save user profile when user is saved"""
    try:
        if hasattr(instance, 'profile'):
            instance.profile.save()
        if hasattr(instance, 'preferences'):
            instance.preferences.save()
    except Exception as e:
        logger.error(f"Error saving user profile: {e}")