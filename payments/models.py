# apps/payments/models.py
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from core.models import BaseModel, SoftDeleteModel
from decimal import Decimal
import uuid
from .managers import (
    SubscriptionManager, 
    PaymentManager, 
    FlutterwaveTransactionManager,
    InvoiceManager,
    RefundRequestManager
)

User = get_user_model()

class PaymentGateway(models.Model):
    """
    Payment gateway configuration
    """
    GATEWAY_CHOICES = [
        ('flutterwave', 'Flutterwave'),
        ('stripe', 'Stripe'),
    ]
    
    name = models.CharField(max_length=50, choices=GATEWAY_CHOICES, unique=True)
    is_active = models.BooleanField(default=True)
    supported_countries = models.JSONField(default=list)
    supported_currencies = models.JSONField(default=list)
    priority = models.IntegerField(default=1)  # Lower number = higher priority
    
    class Meta:
        db_table = 'payment_gateways'
        ordering = ['priority']
    
    def __str__(self):
        return self.get_name_display()

class FlutterwaveTransaction(BaseModel):
    """
    Store complete Flutterwave transaction data
    """
    TRANSACTION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('successful', 'Successful'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    TRANSACTION_TYPE_CHOICES = [
        ('subscription', 'Subscription'),
        ('one_time', 'One Time Payment'),
        ('refund', 'Refund'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='flutterwave_transactions')
    tx_ref = models.CharField(max_length=100, unique=True, db_index=True)
    flw_ref = models.CharField(max_length=100, blank=True, db_index=True)
    transaction_id = models.CharField(max_length=100, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    currency = models.CharField(max_length=3, default='USD')
    status = models.CharField(max_length=20, choices=TRANSACTION_STATUS_CHOICES, default='pending', db_index=True)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPE_CHOICES, default='one_time')
    payment_type = models.CharField(max_length=50, blank=True)  # card, mobile_money, bank_transfer
    customer_email = models.EmailField()
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_name = models.CharField(max_length=200, blank=True)
    redirect_url = models.URLField(blank=True)
    webhook_processed = models.BooleanField(default=False)
    failure_reason = models.TextField(blank=True)
    raw_response = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)  # Additional transaction metadata
    
    # Add custom manager
    objects = FlutterwaveTransactionManager()
    
    class Meta:
        db_table = 'flutterwave_transactions'
        verbose_name = 'Flutterwave Transaction'
        verbose_name_plural = 'Flutterwave Transactions'
        indexes = [
            models.Index(fields=['tx_ref', 'status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.tx_ref} - {self.status}"
    
    def is_successful(self):
        return self.status == 'successful'
    
    def is_pending(self):
        return self.status == 'pending'

class Subscription(BaseModel):
    """
    User subscription with multi-gateway support
    """
    SUBSCRIPTION_STATUS_CHOICES = [
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
        ('pending', 'Pending'),
        ('past_due', 'Past Due'),
        ('paused', 'Paused'),
    ]
    
    PLAN_CHOICES = [
        ('free', 'Free'),
        ('premium_monthly', 'Premium Monthly'),
        ('premium_yearly', 'Premium Yearly'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan_name = models.CharField(max_length=50, choices=PLAN_CHOICES, default='free')
    gateway = models.CharField(max_length=20, default='flutterwave')  # flutterwave, stripe
    gateway_plan_id = models.CharField(max_length=100, blank=True)
    gateway_subscription_id = models.CharField(max_length=100, blank=True)
    gateway_customer_id = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='USD')
    interval = models.CharField(max_length=20, blank=True)  # monthly, yearly
    status = models.CharField(max_length=20, choices=SUBSCRIPTION_STATUS_CHOICES, default='free')
    trial_end_date = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    next_billing_date = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict)
    
    # Add custom manager
    objects = SubscriptionManager()
    
    class Meta:
        db_table = 'subscriptions'
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'next_billing_date']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.plan_name} ({self.status})"
    
    def is_active_premium(self):
        """Check if subscription is active and premium"""
        return (self.status == 'active' and 
                self.plan_name in ['premium_monthly', 'premium_yearly'])
    
    def is_free_tier(self):
        """Check if user is on free tier"""
        return self.plan_name == 'free' or self.status in ['cancelled', 'expired']
    
    def days_until_renewal(self):
        """Get days until next billing"""
        if self.next_billing_date:
            delta = self.next_billing_date.date() - timezone.now().date()
            return delta.days
        return None
    
    def cancel_subscription(self, at_period_end=True):
        """Cancel subscription"""
        if at_period_end:
            self.cancel_at_period_end = True
        else:
            self.status = 'cancelled'
            self.cancelled_at = timezone.now()
        self.save()
    
    def reactivate_subscription(self):
        """Reactivate cancelled subscription"""
        if self.status == 'cancelled' and not self.cancel_at_period_end:
            self.status = 'active'
            self.cancelled_at = None
            self.save()

class Payment(BaseModel):
    """
    General payment record linking to gateway-specific transactions
    """
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
        ('partially_refunded', 'Partially Refunded'),
    ]
    
    PAYMENT_TYPE_CHOICES = [
        ('subscription_initial', 'Initial Subscription Payment'),
        ('subscription_renewal', 'Subscription Renewal'),
        ('subscription_upgrade', 'Subscription Upgrade'),
        ('one_time', 'One Time Payment'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, null=True, blank=True)
    gateway = models.CharField(max_length=20)
    gateway_transaction_id = models.CharField(max_length=100, blank=True)
    reference_id = models.CharField(max_length=100, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    payment_type = models.CharField(max_length=30, choices=PAYMENT_TYPE_CHOICES)
    payment_method = models.CharField(max_length=50, blank=True)  # card, mobile_money, etc.
    description = models.CharField(max_length=255, blank=True)
    failure_reason = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    metadata = models.JSONField(default=dict)
    
    # Add custom manager
    objects = PaymentManager()
    
    class Meta:
        db_table = 'payments'
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['reference_id']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.reference_id} - {self.amount} {self.currency}"
    
    def mark_as_completed(self):
        self.status = 'completed'
        self.processed_at = timezone.now()
        self.save()
    
    def mark_as_failed(self, reason=''):
        self.status = 'failed'
        self.failure_reason = reason
        self.save()

class Invoice(BaseModel):
    """
    Invoice records for payments and subscriptions
    """
    INVOICE_STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invoices')
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, null=True, blank=True)
    payment = models.OneToOneField(Payment, on_delete=models.SET_NULL, null=True, blank=True)
    invoice_number = models.CharField(max_length=50, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=INVOICE_STATUS_CHOICES, default='draft')
    due_date = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    billing_period_start = models.DateTimeField(null=True, blank=True)
    billing_period_end = models.DateTimeField(null=True, blank=True)
    line_items = models.JSONField(default=list)  # Detailed billing items
    
    # Add custom manager
    objects = InvoiceManager()
    
    class Meta:
        db_table = 'invoices'
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['invoice_number']),
            models.Index(fields=['due_date']),
        ]
    
    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.user.email}"
    
    def mark_as_paid(self):
        self.status = 'paid'
        self.paid_at = timezone.now()
        self.save()

