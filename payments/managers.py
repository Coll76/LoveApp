# apps/payments/managers.py
from django.db import models
from django.utils import timezone
from django.db.models import Q

class SubscriptionManager(models.Manager):
    def active_subscriptions(self):
        """Get all active subscriptions"""
        return self.filter(status='active')
        
    def premium_subscriptions(self):
        """Get all premium subscriptions"""
        return self.filter(
            status='active',
            plan_name__in=['premium_monthly', 'premium_yearly']
        )
        
    def expiring_soon(self, days=3):
        """Get subscriptions expiring in specified days"""
        cutoff_date = timezone.now() + timezone.timedelta(days=days)
        return self.filter(
            status='active',
            next_billing_date__lte=cutoff_date,
            cancel_at_period_end=False
        )
        
    def cancelled_subscriptions(self):
        """Get cancelled subscriptions"""
        return self.filter(status='cancelled')
        
    def free_tier_users(self):
        """Get users on free tier"""
        return self.filter(
            Q(plan_name='free') | 
            Q(status__in=['cancelled', 'expired'])
        )

class PaymentManager(models.Manager):
    def successful_payments(self):
        """Get all successful payments"""
        return self.filter(status='completed')
        
    def failed_payments(self):
        """Get all failed payments"""
        return self.filter(status='failed')
        
    def pending_payments(self):
        """Get pending payments"""
        return self.filter(status='pending')
        
    def by_date_range(self, start_date, end_date):
        """Get payments within date range"""
        return self.filter(created_at__range=[start_date, end_date])
        
    def subscription_payments(self):
        """Get subscription-related payments"""
        return self.filter(
            payment_type__in=[
                'subscription_initial',
                'subscription_renewal',
                'subscription_upgrade'
            ]
        )

class FlutterwaveTransactionManager(models.Manager):
    def successful_transactions(self):
        """Get successful transactions"""
        return self.filter(status='successful')
        
    def pending_verification(self):
        """Get transactions pending webhook processing"""
        return self.filter(
            status='pending',
            webhook_processed=False
        )
        
    def failed_transactions(self):
        """Get failed transactions"""
        return self.filter(status='failed')
        
    def by_transaction_type(self, transaction_type):
        """Get transactions by type"""
        return self.filter(transaction_type=transaction_type)

class InvoiceManager(models.Manager):
    def paid_invoices(self):
        """Get paid invoices"""
        return self.filter(status='paid')
        
    def overdue_invoices(self):
        """Get overdue invoices"""
        return self.filter(
            status__in=['sent', 'overdue'],
            due_date__lt=timezone.now()
        )
        
    def pending_invoices(self):
        """Get pending invoices"""
        return self.filter(status='sent')

class RefundRequestManager(models.Manager):
    def pending_refunds(self):
        """Get pending refund requests"""
        return self.filter(status='pending')
        
    def completed_refunds(self):
        """Get completed refunds"""
        return self.filter(status='completed')