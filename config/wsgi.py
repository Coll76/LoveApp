import os
from django.core.wsgi import get_wsgi_application

print("Setting DJANGO_SETTINGS_MODULE to: config.settings.development")
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

try:
    print("Attempting to get WSGI application...")
    application = get_wsgi_application()
    print("WSGI application loaded successfully!")
except Exception as e:
    print(f"Error loading WSGI application: {e}")
    raise