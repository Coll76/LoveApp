# apps/payments/views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import View
from django.db import transaction as db_transaction
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum, Count
from django.http import Http404
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.urls import reverse

import json
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from .models import (
    FlutterwaveTransaction, Subscription, Payment, Invoice, 
    RefundRequest, PaymentGateway, PaymentMethod, FlutterwaveWebhookLog
)
from .services import FlutterwaveService
from .exceptions import (
    PaymentGatewayError, InvalidTransactionError, 
    DuplicateTransactionError, SubscriptionError, WebhookError,
    SignatureVerificationError
)
from core.utils import generate_tx_ref, get_user_country_currency
from core.decorators import api_view, permission_required
from core.response import APIResponse

User = get_user_model()
logger = logging.getLogger(__name__)

class PaymentInitializationView(View):
    """
    Initialize payment for subscription
    """
    
    @method_decorator(login_required)
    def post(self, request):
        try:
            data = json.loads(request.body)
            plan_name = data.get('plan_name')
            currency = data.get('currency')
            
            if not plan_name or plan_name not in ['premium_monthly', 'premium_yearly']:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid plan name'
                }, status=400)
            
            # Check if user already has active subscription
            existing_subscription = getattr(request.user, 'subscription', None)
            if existing_subscription and existing_subscription.is_active_premium():
                return JsonResponse({
                    'success': False,
                    'error': 'User already has an active premium subscription'
                }, status=400)
            
            service = FlutterwaveService()
            result = service.initialize_subscription_payment(
                user=request.user,
                plan_name=plan_name,
                currency=currency
            )
            
            return JsonResponse({
                'success': True,
                'data': result
            })
            
        except PaymentGatewayError as e:
            logger.error(f"Payment gateway error: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'Payment initialization failed. Please try again.'
            }, status=500)
        except Exception as e:
            logger.error(f"Unexpected error in payment initialization: {str(e)}")
            return JsonResponse({
                'success': False,
                'error': 'An unexpected error occurred'
            }, status=500)

class PaymentCallbackView(View):
    """
    Handle payment callback from Flutterwave
    """
    
    def get(self, request):
        tx_ref = request.GET.get('tx_ref')
        transaction_id = request.GET.get('transaction_id')
        status = request.GET.get('status')
        
        if not tx_ref:
            return render(request, 'payments/callback_error.html', {
                'error': 'Invalid payment callback'
            })
        
        try:
            service = FlutterwaveService()
            result = service.verify_transaction(tx_ref)
            
            if result.get('status') == 'success':
                return render(request, 'payments/callback_success.html', {
                    'transaction': result,
                    'subscription_status': result.get('subscription_status'),
                    'plan_name': result.get('plan_name')
                })
            else:
                return render(request, 'payments/callback_error.html', {
                    'error': result.get('message', 'Payment verification failed')
                })
                
        except Exception as e:
            logger.error(f"Payment callback error: {str(e)}")
            return render(request, 'payments/callback_error.html', {
                'error': 'Payment verification failed'
            })

@csrf_exempt
@require_POST
def flutterwave_webhook(request):
    """
    Handle Flutterwave webhook events
    """
    try:
        # Get webhook signature
        signature = request.headers.get('verif-hash')
        if not signature:
            logger.warning("Webhook received without signature")
            return HttpResponse(status=400)
        
        # Get request body
        payload = request.body.decode('utf-8')
        
        # Parse JSON payload
        try:
            webhook_data = json.loads(payload)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook payload")
            return HttpResponse(status=400)
        
        # Extract event details
        event_type = webhook_data.get('event')
        event_id = webhook_data.get('event.id', '')
        
        # Log webhook event
        webhook_log = FlutterwaveWebhookLog.objects.create(
            event_type=event_type,
            event_id=event_id,
            payload=webhook_data,
            signature=signature,
            tx_ref=webhook_data.get('data', {}).get('tx_ref', ''),
            status=webhook_data.get('data', {}).get('status', ''),
            user_id=webhook_data.get('data', {}).get('meta', {}).get('user_id')
        )
        
        # Verify webhook signature
        service = FlutterwaveService()
        if not service.client.verify_webhook_signature(payload, signature):
            webhook_log.signature_valid = False
            webhook_log.error_message = "Invalid webhook signature"
            webhook_log.save()
            logger.warning(f"Invalid webhook signature for event {event_id}")
            return HttpResponse(status=401)
        
        webhook_log.signature_valid = True
        webhook_log.save()
        
        # Process webhook based on event type
        if event_type == 'charge.completed':
            return _process_charge_completed(webhook_data, webhook_log)
        elif event_type == 'subscription.cancelled':
            return _process_subscription_cancelled(webhook_data, webhook_log)
        elif event_type == 'transfer.completed':
            return _process_transfer_completed(webhook_data, webhook_log)
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
            webhook_log.processed = True
            webhook_log.save()
            return HttpResponse(status=200)
            
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return HttpResponse(status=500)

def _process_charge_completed(webhook_data: Dict, webhook_log: FlutterwaveWebhookLog) -> HttpResponse:
    """Process charge.completed webhook event"""
    try:
        data = webhook_data.get('data', {})
        tx_ref = data.get('tx_ref')
        
        if not tx_ref:
            webhook_log.error_message = "Missing tx_ref in webhook data"
            webhook_log.save()
            return HttpResponse(status=400)
        
        # Process the transaction
        service = FlutterwaveService()
        with db_transaction.atomic():
            result = service.verify_transaction(tx_ref)
            webhook_log.processed = True
            webhook_log.save()
        
        return HttpResponse(status=200)
        
    except Exception as e:
        webhook_log.error_message = str(e)
        webhook_log.increment_processing_attempts()
        logger.error(f"Error processing charge.completed webhook: {str(e)}")
        return HttpResponse(status=500)

def _process_subscription_cancelled(webhook_data: Dict, webhook_log: FlutterwaveWebhookLog) -> HttpResponse:
    """Process subscription.cancelled webhook event"""
    try:
        data = webhook_data.get('data', {})
        subscription_id = data.get('id')
        
        # Find and cancel the subscription
        try:
            subscription = Subscription.objects.get(gateway_subscription_id=subscription_id)
            subscription.cancel_subscription(at_period_end=False)
            
            # Send cancellation email
            _send_subscription_cancelled_email(subscription.user, subscription)
            
        except Subscription.DoesNotExist:
            logger.warning(f"Subscription not found for gateway ID: {subscription_id}")
        
        webhook_log.processed = True
        webhook_log.save()
        return HttpResponse(status=200)
        
    except Exception as e:
        webhook_log.error_message = str(e)
        webhook_log.increment_processing_attempts()
        logger.error(f"Error processing subscription.cancelled webhook: {str(e)}")
        return HttpResponse(status=500)

def _process_transfer_completed(webhook_data: Dict, webhook_log: FlutterwaveWebhookLog) -> HttpResponse:
    """Process transfer.completed webhook event"""
    try:
        # Handle transfer completion logic here
        # This could be for affiliate payouts, refunds, etc.
        
        webhook_log.processed = True
        webhook_log.save()
        return HttpResponse(status=200)
        
    except Exception as e:
        webhook_log.error_message = str(e)
        webhook_log.increment_processing_attempts()
        logger.error(f"Error processing transfer.completed webhook: {str(e)}")
        return HttpResponse(status=500)

@api_view(['GET'])
@login_required
def get_user_subscription(request):
    """Get current user subscription details"""
    try:
        subscription = getattr(request.user, 'subscription', None)
        if not subscription:
            return APIResponse.error("Subscription not found", status_code=404)
        
        return APIResponse.success({
            'id': subscription.id,
            'plan_name': subscription.plan_name,
            'status': subscription.status,
            'amount': str(subscription.amount),
            'currency': subscription.currency,
            'interval': subscription.interval,
            'current_period_start': subscription.current_period_start,
            'current_period_end': subscription.current_period_end,
            'next_billing_date': subscription.next_billing_date,
            'cancel_at_period_end': subscription.cancel_at_period_end,
            'days_until_renewal': subscription.days_until_renewal(),
            'is_active_premium': subscription.is_active_premium(),
            'is_free_tier': subscription.is_free_tier()
        })
        
    except Exception as e:
        logger.error(f"Error fetching user subscription: {str(e)}")
        return APIResponse.error("Failed to fetch subscription details")

