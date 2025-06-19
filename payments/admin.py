# apps/payments/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta
import json

from .models import (
    PaymentGateway,
    FlutterwaveTransaction,
    Subscription,
    Payment,
    Invoice,
    RefundRequest,
    FlutterwaveWebhookLog,
    PaymentMethod
)


@admin.register(PaymentGateway)
class PaymentGatewayAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'priority', 'supported_countries_count', 'supported_currencies_count']
    list_filter = ['is_active', 'name']
    list_editable = ['is_active', 'priority']
    ordering = ['priority']
    
    def supported_countries_count(self, obj):
        return len(obj.supported_countries) if obj.supported_countries else 0
    supported_countries_count.short_description = 'Countries'
    
    def supported_currencies_count(self, obj):
        return len(obj.supported_currencies) if obj.supported_currencies else 0
    supported_currencies_count.short_description = 'Currencies'


@admin.register(FlutterwaveTransaction)
class FlutterwaveTransactionAdmin(admin.ModelAdmin):
    list_display = [
        'tx_ref', 'user_email', 'amount', 'currency', 'status', 
        'transaction_type', 'payment_type', 'webhook_processed', 'created_at'
    ]
    list_filter = [
        'status', 'transaction_type', 'payment_type', 'webhook_processed',
        'currency', 'created_at'
    ]
    search_fields = ['tx_ref', 'flw_ref', 'transaction_id', 'user__email', 'customer_email']
    readonly_fields = [
        'created_at', 'updated_at', 'raw_response_formatted', 'metadata_formatted'
    ]
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Transaction Details', {
            'fields': (
                'user', 'tx_ref', 'flw_ref', 'transaction_id', 
                'amount', 'currency', 'status', 'transaction_type'
            )
        }),
        ('Customer Information', {
            'fields': ('customer_email', 'customer_phone', 'customer_name')
        }),
        ('Payment Details', {
            'fields': ('payment_type', 'redirect_url', 'failure_reason')
        }),
        ('Processing Status', {
            'fields': ('webhook_processed', 'created_at', 'updated_at')
        }),
        ('Raw Data', {
            'fields': ('raw_response_formatted', 'metadata_formatted'),
            'classes': ['collapse']
        })
    )
    
    def user_email(self, obj):
        return obj.user.email if obj.user else obj.customer_email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    def raw_response_formatted(self, obj):
        if obj.raw_response:
            return format_html('<pre>{}</pre>', json.dumps(obj.raw_response, indent=2))
        return 'No data'
    raw_response_formatted.short_description = 'Raw Response'
    
    def metadata_formatted(self, obj):
        if obj.metadata:
            return format_html('<pre>{}</pre>', json.dumps(obj.metadata, indent=2))
        return 'No metadata'
    metadata_formatted.short_description = 'Metadata'
    
    actions = ['mark_webhook_processed', 'mark_webhook_unprocessed']
    
    def mark_webhook_processed(self, request, queryset):
        count = queryset.update(webhook_processed=True)
        self.message_user(request, f'{count} transactions marked as webhook processed.')
    mark_webhook_processed.short_description = 'Mark webhook as processed'
    
    def mark_webhook_unprocessed(self, request, queryset):
        count = queryset.update(webhook_processed=False)
        self.message_user(request, f'{count} transactions marked as webhook unprocessed.')
    mark_webhook_unprocessed.short_description = 'Mark webhook as unprocessed'


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        'user_email', 'plan_name', 'status', 'gateway', 
        'amount', 'currency', 'next_billing_date', 'cancel_at_period_end'
    ]
    list_filter = [
        'plan_name', 'status', 'gateway', 'currency', 
        'cancel_at_period_end', 'created_at'
    ]
    search_fields = ['user__email', 'gateway_subscription_id', 'gateway_customer_id']
    readonly_fields = ['created_at', 'updated_at', 'days_until_renewal_display']
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('User & Plan', {
            'fields': ('user', 'plan_name', 'status')
        }),
        ('Gateway Details', {
            'fields': (
                'gateway', 'gateway_plan_id', 'gateway_subscription_id', 
                'gateway_customer_id'
            )
        }),
        ('Billing Information', {
            'fields': (
                'amount', 'currency', 'interval', 
                'current_period_start', 'current_period_end', 'next_billing_date'
            )
        }),
        ('Trial & Cancellation', {
            'fields': (
                'trial_end_date', 'cancelled_at', 'cancel_at_period_end'
            )
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at', 'days_until_renewal_display'),
            'classes': ['collapse']
        })
    )
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    def days_until_renewal_display(self, obj):
        days = obj.days_until_renewal()
        if days is not None:
            if days < 0:
                return format_html('<span style="color: red;">Overdue by {} days</span>', abs(days))
            elif days == 0:
                return format_html('<span style="color: orange;">Due today</span>')
            elif days <= 7:
                return format_html('<span style="color: orange;">{} days</span>', days)
            else:
                return f'{days} days'
        return 'No renewal date'
    days_until_renewal_display.short_description = 'Days Until Renewal'
    
    actions = ['cancel_subscriptions', 'activate_subscriptions']
    
    def cancel_subscriptions(self, request, queryset):
        count = 0
        for subscription in queryset:
            if subscription.status == 'active':
                subscription.cancel_subscription()
                count += 1
        self.message_user(request, f'{count} subscriptions cancelled.')
    cancel_subscriptions.short_description = 'Cancel selected subscriptions'
    
    def activate_subscriptions(self, request, queryset):
        count = 0
        for subscription in queryset:
            if subscription.status == 'cancelled':
                subscription.reactivate_subscription()
                count += 1
        self.message_user(request, f'{count} subscriptions reactivated.')
    activate_subscriptions.short_description = 'Reactivate selected subscriptions'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = [
        'reference_id', 'user_email', 'amount', 'currency', 
        'status', 'payment_type', 'gateway', 'created_at'
    ]
    list_filter = [
        'status', 'payment_type', 'gateway', 'currency', 
        'payment_method', 'created_at'
    ]
    search_fields = [
        'reference_id', 'gateway_transaction_id', 
        'user__email', 'description'
    ]
    readonly_fields = ['created_at', 'updated_at', 'processed_at']
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Payment Details', {
            'fields': (
                'user', 'subscription', 'reference_id', 
                'amount', 'currency', 'status'
            )
        }),
        ('Gateway Information', {
            'fields': (
                'gateway', 'gateway_transaction_id', 
                'payment_type', 'payment_method'
            )
        }),
        ('Processing Information', {
            'fields': (
                'description', 'failure_reason', 
                'processed_at', 'created_at', 'updated_at'
            )
        }),
        ('Refund Information', {
            'fields': ('refunded_amount',)
        })
    )
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    actions = ['mark_as_completed', 'mark_as_failed']
    
    def mark_as_completed(self, request, queryset):
        count = 0
        for payment in queryset.filter(status='pending'):
            payment.mark_as_completed()
            count += 1
        self.message_user(request, f'{count} payments marked as completed.')
    mark_as_completed.short_description = 'Mark as completed'
    
    def mark_as_failed(self, request, queryset):
        count = 0
        for payment in queryset.filter(status__in=['pending', 'processing']):
            payment.mark_as_failed('Manually marked as failed by admin')
            count += 1
        self.message_user(request, f'{count} payments marked as failed.')
    mark_as_failed.short_description = 'Mark as failed'


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = [
        'invoice_number', 'user_email', 'total_amount', 'currency', 
        'status', 'due_date', 'paid_at'
    ]
    list_filter = ['status', 'currency', 'due_date', 'created_at']
    search_fields = ['invoice_number', 'user__email']
    readonly_fields = ['created_at', 'updated_at', 'paid_at']
    date_hierarchy = 'due_date'
    list_per_page = 50
    
    fieldsets = (
        ('Invoice Details', {
            'fields': (
                'user', 'subscription', 'payment', 'invoice_number', 'status'
            )
        }),
        ('Amounts', {
            'fields': (
                'amount', 'tax_amount', 'discount_amount', 
                'total_amount', 'currency'
            )
        }),
        ('Billing Period', {
            'fields': (
                'billing_period_start', 'billing_period_end', 
                'due_date', 'paid_at'
            )
        }),
        ('Line Items', {
            'fields': ('line_items',),
            'classes': ['collapse']
        })
    )
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    actions = ['mark_as_paid']
    
    def mark_as_paid(self, request, queryset):
        count = 0
        for invoice in queryset.exclude(status='paid'):
            invoice.mark_as_paid()
            count += 1
        self.message_user(request, f'{count} invoices marked as paid.')
    mark_as_paid.short_description = 'Mark as paid'


