from django.core.management.utils import get_random_secret_key
print("JWT Secret Key:", get_random_secret_key())