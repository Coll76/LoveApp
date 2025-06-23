# apps/ideas/tasks.py
import logging
import json
import traceback
from typing import Dict, List, Optional, Any
from decimal import Decimal
from datetime import datetime, timedelta

from celery import shared_task, chain, group, chord
from celery.exceptions import Retry, MaxRetriesExceededError
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.models import F, Q, Avg, Count, Sum
from django.utils import timezone
from django.core.cache import cache
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model

from core.exceptions import ServiceUnavailableError, ValidationError
from .models import (
    IdeaRequest, GeneratedIdea, IdeaTemplate, IdeaFeedback,
    IdeaUsageStats, AIModelConfiguration, IdeaCategory
)
from .services import IdeaGenerationService, IdeaAnalyticsService
from .ai_client import AIClient
from .prompt_templates import PromptTemplateEngine

User = get_user_model()
logger = logging.getLogger(__name__)


# ==============================================================================
# MAIN IDEA GENERATION TASKS
# ==============================================================================

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 60},
    retry_backoff=True,
    retry_jitter=False
)
def generate_ideas_async(self, request_id: int) -> Dict[str, Any]:
    """
    Main task for generating AI-powered date ideas.
    This is the primary entry point for idea generation.
    """
    try:
        # Get the request object
        try:
            idea_request = IdeaRequest.objects.select_related('user').get(id=request_id)
        except IdeaRequest.DoesNotExist:
            logger.error(f"IdeaRequest {request_id} does not exist")
            return {'success': False, 'error': 'Request not found'}

        # Mark as processing
        idea_request.mark_as_processing()
        
        logger.info(f"Starting idea generation for request {request_id}")

        # Initialize services
        generation_service = IdeaGenerationService()
        ai_client = AIClient()
        
        # Check if AI service is available
        if not ai_client.is_service_available():
            raise ServiceUnavailableError("AI service is currently unavailable")

        # Get AI model configuration
        model_config = _get_ai_model_config(idea_request.ai_model)
        if not model_config:
            raise ValidationError(f"AI model {idea_request.ai_model} not configured")

        # Prepare generation request
        generation_request = _prepare_generation_request(idea_request)
        
        # Generate ideas
        with transaction.atomic():
            # Use chord pattern for parallel generation if multiple ideas needed
            ideas_count = _get_ideas_count_for_user(idea_request.user)
            
            if ideas_count > 1:
                # Generate multiple ideas in parallel
                result = _generate_multiple_ideas_parallel(
                    generation_request, 
                    ideas_count,
                    model_config
                )
            else:
                # Generate single idea
                result = _generate_single_idea(
                    generation_request,
                    model_config
                )

            # Mark request as completed
            idea_request.mark_as_completed()
            
            # Schedule post-generation tasks
            _schedule_post_generation_tasks.delay(
                request_id=request_id,
                ideas_generated=len(result.get('ideas', [])),
                user_id=idea_request.user.id
            )

        logger.info(f"Successfully generated {len(result.get('ideas', []))} ideas for request {request_id}")
        
        return {
            'success': True,
            'request_id': request_id,
            'ideas_generated': len(result.get('ideas', [])),
            'processing_time': idea_request.get_processing_time()
        }

    except Exception as exc:
        # Handle failures
        logger.error(f"Failed to generate ideas for request {request_id}: {str(exc)}")
        logger.error(traceback.format_exc())
        
        try:
            idea_request = IdeaRequest.objects.get(id=request_id)
            idea_request.mark_as_failed(str(exc))
        except IdeaRequest.DoesNotExist:
            pass

        # Retry logic
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying idea generation for request {request_id} (retry {self.request.retries + 1})")
            raise self.retry(countdown=60 * (2 ** self.request.retries), exc=exc)
        else:
            # Max retries exceeded, notify user
            _notify_generation_failed.delay(request_id, str(exc))
            return {'success': False, 'error': str(exc), 'max_retries_exceeded': True}


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=2)
def generate_single_idea_task(self, generation_request_data: Dict, model_config_data: Dict) -> Dict[str, Any]:
    """
    Task for generating a single idea (used in parallel generation)
    """
    try:
        generation_service = IdeaGenerationService()
        
        # Convert dict back to dataclass
        from .services import IdeaGenerationRequest
        generation_request = IdeaGenerationRequest(**generation_request_data)
        
        # Generate the idea
        result = generation_service.generate_ideas(generation_request, model_config_data)
        
        return {
            'success': True,
            'idea': result,
            'tokens_used': result.generation_tokens if hasattr(result, 'generation_tokens') else 0
        }
        
    except Exception as exc:
        logger.error(f"Failed to generate single idea: {str(exc)}")
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=30, exc=exc)
        return {'success': False, 'error': str(exc)}


