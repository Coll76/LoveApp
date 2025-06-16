# apps/payments/validators.py
from django.core.exceptions import ValidationError
from django.conf import settings
import re

def validate_currency_code(value):
    """Validate currency code format"""
    if not re.match(r'^[A-Z]{3}$', value):
        raise ValidationError('Currency code must be 3 uppercase letters')
    
    if value not in settings.SUPPORTED_CURRENCIES:
        raise ValidationError(f'Currency {value} is not supported')

def validate_transaction_reference(value):
    """Validate transaction reference format"""
    if not re.match(r'^lc_\d+_[a-zA-Z0-9]{8}$', value):
        raise ValidationError('Invalid transaction reference format')

def validate_positive_amount(value):
    """Validate amount is positive"""
    if value <= 0:
        raise ValidationError('Amount must be greater than zero')