@admin.register(RefundRequest)
class RefundRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user_email', 'payment_reference', 'amount', 
        'currency', 'reason', 'status', 'created_at'
    ]
    list_filter = ['status', 'reason', 'currency', 'created_at']
    search_fields = [
        'user__email', 'payment__reference_id', 
        'gateway_refund_id', 'reason_description'
    ]
    readonly_fields = ['created_at', 'updated_at', 'processed_at']
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Refund Details', {
            'fields': (
                'user', 'payment', 'amount', 'currency', 
                'reason', 'reason_description', 'status'
            )
        }),
        ('Gateway Information', {
            'fields': ('gateway_refund_id', 'failure_reason')
        }),
        ('Processing Information', {
            'fields': ('processed_at', 'admin_notes')
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ['collapse']
        })
    )
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    def payment_reference(self, obj):
        return obj.payment.reference_id if obj.payment else 'N/A'
    payment_reference.short_description = 'Payment Reference'
    
    actions = ['approve_refunds', 'reject_refunds']
    
    def approve_refunds(self, request, queryset):
        count = queryset.filter(status='pending').update(status='processing')
        self.message_user(request, f'{count} refund requests approved for processing.')
    approve_refunds.short_description = 'Approve for processing'
    
    def reject_refunds(self, request, queryset):
        count = queryset.filter(status='pending').update(status='cancelled')
        self.message_user(request, f'{count} refund requests rejected.')
    reject_refunds.short_description = 'Reject refund requests'