@api_view(['POST'])
@login_required
def cancel_subscription(request):
    """Cancel user subscription"""
    try:
        data = json.loads(request.body)
        at_period_end = data.get('at_period_end', True)
        
        subscription = getattr(request.user, 'subscription', None)
        if not subscription:
            return APIResponse.error("Subscription not found", status_code=404)
        
        if not subscription.is_active_premium():
            return APIResponse.error("No active premium subscription to cancel", status_code=400)
        
        # Cancel with gateway if needed
        if subscription.gateway_subscription_id:
            try:
                service = FlutterwaveService()
                service.client.cancel_subscription(subscription.gateway_subscription_id)
            except Exception as e:
                logger.error(f"Failed to cancel subscription with gateway: {str(e)}")
                # Continue with local cancellation even if gateway fails
        
        subscription.cancel_subscription(at_period_end=at_period_end)
        
        # Send cancellation confirmation email
        _send_subscription_cancelled_email(request.user, subscription)
        
        return APIResponse.success({
            'message': 'Subscription cancelled successfully',
            'cancel_at_period_end': subscription.cancel_at_period_end,
            'cancelled_at': subscription.cancelled_at
        })
        
    except Exception as e:
        logger.error(f"Error cancelling subscription: {str(e)}")
        return APIResponse.error("Failed to cancel subscription")

@api_view(['GET'])
@login_required
def get_payment_history(request):
    """Get user payment history"""
    try:
        page = int(request.GET.get('page', 1))
        per_page = min(int(request.GET.get('per_page', 10)), 50)
        
        payments = Payment.objects.filter(user=request.user).order_by('-created_at')
        
        # Pagination
        start = (page - 1) * per_page
        end = start + per_page
        paginated_payments = payments[start:end]
        
        payment_data = []
        for payment in paginated_payments:
            payment_data.append({
                'id': payment.id,
                'reference_id': payment.reference_id,
                'amount': str(payment.amount),
                'currency': payment.currency,
                'status': payment.status,
                'payment_type': payment.payment_type,
                'payment_method': payment.payment_method,
                'description': payment.description,
                'processed_at': payment.processed_at,
                'created_at': payment.created_at
            })
        
        return APIResponse.success({
            'payments': payment_data,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': payments.count(),
                'has_next': end < payments.count()
            }
        })
        
    except Exception as e:
        logger.error(f"Error fetching payment history: {str(e)}")
        return APIResponse.error("Failed to fetch payment history")

@api_view(['GET'])
@login_required
def get_invoices(request):
    """Get user invoices"""
    try:
        invoices = Invoice.objects.filter(user=request.user).order_by('-created_at')
        
        invoice_data = []
        for invoice in invoices:
            invoice_data.append({
                'id': invoice.id,
                'invoice_number': invoice.invoice_number,
                'amount': str(invoice.amount),
                'currency': invoice.currency,
                'total_amount': str(invoice.total_amount),
                'status': invoice.status,
                'due_date': invoice.due_date,
                'paid_at': invoice.paid_at,
                'billing_period_start': invoice.billing_period_start,
                'billing_period_end': invoice.billing_period_end,
                'line_items': invoice.line_items,
                'created_at': invoice.created_at
            })
        
        return APIResponse.success({'invoices': invoice_data})
        
    except Exception as e:
        logger.error(f"Error fetching invoices: {str(e)}")
        return APIResponse.error("Failed to fetch invoices")

