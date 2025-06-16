# apps/ideas/managers.py
from django.db import models
from django.utils import timezone
from django.db.models import Avg, Count, Q, F
from datetime import timedelta

class IdeaRequestManager(models.Manager):
    """
    Custom manager for IdeaRequest model
    """
    
    def for_user(self, user):
        """Get requests for a specific user"""
        return self.filter(user=user, is_deleted=False)
    
    def pending(self):
        """Get pending requests"""
        return self.filter(status='pending', is_deleted=False)
    
    def processing(self):
        """Get currently processing requests"""
        return self.filter(status='processing', is_deleted=False)
    
    def completed(self):
        """Get completed requests"""
        return self.filter(status='completed', is_deleted=False)
    
    def failed(self):
        """Get failed requests"""
        return self.filter(status='failed', is_deleted=False)
    
    def can_retry(self, max_retries=3):
        """Get failed requests that can be retried"""
        return self.filter(
            status='failed',
            retry_count__lt=max_retries,
            is_deleted=False
        )
    
    def recent(self, days=7):
        """Get recent requests within specified days"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since, is_deleted=False)
    
    def by_budget(self, budget):
        """Get requests by budget range"""
        return self.filter(budget=budget, is_deleted=False)
    
    def by_location_type(self, location_type):
        """Get requests by location type"""
        return self.filter(location_type=location_type, is_deleted=False)
    
    def with_generated_ideas(self):
        """Get requests that have generated ideas"""
        return self.filter(
            generated_ideas__isnull=False,
            is_deleted=False
        ).distinct()
    
    def user_daily_count(self, user, date=None):
        """Get user's request count for a specific date"""
        if date is None:
            date = timezone.now().date()
        
        return self.filter(
            user=user,
            created_at__date=date,
            is_deleted=False
        ).count()
    
    def processing_time_stats(self):
        """Get processing time statistics"""
        return self.filter(
            status='completed',
            processing_started_at__isnull=False,
            processing_completed_at__isnull=False,
            is_deleted=False
        ).aggregate(
            avg_processing_time=Avg(
                F('processing_completed_at') - F('processing_started_at')
            ),
            total_completed=Count('id')
        )

class GeneratedIdeaManager(models.Manager):
    """
    Custom manager for GeneratedIdea model
    """
    
    def for_user(self, user):
        """Get ideas for a specific user"""
        return self.filter(request__user=user, request__is_deleted=False)
    
    def top_rated(self, limit=10):
        """Get top rated ideas"""
        return self.filter(
            user_rating__isnull=False,
            request__is_deleted=False
        ).order_by('-user_rating')[:limit]
    
    def most_viewed(self, limit=10):
        """Get most viewed ideas"""
        return self.filter(
            request__is_deleted=False
        ).order_by('-view_count')[:limit]
    
    def most_liked(self, limit=10):
        """Get most liked ideas"""
        return self.filter(
            request__is_deleted=False
        ).order_by('-like_count')[:limit]
    
    def recent(self, days=7):
        """Get recent ideas within specified days"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(
            created_at__gte=since,
            request__is_deleted=False
        )
    
    def by_template(self, template):
        """Get ideas generated using a specific template"""
        return self.filter(
            template_used=template,
            request__is_deleted=False
        )
    
    def high_quality(self, min_rating=4.0):
        """Get high quality ideas based on rating"""
        return self.filter(
            user_rating__gte=min_rating,
            request__is_deleted=False
        )
    
    def with_feedback(self):
        """Get ideas that have user feedback"""
        return self.filter(
            feedback__isnull=False,
            request__is_deleted=False
        ).distinct()
    
    def bookmarked_by_user(self, user):
        """Get ideas bookmarked by a specific user"""
        return self.filter(
            bookmarks__user=user,
            request__is_deleted=False
        )
    
    def search(self, query):
        """Search ideas by title and description"""
        return self.filter(
            Q(title__icontains=query) | Q(description__icontains=query),
            request__is_deleted=False
        )
    
    def rating_stats(self):
        """Get rating statistics"""
        return self.filter(
            user_rating__isnull=False,
            request__is_deleted=False
        ).aggregate(
            avg_rating=Avg('user_rating'),
            total_rated=Count('user_rating'),
            min_rating=models.Min('user_rating'),
            max_rating=models.Max('user_rating')
        )

class IdeaTemplateManager(models.Manager):
    """
    Custom manager for IdeaTemplate model
    """
    
    def active(self):
        """Get active templates"""
        return self.filter(is_active=True)
    
    def free_templates(self):
        """Get free templates"""
        return self.filter(is_premium=False, is_active=True)
    
    def premium_templates(self):
        """Get premium templates"""
        return self.filter(is_premium=True, is_active=True)
    
    def by_type(self, template_type):
        """Get templates by type"""
        return self.filter(template_type=template_type, is_active=True)
    
    def by_category(self, category):
        """Get templates by category"""
        return self.filter(category=category, is_active=True)
    
    def most_used(self, limit=10):
        """Get most used templates"""
        return self.filter(is_active=True).order_by('-usage_count')[:limit]
    
    def top_rated(self, limit=10):
        """Get top rated templates"""
        return self.filter(
            is_active=True,
            average_rating__gt=0
        ).order_by('-average_rating')[:limit]
    
    def for_user_tier(self, user):
        """Get templates available for user's subscription tier"""
        if user.has_active_subscription():
            return self.filter(is_active=True)
        else:
            return self.filter(is_premium=False, is_active=True)
    
    def update_rating(self, template_id, new_rating):
        """Update template's average rating"""
        try:
            template = self.get(id=template_id)
            # This would typically be calculated from actual ratings
            # For now, we'll use a simple approach
            template.average_rating = new_rating
            template.save(update_fields=['average_rating'])
            return template
        except self.model.DoesNotExist:
            return None