# ==============================================================================
# POST-GENERATION TASKS
# ==============================================================================

@shared_task
def _schedule_post_generation_tasks(request_id: int, ideas_generated: int, user_id: int):
    """
    Schedule all post-generation tasks using Celery's workflow patterns
    """
    # Create a group of independent tasks
    post_tasks = group(
        update_usage_stats.s(user_id, ideas_generated),
        update_template_usage_stats.s(request_id),
        cache_user_recent_ideas.s(user_id),
        analyze_content_quality.s(request_id),
        schedule_feedback_reminder.s(user_id, request_id)
    )
    
    # Execute all tasks in parallel
    job = post_tasks.apply_async()
    logger.info(f"Scheduled post-generation tasks for request {request_id}")
    return job.id


@shared_task
def update_usage_stats(user_id: int, ideas_generated: int, tokens_used: int = 0, model_used: str = ""):
    """
    Update daily usage statistics
    """
    try:
        today = timezone.now().date()
        
        with transaction.atomic():
            stats, created = IdeaUsageStats.objects.get_or_create(
                date=today,
                defaults={
                    'total_requests': 0,
                    'successful_generations': 0,
                    'failed_generations': 0,
                    'total_users': 0,
                    'free_tier_requests': 0,
                    'premium_requests': 0,
                    'total_tokens_used': 0
                }
            )
            
            # Update stats
            stats.total_requests = F('total_requests') + 1
            stats.successful_generations = F('successful_generations') + ideas_generated
            stats.total_tokens_used = F('total_tokens_used') + tokens_used
            
            # Check user subscription tier
            try:
                user = User.objects.select_related('subscription').get(id=user_id)
                if hasattr(user, 'subscription') and user.subscription.is_active:
                    stats.premium_requests = F('premium_requests') + 1
                else:
                    stats.free_tier_requests = F('free_tier_requests') + 1
            except User.DoesNotExist:
                stats.free_tier_requests = F('free_tier_requests') + 1
            
            stats.save()
            
            # Update unique users count (done separately to avoid complex F expressions)
            _update_daily_unique_users.delay(today.isoformat(), user_id)
            
        logger.info(f"Updated usage stats for user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to update usage stats: {str(e)}")


@shared_task
def _update_daily_unique_users(date_str: str, user_id: int):
    """
    Update daily unique users count
    """
    try:
        date_obj = datetime.fromisoformat(date_str).date()
        cache_key = f"daily_users_{date_str}"
        
        # Use Redis set to track unique users
        daily_users = cache.get(cache_key, set())
        if not isinstance(daily_users, set):
            daily_users = set()
            
        daily_users.add(user_id)
        cache.set(cache_key, daily_users, 86400)  # 24 hours
        
        # Update database
        IdeaUsageStats.objects.filter(date=date_obj).update(
            total_users=len(daily_users)
        )
        
    except Exception as e:
        logger.error(f"Failed to update daily unique users: {str(e)}")


@shared_task
def update_template_usage_stats(request_id: int):
    """
    Update template usage statistics
    """
    try:
        # Get all ideas generated for this request and their templates
        ideas = GeneratedIdea.objects.filter(
            request_id=request_id
        ).exclude(template_used__isnull=True)
        
        template_ids = ideas.values_list('template_used_id', flat=True).distinct()
        
        for template_id in template_ids:
            IdeaTemplate.objects.filter(id=template_id).update(
                usage_count=F('usage_count') + 1
            )
            
        logger.info(f"Updated template usage stats for request {request_id}")
        
    except Exception as e:
        logger.error(f"Failed to update template usage stats: {str(e)}")


