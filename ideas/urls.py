# apps/ideas/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers

from .views import (
    IdeaCategoryViewSet,
    IdeaTemplateViewSet,
    IdeaRequestViewSet,
    GeneratedIdeaViewSet,
    IdeaFeedbackViewSet,
    IdeaBookmarkViewSet,
    IdeaAnalyticsViewSet,
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

# Nested routers for related resources
requests_router = routers.NestedDefaultRouter(router, r'requests', lookup='request')
requests_router.register(r'ideas', GeneratedIdeaViewSet, basename='request-ideas')

ideas_router = routers.NestedDefaultRouter(router, r'ideas', lookup='idea')
ideas_router.register(r'feedback', IdeaFeedbackViewSet, basename='idea-feedback')

urlpatterns = [
    path('', include(router.urls)),
    path('', include(requests_router.urls)),
    path('', include(ideas_router.urls)),
]