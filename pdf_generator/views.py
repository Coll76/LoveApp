# apps/pdf_generator/views.py
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count, Avg, Sum
from django.http import HttpResponse, Http404, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from rest_framework import status, permissions, filters
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.generics import (
    ListCreateAPIView, RetrieveUpdateDestroyAPIView, 
    ListAPIView, RetrieveAPIView, CreateAPIView
)
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
from rest_framework.renderers import JSONRenderer
from rest_framework.pagination import PageNumberPagination

from ideas.models import GeneratedIdea
from users.models import User
from .models import (
    PDFDocument, PDFTemplate, PDFCustomization, 
    PDFGenerationQueue, PDFUsageStats
)
from .serializers import (
    PDFDocumentSerializer, PDFTemplateSerializer, PDFCustomizationSerializer,
    PDFGenerationQueueSerializer, PDFUsageStatsSerializer,
    PDFDocumentCreateSerializer, PDFDocumentDetailSerializer,
    PDFAnalyticsSerializer
)
from .services import (
    PDFGeneratorService, PDFQueueService, PDFTemplateService,
    PDFAnalyticsService, PDFOptimizationService, PDFGenerationError
)
from .permissions import (
    IsPDFOwnerOrReadOnly, CanGeneratePDF, CanAccessPremiumTemplate,
    IsOwnerOrReadOnly
)
from .filters import PDFDocumentFilter, PDFTemplateFilter
from .throttles import PDFGenerationThrottle, PDFDownloadThrottle
from .views import generate_pdf_async, optimize_pdf_async
from .utils import get_client_ip, log_pdf_access

logger = logging.getLogger(__name__)


