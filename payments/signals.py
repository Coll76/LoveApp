# apps/payments/signals.py
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.cache import cache
from .models import Subscription, Payment, Invoice, FlutterwaveTransaction
from core.utils import generate_random_string

User = get_user_model()

@receiver(post_save, sender=User)
def create_user_subscription(sender, instance, created, **kwargs):
    """Create default free subscription for new users"""
    if created:
        from core.utils import get_user_country_currency
        currency = get_user_country_currency(instance)
        
        Subscription.objects.create(
            user=instance,
            plan_name='free',
            status='active',
            currency=currency,
            amount=0,
            interval='monthly',
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=30),
        )

@receiver(pre_save, sender=Invoice)
def generate_invoice_number(sender, instance, **kwargs):
    """Generate unique invoice number if not set"""
    if not instance.invoice_number:
        prefix = 'INV'
        timestamp = instance.created_at.strftime('%Y%m%d') if instance.created_at else timezone.now().strftime('%Y%m%d')
        random_part = generate_random_string(6).upper()
        instance.invoice_number = f"{prefix}-{timestamp}-{random_part}"

@receiver(post_save, sender=Payment)
def update_subscription_on_payment(sender, instance, created, **kwargs):
    """Update subscription status when payment is completed"""
    if instance.status == 'completed' and instance.subscription:
        subscription = instance.subscription
        if instance.payment_type in ['subscription_initial', 'subscription_renewal']:
            subscription.status = 'active'
            
            # Update billing dates based on plan
            if subscription.plan_name == 'premium_monthly':
                subscription.current_period_start = timezone.now()
                subscription.current_period_end = timezone.now() + timezone.timedelta(days=30)
                subscription.next_billing_date = subscription.current_period_end
            elif subscription.plan_name == 'premium_yearly':
                subscription.current_period_start = timezone.now()
                subscription.current_period_end = timezone.now() + timezone.timedelta(days=365)
                subscription.next_billing_date = subscription.current_period_end
                         
            subscription.save()
            
            # Clear user cache
            cache.delete(f"user_subscription_{instance.user.id}")
            cache.delete(f"user_usage_limits_{instance.user.id}")

@receiver(post_save, sender=Subscription)
def clear_user_cache_on_subscription_change(sender, instance, **kwargs):
    """Clear user cache when subscription changes"""
    cache.delete(f"user_subscription_{instance.user.id}")
    cache.delete(f"user_usage_limits_{instance.user.id}")
    cache.delete(f"user_daily_ideas_{instance.user.id}")

@receiver(post_save, sender=FlutterwaveTransaction)
def log_transaction_status_change(sender, instance, created, **kwargs):
    """Log transaction status changes"""
    if not created and instance.status in ['successful', 'failed']:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Transaction {instance.tx_ref} status changed to {instance.status}")