@shared_task
def cache_user_recent_ideas(user_id: int):
    """
    Cache user's recent ideas for quick access
    """
    try:
        recent_ideas = GeneratedIdea.objects.filter(
            request__user_id=user_id
        ).select_related('request').order_by('-created_at')[:10]
        
        cache_key = f"user_recent_ideas_{user_id}"
        cache_data = []
        
        for idea in recent_ideas:
            cache_data.append({
                'id': idea.id,
                'title': idea.title,
                'description': idea.description[:200],
                'created_at': idea.created_at.isoformat(),
                'like_count': idea.like_count,
                'view_count': idea.view_count
            })
        
        cache.set(cache_key, cache_data, 3600)  # 1 hour
        logger.info(f"Cached recent ideas for user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to cache user recent ideas: {str(e)}")


# ==============================================================================
# ANALYTICS TASKS
# ==============================================================================

@shared_task
def analyze_content_quality(request_id: int):
    """
    Analyze the quality of generated content
    """
    try:
        ideas = GeneratedIdea.objects.filter(request_id=request_id)
        
        for idea in ideas:
            # Simple quality metrics
            quality_score = _calculate_content_quality_score(idea)
            
            idea.content_quality_score = quality_score
            idea.save(update_fields=['content_quality_score'])
            
        logger.info(f"Analyzed content quality for request {request_id}")
        
    except Exception as e:
        logger.error(f"Failed to analyze content quality: {str(e)}")


@shared_task
def log_interaction_async(user_id: int, idea_id: int, interaction_type: str, metadata: Dict = None):
    """
    Log user interactions with ideas for analytics
    """
    try:
        # This could be expanded to log to a separate analytics database
        # For now, we'll update the idea's engagement metrics
        
        if interaction_type == 'view':
            GeneratedIdea.objects.filter(id=idea_id).update(
                view_count=F('view_count') + 1
            )
        elif interaction_type == 'like':
            GeneratedIdea.objects.filter(id=idea_id).update(
                like_count=F('like_count') + 1
            )
        elif interaction_type == 'share':
            GeneratedIdea.objects.filter(id=idea_id).update(
                share_count=F('share_count') + 1
            )
        elif interaction_type == 'pdf_download':
            GeneratedIdea.objects.filter(id=idea_id).update(
                pdf_download_count=F('pdf_download_count') + 1
            )
        
        # Log to analytics service if available
        if hasattr(settings, 'ANALYTICS_ENABLED') and settings.ANALYTICS_ENABLED:
            _send_to_analytics.delay(user_id, idea_id, interaction_type, metadata)
            
        logger.debug(f"Logged {interaction_type} interaction for idea {idea_id}")
        
    except Exception as e:
        logger.error(f"Failed to log interaction: {str(e)}")


@shared_task
def _send_to_analytics(user_id: int, idea_id: int, interaction_type: str, metadata: Dict):
    """
    Send interaction data to external analytics service
    """
    try:
        # Placeholder for external analytics integration
        # Could integrate with Google Analytics, Mixpanel, etc.
        analytics_data = {
            'user_id': user_id,
            'idea_id': idea_id,
            'interaction_type': interaction_type,
            'timestamp': timezone.now().isoformat(),
            'metadata': metadata or {}
        }
        
        # Example: Send to external service
        # analytics_client.track_event(analytics_data)
        
        logger.debug(f"Sent analytics data for user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to send analytics data: {str(e)}")


# ==============================================================================
# SCHEDULED TASKS
# ==============================================================================

