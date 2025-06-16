# apps/ideas/views.py
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle
from django.db.models import Q, Count, Avg, F
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters
import logging

from core.permissions import IsOwnerOrReadOnly, IsPremiumUser
from core.pagination import StandardResultsSetPagination
from core.exceptions import ServiceUnavailableError, ValidationError
from .models import (
    IdeaCategory, IdeaTemplate, IdeaRequest, GeneratedIdea, 
    IdeaFeedback, IdeaBookmark, IdeaUsageStats
)
from .serializers import (
    IdeaCategorySerializer, IdeaTemplateListSerializer, IdeaTemplateDetailSerializer,
    IdeaRequestCreateSerializer, IdeaRequestSerializer, GeneratedIdeaSerializer,
    IdeaFeedbackCreateSerializer, IdeaFeedbackSerializer, IdeaBookmarkCreateSerializer,
    IdeaBookmarkSerializer, QuickIdeaRequestSerializer, IdeaSearchSerializer,
    UserIdeaStatsSerializer
)
from .services import IdeaGenerationService, IdeaAnalyticsService
from .tasks import generate_ideas_async, update_usage_stats

logger = logging.getLogger(__name__)


class IdeaGenerationThrottle(UserRateThrottle):
    """Custom throttle for idea generation"""
    scope = 'idea_generation'


class IdeaCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for idea categories
    """
    queryset = IdeaCategory.objects.filter(is_active=True)
    serializer_class = IdeaCategorySerializer
    pagination_class = None
    throttle_classes = [AnonRateThrottle, UserRateThrottle]
    
    def get_queryset(self):
        """Cache categories for better performance"""
        cache_key = 'idea_categories_active'
        categories = cache.get(cache_key)
        
        if categories is None:
            categories = list(self.queryset.all())
            cache.set(cache_key, categories, 3600)  # Cache for 1 hour
        
        return categories


class IdeaTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for idea templates
    """
    queryset = IdeaTemplate.objects.active()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['template_type', 'category', 'is_premium']
    search_fields = ['name', 'description']
    ordering_fields = ['usage_count', 'average_rating', 'created_at']
    ordering = ['-usage_count']
    throttle_classes = [AnonRateThrottle, UserRateThrottle]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return IdeaTemplateListSerializer
        return IdeaTemplateDetailSerializer
    
    def get_queryset(self):
        queryset = super().get_queryset()
        
        # Filter by user's subscription tier
        if self.request.user.is_authenticated:
            return IdeaTemplate.objects.for_user_tier(self.request.user)
        else:
            return queryset.filter(is_premium=False)
    
    @action(detail=False, methods=['get'])
    def popular(self, request):
        """Get most popular templates"""
        templates = self.get_queryset().most_used(limit=10)
        serializer = self.get_serializer(templates, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def top_rated(self, request):
        """Get top rated templates"""
        templates = self.get_queryset().top_rated(limit=10)
        serializer = self.get_serializer(templates, many=True)
        return Response(serializer.data)


class IdeaRequestViewSet(viewsets.ModelViewSet):
    """
    ViewSet for idea requests
    """
    serializer_class = IdeaRequestSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'budget', 'location_type']
    ordering_fields = ['created_at', 'processing_completed_at']
    ordering = ['-created_at']
    throttle_classes = [IdeaGenerationThrottle]
    
    def get_queryset(self):
        """Get requests for current user only"""
        return IdeaRequest.objects.for_user(self.request.user)
    
    def get_serializer_class(self):
        if self.action == 'create':
            return IdeaRequestCreateSerializer
        return IdeaRequestSerializer
    
    def perform_create(self, serializer):
        """Create request and trigger idea generation"""
        request = serializer.save()
        
        # Check user's daily limit
        daily_count = IdeaRequest.objects.user_daily_count(
            self.request.user, 
            timezone.now().date()
        )
        
        # Apply rate limiting based on subscription
        max_daily_requests = self.get_max_daily_requests()
        if daily_count > max_daily_requests:
            raise ValidationError(
                f"Daily limit of {max_daily_requests} requests exceeded. "
                "Please upgrade your subscription for more requests."
            )
        
        # Queue idea generation
        try:
            generate_ideas_async.delay(request.id)
            logger.info(f"Queued idea generation for request {request.id}")
        except Exception as e:
            logger.error(f"Failed to queue idea generation: {str(e)}")
            request.mark_as_failed("Failed to queue for processing")
            raise ServiceUnavailableError("Service temporarily unavailable")
    
    def get_max_daily_requests(self):
        """Get max daily requests based on user's subscription"""
        user = self.request.user
        if hasattr(user, 'subscription') and user.subscription.is_active:
            return user.subscription.plan.max_daily_requests
        return getattr(settings, 'FREE_TIER_DAILY_REQUESTS', 5)
    
    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        """Retry failed request"""
        idea_request = self.get_object()
        
        if not idea_request.can_retry():
            return Response(
                {'error': 'Request cannot be retried'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Reset status and queue for processing
        idea_request.status = 'pending'
        idea_request.error_message = ''
        idea_request.save(update_fields=['status', 'error_message'])
        
        generate_ideas_async.delay(idea_request.id)
        
        return Response({'message': 'Request queued for retry'})
    
    @action(detail=False, methods=['post'])
    def quick_generate(self, request):
        """Quick idea generation with minimal input"""
        serializer = QuickIdeaRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Create request from quick input
        request_data = {
            'partner_interests': serializer.validated_data['interests'],
            'budget': serializer.validated_data['budget'],
            'location_type': serializer.validated_data['location_type'],
            'location_city': serializer.validated_data.get('location_city', ''),
            'title': f"Quick Ideas - {serializer.validated_data['interests'][:50]}..."
        }
        
        request_serializer = IdeaRequestCreateSerializer(
            data=request_data,
            context={'request': request}
        )
        request_serializer.is_valid(raise_exception=True)
        
        # Use the standard creation logic
        self.perform_create(request_serializer)
        
        return Response(
            request_serializer.data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get user's request statistics"""
        stats = IdeaAnalyticsService.get_user_stats(request.user)
        serializer = UserIdeaStatsSerializer(stats)
        return Response(serializer.data)


class GeneratedIdeaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for generated ideas
    """
    serializer_class = GeneratedIdeaSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['request__budget', 'request__location_type']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'view_count', 'like_count', 'user_rating']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Get ideas for current user only"""
        return GeneratedIdea.objects.for_user(self.request.user)
    
    def retrieve(self, request, *args, **kwargs):
        """Increment view count when retrieving idea"""
        instance = self.get_object()
        instance.increment_view_count()
        
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        """Like an idea"""
        idea = self.get_object()
        
        # Check if user already liked this idea
        existing_like = IdeaFeedback.objects.filter(
            user=request.user,
            idea=idea,
            feedback_type='like'
        ).first()
        
        if existing_like:
            # Unlike
            existing_like.delete()
            idea.like_count = max(0, idea.like_count - 1)
            idea.save(update_fields=['like_count'])
            return Response({'liked': False, 'like_count': idea.like_count})
        else:
            # Like
            IdeaFeedback.objects.create(
                user=request.user,
                idea=idea,
                feedback_type='like'
            )
            idea.increment_like_count()
            return Response({'liked': True, 'like_count': idea.like_count})
    
    @action(detail=True, methods=['post'])
    def share(self, request, pk=None):
        """Track idea sharing"""
        idea = self.get_object()
        idea.increment_share_count()
        return Response({'message': 'Share tracked', 'share_count': idea.share_count})
    
    @action(detail=False, methods=['get'])
    def popular(self, request):
        """Get popular ideas"""
        ideas = self.get_queryset().most_liked(limit=20)
        page = self.paginate_queryset(ideas)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(ideas, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def top_rated(self, request):
        """Get top rated ideas"""
        ideas = self.get_queryset().top_rated(limit=20)
        page = self.paginate_queryset(ideas)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(ideas, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def search(self, request):
        """Advanced idea search"""
        serializer = IdeaSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        queryset = self.get_queryset()
        
        # Apply search filters
        query = serializer.validated_data['query']
        queryset = queryset.search(query)
        
        if 'budget' in serializer.validated_data:
            queryset = queryset.filter(request__budget=serializer.validated_data['budget'])
        
        if 'location_type' in serializer.validated_data:
            queryset = queryset.filter(request__location_type=serializer.validated_data['location_type'])
        
        if 'min_rating' in serializer.validated_data:
            queryset = queryset.filter(user_rating__gte=serializer.validated_data['min_rating'])
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class IdeaFeedbackViewSet(viewsets.ModelViewSet):
    """
    ViewSet for idea feedback
    """
    serializer_class = IdeaFeedbackSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Get feedback for current user only"""
        return IdeaFeedback.objects.for_user(self.request.user)
    
    def get_serializer_class(self):
        if self.action == 'create':
            return IdeaFeedbackCreateSerializer
        return IdeaFeedbackSerializer
    
    def create(self, request, *args, **kwargs):
        """Create feedback for a specific idea"""
        idea_id = request.data.get('idea_id')
        if not idea_id:
            return Response(
                {'error': 'idea_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        idea = get_object_or_404(GeneratedIdea, id=idea_id)
        
        # Check if user owns this idea
        if idea.request.user != request.user:
            return Response(
                {'error': 'You can only provide feedback for your own ideas'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'idea': idea}
        )
        serializer.is_valid(raise_exception=True)
        
        # Check for existing feedback of same type
        existing_feedback = IdeaFeedback.objects.filter(
            user=request.user,
            idea=idea,
            feedback_type=serializer.validated_data['feedback_type']
        ).first()
        
        if existing_feedback:
            # Update existing feedback
            for attr, value in serializer.validated_data.items():
                setattr(existing_feedback, attr, value)
            existing_feedback.save()
            
            response_serializer = IdeaFeedbackSerializer(existing_feedback)
            return Response(response_serializer.data)
        else:
            # Create new feedback
            feedback = serializer.save()
            response_serializer = IdeaFeedbackSerializer(feedback)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class IdeaBookmarkViewSet(viewsets.ModelViewSet):
    """
    ViewSet for idea bookmarks
    """
    serializer_class = IdeaBookmarkSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    
    def get_queryset(self):
        """Get bookmarks for current user only"""
        return IdeaBookmark.objects.filter(user=self.request.user)
    
    def get_serializer_class(self):
        if self.action == 'create':
            return IdeaBookmarkCreateSerializer
        return IdeaBookmarkSerializer
    
    def create(self, request, *args, **kwargs):
        """Create bookmark for a specific idea"""
        idea_id = request.data.get('idea_id')
        if not idea_id:
            return Response(
                {'error': 'idea_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        idea = get_object_or_404(GeneratedIdea, id=idea_id)
        
        # Check if user owns this idea
        if idea.request.user != request.user:
            return Response(
                {'error': 'You can only bookmark your own ideas'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Check if already bookmarked
        existing_bookmark = IdeaBookmark.objects.filter(
            user=request.user,
            idea=idea
        ).first()
        
        if existing_bookmark:
            return Response(
                {'error': 'Idea already bookmarked'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = self.get_serializer(
            data=request.data,
            context={'request': request, 'idea': idea}
        )
        serializer.is_valid(raise_exception=True)
        
        bookmark = serializer.save()
        response_serializer = IdeaBookmarkSerializer(bookmark)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['post'])
    def toggle(self, request):
        """Toggle bookmark status for an idea"""
        idea_id = request.data.get('idea_id')
        if not idea_id:
            return Response(
                {'error': 'idea_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        idea = get_object_or_404(GeneratedIdea, id=idea_id)
        
        # Check if user owns this idea
        if idea.request.user != request.user:
            return Response(
                {'error': 'You can only bookmark your own ideas'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        bookmark = IdeaBookmark.objects.filter(
            user=request.user,
            idea=idea
        ).first()
        
        if bookmark:
            # Remove bookmark
            bookmark.delete()
            return Response({'bookmarked': False})
        else:
            # Create bookmark
            bookmark = IdeaBookmark.objects.create(
                user=request.user,
                idea=idea,
                notes=request.data.get('notes', '')
            )
            return Response({'bookmarked': True, 'id': bookmark.id})


class IdeaAnalyticsViewSet(viewsets.GenericViewSet):
    """
    ViewSet for idea analytics
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def overview(self, request):
        """Get user's ideas overview"""
        user_stats = IdeaAnalyticsService.get_user_overview(request.user)
        return Response(user_stats)
    
    @action(detail=False, methods=['get'])
    def trends(self, request):
        """Get user's usage trends"""
        days = int(request.query_params.get('days', 30))
        trends = IdeaAnalyticsService.get_user_trends(request.user, days)
        return Response(trends)
    
    @action(detail=False, methods=['get'])
    def popular_templates(self, request):
        """Get user's most used templates"""
        templates = IdeaAnalyticsService.get_user_popular_templates(request.user)
        return Response(templates)