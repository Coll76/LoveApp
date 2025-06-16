# apps/payments/flutterwave_client.py
import requests
import hashlib
import hmac
import json
import logging
from typing import Dict, Any, Optional, List
from django.conf import settings
from django.core.cache import cache
from .exceptions import PaymentGatewayError, InvalidTransactionError
from core.utils import generate_tx_ref

logger = logging.getLogger(__name__)

class FlutterwaveClient:
    """
    Flutterwave API integration client with comprehensive error handling
    """
    
    def __init__(self):
        self.secret_key = settings.FLUTTERWAVE_SECRET_KEY
        self.public_key = settings.FLUTTERWAVE_PUBLIC_KEY
        self.base_url = settings.FLUTTERWAVE_BASE_URL.rstrip('/')
        self.webhook_secret = settings.FLUTTERWAVE_WEBHOOK_SECRET
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json'
        })
    
    def _make_request(self, method: str, endpoint: str, data: Optional[Dict] = None, 
                     params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make HTTP request to Flutterwave API with error handling
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                json=data,
                params=params,
                timeout=30
            )
            
            # Log the request for debugging
            logger.info(f"Flutterwave API {method} {endpoint}: Status {response.status_code}")
            
            response_data = response.json()
            
            if response.status_code >= 400:
                error_message = response_data.get('message', 'Unknown error occurred')
                logger.error(f"Flutterwave API Error: {error_message}")
                raise PaymentGatewayError(f"Flutterwave API Error: {error_message}")
            
            return response_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Flutterwave API request failed: {str(e)}")
            raise PaymentGatewayError(f"API request failed: {str(e)}")
        except ValueError as e:
            logger.error(f"Invalid JSON response from Flutterwave: {str(e)}")
            raise PaymentGatewayError("Invalid response from payment gateway")
    
    # Payment Methods
    def initialize_payment(self, amount: float, email: str, tx_ref: str = None, 
                          callback_url: str = None, **kwargs) -> Dict[str, Any]:
        """
        Initialize payment transaction
        """
        if not tx_ref:
            tx_ref = generate_tx_ref()
        
        payment_data = {
            "tx_ref": tx_ref,
            "amount": str(amount),
            "currency": kwargs.get('currency', 'USD'),
            "redirect_url": callback_url or settings.FLUTTERWAVE_CALLBACK_URL,
            "customer": {
                "email": email,
                "name": kwargs.get('customer_name', ''),
                "phonenumber": kwargs.get('customer_phone', '')
            },
            "customizations": {
                "title": kwargs.get('title', 'LoveCraft Payment'),
                "description": kwargs.get('description', 'Premium subscription payment'),
                "logo": kwargs.get('logo', settings.SITE_LOGO_URL)
            },
            "meta": kwargs.get('metadata', {})
        }
        
        # Add payment options if specified
        if 'payment_options' in kwargs:
            payment_data['payment_options'] = kwargs['payment_options']
        
        return self._make_request('POST', 'payments', payment_data)
    
    def verify_transaction(self, tx_ref: str) -> Dict[str, Any]:
        """
        Verify transaction status by transaction reference
        """
        cache_key = f"flw_verify_{tx_ref}"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return cached_result
        
        result = self._make_request('GET', f'transactions/verify_by_reference', 
                                  params={'tx_ref': tx_ref})
        
        # Cache successful verifications for 5 minutes
        if result.get('status') == 'success':
            cache.set(cache_key, result, 300)
        
        return result
    
    def get_transaction_details(self, transaction_id: str) -> Dict[str, Any]:
        """
        Get complete transaction details by ID
        """
        return self._make_request('GET', f'transactions/{transaction_id}')
    
    def verify_transaction_by_id(self, transaction_id: str) -> Dict[str, Any]:
        """
        Verify transaction by transaction ID
        """
        return self._make_request('GET', f'transactions/{transaction_id}/verify')
    
    # Subscription Methods
    def create_payment_plan(self, name: str, amount: float, interval: str, 
                           currency: str = 'USD', **kwargs) -> Dict[str, Any]:
        """
        Create recurring payment plan
        """
        plan_data = {
            "amount": int(amount * 100),  # Convert to kobo/cents
            "name": name,
            "interval": interval,  # monthly, quarterly, yearly
            "currency": currency
        }
        
        # Add optional fields
        optional_fields = ['duration', 'description']
        for field in optional_fields:
            if field in kwargs:
                plan_data[field] = kwargs[field]
        
        return self._make_request('POST', 'payment-plans', plan_data)
    
    def get_payment_plan(self, plan_id: str) -> Dict[str, Any]:
        """
        Get payment plan details
        """
        return self._make_request('GET', f'payment-plans/{plan_id}')
    
    def update_payment_plan(self, plan_id: str, **kwargs) -> Dict[str, Any]:
        """
        Update payment plan
        """
        return self._make_request('PUT', f'payment-plans/{plan_id}', data=kwargs)
    
    def cancel_payment_plan(self, plan_id: str) -> Dict[str, Any]:
        """
        Cancel payment plan
        """
        return self._make_request('PUT', f'payment-plans/{plan_id}/cancel')
    
    def subscribe_customer(self, email: str, plan_id: str, tx_ref: str = None,
                          **kwargs) -> Dict[str, Any]:
        """
        Subscribe customer to payment plan
        """
        if not tx_ref:
            tx_ref = generate_tx_ref()
        
        subscription_data = {
            "tx_ref": tx_ref,
            "amount": kwargs.get('amount', 0),
            "currency": kwargs.get('currency', 'USD'),
            "payment_plan": plan_id,
            "redirect_url": kwargs.get('redirect_url', settings.FLUTTERWAVE_CALLBACK_URL),
            "customer": {
                "email": email,
                "name": kwargs.get('customer_name', ''),
                "phonenumber": kwargs.get('customer_phone', '')
            }
        }
        
        return self._make_request('POST', 'payments', subscription_data)
    
    def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Get subscription details
        """
        return self._make_request('GET', f'subscriptions/{subscription_id}')
    
    def cancel_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Cancel active subscription
        """
        return self._make_request('PUT', f'subscriptions/{subscription_id}/cancel')
    
    def activate_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Activate subscription
        """
        return self._make_request('PUT', f'subscriptions/{subscription_id}/activate')
    
    # Customer Methods
    def create_customer(self, email: str, name: str, phone: str = None) -> Dict[str, Any]:
        """
        Create customer profile
        """
        customer_data = {
            "email": email,
            "name": name
        }
        
        if phone:
            customer_data["phone_number"] = phone
        
        return self._make_request('POST', 'customers', customer_data)
    
    def get_customer(self, customer_id: str) -> Dict[str, Any]:
        """
        Get customer details
        """
        return self._make_request('GET', f'customers/{customer_id}')
    
    def update_customer(self, customer_id: str, **kwargs) -> Dict[str, Any]:
        """
        Update customer details
        """
        return self._make_request('PUT', f'customers/{customer_id}', data=kwargs)
    
    # Refund Methods
    def initiate_refund(self, transaction_id: str, amount: Optional[float] = None,
                       **kwargs) -> Dict[str, Any]:
        """
        Process refund for transaction
        """
        refund_data = {
            "id": transaction_id
        }
        
        if amount:
            refund_data["amount"] = amount
        
        # Add optional fields
        if 'comments' in kwargs:
            refund_data['comments'] = kwargs['comments']
        
        return self._make_request('POST', 'transactions/refund', refund_data)
    
    def get_refund_details(self, refund_id: str) -> Dict[str, Any]:
        """
        Get refund details
        """
        return self._make_request('GET', f'refunds/{refund_id}')
    
    # Transfer Methods
    def initiate_transfer(self, amount: float, account_bank: str, account_number: str,
                         currency: str = 'NGN', **kwargs) -> Dict[str, Any]:
        """
        Initiate bank transfer
        """
        transfer_data = {
            "account_bank": account_bank,
            "account_number": account_number,
            "amount": amount,
            "currency": currency,
            "reference": kwargs.get('reference', generate_tx_ref()),
            "callback_url": kwargs.get('callback_url'),
            "debit_currency": kwargs.get('debit_currency', currency)
        }
        
        # Add beneficiary details if provided
        if 'beneficiary_name' in kwargs:
            transfer_data['beneficiary_name'] = kwargs['beneficiary_name']
        
        return self._make_request('POST', 'transfers', transfer_data)
    
    def get_transfer_details(self, transfer_id: str) -> Dict[str, Any]:
        """
        Get transfer details
        """
        return self._make_request('GET', f'transfers/{transfer_id}')
    
    # Bank and Account Methods
    def get_banks(self, country: str = 'NG') -> Dict[str, Any]:
        """
        Get list of banks for a country
        """
        return self._make_request('GET', f'banks/{country}')
    
    def resolve_account(self, account_number: str, account_bank: str) -> Dict[str, Any]:
        """
        Resolve bank account details
        """
        return self._make_request('POST', 'accounts/resolve', {
            "account_number": account_number,
            "account_bank": account_bank
        })
    
    # Utility Methods
    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        """
        Verify webhook signature using HMAC
        """
        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected_signature)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {str(e)}")
            return False
    
    def get_supported_countries(self) -> List[Dict[str, Any]]:
        """
        Get list of supported countries
        """
        cache_key = "flw_supported_countries"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return cached_result
        
        result = self._make_request('GET', 'countries')
        
        # Cache for 24 hours
        cache.set(cache_key, result, 86400)
        return result
    
    def get_supported_currencies(self) -> List[Dict[str, Any]]:
        """
        Get list of supported currencies
        """
        cache_key = "flw_supported_currencies"
        cached_result = cache.get(cache_key)
        
        if cached_result:
            return cached_result
        
        result = self._make_request('GET', 'currencies')
        
        # Cache for 24 hours
        cache.set(cache_key, result, 86400)
        return result
    
    def get_fx_rates(self, from_currency: str, to_currency: str, 
                     amount: float) -> Dict[str, Any]:
        """
        Get foreign exchange rates
        """
        return self._make_request('GET', 'fx-rates', params={
            'from': from_currency,
            'to': to_currency,
            'amount': amount
        })
    
    def validate_charge(self, flw_ref: str, otp: str) -> Dict[str, Any]:
        """
        Validate charge with OTP
        """
        return self._make_request('POST', 'validate-charge', {
            "otp": otp,
            "flw_ref": flw_ref,
            "type": "card"
        })
    
    def get_transaction_fees(self, amount: float, currency: str = 'NGN',
                           payment_type: str = 'card') -> Dict[str, Any]:
        """
        Calculate transaction fees
        """
        return self._make_request('GET', 'transactions/fee', params={
            'amount': amount,
            'currency': currency,
            'payment_type': payment_type
        })
    
    def get_settlement_details(self, settlement_id: str) -> Dict[str, Any]:
        """
        Get settlement details
        """
        return self._make_request('GET', f'settlements/{settlement_id}')
    
    def get_balance(self, currency: str = None) -> Dict[str, Any]:
        """
        Get account balance
        """
        params = {}
        if currency:
            params['currency'] = currency
        
        return self._make_request('GET', 'balances', params=params)
    
    def create_virtual_account(self, email: str, is_permanent: bool = True,
                              **kwargs) -> Dict[str, Any]:
        """
        Create virtual account number
        """
        account_data = {
            "email": email,
            "is_permanent": is_permanent,
            "bvn": kwargs.get('bvn'),
            "tx_ref": kwargs.get('tx_ref', generate_tx_ref())
        }
        
        # Remove None values
        account_data = {k: v for k, v in account_data.items() if v is not None}
        
        return self._make_request('POST', 'virtual-account-numbers', account_data)
    
    def get_virtual_account(self, order_ref: str) -> Dict[str, Any]:
        """
        Get virtual account details
        """
        return self._make_request('GET', f'virtual-account-numbers/{order_ref}')
    
    def get_bvn_details(self, bvn: str) -> Dict[str, Any]:
        """
        Get BVN details (Nigeria specific)
        """
        return self._make_request('GET', f'kyc/bvns/{bvn}')
    
    def charge_card(self, card_number: str, cvv: str, expiry_month: str,
                   expiry_year: str, amount: float, email: str, **kwargs) -> Dict[str, Any]:
        """
        Charge card directly
        """
        charge_data = {
            "card_number": card_number,
            "cvv": cvv,
            "expiry_month": expiry_month,
            "expiry_year": expiry_year,
            "currency": kwargs.get('currency', 'NGN'),
            "amount": str(amount),
            "redirect_url": kwargs.get('redirect_url'),
            "email": email,
            "fullname": kwargs.get('fullname', ''),
            "phone_number": kwargs.get('phone_number', ''),
            "tx_ref": kwargs.get('tx_ref', generate_tx_ref())
        }
        
        return self._make_request('POST', 'charges?type=card', charge_data)
    
    def charge_bank_account(self, account_bank: str, account_number: str,
                           amount: float, email: str, **kwargs) -> Dict[str, Any]:
        """
        Charge bank account
        """
        charge_data = {
            "account_bank": account_bank,
            "account_number": account_number,
            "amount": str(amount),
            "currency": kwargs.get('currency', 'NGN'),
            "email": email,
            "phone_number": kwargs.get('phone_number', ''),
            "fullname": kwargs.get('fullname', ''),
            "tx_ref": kwargs.get('tx_ref', generate_tx_ref())
        }
        
        return self._make_request('POST', 'charges?type=debit_ng_account', charge_data)
    
    def charge_mobile_money(self, phone_number: str, network: str, amount: float,
                           email: str, **kwargs) -> Dict[str, Any]:
        """
        Charge mobile money
        """
        charge_data = {
            "phone_number": phone_number,
            "network": network,  # MTN, AIRTEL, TIGO, VODAFONE
            "amount": str(amount),
            "currency": kwargs.get('currency', 'UGX'),
            "email": email,
            "tx_ref": kwargs.get('tx_ref', generate_tx_ref()),
            "order_id": kwargs.get('order_id', generate_tx_ref())
        }
        
        return self._make_request('POST', 'charges?type=mobile_money_uganda', charge_data)