@shared_task
def generate_daily_analytics_report():
    """
    Generate daily analytics report
    """
    try:
        yesterday = timezone.now().date() - timedelta(days=1)
        
        # Get usage stats
        stats = IdeaUsageStats.objects.filter(date=yesterday).first()
        if not stats:
            logger.warning(f"No usage stats found for {yesterday}")
            return
        
        # Calculate additional metrics
        success_rate = (stats.successful_generations / stats.total_requests * 100) if stats.total_requests > 0 else 0
        avg_ideas_per_user = (stats.successful_generations / stats.total_users) if stats.total_users > 0 else 0
        
        # Prepare report data
        report_data = {
            'date': yesterday,
            'total_requests': stats.total_requests,
            'successful_generations': stats.successful_generations,
            'failed_generations': stats.failed_generations,
            'success_rate': round(success_rate, 2),
            'total_users': stats.total_users,
            'avg_ideas_per_user': round(avg_ideas_per_user, 2),
            'premium_requests': stats.premium_requests,
            'free_tier_requests': stats.free_tier_requests,
            'total_tokens_used': stats.total_tokens_used
        }
        
        # Send to admin team
        _send_analytics_report.delay(report_data)
        
        logger.info(f"Generated daily analytics report for {yesterday}")
        
    except Exception as e:
        logger.error(f"Failed to generate daily analytics report: {str(e)}")


@shared_task
def cleanup_old_data():
    """
    Clean up old data to maintain database performance
    """
    try:
        cutoff_date = timezone.now() - timedelta(days=90)
        
        # Clean up old failed requests
        old_failed_requests = IdeaRequest.objects.filter(
            status='failed',
            created_at__lt=cutoff_date
        )
        
        deleted_count = old_failed_requests.count()
        old_failed_requests.delete()
        
        # Clean up old usage stats (keep only last year)
        old_stats_cutoff = timezone.now().date() - timedelta(days=365)
        old_stats = IdeaUsageStats.objects.filter(date__lt=old_stats_cutoff)
        old_stats_count = old_stats.count()
        old_stats.delete()
        
        logger.info(f"Cleaned up {deleted_count} old requests and {old_stats_count} old stats")
        
    except Exception as e:
        logger.error(f"Failed to cleanup old data: {str(e)}")


# ==============================================================================
# NOTIFICATION TASKS
# ==============================================================================

@shared_task
def schedule_feedback_reminder(user_id: int, request_id: int):
    """
    Schedule a reminder for user to provide feedback
    """
    try:
        # Schedule reminder for 24 hours later
        reminder_time = timezone.now() + timedelta(hours=24)
        
        send_feedback_reminder.apply_async(
            args=[user_id, request_id],
            eta=reminder_time
        )
        
        logger.info(f"Scheduled feedback reminder for user {user_id}")
        
    except Exception as e:
        logger.error(f"Failed to schedule feedback reminder: {str(e)}")


@shared_task
def send_feedback_reminder(user_id: int, request_id: int):
    """
    Send feedback reminder email to user
    """
    try:
        user = User.objects.get(id=user_id)
        request_obj = IdeaRequest.objects.get(id=request_id)
        
        # Check if user has already provided feedback
        has_feedback = IdeaFeedback.objects.filter(
            user=user,
            idea__request=request_obj
        ).exists()
        
        if has_feedback:
            logger.info(f"User {user_id} already provided feedback for request {request_id}")
            return
        
        # Send reminder email
        subject = "How were your date ideas? We'd love your feedback!"
        message = render_to_string('emails/feedback_reminder.html', {
            'user': user,
            'request': request_obj
        })
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=message
        )
        
        logger.info(f"Sent feedback reminder to user {user_id}")
        
    except (User.DoesNotExist, IdeaRequest.DoesNotExist):
        logger.warning(f"User {user_id} or request {request_id} not found for feedback reminder")
    except Exception as e:
        logger.error(f"Failed to send feedback reminder: {str(e)}")


@shared_task
def _notify_generation_failed(request_id: int, error_message: str):
    """
    Notify user that idea generation failed
    """
    try:
        request_obj = IdeaRequest.objects.select_related('user').get(id=request_id)
        
        subject = "We're having trouble generating your date ideas"
        message = render_to_string('emails/generation_failed.html', {
            'user': request_obj.user,
            'request': request_obj,
            'error_message': error_message
        })
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[request_obj.user.email],
            html_message=message
        )
        
        logger.info(f"Sent generation failed notification to user {request_obj.user.id}")
        
    except Exception as e:
        logger.error(f"Failed to send generation failed notification: {str(e)}")


