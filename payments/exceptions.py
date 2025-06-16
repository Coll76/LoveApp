# apps/payments/exceptions.py
"""
Custom exceptions for payment processing
"""

class PaymentError(Exception):
    """Base exception for payment-related errors"""
    pass

class PaymentGatewayError(PaymentError):
    """Exception raised when payment gateway returns an error"""
    def __init__(self, message, gateway_response=None, error_code=None):
        super().__init__(message)
        self.gateway_response = gateway_response
        self.error_code = error_code

class InvalidTransactionError(PaymentError):
    """Exception raised for invalid transaction data"""
    pass

class TransactionNotFoundError(PaymentError):
    """Exception raised when transaction is not found"""
    pass

class DuplicateTransactionError(PaymentError):
    """Exception raised for duplicate transaction attempts"""
    pass

class InsufficientFundsError(PaymentError):
    """Exception raised when customer has insufficient funds"""
    pass

class PaymentMethodError(PaymentError):
    """Exception raised for payment method related errors"""
    pass

class SubscriptionError(PaymentError):
    """Exception raised for subscription-related errors"""
    pass

class RefundError(PaymentError):
    """Exception raised for refund processing errors"""
    pass

class WebhookError(PaymentError):
    """Exception raised for webhook processing errors"""
    pass

class SignatureVerificationError(WebhookError):
    """Exception raised when webhook signature verification fails"""
    pass