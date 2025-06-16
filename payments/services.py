# apps/payments/services.py
import logging
from typing import Dict, Any, Optional, Tuple, List
from decimal import Decimal
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction as db_transaction
from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import (
    FlutterwaveTransaction, Subscription, Payment, Invoice, 
    RefundRequest, PaymentGateway, PaymentMethod
)
from .flutterwave_client import FlutterwaveClient
from .exceptions import (
    PaymentGatewayError, InvalidTransactionError, 
    DuplicateTransactionError, SubscriptionError,
    RefundError, PaymentMethodError
)
from core.utils import generate_tx_ref, get_user_country_currency

User = get_user_model()
logger = logging.getLogger(__name__)

class FlutterwaveService:
    """
    Service class for Flutterwave payment processing
    """
    
    def __init__(self):
        self.client = FlutterwaveClient()
    
    def get_regional_pricing(self, plan_name: str, currency: str) -> Decimal:
        """
        Get regional pricing for subscription plans
        """
        plans = settings.SUBSCRIPTION_PLANS
        if plan_name not in plans:
            raise ValueError(f"Invalid plan name: {plan_name}")
        
        plan = plans[plan_name]
        if 'regional_pricing' in plan:
            return Decimal(str(plan['regional_pricing'].get(currency, plan['regional_pricing']['USD'])))
        
        return Decimal(str(plan.get('amount', 0)))
    
    def initialize_subscription_payment(self, user: User, plan_name: str, 
                                      currency: str = None) -> Dict[str, Any]:
        """
        Initialize subscription payment
        """
        if not currency:
            currency = get_user_country_currency(user)
        
        amount = self.get_regional_pricing(plan_name, currency)
        tx_ref = generate_tx_ref()
        
        # Check for existing pending transaction
        existing_transaction = FlutterwaveTransaction.objects.filter(
            user=user,
            status='pending',
            metadata__plan_name=plan_name
        ).first()
        
        if existing_transaction:
            raise DuplicateTransactionError("A pending transaction already exists for this plan")
        
        # Create FlutterwaveTransaction record
        flw_transaction = FlutterwaveTransaction.objects.create(
            user=user,
            tx_ref=tx_ref,
            amount=amount,
            currency=currency,
            transaction_type='subscription',
            customer_email=user.email,
            customer_name=user.get_full_name(),
            status='pending',
            metadata={
                'plan_name': plan_name,
                'user_id': str(user.id)
            }
        )
        
        try:
            # Initialize payment with Flutterwave
            response = self.client.initialize_payment(
                amount=float(amount),
                email=user.email,
                tx_ref=tx_ref,
                currency=currency,
                customer_name=user.get_full_name(),
                customer_phone=getattr(user, 'phone_number', ''),
                description=f"LoveCraft {plan_name.replace('_', ' ').title()} Subscription",
                metadata={
                    'plan_name': plan_name,
                    'user_id': str(user.id),
                    'subscription_type': plan_name
                }
            )
            
            if response.get('status') == 'success':
                # Update transaction with Flutterwave response
                flw_transaction.raw_response = response
                flw_transaction.redirect_url = response['data']['link']
                flw_transaction.save()
                
                return {
                    'status': 'success',
                    'payment_link': response['data']['link'],
                    'tx_ref': tx_ref,
                    'amount': amount,
                    'currency': currency
                }
            else:
                flw_transaction.status = 'failed'
                flw_transaction.failure_reason = response.get('message', 'Payment initialization failed')
                flw_transaction.save()
                
                raise PaymentGatewayError(f"Payment initialization failed: {response.get('message')}")
                
        except Exception as e:
            flw_transaction.status = 'failed'
            flw_transaction.failure_reason = str(e)
            flw_transaction.save()
            raise
    
    def verify_transaction(self, tx_ref: str) -> Dict[str, Any]:
        """
        Verify transaction and process payment
        """
        try:
            flw_transaction = FlutterwaveTransaction.objects.get(tx_ref=tx_ref)
        except FlutterwaveTransaction.DoesNotExist:
            raise InvalidTransactionError(f"Transaction {tx_ref} not found")
        
        if flw_transaction.webhook_processed:
            return {
                'status': 'already_processed',
                'transaction_status': flw_transaction.status
            }
        
        try:
            # Verify with Flutterwave
            response = self.client.verify_transaction(tx_ref)
            
            if response.get('status') == 'success':
                transaction_data = response['data']
                
                with db_transaction.atomic():
                    # Update Flutterwave transaction
                    flw_transaction.flw_ref = transaction_data.get('flw_ref', '')
                    flw_transaction.transaction_id = str(transaction_data.get('id', ''))
                    flw_transaction.status = transaction_data.get('status', 'failed')
                    flw_transaction.payment_type = transaction_data.get('payment_type', '')
                    flw_transaction.raw_response = response
                    flw_transaction.webhook_processed = True
                    flw_transaction.save()
                    
                    # Process successful payment
                    if transaction_data.get('status') == 'successful':
                        return self._process_successful_payment(flw_transaction, transaction_data)
                    else:
                        flw_transaction.failure_reason = transaction_data.get('processor_response', 'Payment failed')
                        flw_transaction.save()
                        
                        return {
                            'status': 'failed',
                            'message': flw_transaction.failure_reason
                        }
            else:
                raise PaymentGatewayError(f"Transaction verification failed: {response.get('message')}")
                
        except Exception as e:
            logger.error(f"Transaction verification error for {tx_ref}: {str(e)}")
            raise
    
    def _process_successful_payment(self, flw_transaction: FlutterwaveTransaction, 
                                  transaction_data: Dict) -> Dict[str, Any]:
        """
        Process successful payment and update subscription
        """
        user = flw_transaction.user
        plan_name = flw_transaction.metadata.get('plan_name', 'premium_monthly')
        
        # Create Payment record
        payment = Payment.objects.create(
            user=user,
            gateway='flutterwave',
            gateway_transaction_id=flw_transaction.transaction_id,
            reference_id=flw_transaction.tx_ref,
            amount=flw_transaction.amount,
            currency=flw_transaction.currency,
            status='completed',
            payment_type='subscription_initial',
            payment_method=transaction_data.get('payment_type', ''),
            description=f"LoveCraft {plan_name.replace('_', ' ').title()} Subscription",
            processed_at=timezone.now(),
            metadata=flw_transaction.metadata
        )
        
        # Update or create subscription
        subscription, created = Subscription.objects.get_or_create(
            user=user,
            defaults={
                'plan_name': plan_name,
                'gateway': 'flutterwave',
                'amount': flw_transaction.amount,
                'currency': flw_transaction.currency,
                'status': 'active'
            }
        )
        
        if not created:
            # Update existing subscription
            subscription.plan_name = plan_name
            subscription.gateway = 'flutterwave'
            subscription.amount = flw_transaction.amount
            subscription.currency = flw_transaction.currency
            subscription.status = 'active'
        
        # Set billing dates
        if plan_name == 'premium_monthly':
            subscription.interval = 'monthly'
            subscription.current_period_start = timezone.now()
            subscription.current_period_end = timezone.now() + timezone.timedelta(days=30)
            subscription.next_billing_date = subscription.current_period_end
        elif plan_name == 'premium_yearly':
            subscription.interval = 'yearly'
            subscription.current_period_start = timezone.now()
            subscription.current_period_end = timezone.now() + timezone.timedelta(days=365)
            subscription.next_billing_date = subscription.current_period_end
        
        subscription.save()
        
        # Link payment to subscription
        payment.subscription = subscription
        payment.save()
        
        # Create invoice
        self._create_invoice(payment, subscription)
        
        # Send confirmation email
        self._send_payment_confirmation_email(user, payment, subscription)
        
        # Clear user's usage cache
        cache.delete(f"user_daily_ideas_{user.id}")
        
        logger.info(f"Successfully processed payment for user {user.id}, plan {plan_name}")
        
        return {
            'status': 'success',
            'message': 'Payment processed successfully',
            'subscription_status': subscription.status,
            'plan_name': subscription.plan_name,
            'next_billing_date': subscription.next_billing_date
        }
    
    def _create_invoice(self, payment: Payment, subscription: Subscription):
        """
        Create invoice for payment
        """
        Invoice.objects.create(
            user=payment.user,
            subscription=subscription,
            payment=payment,
            invoice_number=f"INV-{timezone.now().strftime('%Y%m%d')}-{payment.reference_id[-6:]}",
            amount=payment.amount,
            currency=payment.currency,
            total_amount=payment.amount,
            status='paid',
            due_date=timezone.now(),
            paid_at=payment.processed_at,
            billing_period_start=subscription.current_period_start,
            billing_period_end=subscription.current_period_end,
            line_items=[{
                'description': f"LoveCraft {subscription.plan_name.replace('_', ' ').title()} Subscription",
                'amount': float(payment.amount),
                'currency': payment.currency,
                'period_start': subscription.current_period_start.isoformat(),
                'period_end': subscription.current_period_end.isoformat()
            }]
        )
    
    def _send_payment_confirmation_email(self, user: User, payment: Payment, subscription: Subscription):
        """
        Send payment confirmation email to user
        """
        try:
            subject = f"Payment Confirmation - {subscription.plan_name.replace('_', ' ').title()}"
            html_content = render_to_string('emails/payment_confirmation.html', {
                'user': user,
                'payment': payment,
                'subscription': subscription,
                'amount': payment.amount,
                'currency': payment.currency
            })
            
            send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_content,
                fail_silently=True
            )
        except Exception as e:
            logger.error(f"Failed to send payment confirmation email: {str(e)}")
    
    def process_webhook(self, payload: Dict[str, Any], signature: str) -> Dict[str, Any]:
        """
        Process Flutterwave webhook
        """
        # Verify webhook signature
        if not self.client.verify_webhook_signature(payload, signature):
            raise PaymentGatewayError("Invalid webhook signature")
        
        event_type = payload.get('event')
        data = payload.get('data', {})
        
        if event_type == 'charge.completed':
            return self._handle_charge_completed(data)
        elif event_type == 'transfer.completed':
            return self._handle_transfer_completed(data)
        else:
            logger.info(f"Unhandled webhook event: {event_type}")
            return {'status': 'ignored', 'event': event_type}
    
    def _handle_charge_completed(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle charge completed webhook
        """
        tx_ref = data.get('tx_ref')
        if not tx_ref:
            raise InvalidTransactionError("Missing tx_ref in webhook data")
        
        try:
            return self.verify_transaction(tx_ref)
        except Exception as e:
            logger.error(f"Error processing charge completed webhook: {str(e)}")
            raise
    
    def _handle_transfer_completed(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle transfer completed webhook (for refunds)
        """
        reference = data.get('reference')
        status = data.get('status')
        
        try:
            refund_request = RefundRequest.objects.get(reference_id=reference)
            if status == 'SUCCESSFUL':
                refund_request.status = 'completed'
                refund_request.processed_at = timezone.now()
            else:
                refund_request.status = 'failed'
                refund_request.failure_reason = data.get('complete_message', 'Transfer failed')
            
            refund_request.save()
            
            return {
                'status': 'success',
                'refund_status': refund_request.status
            }
        except RefundRequest.DoesNotExist:
            logger.error(f"Refund request not found for reference: {reference}")
            return {'status': 'error', 'message': 'Refund request not found'}
    
    def cancel_subscription(self, user: User, reason: str = None) -> Dict[str, Any]:
        """
        Cancel user subscription
        """
        try:
            subscription = Subscription.objects.get(user=user, status='active')
        except Subscription.DoesNotExist:
            raise SubscriptionError("No active subscription found")
        
        with db_transaction.atomic():
            subscription.status = 'cancelled'
            subscription.cancelled_at = timezone.now()
            subscription.cancellation_reason = reason or 'User requested cancellation'
            subscription.cancel_at_period_end = True
            subscription.save()
            
            # Send cancellation email
            self._send_cancellation_email(user, subscription)
            
            logger.info(f"Subscription cancelled for user {user.id}")
            
            return {
                'status': 'success',
                'message': 'Subscription cancelled successfully',
                'ends_at': subscription.current_period_end
            }
    
    def _send_cancellation_email(self, user: User, subscription: Subscription):
        """
        Send subscription cancellation email
        """
        try:
            subject = "Subscription Cancelled"
            html_content = render_to_string('emails/subscription_cancelled.html', {
                'user': user,
                'subscription': subscription,
                'ends_at': subscription.current_period_end
            })
            
            send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_content,
                fail_silently=True
            )
        except Exception as e:
            logger.error(f"Failed to send cancellation email: {str(e)}")
    
    def reactivate_subscription(self, user: User, plan_name: str = None) -> Dict[str, Any]:
        """
        Reactivate cancelled subscription
        """
        try:
            subscription = Subscription.objects.get(user=user, status='cancelled')
        except Subscription.DoesNotExist:
            raise SubscriptionError("No cancelled subscription found")
        
        if plan_name:
            subscription.plan_name = plan_name
        
        subscription.status = 'active'
        subscription.cancelled_at = None
        subscription.cancellation_reason = None
        subscription.cancel_at_period_end = False
        subscription.save()
        
        return {
            'status': 'success',
            'message': 'Subscription reactivated successfully'
        }
    
    def initiate_refund(self, payment_id: int, amount: Optional[Decimal] = None, 
                       reason: str = None) -> Dict[str, Any]:
        """
        Initiate refund for a payment
        """
        try:
            payment = Payment.objects.get(id=payment_id, status='completed')
        except Payment.DoesNotExist:
            raise InvalidTransactionError("Payment not found or not eligible for refund")
        
        refund_amount = amount or payment.amount
        if refund_amount > payment.amount:
            raise RefundError("Refund amount cannot exceed payment amount")
        
        # Check if refund already exists
        existing_refund = RefundRequest.objects.filter(
            payment=payment,
            status__in=['pending', 'completed']
        ).first()
        
        if existing_refund:
            raise RefundError("Refund already requested for this payment")
        
        # Create refund request
        refund_request = RefundRequest.objects.create(
            payment=payment,
            user=payment.user,
            amount=refund_amount,
            currency=payment.currency,
            reason=reason or 'Customer requested refund',
            status='pending',
            reference_id=generate_tx_ref()
        )
        
        try:
            # Initiate refund with Flutterwave
            response = self.client.initiate_refund(
                transaction_id=payment.gateway_transaction_id,
                amount=float(refund_amount),
                comments=reason
            )
            
            if response.get('status') == 'success':
                refund_request.gateway_response = response
                refund_request.status = 'processing'
                refund_request.save()
                
                return {
                    'status': 'success',
                    'message': 'Refund initiated successfully',
                    'refund_id': refund_request.id
                }
            else:
                refund_request.status = 'failed'
                refund_request.failure_reason = response.get('message', 'Refund initiation failed')
                refund_request.save()
                
                raise RefundError(f"Refund initiation failed: {response.get('message')}")
                
        except Exception as e:
            refund_request.status = 'failed'
            refund_request.failure_reason = str(e)
            refund_request.save()
            raise
    
    def get_subscription_status(self, user: User) -> Dict[str, Any]:
        """
        Get user's subscription status and details
        """
        try:
            subscription = Subscription.objects.get(user=user)
        except Subscription.DoesNotExist:
            return {
                'has_subscription': False,
                'plan_name': 'free',
                'status': 'inactive'
            }
        
        return {
            'has_subscription': True,
            'plan_name': subscription.plan_name,
            'status': subscription.status,
            'current_period_start': subscription.current_period_start,
            'current_period_end': subscription.current_period_end,
            'next_billing_date': subscription.next_billing_date,
            'amount': subscription.amount,
            'currency': subscription.currency,
            'cancel_at_period_end': subscription.cancel_at_period_end
        }
    
    def get_payment_history(self, user: User, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get user's payment history
        """
        payments = Payment.objects.filter(user=user).order_by('-created_at')[:limit]
        
        return [{
            'id': payment.id,
            'amount': payment.amount,
            'currency': payment.currency,
            'status': payment.status,
            'payment_type': payment.payment_type,
            'description': payment.description,
            'created_at': payment.created_at,
            'processed_at': payment.processed_at
        } for payment in payments]
    
    def get_usage_limits(self, user: User) -> Dict[str, Any]:
        """
        Get user's usage limits based on subscription
        """
        subscription_status = self.get_subscription_status(user)
        plan_name = subscription_status['plan_name']
        
        if plan_name == 'free':
            return {
                'daily_ideas': 5,
                'monthly_ideas': 150,
                'has_premium_features': False
            }
        elif plan_name in ['premium_monthly', 'premium_yearly']:
            return {
                'daily_ideas': -1,  # Unlimited
                'monthly_ideas': -1,  # Unlimited
                'has_premium_features': True
            }
        
        return {
            'daily_ideas': 0,
            'monthly_ideas': 0,
            'has_premium_features': False
        }
    
    def check_subscription_expiry(self) -> List[Dict[str, Any]]:
        """
        Check for expiring subscriptions and notify users
        """
        expiring_subscriptions = Subscription.objects.filter(
            status='active',
            next_billing_date__lte=timezone.now() + timezone.timedelta(days=3),
            cancel_at_period_end=False
        )
        
        notifications = []
        for subscription in expiring_subscriptions:
            try:
                self._send_renewal_reminder_email(subscription.user, subscription)
                notifications.append({
                    'user_id': subscription.user.id,
                    'plan_name': subscription.plan_name,
                    'expires_at': subscription.next_billing_date
                })
            except Exception as e:
                logger.error(f"Failed to send renewal reminder: {str(e)}")
        
        return notifications
    
    def _send_renewal_reminder_email(self, user: User, subscription: Subscription):
        """
        Send subscription renewal reminder email
        """
        subject = "Subscription Renewal Reminder"
        html_content = render_to_string('emails/renewal_reminder.html', {
            'user': user,
            'subscription': subscription,
            'expires_at': subscription.next_billing_date
        })
        
        send_mail(
            subject=subject,
            message='',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_content,
            fail_silently=True
        )
    
    def handle_failed_payment(self, tx_ref: str) -> Dict[str, Any]:
        """
        Handle failed payment processing
        """
        try:
            flw_transaction = FlutterwaveTransaction.objects.get(tx_ref=tx_ref)
            flw_transaction.status = 'failed'
            flw_transaction.save()
            
            # Notify user of failed payment
            self._send_payment_failed_email(flw_transaction.user, flw_transaction)
            
            return {
                'status': 'handled',
                'message': 'Failed payment processed'
            }
        except FlutterwaveTransaction.DoesNotExist:
            return {
                'status': 'error',
                'message': 'Transaction not found'
            }
    
    def _send_payment_failed_email(self, user: User, transaction: FlutterwaveTransaction):
        """
        Send payment failed notification email
        """
        try:
            subject = "Payment Failed"
            html_content = render_to_string('emails/payment_failed.html', {
                'user': user,
                'transaction': transaction,
                'amount': transaction.amount,
                'currency': transaction.currency
            })
            
            send_mail(
                subject=subject,
                message='',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_content,
                fail_silently=True
            )
        except Exception as e:
            logger.error(f"Failed to send payment failed email: {str(e)}")


class PaymentAnalyticsService:
    """
    Service for payment analytics and reporting
    """
    
    def get_revenue_summary(self, start_date: datetime = None, 
                           end_date: datetime = None) -> Dict[str, Any]:
        """
        Get revenue summary for specified period
        """
        if not start_date:
            start_date = timezone.now() - timezone.timedelta(days=30)
        if not end_date:
            end_date = timezone.now()
        
        payments = Payment.objects.filter(
            status='completed',
            processed_at__range=[start_date, end_date]
        )
        
        total_revenue = sum(payment.amount for payment in payments)
        payment_count = payments.count()
        
        return {
            'period': {
                'start': start_date,
                'end': end_date
            },
            'total_revenue': total_revenue,
            'payment_count': payment_count,
            'average_payment': total_revenue / payment_count if payment_count > 0 else 0
        }
    
    def get_subscription_metrics(self) -> Dict[str, Any]:
        """
        Get subscription metrics
        """
        active_subscriptions = Subscription.objects.filter(status='active').count()
        cancelled_subscriptions = Subscription.objects.filter(status='cancelled').count()
        total_subscriptions = active_subscriptions + cancelled_subscriptions
        
        monthly_subscriptions = Subscription.objects.filter(
            status='active',
            plan_name='premium_monthly'
        ).count()
        
        yearly_subscriptions = Subscription.objects.filter(
            status='active',
            plan_name='premium_yearly'
        ).count()
        
        return {
            'active_subscriptions': active_subscriptions,
            'cancelled_subscriptions': cancelled_subscriptions,
            'total_subscriptions': total_subscriptions,
            'retention_rate': (active_subscriptions / total_subscriptions * 100) if total_subscriptions > 0 else 0,
            'monthly_subscriptions': monthly_subscriptions,
            'yearly_subscriptions': yearly_subscriptions
        }