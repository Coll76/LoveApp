# apps/ideas/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested.routers import NestedDefaultRouter
from .views import (
    IdeaCategoryViewSet,
    IdeaTemplateViewSet,
    IdeaRequestViewSet,
    GeneratedIdeaViewSet,
    IdeaFeedbackViewSet,
    IdeaBookmarkViewSet,
    IdeaAnalyticsViewSet,
    debug_celery,
    check_task_status,
)

app_name = 'ideas'

# Main router
router = DefaultRouter()
router.register(r'categories', IdeaCategoryViewSet, basename='category')
router.register(r'templates', IdeaTemplateViewSet, basename='template')
router.register(r'requests', IdeaRequestViewSet, basename='request')
router.register(r'ideas', GeneratedIdeaViewSet, basename='idea')
router.register(r'feedback', IdeaFeedbackViewSet, basename='feedback')
router.register(r'bookmarks', IdeaBookmarkViewSet, basename='bookmark')
router.register(r'analytics', IdeaAnalyticsViewSet, basename='analytics')

# Nested routers with unique basenames to avoid conflicts
# /requests/{request_pk}/ideas/
requests_router = NestedDefaultRouter(router, r'requests', lookup='request')
requests_router.register(r'ideas', GeneratedIdeaViewSet, basename='request-idea')

# /ideas/{idea_pk}/feedback/
ideas_router = NestedDefaultRouter(router, r'ideas', lookup='idea')
ideas_router.register(r'feedback', IdeaFeedbackViewSet, basename='idea-feedback')

# URL patterns with proper namespacing
urlpatterns = [
    path('api/v1/', include(router.urls)),
    path('api/v1/', include(requests_router.urls)),
    path('api/v1/', include(ideas_router.urls)),
    path('debug/celery/', debug_celery, name='debug-celery'),
    path('debug/task/<str:task_id>/', check_task_status, name='check-task-status'),
]