@shared_task
def _send_analytics_report(report_data: Dict):
    """
    Send analytics report to admin team
    """
    try:
        subject = f"LoveCraft Daily Analytics Report - {report_data['date']}"
        message = render_to_string('emails/analytics_report.html', {
            'report': report_data
        })
        
        admin_emails = getattr(settings, 'ADMIN_EMAILS', [])
        if admin_emails:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=admin_emails,
                html_message=message
            )
        
        logger.info("Sent daily analytics report to admin team")
        
    except Exception as e:
        logger.error(f"Failed to send analytics report: {str(e)}")


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def _prepare_generation_request(idea_request: IdeaRequest) -> Dict[str, Any]:
    """
    Prepare generation request data from IdeaRequest model
    """
    return {
        'user_id': idea_request.user.id,
        'request_id': idea_request.id,
        'occasion': idea_request.occasion,
        'partner_interests': idea_request.partner_interests,
        'user_interests': idea_request.user_interests,
        'personality_type': idea_request.personality_type,
        'budget': idea_request.budget,
        'location_type': idea_request.location_type,
        'location_city': idea_request.location_city,
        'duration': idea_request.duration,
        'special_requirements': idea_request.special_requirements,
        'custom_prompt': idea_request.custom_prompt,
        'ai_model': idea_request.ai_model,
        'temperature': idea_request.temperature,
        'max_tokens': idea_request.max_tokens
    }


def _get_ai_model_config(model_name: str) -> Optional[Dict]:
    """
    Get AI model configuration
    """
    try:
        config = AIModelConfiguration.objects.filter(
            name=model_name,
            is_active=True
        ).first()
        
        if config:
            return {
                'name': config.name,
                'provider': config.provider,
                'model_id': config.model_id,
                'max_tokens': config.max_tokens,
                'temperature': config.temperature,
                'cost_per_1k_tokens': float(config.cost_per_1k_tokens)
            }
        return None
        
    except Exception as e:
        logger.error(f"Failed to get AI model config: {str(e)}")
        return None


def _get_ideas_count_for_user(user: User) -> int:
    """
    Get number of ideas to generate based on user's subscription
    """
    try:
        if hasattr(user, 'subscription') and user.subscription.is_active:
            return user.subscription.plan.ideas_per_request
        return getattr(settings, 'FREE_TIER_IDEAS_COUNT', 3)
    except Exception:
        return 3


def _generate_single_idea(generation_request: Dict, model_config: Dict) -> Dict[str, Any]:
    """
    Generate a single idea
    """
    generation_service = IdeaGenerationService()
    from .services import IdeaGenerationRequest
    
    request_obj = IdeaGenerationRequest(**generation_request)
    result = generation_service.generate_ideas(request_obj, model_config)
    
    return {'ideas': [result] if result else []}


def _generate_multiple_ideas_parallel(generation_request: Dict, count: int, model_config: Dict) -> Dict[str, Any]:
    """
    Generate multiple ideas in parallel using Celery chord
    """
    # Create parallel tasks for each idea
    generation_tasks = [
        generate_single_idea_task.s(generation_request, model_config)
        for _ in range(count)
    ]
    
    # Execute in parallel and collect results
    job = group(*generation_tasks)()
    results = job.get(timeout=300)  # 5 minutes timeout
    
    # Process results
    ideas = []
    for result in results:
        if result.get('success') and result.get('idea'):
            ideas.append(result['idea'])
    
    return {'ideas': ideas}


def _calculate_content_quality_score(idea: GeneratedIdea) -> float:
    """
    Calculate content quality score based on various metrics
    """
    try:
        score = 0.0
        
        # Length checks
        if len(idea.description) > 100:
            score += 0.2
        if len(idea.detailed_plan) > 200:
            score += 0.2
        
        # Content diversity
        if idea.location_suggestions:
            score += 0.2
        if idea.preparation_tips:
            score += 0.1
        if idea.alternatives:
            score += 0.1
        
        # Engagement metrics
        if idea.view_count > 0:
            score += 0.1
        if idea.like_count > 0:
            score += 0.1
        
        return min(score, 1.0)  # Cap at 1.0
        
    except Exception as e:
        logger.error(f"Failed to calculate quality score: {str(e)}")
        return 0.5  # Default score
    
    
    