class StandardResultsSetPagination(PageNumberPagination):
    """Standard pagination class for PDF endpoints"""
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class PDFDocumentViewSet(ModelViewSet):
    """
    ViewSet for PDF document CRUD operations
    
    Endpoints:
    - GET /api/pdf/documents/ - List user's PDF documents
    - POST /api/pdf/documents/ - Generate new PDF document
    - GET /api/pdf/documents/{id}/ - Get specific PDF document
    - PUT/PATCH /api/pdf/documents/{id}/ - Update PDF document
    - DELETE /api/pdf/documents/{id}/ - Delete PDF document
    """
    
    serializer_class = PDFDocumentSerializer
    permission_classes = [IsAuthenticated, IsPDFOwnerOrReadOnly]
    pagination_class = StandardResultsSetPagination
    filterset_class = PDFDocumentFilter
    filter_backends = [filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'idea__title', 'idea__description']
    ordering_fields = ['created_at', 'updated_at', 'download_count', 'generation_time']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Return PDFs for the current user only"""
        return PDFDocument.objects.select_related(
            'user', 'idea', 'template'
        ).prefetch_related(
            'idea__request'
        ).for_user(self.request.user)
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'create':
            return PDFDocumentCreateSerializer
        elif self.action == 'retrieve':
            return PDFDocumentDetailSerializer
        return PDFDocumentSerializer
    
    @method_decorator(throttle_classes([PDFGenerationThrottle]))
    def create(self, request, *args, **kwargs):
        """Generate new PDF document"""
        
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            with transaction.atomic():
                # Get validated data
                idea_id = serializer.validated_data['idea_id']
                template_id = serializer.validated_data.get('template_id')
                custom_options = serializer.validated_data.get('custom_options', {})
                
                # Get related objects
                idea = get_object_or_404(
                    GeneratedIdea.objects.select_related('request'),
                    id=idea_id,
                    request__user=request.user
                )
                
                template = None
                if template_id:
                    template = get_object_or_404(
                        PDFTemplate.objects.active(),
                        id=template_id
                    )
                    
                    # Check premium template access
                    if template.is_premium and not request.user.has_active_subscription():
                        raise PermissionDenied("Premium template requires active subscription")
                
                # Check generation limits
                if not PDFDocument.objects.user_can_generate(request.user):
                    return Response(
                        {'error': 'Daily PDF generation limit exceeded'},
                        status=status.HTTP_429_TOO_MANY_REQUESTS
                    )
                
                # Generate PDF
                pdf_service = PDFGeneratorService()
                
                # Check if async generation is preferred
                if custom_options.get('async_generation', False) or request.user.preferences.get('async_pdf', False):
                    # Create document record
                    pdf_doc = pdf_service._create_pdf_document(
                        request.user, idea, template, custom_options
                    )
                    
                    # Add to queue for async processing
                    queue_service = PDFQueueService()
                    priority = 'high' if request.user.has_active_subscription() else 'normal'
                    queue_service.add_to_queue(pdf_doc, priority)
                    
                    # Return queued response
                    serializer = PDFDocumentSerializer(pdf_doc)
                    return Response(
                        {
                            'pdf_document': serializer.data,
                            'message': 'PDF generation queued. You will be notified when ready.',
                            'queue_position': pdf_doc.queue_item.queue_position if hasattr(pdf_doc, 'queue_item') else None
                        },
                        status=status.HTTP_202_ACCEPTED
                    )
                else:
                    # Synchronous generation
                    pdf_doc = pdf_service.generate_pdf(
                        request.user, idea, template, custom_options
                    )
                    
                    serializer = PDFDocumentDetailSerializer(pdf_doc)
                    return Response(serializer.data, status=status.HTTP_201_CREATED)
                
        except ValidationError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except PDFGenerationError as e:
            logger.error(f"PDF generation failed for user {request.user.id}: {str(e)}")
            return Response(
                {'error': 'PDF generation failed. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            logger.error(f"Unexpected error in PDF generation: {str(e)}")
            return Response(
                {'error': 'An unexpected error occurred'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def retrieve(self, request, *args, **kwargs):
        """Get specific PDF document with access logging"""
        
        pdf_doc = self.get_object()
        
        # Log access
        log_pdf_access(
            pdf_doc=pdf_doc,
            user=request.user,
            action='view',
            ip_address=get_client_ip(request)
        )
        
        return super().retrieve(request, *args, **kwargs)
    
    def update(self, request, *args, **kwargs):
        """Update PDF document metadata only"""
        
        # Only allow updating specific fields
        allowed_fields = ['custom_options', 'is_public', 'include_qr_code', 'include_watermark']
        
        # Filter request data to only allowed fields
        filtered_data = {k: v for k, v in request.data.items() if k in allowed_fields}
        
        # If making public, generate access token
        if filtered_data.get('is_public', False):
            pdf_doc = self.get_object()
            if not pdf_doc.public_access_token:
                pdf_doc.generate_public_token()
        
        # Create new serializer with filtered data
        serializer = self.get_serializer(self.get_object(), data=filtered_data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        return Response(serializer.data)
    
    def destroy(self, request, *args, **kwargs):
        """Soft delete PDF document"""
        
        pdf_doc = self.get_object()
        
        # Soft delete instead of hard delete
        pdf_doc.delete()  # This uses SoftDeleteModel
        
        # Clean up file system
        if pdf_doc.file_path and os.path.exists(pdf_doc.file_path):
            try:
                os.remove(pdf_doc.file_path)
            except OSError:
                logger.warning(f"Could not remove PDF file: {pdf_doc.file_path}")
        
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([PDFDownloadThrottle])
def download_pdf(request, pdf_id):
    """
    Download PDF file
    
    GET /api/pdf/documents/{id}/download/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            id=pdf_id
        )
        
        # Check permissions
        if pdf_doc.user != request.user and not pdf_doc.is_public:
            raise PermissionDenied("You don't have permission to download this PDF")
        
        # Check if file exists
        if not pdf_doc.file_path or not os.path.exists(pdf_doc.file_path):
            return Response(
                {'error': 'PDF file not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if PDF is ready
        if pdf_doc.status != 'completed':
            return Response(
                {'error': f'PDF is not ready. Status: {pdf_doc.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Log download
        log_pdf_access(
            pdf_doc=pdf_doc,
            user=request.user,
            action='download',
            ip_address=get_client_ip(request)
        )
        
        # Increment download count
        pdf_doc.increment_download_count()
        
        # Return file response
        response = FileResponse(
            open(pdf_doc.file_path, 'rb'),
            content_type='application/pdf',
            as_attachment=True,
            filename=pdf_doc.filename
        )
        
        # Add security headers
        response['X-Content-Type-Options'] = 'nosniff'
        response['X-Frame-Options'] = 'DENY'
        response['Content-Security-Policy'] = "default-src 'none'"
        
        return response
        
    except Exception as e:
        logger.error(f"PDF download failed: {str(e)}")
        return Response(
            {'error': 'Download failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
def download_public_pdf(request, access_token):
    """
    Download publicly shared PDF
    
    GET /api/pdf/public/{access_token}/download/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            public_access_token=access_token,
            is_public=True
        )
        
        # Check if file exists
        if not pdf_doc.file_path or not os.path.exists(pdf_doc.file_path):
            raise Http404("PDF file not found")
        
        # Check if PDF is ready
        if pdf_doc.status != 'completed':
            return Response(
                {'error': f'PDF is not ready. Status: {pdf_doc.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Log access
        log_pdf_access(
            pdf_doc=pdf_doc,
            user=request.user if request.user.is_authenticated else None,
            action='public_download',
            ip_address=get_client_ip(request)
        )
        
        # Increment download count
        pdf_doc.increment_download_count()
        
        return FileResponse(
            open(pdf_doc.file_path, 'rb'),
            content_type='application/pdf',
            as_attachment=True,
            filename=pdf_doc.filename
        )
        
    except Exception as e:
        logger.error(f"Public PDF download failed: {str(e)}")
        return Response(
            {'error': 'Download failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def share_pdf(request, pdf_id):
    """
    Generate sharing link for PDF
    
    POST /api/pdf/documents/{id}/share/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            id=pdf_id,
            user=request.user
        )
        
        # Make PDF public and generate access token
        pdf_doc.is_public = True
        access_token = pdf_doc.generate_public_token()
        pdf_doc.save()
        
        # Increment share count
        pdf_doc.increment_share_count()
        
        # Generate sharing URL
        share_url = f"{settings.FRONTEND_URL}/shared/pdf/{access_token}"
        
        return Response({
            'share_url': share_url,
            'access_token': access_token,
            'expires_at': None,  # Public links don't expire by default
            'message': 'PDF is now publicly shareable'
        })
        
    except Exception as e:
        logger.error(f"PDF sharing failed: {str(e)}")
        return Response(
            {'error': 'Sharing failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def unshare_pdf(request, pdf_id):
    """
    Remove public sharing for PDF
    
    POST /api/pdf/documents/{id}/unshare/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            id=pdf_id,
            user=request.user
        )
        
        # Remove public access
        pdf_doc.is_public = False
        pdf_doc.public_access_token = ''
        pdf_doc.save()
        
        return Response({
            'message': 'PDF sharing has been disabled'
        })
        
    except Exception as e:
        logger.error(f"PDF unsharing failed: {str(e)}")
        return Response(
            {'error': 'Unsharing failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


class PDFTemplateViewSet(ReadOnlyModelViewSet):
    """
    Read-only ViewSet for PDF templates
    
    Endpoints:
    - GET /api/pdf/templates/ - List available templates
    - GET /api/pdf/templates/{id}/ - Get specific template
    """
    
    serializer_class = PDFTemplateSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    pagination_class = StandardResultsSetPagination
    filterset_class = PDFTemplateFilter
    filter_backends = [filters.DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'usage_count', 'created_at']
    ordering = ['sort_order', 'name']
    
    def get_queryset(self):
        """Return active templates, filtered by user subscription"""
        
        queryset = PDFTemplate.objects.active()
        
        # Filter premium templates for non-subscribers
        if not self.request.user.is_authenticated or not self.request.user.has_active_subscription():
            queryset = queryset.filter(is_premium=False)
        
        return queryset
    
    @method_decorator(cache_page(300))  # Cache for 5 minutes
    def list(self, request, *args, **kwargs):
        """List templates with caching"""
        return super().list(request, *args, **kwargs)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def template_preview(request, template_id):
    """
    Get template preview
    
    GET /api/pdf/templates/{id}/preview/
    """
    
    try:
        template = get_object_or_404(
            PDFTemplate.objects.active(),
            id=template_id
        )
        
        # Check premium access
        if template.is_premium and not request.user.has_active_subscription():
            raise PermissionDenied("Premium template requires active subscription")
        
        # Return preview data
        preview_data = {
            'template': PDFTemplateSerializer(template).data,
            'preview_url': template.preview_image.url if template.preview_image else None,
            'sample_html': template.html_template[:500] + '...' if len(template.html_template) > 500 else template.html_template,
        }
        
        return Response(preview_data)
        
    except Exception as e:
        logger.error(f"Template preview failed: {str(e)}")
        return Response(
            {'error': 'Preview failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


class PDFCustomizationAPIView(APIView):
    """
    API for managing user's PDF customization preferences
    
    GET /api/pdf/customization/ - Get user's customization
    POST /api/pdf/customization/ - Create/update customization
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = PDFCustomizationSerializer
    
    def get(self, request):
        """Get user's PDF customization"""
        
        try:
            customization = PDFCustomization.objects.get(user=request.user)
            serializer = PDFCustomizationSerializer(customization)
            return Response(serializer.data)
        except PDFCustomization.DoesNotExist:
            # Return default customization
            return Response({
                'color_scheme': 'default',
                'primary_color': '#007bff',
                'secondary_color': '#6c757d',
                'accent_color': '#28a745',
                'font_family': 'default',
                'font_size': 12,
                'include_cover_page': True,
                'include_table_of_contents': True,
                'include_footer': True,
                'include_page_numbers': True,
            })
    
    def post(self, request):
        """Create or update user's PDF customization"""
        
        try:
            customization, created = PDFCustomization.objects.get_or_create(
                user=request.user,
                defaults={}
            )
            
            serializer = PDFCustomizationSerializer(
                customization, 
                data=request.data, 
                partial=True
            )
            
            if serializer.is_valid():
                serializer.save()
                return Response(
                    serializer.data,
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
                )
            else:
                return Response(
                    serializer.errors,
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        except Exception as e:
            logger.error(f"PDF customization update failed: {str(e)}")
            return Response(
                {'error': 'Customization update failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pdf_queue_status(request):
    """
    Get user's PDF generation queue status
    
    GET /api/pdf/queue/status/
    """
    
    try:
        # Get user's pending queue items
        queue_items = PDFGenerationQueue.objects.filter(
            user=request.user,
            status__in=['pending', 'processing']
        ).select_related('pdf_document').order_by('created_at')
        
        queue_data = []
        for item in queue_items:
            queue_data.append({
                'id': item.id,
                'pdf_document_id': item.pdf_document.id,
                'pdf_title': item.pdf_document.title,
                'status': item.status,
                'priority': item.priority,
                'queue_position': item.queue_position,
                'estimated_completion_time': item.estimated_completion_time,
                'created_at': item.created_at,
            })
        
        return Response({
            'queue_items': queue_data,
            'total_pending': len([item for item in queue_data if item['status'] == 'pending']),
            'total_processing': len([item for item in queue_data if item['status'] == 'processing']),
        })
        
    except Exception as e:
        logger.error(f"Queue status check failed: {str(e)}")
        return Response(
            {'error': 'Queue status check failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pdf_analytics(request):
    """
    Get PDF analytics for user
    
    GET /api/pdf/analytics/
    """
    
    try:
        analytics_service = PDFAnalyticsService()
        stats = analytics_service.get_user_stats(request.user)
        
        return Response(stats)
        
    except Exception as e:
        logger.error(f"PDF analytics failed: {str(e)}")
        return Response(
            {'error': 'Analytics retrieval failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pdf_usage_summary(request):
    """
    Get PDF usage summary for current month
    
    GET /api/pdf/usage/summary/
    """
    
    try:
        # Check if user is premium for detailed stats
        if not request.user.has_active_subscription():
            return Response(
                {'error': 'Premium subscription required for detailed usage statistics'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Get current month's data
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        user_docs = PDFDocument.objects.filter(
            user=request.user,
            created_at__gte=start_of_month
        )
        
        summary = {
            'current_month': {
                'total_generated': user_docs.count(),
                'completed': user_docs.filter(status='completed').count(),
                'pending': user_docs.filter(status='pending').count(),
                'failed': user_docs.filter(status='failed').count(),
                'total_downloads': sum(doc.download_count for doc in user_docs),
                'total_shares': sum(doc.share_count for doc in user_docs),
            },
            'limits': {
                'monthly_limit': request.user.get_monthly_pdf_limit(),
                'daily_limit': request.user.get_daily_pdf_limit(),
                'remaining_today': request.user.get_remaining_daily_pdfs(),
                'remaining_month': request.user.get_remaining_monthly_pdfs(),
            },
            'recent_activity': []
        }
        
        # Add recent activity
        recent_docs = user_docs.order_by('-created_at')[:10]
        for doc in recent_docs:
            summary['recent_activity'].append({
                'id': doc.id,
                'title': doc.title,
                'status': doc.status,
                'created_at': doc.created_at,
                'download_count': doc.download_count,
            })
        
        return Response(summary)
        
    except Exception as e:
        logger.error(f"PDF usage summary failed: {str(e)}")
        return Response(
            {'error': 'Usage summary retrieval failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def regenerate_pdf(request, pdf_id):
    """
    Regenerate existing PDF with new options
    
    POST /api/pdf/documents/{id}/regenerate/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user', 'idea', 'template'),
            id=pdf_id,
            user=request.user
        )
        
        # Check if PDF can be regenerated
        if pdf_doc.status == 'processing':
            return Response(
                {'error': 'PDF is currently being processed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check generation limits
        if not PDFDocument.objects.user_can_generate(request.user):
            return Response(
                {'error': 'Daily PDF generation limit exceeded'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Get new options from request
        new_options = request.data.get('custom_options', {})
        new_template_id = request.data.get('template_id')
        
        # Get new template if specified
        new_template = pdf_doc.template
        if new_template_id and new_template_id != pdf_doc.template.id:
            new_template = get_object_or_404(
                PDFTemplate.objects.active(),
                id=new_template_id
            )
            
            # Check premium access
            if new_template.is_premium and not request.user.has_active_subscription():
                raise PermissionDenied("Premium template requires active subscription")
        
        # Update PDF document
        pdf_doc.template = new_template
        pdf_doc.custom_options.update(new_options)
        pdf_doc.status = 'pending'
        pdf_doc.error_message = ''
        pdf_doc.save()
        
        # Regenerate PDF
        pdf_service = PDFGeneratorService()
        
        if new_options.get('async_generation', False):
            # Add to queue
            queue_service = PDFQueueService()
            priority = 'high' if request.user.has_active_subscription() else 'normal'
            queue_service.add_to_queue(pdf_doc, priority)
            
            return Response({
                'message': 'PDF regeneration queued',
                'pdf_document': PDFDocumentSerializer(pdf_doc).data
            }, status=status.HTTP_202_ACCEPTED)
        else:
            # Synchronous regeneration
            pdf_service._generate_pdf_file(pdf_doc)
            
            return Response({
                'message': 'PDF regenerated successfully',
                'pdf_document': PDFDocumentDetailSerializer(pdf_doc).data
            })
        
    except Exception as e:
        logger.error(f"PDF regeneration failed: {str(e)}")
        return Response(
            {'error': 'PDF regeneration failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def optimize_pdf_file(request, pdf_id):
    """
    Optimize existing PDF file for size
    
    POST /api/pdf/documents/{id}/optimize/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            id=pdf_id,
            user=request.user
        )
        
        # Check if PDF is completed
        if pdf_doc.status != 'completed':
            return Response(
                {'error': 'PDF must be completed before optimization'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if file exists
        if not pdf_doc.file_path or not os.path.exists(pdf_doc.file_path):
            return Response(
                {'error': 'PDF file not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get optimization level
        compression_level = request.data.get('compression_level', 'medium')
        if compression_level not in ['low', 'medium', 'high']:
            compression_level = 'medium'
        
        # Check if already optimized recently to prevent duplicate work
        if hasattr(pdf_doc, 'last_optimized') and pdf_doc.last_optimized:
            time_since_optimization = timezone.now() - pdf_doc.last_optimized
            if time_since_optimization.total_seconds() < 300:  # 5 minutes
                return Response(
                    {'error': 'PDF was recently optimized. Please wait before optimizing again.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )
        
        # Store original file size
        original_size = os.path.getsize(pdf_doc.file_path)
        
        # Optimize PDF
        optimization_service = PDFOptimizationService()
        
        try:
            optimized_path = optimization_service.optimize_pdf(pdf_doc.file_path, compression_level)
            
            # Verify optimization was successful
            if not os.path.exists(optimized_path):
                raise FileNotFoundError("Optimized file was not created")
            
            new_size = os.path.getsize(optimized_path)
            
            # Replace original file with optimized version
            if optimized_path != pdf_doc.file_path:
                # Backup original file (optional)
                backup_path = f"{pdf_doc.file_path}.backup"
                os.rename(pdf_doc.file_path, backup_path)
                
                # Move optimized file to original location
                os.rename(optimized_path, pdf_doc.file_path)
                
                # Remove backup after successful replacement
                os.remove(backup_path)
            
        except Exception as optimization_error:
            logger.error(f"PDF optimization process failed: {str(optimization_error)}")
            return Response(
                {'error': 'PDF optimization process failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Calculate size reduction
        size_reduction = ((original_size - new_size) / original_size) * 100 if original_size > 0 else 0
        
        # Update document record
        update_fields = ['file_size']
        pdf_doc.file_size = new_size
        
        # Add last_optimized timestamp if field exists
        if hasattr(pdf_doc, 'last_optimized'):
            pdf_doc.last_optimized = timezone.now()
            update_fields.append('last_optimized')
        
        pdf_doc.save(update_fields=update_fields)
        
        # Log successful optimization
        logger.info(f"PDF {pdf_id} optimized: {original_size} -> {new_size} bytes ({size_reduction:.2f}% reduction)")
        
        return Response({
            'message': 'PDF optimized successfully',
            'original_size': original_size,
            'optimized_size': new_size,
            'size_reduction_percent': round(size_reduction, 2),
            'compression_level': compression_level,
            'pdf_document': {
                'id': pdf_doc.id,
                'title': pdf_doc.title,
                'file_size': new_size,
                'status': pdf_doc.status
            }
        })
        
    except PDFDocument.DoesNotExist:
        return Response(
            {'error': 'PDF document not found'},
            status=status.HTTP_404_NOT_FOUND
        )
    except PermissionError:
        logger.error(f"Permission denied accessing PDF file: {pdf_id}")
        return Response(
            {'error': 'Permission denied accessing PDF file'},
            status=status.HTTP_403_FORBIDDEN
        )
    except OSError as os_error:
        logger.error(f"File system error during PDF optimization: {str(os_error)}")
        return Response(
            {'error': 'File system error during optimization'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        logger.error(f"PDF optimization failed for document {pdf_id}: {str(e)}")
        return Response(
            {'error': 'PDF optimization failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def batch_generate_pdfs(request):
    """
    Generate multiple PDFs in batch
    
    POST /api/pdf/batch-generate/
    Body: {
        "idea_ids": [1, 2, 3],
        "template_id": 1,
        "custom_options": {},
        "async_generation": true
    }
    """
    
    try:
        idea_ids = request.data.get('idea_ids', [])
        template_id = request.data.get('template_id')
        custom_options = request.data.get('custom_options', {})
        async_generation = request.data.get('async_generation', True)
        
        if not idea_ids or len(idea_ids) > 50:  # Limit batch size
            return Response(
                {'error': 'Please provide 1-50 idea IDs'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if user can generate this many PDFs
        user_limit = request.user.get_remaining_daily_pdfs()
        if len(idea_ids) > user_limit:
            return Response(
                {'error': f'Batch size exceeds daily limit. You can generate {user_limit} more PDFs today.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Get ideas that belong to the user
        ideas = GeneratedIdea.objects.filter(
            id__in=idea_ids,
            request__user=request.user
        ).select_related('request')
        
        if ideas.count() != len(idea_ids):
            return Response(
                {'error': 'Some ideas were not found or do not belong to you'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template if specified
        template = None
        if template_id:
            template = get_object_or_404(
                PDFTemplate.objects.active(),
                id=template_id
            )
            
            if template.is_premium and not request.user.has_active_subscription():
                raise PermissionDenied("Premium template requires active subscription")
        
        pdf_service = PDFGeneratorService()
        created_pdfs = []
        
        with transaction.atomic():
            for idea in ideas:
                # Create PDF document record
                pdf_doc = pdf_service._create_pdf_document(
                    request.user, idea, template, custom_options
                )
                created_pdfs.append(pdf_doc)
                
                if async_generation:
                    # Add to queue
                    queue_service = PDFQueueService()
                    priority = 'high' if request.user.has_active_subscription() else 'normal'
                    queue_service.add_to_queue(pdf_doc, priority)
        
        # Serialize created PDFs
        serializer = PDFDocumentSerializer(created_pdfs, many=True)
        
        return Response({
            'message': f'{len(created_pdfs)} PDFs {"queued for generation" if async_generation else "generated"}',
            'pdf_documents': serializer.data,
            'batch_id': str(uuid.uuid4()),  # For tracking the batch
        }, status=status.HTTP_202_ACCEPTED if async_generation else status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.error(f"Batch PDF generation failed: {str(e)}")
        return Response(
            {'error': 'Batch generation failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_pdf_generation(request, pdf_id):
    """
    Cancel PDF generation if it's still in queue
    
    POST /api/pdf/documents/{id}/cancel/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            id=pdf_id,
            user=request.user
        )
        
        # Check if PDF can be cancelled
        if pdf_doc.status not in ['pending', 'processing']:
            return Response(
                {'error': f'Cannot cancel PDF with status: {pdf_doc.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Cancel queue item if it exists
        try:
            queue_item = pdf_doc.queue_item
            if queue_item.status in ['pending', 'processing']:
                queue_item.status = 'cancelled'
                queue_item.save()
        except PDFGenerationQueue.DoesNotExist:
            pass
        
        # Update PDF document status
        pdf_doc.status = 'failed'
        pdf_doc.error_message = 'Cancelled by user'
        pdf_doc.save()
        
        return Response({
            'message': 'PDF generation cancelled successfully'
        })
        
    except Exception as e:
        logger.error(f"PDF cancellation failed: {str(e)}")
        return Response(
            {'error': 'Cancellation failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pdf_generation_limits(request):
    """
    Get user's PDF generation limits and usage
    
    GET /api/pdf/limits/
    """
    
    try:
        user = request.user
        
        limits_data = {
            'daily_limit': user.get_daily_pdf_limit(),
            'monthly_limit': user.get_monthly_pdf_limit(),
            'remaining_today': user.get_remaining_daily_pdfs(),
            'remaining_month': user.get_remaining_monthly_pdfs(),
            'used_today': user.get_daily_pdf_count(),
            'used_month': user.get_monthly_pdf_count(),
            'subscription_type': user.subscription_type if hasattr(user, 'subscription_type') else 'free',
            'has_active_subscription': user.has_active_subscription(),
            'reset_time': {
                'daily_reset': timezone.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=1),
                'monthly_reset': timezone.now().replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=32)  # Next month
            }
        }
        
        return Response(limits_data)
        
    except Exception as e:
        logger.error(f"PDF limits check failed: {str(e)}")
        return Response(
            {'error': 'Limits check failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pdf_health_check(request):
    """
    Health check for PDF generation system
    
    GET /api/pdf/health/
    """
    
    try:
        health_data = {
            'pdf_service_status': 'healthy',
            'queue_status': {
                'pending_jobs': PDFGenerationQueue.objects.filter(status='pending').count(),
                'processing_jobs': PDFGenerationQueue.objects.filter(status='processing').count(),
                'failed_jobs_last_hour': PDFGenerationQueue.objects.filter(
                    status='failed',
                    created_at__gte=timezone.now() - timedelta(hours=1)
                ).count(),
            },
            'templates_available': PDFTemplate.objects.active().count(),
            'premium_templates_count': PDFTemplate.objects.active().filter(is_premium=True).count(),
            'storage_status': 'healthy',  # You can add actual storage checks here
            'generation_stats_today': {
                'total_generated': PDFDocument.objects.filter(
                    created_at__date=timezone.now().date()
                ).count(),
                'successful': PDFDocument.objects.filter(
                    created_at__date=timezone.now().date(),
                    status='completed'
                ).count(),
                'failed': PDFDocument.objects.filter(
                    created_at__date=timezone.now().date(),
                    status='failed'
                ).count(),
            }
        }
        
        return Response(health_data)
        
    except Exception as e:
        logger.error(f"PDF health check failed: {str(e)}")
        return Response(
            {'error': 'Health check failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def duplicate_pdf(request, pdf_id):
    """
    Duplicate an existing PDF with optional modifications
    
    POST /api/pdf/documents/{id}/duplicate/
    Body: {
        "new_title": "Optional new title",
        "template_id": "Optional new template ID",
        "custom_options": {}
    }
    """
    
    try:
        original_pdf = get_object_or_404(
            PDFDocument.objects.select_related('user', 'idea', 'template'),
            id=pdf_id,
            user=request.user
        )
        
        # Check generation limits
        if not PDFDocument.objects.user_can_generate(request.user):
            return Response(
                {'error': 'Daily PDF generation limit exceeded'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Get new options
        new_title = request.data.get('new_title', f"Copy of {original_pdf.title}")
        new_template_id = request.data.get('template_id')
        new_custom_options = request.data.get('custom_options', original_pdf.custom_options)
        
        # Get new template if specified
        template = original_pdf.template
        if new_template_id and new_template_id != original_pdf.template.id:
            template = get_object_or_404(
                PDFTemplate.objects.active(),
                id=new_template_id
            )
            
            if template.is_premium and not request.user.has_active_subscription():
                raise PermissionDenied("Premium template requires active subscription")
        
        # Create duplicate PDF
        pdf_service = PDFGeneratorService()
        
        duplicate_pdf = pdf_service._create_pdf_document(
            request.user, 
            original_pdf.idea, 
            template, 
            new_custom_options,
            title_override=new_title
        )
        
        # Generate the duplicate
        if new_custom_options.get('async_generation', False):
            queue_service = PDFQueueService()
            priority = 'high' if request.user.has_active_subscription() else 'normal'
            queue_service.add_to_queue(duplicate_pdf, priority)
            
            return Response({
                'message': 'PDF duplication queued',
                'pdf_document': PDFDocumentSerializer(duplicate_pdf).data
            }, status=status.HTTP_202_ACCEPTED)
        else:
            pdf_service._generate_pdf_file(duplicate_pdf)
            
            return Response({
                'message': 'PDF duplicated successfully',
                'pdf_document': PDFDocumentDetailSerializer(duplicate_pdf).data
            })
        
    except Exception as e:
        logger.error(f"PDF duplication failed: {str(e)}")
        return Response(
            {'error': 'PDF duplication failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def merge_pdfs(request):
    """
    Merge multiple PDFs into one
    
    POST /api/pdf/merge/
    Body: {
        "pdf_ids": [1, 2, 3],
        "merged_title": "Merged PDF Title",
        "include_cover_page": true
    }
    """
    
    try:
        pdf_ids = request.data.get('pdf_ids', [])
        merged_title = request.data.get('merged_title', 'Merged PDF')
        include_cover_page = request.data.get('include_cover_page', True)
        
        if not pdf_ids or len(pdf_ids) < 2:
            return Response(
                {'error': 'At least 2 PDFs are required for merging'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if len(pdf_ids) > 10:  # Limit merge operations
            return Response(
                {'error': 'Cannot merge more than 10 PDFs at once'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check generation limits
        if not PDFDocument.objects.user_can_generate(request.user):
            return Response(
                {'error': 'Daily PDF generation limit exceeded'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        
        # Get PDFs that belong to the user and are completed
        pdfs = PDFDocument.objects.filter(
            id__in=pdf_ids,
            user=request.user,
            status='completed'
        ).order_by('created_at')
        
        if pdfs.count() != len(pdf_ids):
            return Response(
                {'error': 'Some PDFs were not found, do not belong to you, or are not completed'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if all files exist
        missing_files = []
        for pdf in pdfs:
            if not pdf.file_path or not os.path.exists(pdf.file_path):
                missing_files.append(pdf.title)
        
        if missing_files:
            return Response(
                {'error': f'PDF files not found: {", ".join(missing_files)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create merged PDF record
        from .services import PDFMergeService
        merge_service = PDFMergeService()
        
        merged_pdf = merge_service.merge_pdfs(
            user=request.user,
            pdfs_to_merge=list(pdfs),
            merged_title=merged_title,
            include_cover_page=include_cover_page
        )
        
        return Response({
            'message': 'PDFs merged successfully',
            'merged_pdf': PDFDocumentDetailSerializer(merged_pdf).data,
            'source_pdfs': [pdf.title for pdf in pdfs],
        })
        
    except Exception as e:
        logger.error(f"PDF merge failed: {str(e)}")
        return Response(
            {'error': 'PDF merge failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pdf_metadata(request, pdf_id):
    """
    Get detailed PDF metadata
    
    GET /api/pdf/documents/{id}/metadata/
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user', 'idea', 'template'),
            id=pdf_id,
            user=request.user
        )
        
        # Get file stats if file exists
        file_stats = {}
        if pdf_doc.file_path and os.path.exists(pdf_doc.file_path):
            stat = os.stat(pdf_doc.file_path)
            file_stats = {
                'file_size_bytes': stat.st_size,
                'file_size_human': f"{stat.st_size / (1024*1024):.2f} MB",
                'last_modified': datetime.fromtimestamp(stat.st_mtime),
                'creation_time': datetime.fromtimestamp(stat.st_ctime),
            }
        
        metadata = {
            'pdf_info': {
                'id': pdf_doc.id,
                'title': pdf_doc.title,
                'filename': pdf_doc.filename,
                'status': pdf_doc.status,
                'page_count': pdf_doc.page_count,
                'created_at': pdf_doc.created_at,
                'updated_at': pdf_doc.updated_at,
            },
            'generation_info': {
                'template_used': pdf_doc.template.name if pdf_doc.template else 'Default',
                'generation_time': pdf_doc.generation_time,
                'generation_started_at': pdf_doc.generation_started_at,
                'generation_completed_at': pdf_doc.generation_completed_at,
                'retry_count': pdf_doc.retry_count,
                'error_message': pdf_doc.error_message,
            },
            'customization': pdf_doc.custom_options,
            'access_info': {
                'download_count': pdf_doc.download_count,
                'last_downloaded_at': pdf_doc.last_downloaded_at,
                'share_count': pdf_doc.share_count,
                'is_public': pdf_doc.is_public,
                'public_access_token': pdf_doc.public_access_token if pdf_doc.is_public else None,
            },
            'source_idea': {
                'id': pdf_doc.idea.id,
                'title': pdf_doc.idea.title,
                'description': pdf_doc.idea.description,
                'created_at': pdf_doc.idea.created_at,
            },
            'file_stats': file_stats,
            'metadata': pdf_doc.metadata,
        }
        
        return Response(metadata)
        
    except Exception as e:
        logger.error(f"PDF metadata retrieval failed: {str(e)}")
        return Response(
            {'error': 'Metadata retrieval failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pdf_watermark(request, pdf_id):
    """
    Add watermark to existing PDF
    
    POST /api/pdf/documents/{id}/watermark/
    Body: {
        "watermark_text": "CONFIDENTIAL",
        "opacity": 0.3,
        "position": "center"
    }
    """
    
    try:
        pdf_doc = get_object_or_404(
            PDFDocument.objects.select_related('user'),
            id=pdf_id,
            user=request.user
        )
        
        # Check if PDF is completed
        if pdf_doc.status != 'completed':
            return Response(
                {'error': 'PDF must be completed before adding watermark'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if file exists
        if not pdf_doc.file_path or not os.path.exists(pdf_doc.file_path):
            return Response(
                {'error': 'PDF file not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get watermark options
        watermark_text = request.data.get('watermark_text', 'CONFIDENTIAL')
        opacity = request.data.get('opacity', 0.3)
        position = request.data.get('position', 'center')
        
        # Validate inputs
        if not 0.1 <= opacity <= 1.0:
            return Response(
                {'error': 'Opacity must be between 0.1 and 1.0'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if position not in ['center', 'top-left', 'top-right', 'bottom-left', 'bottom-right']:
            return Response(
                {'error': 'Invalid watermark position'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Apply watermark
        from .services import PDFWatermarkService
        watermark_service = PDFWatermarkService()
        
        watermarked_path = watermark_service.add_watermark(
            pdf_path=pdf_doc.file_path,
            watermark_text=watermark_text,
            opacity=opacity,
            position=position
        )
        
        # Update PDF document
        pdf_doc.file_path = watermarked_path
        pdf_doc.include_watermark = True
        pdf_doc.custom_options['watermark'] = {
            'text': watermark_text,
            'opacity': opacity,
            'position': position,
            'applied_at': timezone.now().isoformat()
        }
        pdf_doc.save()
        
        return Response({
            'message': 'Watermark added successfully',
            'watermark_info': {
                'text': watermark_text,
                'opacity': opacity,
                'position': position,
            }
        })
        
    except Exception as e:
        logger.error(f"PDF watermark failed: {str(e)}")
        return Response(
            {'error': 'Watermark application failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_pdf_stats(request):
    """
    Get comprehensive PDF statistics for the user
    
    GET /api/pdf/my-stats/
    """
    
    try:
        user = request.user
        
        # Get user's PDFs
        user_pdfs = PDFDocument.objects.filter(user=user)
        
        # Calculate various statistics
        stats = {
            'totals': {
                'total_pdfs': user_pdfs.count(),
                'completed_pdfs': user_pdfs.filter(status='completed').count(),
                'pending_pdfs': user_pdfs.filter(status='pending').count(),
                'failed_pdfs': user_pdfs.filter(status='failed').count(),
                'total_downloads': sum(pdf.download_count for pdf in user_pdfs),
                'total_shares': sum(pdf.share_count for pdf in user_pdfs),
                'public_pdfs': user_pdfs.filter(is_public=True).count(),
            },
            'this_month': {
                'pdfs_generated': user_pdfs.filter(
                    created_at__month=timezone.now().month,
                    created_at__year=timezone.now().year
                ).count(),
                'downloads': sum(
                    pdf.download_count for pdf in user_pdfs.filter(
                        created_at__month=timezone.now().month,
                        created_at__year=timezone.now().year
                    )
                ),
            },
            'templates_used': {},
            'average_generation_time': 0,
            'total_file_size': 0,
            'most_popular_pdf': None,
        }
        
        # Template usage statistics
        completed_pdfs = user_pdfs.filter(status='completed')
        for pdf in completed_pdfs:
            if pdf.template:
                template_name = pdf.template.name
                stats['templates_used'][template_name] = stats['templates_used'].get(template_name, 0) + 1
        
        # Average generation time
        generation_times = [pdf.generation_time for pdf in completed_pdfs if pdf.generation_time]
        if generation_times:
            stats['average_generation_time'] = sum(generation_times) / len(generation_times)
        
        # Total file size
        stats['total_file_size'] = sum(pdf.file_size for pdf in completed_pdfs if pdf.file_size)
        
        # Most popular PDF (by downloads)
        most_popular = completed_pdfs.order_by('-download_count').first()
        if most_popular:
            stats['most_popular_pdf'] = {
                'id': most_popular.id,
                'title': most_popular.title,
                'downloads': most_popular.download_count,
            }
        
        # Recent activity (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_activity = []
        recent_pdfs = user_pdfs.filter(created_at__gte=thirty_days_ago).order_by('-created_at')[:10]
        
        for pdf in recent_pdfs:
            recent_activity.append({
                'id': pdf.id,
                'title': pdf.title,
                'status': pdf.status,
                'created_at': pdf.created_at,
                'downloads': pdf.download_count,
            })
        
        stats['recent_activity'] = recent_activity
        
        return Response(stats)
        
    except Exception as e:
        logger.error(f"User PDF stats failed: {str(e)}")
        return Response(
            {'error': 'Statistics retrieval failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# Admin and management endpoints
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def cleanup_failed_pdfs(request):
    """
    Cleanup failed PDF files and records (admin only)
    
    GET /api/pdf/admin/cleanup/
    """
    
    if not request.user.is_staff:
        raise PermissionDenied("Admin access required")
    
    try:
        # Find PDFs that failed more than 24 hours ago
        cutoff_time = timezone.now() - timedelta(hours=24)
        
        failed_pdfs = PDFDocument.objects.filter(
            status='failed',
            updated_at__lt=cutoff_time
        )
        
        cleanup_stats = {
            'cleaned_records': 0,
            'cleaned_files': 0,
            'errors': []
        }
        
        for pdf in failed_pdfs:
            try:
                # Remove file if it exists
                if pdf.file_path and os.path.exists(pdf.file_path):
                    os.remove(pdf.file_path)
                    cleanup_stats['cleaned_files'] += 1
                
                # Soft delete the record
                pdf.delete()
                cleanup_stats['cleaned_records'] += 1
                
            except Exception as e:
                cleanup_stats['errors'].append(f"Failed to cleanup PDF {pdf.id}: {str(e)}")
        
        return Response({
            'message': 'Cleanup completed',
            'stats': cleanup_stats
        })
        
    except Exception as e:
        logger.error(f"PDF cleanup failed: {str(e)}")
        return Response(
            {'error': 'Cleanup failed'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )