# apps/pdf_generator/permissions.py
from rest_framework import permissions
from rest_framework.permissions import BasePermission
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from datetime import timedelta

from users.models import User
from .models import PDFDocument, PDFTemplate


class IsPDFOwnerOrReadOnly(BasePermission):
    """
    Custom permission to only allow owners of a PDF to edit it.
    Read permissions are allowed for public PDFs.
    """

    def has_permission(self, request, view):
        """
        Check if user has permission to access the view
        """
        # Allow authenticated users to access the view
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        """
        Check if user has permission to access specific object
        """
        # Read permissions are allowed for any request for public PDFs
        if request.method in permissions.SAFE_METHODS:
            # Allow owner to read their own PDFs
            if obj.user == request.user:
                return True
            # Allow public access to public PDFs
            if hasattr(obj, 'is_public') and obj.is_public:
                return True
            return False

        # Write permissions are only allowed to the owner of the PDF
        return obj.user == request.user


class IsOwnerOrReadOnly(BasePermission):
    """
    Generic permission to only allow owners of an object to edit it.
    """

    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed for any request,
        # so we'll always allow GET, HEAD or OPTIONS requests.
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the owner of the object.
        return obj.user == request.user


class CanGeneratePDF(BasePermission):
    """
    Permission class to check if user can generate PDFs based on their subscription
    and usage limits.
    """

    def has_permission(self, request, view):
        """
        Check if user can generate PDFs
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Check if user has reached their daily limit
        if not self._check_daily_limit(request.user):
            return False

        # Check if user has reached their monthly limit
        if not self._check_monthly_limit(request.user):
            return False

        # Check if user has too many pending PDFs
        if not self._check_pending_limit(request.user):
            return False

        return True

    def _check_daily_limit(self, user):
        """
        Check if user has exceeded their daily PDF generation limit
        """
        try:
            daily_limit = user.get_daily_pdf_limit()
            if daily_limit == -1:  # Unlimited
                return True

            today = timezone.now().date()
            today_count = PDFDocument.objects.filter(
                user=user,
                created_at__date=today
            ).count()

            return today_count < daily_limit
        except Exception:
            # If we can't check the limit, allow the request
            return True

    def _check_monthly_limit(self, user):
        """
        Check if user has exceeded their monthly PDF generation limit
        """
        try:
            monthly_limit = user.get_monthly_pdf_limit()
            if monthly_limit == -1:  # Unlimited
                return True

            # Get start of current month
            now = timezone.now()
            start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            month_count = PDFDocument.objects.filter(
                user=user,
                created_at__gte=start_of_month
            ).count()

            return month_count < monthly_limit
        except Exception:
            # If we can't check the limit, allow the request
            return True

    def _check_pending_limit(self, user):
        """
        Check if user has too many pending PDFs
        """
        try:
            # Limit concurrent pending PDFs to prevent abuse
            max_pending = 10 if user.has_active_subscription() else 3
            
            pending_count = PDFDocument.objects.filter(
                user=user,
                status__in=['pending', 'processing']
            ).count()

            return pending_count < max_pending
        except Exception:
            # If we can't check the limit, allow the request
            return True


class CanAccessPremiumTemplate(BasePermission):
    """
    Permission class to check if user can access premium templates
    """

    def has_permission(self, request, view):
        """
        Check if user can access premium templates
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # For list views, we'll filter in the view itself
        return True

    def has_object_permission(self, request, view, obj):
        """
        Check if user can access specific template
        """
        # Allow access to free templates
        if not obj.is_premium:
            return True

        # Check if user has active subscription for premium templates
        return request.user.has_active_subscription()


class CanManagePDFSharing(BasePermission):
    """
    Permission class for PDF sharing operations
    """

    def has_permission(self, request, view):
        """
        Check if user can manage PDF sharing
        """
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        """
        Check if user can share/unshare specific PDF
        """
        # Only the owner can manage sharing
        if obj.user != request.user:
            return False

        # Check if PDF is in a shareable state
        if obj.status != 'completed':
            return False

        return True


class CanDownloadPDF(BasePermission):
    """
    Permission class for PDF download operations
    """

    def has_permission(self, request, view):
        """
        Basic permission check for downloads
        """
        return True  # We'll do object-level checks

    def has_object_permission(self, request, view, obj):
        """
        Check if user can download specific PDF
        """
        # Owner can always download their PDFs
        if request.user.is_authenticated and obj.user == request.user:
            return True

        # Public PDFs can be downloaded by anyone
        if obj.is_public:
            return True

        # Check for temporary access (e.g., shared links)
        if hasattr(view, 'check_temporary_access'):
            return view.check_temporary_access(request, obj)

        return False


