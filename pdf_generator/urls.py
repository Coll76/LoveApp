# apps/pdf_generator/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
# Remove this import - we don't need it
# from rest_framework.urlpatterns import format_suffix_patterns

from . import views

# Create a router for ViewSets
router = DefaultRouter()
router.register(r'documents', views.PDFDocumentViewSet, basename='pdf-documents')
router.register(r'templates', views.PDFTemplateViewSet, basename='pdf-templates')

app_name = 'pdf_generator'

# Define URL patterns
urlpatterns = [
    # Include router URLs for ViewSets
    path('api/pdf/', include(router.urls)),
    
    # Document management endpoints
    path('api/pdf/documents/<int:pdf_id>/download/', 
         views.download_pdf, 
         name='download-pdf'),
    
    path('api/pdf/documents/<int:pdf_id>/share/', 
         views.share_pdf, 
         name='share-pdf'),
    
    path('api/pdf/documents/<int:pdf_id>/unshare/', 
         views.unshare_pdf, 
         name='unshare-pdf'),
    
    path('api/pdf/documents/<int:pdf_id>/regenerate/', 
         views.regenerate_pdf, 
         name='regenerate-pdf'),
    
    path('api/pdf/documents/<int:pdf_id>/optimize/', 
         views.optimize_pdf_file, 
         name='optimize-pdf'),
    
    path('api/pdf/documents/<int:pdf_id>/cancel/', 
         views.cancel_pdf_generation, 
         name='cancel-pdf-generation'),
    
    path('api/pdf/documents/<int:pdf_id>/duplicate/', 
         views.duplicate_pdf, 
         name='duplicate-pdf'),
    
    path('api/pdf/documents/<int:pdf_id>/watermark/', 
         views.pdf_watermark, 
         name='pdf-watermark'),
    
    path('api/pdf/documents/<int:pdf_id>/metadata/', 
         views.pdf_metadata, 
         name='pdf-metadata'),
    
    # Public access endpoints
    path('api/pdf/public/<str:access_token>/download/', 
         views.download_public_pdf, 
         name='download-public-pdf'),
    
    # Template management endpoints
    path('api/pdf/templates/<int:template_id>/preview/', 
         views.template_preview, 
         name='template-preview'),
    
    # Batch operations
    path('api/pdf/batch-generate/', 
         views.batch_generate_pdfs, 
         name='batch-generate-pdfs'),
    
    path('api/pdf/merge/', 
         views.merge_pdfs, 
         name='merge-pdfs'),
    
    # User customization and settings
    path('api/pdf/customization/', 
         views.PDFCustomizationAPIView.as_view(), 
         name='pdf-customization'),
    
    # Queue and status endpoints
    path('api/pdf/queue/status/', 
         views.pdf_queue_status, 
         name='pdf-queue-status'),
    
    # Analytics and statistics endpoints
    path('api/pdf/analytics/', 
         views.pdf_analytics, 
         name='pdf-analytics'),
    
    path('api/pdf/usage/summary/', 
         views.pdf_usage_summary, 
         name='pdf-usage-summary'),
    
    path('api/pdf/my-stats/', 
         views.user_pdf_stats, 
         name='user-pdf-stats'),
    
    path('api/pdf/limits/', 
         views.pdf_generation_limits, 
         name='pdf-generation-limits'),
    
    # System health and maintenance
    path('api/pdf/health/', 
         views.pdf_health_check, 
         name='pdf-health-check'),
    
    # Admin endpoints
    path('api/pdf/admin/cleanup/', 
         views.cleanup_failed_pdfs, 
         name='cleanup-failed-pdfs'),
]

# REMOVE THIS LINE - it's causing the conflict!
# urlpatterns = format_suffix_patterns(urlpatterns)

# Alternative URL patterns for backwards compatibility or different naming conventions
# You can uncomment these if you need alternative URL structures

# urlpatterns += [
#     # Alternative naming patterns
#     path('api/pdfs/', include(router.urls)),  # Shorter alternative
#     path('api/pdf-generator/', include(router.urls)),  # More descriptive alternative
#     
#     # Version-specific URLs (for future API versioning)
#     path('api/v1/pdf/', include(router.urls)),
#     
#     # Mobile-specific endpoints (if needed)
#     path('api/mobile/pdf/', include(router.urls)),
# ]

# WebSocket URLs for real-time updates (if using channels)
# websocket_urlpatterns = [
#     path('ws/pdf/status/<int:user_id>/', consumers.PDFStatusConsumer.as_asgi()),
#     path('ws/pdf/queue/', consumers.PDFQueueConsumer.as_asgi()),
# ]