@api_view(['POST'])
@login_required
def request_refund(request):
    """Request refund for a payment"""
    try:
        data = json.loads(request.body)
        payment_id = data.get('payment_id')
        reason = data.get('reason', 'customer_request')
        reason_description = data.get('reason_description', '')
        
        if not payment_id:
            return APIResponse.error("Payment ID is required", status_code=400)
        
        try:
            payment = Payment.objects.get(id=payment_id, user=request.user)
        except Payment.DoesNotExist:
            return APIResponse.error("Payment not found", status_code=404)
        
        if payment.status != 'completed':
            return APIResponse.error("Only completed payments can be refunded", status_code=400)
        
        # Check if refund already exists
        existing_refund = RefundRequest.objects.filter(payment=payment).first()
        if existing_refund:
            return APIResponse.error("Refund request already exists for this payment", status_code=400)
        
        # Create refund request
        refund_request = RefundRequest.objects.create(
            user=request.user,
            payment=payment,
            amount=payment.amount,
            currency=payment.currency,
            reason=reason,
            reason_description=reason_description,
            status='pending'
        )
        
        # Send notification to admin
        _send_refund_request_notification(refund_request)
        
        return APIResponse.success({
            'message': 'Refund request submitted successfully',
            'refund_id': refund_request.id,
            'status': refund_request.status
        })
        
    except Exception as e:
        logger.error(f"Error requesting refund: {str(e)}")
        return APIResponse.error("Failed to submit refund request")

@api_view(['GET'])
@permission_required('payments.view_payment')
def admin_payment_stats(request):
    """Get payment statistics for admin dashboard"""
    try:
        # Date range filtering
        days = int(request.GET.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        
        # Basic stats
        stats = {
            'total_payments': Payment.objects.count(),
            'successful_payments': Payment.objects.filter(status='completed').count(),
            'failed_payments': Payment.objects.filter(status='failed').count(),
            'total_revenue': Payment.objects.filter(
                status='completed'
            ).aggregate(Sum('amount'))['amount__sum'] or 0,
            'recent_revenue': Payment.objects.filter(
                status='completed',
                created_at__gte=start_date
            ).aggregate(Sum('amount'))['amount__sum'] or 0,
            'active_subscriptions': Subscription.objects.filter(status='active').count(),
            'premium_subscriptions': Subscription.objects.filter(
                status='active',
                plan_name__in=['premium_monthly', 'premium_yearly']
            ).count(),
            'pending_refunds': RefundRequest.objects.filter(status='pending').count()
        }
        
        # Payment method breakdown
        payment_methods = Payment.objects.filter(
            status='completed',
            created_at__gte=start_date
        ).values('payment_method').annotate(
            count=Count('id'),
            total=Sum('amount')
        )
        
        stats['payment_methods'] = list(payment_methods)
        
        # Monthly revenue trend
        monthly_revenue = []
        for i in range(6):
            month_start = timezone.now().replace(day=1) - timedelta(days=30*i)
            month_end = month_start + timedelta(days=30)
            
            revenue = Payment.objects.filter(
                status='completed',
                created_at__range=[month_start, month_end]
            ).aggregate(Sum('amount'))['amount__sum'] or 0
            
            monthly_revenue.append({
                'month': month_start.strftime('%Y-%m'),
                'revenue': float(revenue)
            })
        
        stats['monthly_revenue'] = list(reversed(monthly_revenue))
        
        return APIResponse.success(stats)
        
    except Exception as e:
        logger.error(f"Error fetching payment stats: {str(e)}")
        return APIResponse.error("Failed to fetch payment statistics")

def _send_subscription_cancelled_email(user: User, subscription: Subscription):
    """Send subscription cancellation email"""
    try:
        context = {
            'user': user,
            'subscription': subscription,
            'cancel_at_period_end': subscription.cancel_at_period_end,
            'period_end_date': subscription.current_period_end
        }
        
        subject = 'Subscription Cancelled - LoveCraft'
        html_message = render_to_string('emails/subscription_cancelled.html', context)
        
        send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
            fail_silently=True
        )
        
    except Exception as e:
        logger.error(f"Failed to send subscription cancellation email: {str(e)}")

def _send_refund_request_notification(refund_request: RefundRequest):
    """Send refund request notification to admin"""
    try:
        context = {
            'refund_request': refund_request,
            'user': refund_request.user,
            'payment': refund_request.payment
        }
        
        subject = f'New Refund Request - {refund_request.id}'
        html_message = render_to_string('emails/refund_request_admin.html', context)
        
        send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[settings.ADMIN_EMAIL],
            html_message=html_message,
            fail_silently=True
        )
        
    except Exception as e:
        logger.error(f"Failed to send refund request notification: {str(e)}")