class IdeaFeedbackManager(models.Manager):
    """
    Custom manager for IdeaFeedback model
    """
    
    def for_user(self, user):
        """Get feedback by a specific user"""
        return self.filter(user=user)
    
    def for_idea(self, idea):
        """Get feedback for a specific idea"""
        return self.filter(idea=idea)
    
    def ratings_only(self):
        """Get only rating feedback"""
        return self.filter(feedback_type='rating', rating__isnull=False)
    
    def comments_only(self):
        """Get only comment feedback"""
        return self.filter(feedback_type='comment', comment__isnull=False)
    
    def reports_only(self):
        """Get only report feedback"""
        return self.filter(feedback_type='report')
    
    def likes(self):
        """Get like feedback"""
        return self.filter(feedback_type='like')
    
    def dislikes(self):
        """Get dislike feedback"""
        return self.filter(feedback_type='dislike')
    
    def recent(self, days=7):
        """Get recent feedback within specified days"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since)
    
    def average_rating_for_idea(self, idea):
        """Get average rating for a specific idea"""
        return self.filter(
            idea=idea,
            feedback_type='rating',
            rating__isnull=False
        ).aggregate(avg_rating=Avg('rating'))['avg_rating']
    
    def user_rating_for_idea(self, user, idea):
        """Get user's rating for a specific idea"""
        try:
            feedback = self.get(
                user=user,
                idea=idea,
                feedback_type='rating'
            )
            return feedback.rating
        except self.model.DoesNotExist:
            return None
    
    def has_user_liked_idea(self, user, idea):
        """Check if user has liked an idea"""
        return self.filter(
            user=user,
            idea=idea,
            feedback_type='like'
        ).exists()
    
    def idea_feedback_summary(self, idea):
        """Get comprehensive feedback summary for an idea"""
        feedback_data = self.filter(idea=idea).aggregate(
            total_ratings=Count('rating', filter=Q(feedback_type='rating')),
            average_rating=Avg('rating', filter=Q(feedback_type='rating')),
            total_likes=Count('id', filter=Q(feedback_type='like')),
            total_dislikes=Count('id', filter=Q(feedback_type='dislike')),
            total_comments=Count('id', filter=Q(feedback_type='comment')),
            total_reports=Count('id', filter=Q(feedback_type='report'))
        )
        
        return feedback_data