class RefundRequest(BaseModel, SoftDeleteModel):
    """
    Refund requests and processing
    """
    REFUND_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    ]
    
    REFUND_REASON_CHOICES = [
        ('customer_request', 'Customer Request'),
        ('duplicate_payment', 'Duplicate Payment'),
        ('fraudulent', 'Fraudulent Transaction'),
        ('service_not_provided', 'Service Not Provided'),
        ('technical_issue', 'Technical Issue'),
        ('other', 'Other'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='refund_requests')
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='refund_requests')
    gateway_refund_id = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    reason = models.CharField(max_length=30, choices=REFUND_REASON_CHOICES)
    reason_description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=REFUND_STATUS_CHOICES, default='pending')
    processed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    admin_notes = models.TextField(blank=True)
    
    # Add custom manager
    objects = RefundRequestManager()
    
    class Meta:
        db_table = 'refund_requests'
        verbose_name = 'Refund Request'
        verbose_name_plural = 'Refund Requests'
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['payment', 'status']),
        ]
    
    def __str__(self):
        return f"Refund {self.id} - {self.amount} {self.currency}"

class FlutterwaveWebhookLog(BaseModel):
    """
    Log all webhook events from Flutterwave for debugging and audit
    """
    event_type = models.CharField(max_length=100, db_index=True)
    event_id = models.CharField(max_length=100, unique=True)
    tx_ref = models.CharField(max_length=100, blank=True, db_index=True)
    status = models.CharField(max_length=50, blank=True)
    payload = models.JSONField()
    signature = models.CharField(max_length=255)
    signature_valid = models.BooleanField(default=False)
    processed = models.BooleanField(default=False)
    processing_attempts = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    user_id = models.UUIDField(null=True, blank=True)  # Extracted from payload
    
    class Meta:
        db_table = 'flutterwave_webhook_logs'
        verbose_name = 'Flutterwave Webhook Log'
        verbose_name_plural = 'Flutterwave Webhook Logs'
        indexes = [
            models.Index(fields=['event_type', 'processed']),
            models.Index(fields=['tx_ref']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.event_type} - {self.event_id}"
    
    def mark_as_processed(self):
        self.processed = True
        self.save()
    
    def increment_processing_attempts(self):
        self.processing_attempts += 1
        self.save()

class PaymentMethod(BaseModel):
    """
    Stored payment methods for users (for future recurring payments)
    """
    PAYMENT_METHOD_TYPES = [
        ('card', 'Credit/Debit Card'),
        ('mobile_money', 'Mobile Money'),
        ('bank_account', 'Bank Account'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_methods')
    gateway = models.CharField(max_length=20)
    gateway_method_id = models.CharField(max_length=100)
    method_type = models.CharField(max_length=20, choices=PAYMENT_METHOD_TYPES)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    
    # Card details (masked/tokenized)
    card_last_four = models.CharField(max_length=4, blank=True)
    card_brand = models.CharField(max_length=20, blank=True)
    card_exp_month = models.IntegerField(null=True, blank=True)
    card_exp_year = models.IntegerField(null=True, blank=True)
    
    # Mobile money details
    mobile_number = models.CharField(max_length=20, blank=True)
    mobile_provider = models.CharField(max_length=50, blank=True)
    
    metadata = models.JSONField(default=dict)
    
    class Meta:
        db_table = 'payment_methods'
        verbose_name = 'Payment Method'
        verbose_name_plural = 'Payment Methods'
        unique_together = ['user', 'gateway_method_id']
    
    def __str__(self):
        if self.method_type == 'card':
            return f"**** {self.card_last_four} ({self.card_brand})"
        elif self.method_type == 'mobile_money':
            return f"{self.mobile_provider} - {self.mobile_number}"
        return f"{self.method_type} - {self.gateway_method_id}"