@admin.register(FlutterwaveWebhookLog)
class FlutterwaveWebhookLogAdmin(admin.ModelAdmin):
    list_display = [
        'event_id', 'event_type', 'tx_ref', 'status', 
        'signature_valid', 'processed', 'processing_attempts', 'created_at'
    ]
    list_filter = [
        'event_type', 'status', 'signature_valid', 
        'processed', 'processing_attempts', 'created_at'
    ]
    search_fields = ['event_id', 'tx_ref', 'user_id']
    readonly_fields = [
        'created_at', 'updated_at', 'payload_formatted',
        'signature', 'user_id'
    ]
    date_hierarchy = 'created_at'
    list_per_page = 50
    
    fieldsets = (
        ('Webhook Details', {
            'fields': (
                'event_type', 'event_id', 'tx_ref', 'status', 'user_id'
            )
        }),
        ('Signature Verification', {
            'fields': ('signature', 'signature_valid')
        }),
        ('Processing Status', {
            'fields': (
                'processed', 'processing_attempts', 
                'error_message', 'created_at', 'updated_at'
            )
        }),
        ('Payload Data', {
            'fields': ('payload_formatted',),
            'classes': ['collapse']
        })
    )
    
    def payload_formatted(self, obj):
        if obj.payload:
            return format_html('<pre>{}</pre>', json.dumps(obj.payload, indent=2))
        return 'No payload'
    payload_formatted.short_description = 'Payload'
    
    actions = ['mark_as_processed', 'reset_processing_attempts']
    
    def mark_as_processed(self, request, queryset):
        count = 0
        for log in queryset:
            log.mark_as_processed()
            count += 1
        self.message_user(request, f'{count} webhook logs marked as processed.')
    mark_as_processed.short_description = 'Mark as processed'
    
    def reset_processing_attempts(self, request, queryset):
        count = queryset.update(processing_attempts=0, error_message='')
        self.message_user(request, f'{count} webhook logs reset for reprocessing.')
    reset_processing_attempts.short_description = 'Reset processing attempts'


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = [
        'user_email', 'method_display', 'gateway', 
        'is_default', 'is_active', 'created_at'
    ]
    list_filter = ['method_type', 'gateway', 'is_default', 'is_active', 'card_brand']
    search_fields = [
        'user__email', 'gateway_method_id', 
        'card_last_four', 'mobile_number'
    ]
    readonly_fields = ['created_at', 'updated_at']
    list_per_page = 50
    
    fieldsets = (
        ('Payment Method Details', {
            'fields': (
                'user', 'gateway', 'gateway_method_id', 
                'method_type', 'is_default', 'is_active'
            )
        }),
        ('Card Details', {
            'fields': (
                'card_last_four', 'card_brand', 
                'card_exp_month', 'card_exp_year'
            ),
            'classes': ['collapse']
        }),
        ('Mobile Money Details', {
            'fields': ('mobile_number', 'mobile_provider'),
            'classes': ['collapse']
        }),
        ('System Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ['collapse']
        })
    )
    
    def user_email(self, obj):
        return obj.user.email
    user_email.short_description = 'User Email'
    user_email.admin_order_field = 'user__email'
    
    def method_display(self, obj):
        return str(obj)
    method_display.short_description = 'Payment Method'
    
    actions = ['activate_methods', 'deactivate_methods']
    
    def activate_methods(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f'{count} payment methods activated.')
    activate_methods.short_description = 'Activate payment methods'
    
    def deactivate_methods(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f'{count} payment methods deactivated.')
    deactivate_methods.short_description = 'Deactivate payment methods'


# Custom admin site configurations
admin.site.site_header = 'Payment Management System'
admin.site.site_title = 'Payment Admin'
admin.site.index_title = 'Payment Administration'