class CanOptimizePDF(BasePermission):
    """
    Permission class for PDF optimization operations
    """

    def has_permission(self, request, view):
        """
        Check if user can optimize PDFs
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Check if user has optimization privileges
        # This could be based on subscription level
        return self._check_optimization_privileges(request.user)

    def has_object_permission(self, request, view, obj):
        """
        Check if user can optimize specific PDF
        """
        # Only the owner can optimize their PDFs
        if obj.user != request.user:
            return False

        # Check if PDF is in optimizable state
        if obj.status != 'completed':
            return False

        # Check if recently optimized (rate limiting)
        if hasattr(obj, 'last_optimized') and obj.last_optimized:
            time_since_optimization = timezone.now() - obj.last_optimized
            if time_since_optimization < timedelta(minutes=5):
                return False

        return True

    def _check_optimization_privileges(self, user):
        """
        Check if user has optimization privileges
        """
        try:
            # Premium users get optimization features
            if user.has_active_subscription():
                return True

            # Free users might have limited optimization
            # Check daily optimization limit
            today = timezone.now().date()
            today_optimizations = getattr(user, 'daily_optimizations', 0)
            
            return today_optimizations < 3  # Free users: 3 per day
        except Exception:
            return False


class CanAccessAnalytics(BasePermission):
    """
    Permission class for PDF analytics access
    """

    def has_permission(self, request, view):
        """
        Check if user can access analytics
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Basic analytics for all users, detailed for premium
        return True

    def has_object_permission(self, request, view, obj):
        """
        Check if user can access specific analytics
        """
        # Users can only access their own analytics
        return obj.user == request.user


class IsPDFPublic(BasePermission):
    """
    Permission class for public PDF access
    """

    def has_permission(self, request, view):
        """
        Allow public access to public PDFs
        """
        return True

    def has_object_permission(self, request, view, obj):
        """
        Check if PDF is publicly accessible
        """
        return obj.is_public and obj.status == 'completed'


class CanRegeneratePDF(BasePermission):
    """
    Permission class for PDF regeneration
    """

    def has_permission(self, request, view):
        """
        Check if user can regenerate PDFs
        """
        if not request.user or not request.user.is_authenticated:
            return False

        # Check generation limits (same as CanGeneratePDF)
        return CanGeneratePDF().has_permission(request, view)

    def has_object_permission(self, request, view, obj):
        """
        Check if user can regenerate specific PDF
        """
        # Only owner can regenerate
        if obj.user != request.user:
            return False

        # Can't regenerate if currently processing
        if obj.status == 'processing':
            return False

        return True


class CanManageCustomization(BasePermission):
    """
    Permission class for PDF customization management
    """

    def has_permission(self, request, view):
        """
        Check if user can manage PDF customizations
        """
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        """
        Check if user can manage specific customization
        """
        # Users can only manage their own customizations
        return obj.user == request.user


# Utility function to check if user can perform bulk operations
def can_perform_bulk_operations(user, max_items=10):
    """
    Check if user can perform bulk operations on PDFs
    """
    if not user or not user.is_authenticated:
        return False, "Authentication required"

    # Premium users get higher limits
    if user.has_active_subscription():
        max_items = 50

    return True, f"Can process up to {max_items} items"


# Utility function to check template access
def can_access_template(user, template):
    """
    Check if user can access a specific template
    """
    if not template.is_active:
        return False, "Template is not active"

    if template.is_premium and not user.has_active_subscription():
        return False, "Premium subscription required"

    return True, "Access granted"


# Utility function to check PDF access with detailed reason
def can_access_pdf(user, pdf_doc, action='view'):
    """
    Check if user can access PDF with detailed reason
    """
    if not pdf_doc:
        return False, "PDF not found"

    if pdf_doc.status == 'deleted':
        return False, "PDF has been deleted"

    # Owner access
    if user and user.is_authenticated and pdf_doc.user == user:
        return True, "Owner access"

    # Public access
    if action in ['view', 'download'] and pdf_doc.is_public:
        if pdf_doc.status == 'completed':
            return True, "Public access"
        else:
            return False, f"PDF not ready (status: {pdf_doc.status})"

    # No access
    return False, "Access denied"