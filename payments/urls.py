# apps/payments/urls.py
from django.urls import path, include
from . import views

app_name = 'payments'

urlpatterns = [
    # Payment initialization and processing
    path('initialize/', views.PaymentInitializationView.as_view(), name='initialize_payment'),
    path('callback/', views.PaymentCallbackView.as_view(), name='payment_callback'),
    path('webhook/flutterwave/', views.flutterwave_webhook, name='flutterwave_webhook'),
    
    # Subscription management
    path('subscription/', views.get_user_subscription, name='get_subscription'),
    path('subscription/cancel/', views.cancel_subscription, name='cancel_subscription'),
    
    # Payment history and invoices
    path('history/', views.get_payment_history, name='payment_history'),
    path('invoices/', views.get_invoices, name='get_invoices'),
    
    # Refunds
    path('refund/request/', views.request_refund, name='request_refund'),
    
    # Admin endpoints
    path('admin/stats/', views.admin_payment_stats, name='admin_payment_stats'),
]