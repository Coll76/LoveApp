# apps/ideas/services.py
import logging
import json
import re
from typing import Dict, List, Optional, Tuple, Any
from decimal import Decimal
from datetime import datetime, timedelta
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction, models
from django.db.models import Avg, Count, Q, F, Sum
from django.utils import timezone
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model

from core.exceptions import ServiceUnavailableError, ValidationError as CustomValidationError
from .models import (
    IdeaCategory, IdeaTemplate, IdeaRequest, GeneratedIdea, 
    IdeaFeedback, IdeaBookmark, IdeaUsageStats, AIModelConfiguration
)
from .ai_client import AIClient
from .prompt_templates import PromptTemplateEngine

User = get_user_model()
logger = logging.getLogger(__name__)


@dataclass
class IdeaGenerationRequest:
    """Data class for idea generation requests"""
    user_id: int
    request_id: int
    occasion: str = ""
    partner_interests: str = ""
    user_interests: str = ""
    personality_type: str = ""
    budget: str = "moderate"
    location_type: str = "any"
    location_city: str = ""
    duration: str = ""
    special_requirements: str = ""
    custom_prompt: str = ""
    ai_model: str = "gpt-3.5-turbo"
    temperature: float = 0.7
    max_tokens: int = 1500


@dataclass
class GeneratedIdeaResult:
    """Data class for generated idea results"""
    title: str
    description: str
    detailed_plan: str
    estimated_cost: str
    duration: str
    location_suggestions: List[Dict]
    preparation_tips: str
    alternatives: str
    content_quality_score: float
    ai_response_raw: str
    prompt_used: str
    generation_tokens: int


class IdeaGenerationService:
    """
    Service for handling AI-powered idea generation
    """
    
    def __init__(self):
        self.ai_client = AIClient()
        self.prompt_engine = PromptTemplateEngine()
        self.cache_timeout = getattr(settings, 'IDEA_CACHE_TIMEOUT', 3600)
    
    def generate_ideas(self, request_data: IdeaGenerationRequest) -> List[GeneratedIdeaResult]:
        """
        Generate date ideas based on user input
        
        Args:
            request_data: IdeaGenerationRequest object containing user preferences
            
        Returns:
            List of GeneratedIdeaResult objects
            
        Raises:
            ServiceUnavailableError: If AI service is unavailable
            ValidationError: If input data is invalid
        """
        try:
            # Validate input data
            self._validate_generation_request(request_data)
            
            # Get appropriate AI model configuration
            ai_config = self._get_ai_model_config(request_data.ai_model)
            
            # Generate prompts using template engine
            prompts = self._generate_prompts(request_data)
            
            # Generate ideas using AI
            generated_ideas = []
            for prompt_data in prompts:
                try:
                    ai_response = self.ai_client.generate_completion(
                        prompt=prompt_data['prompt'],
                        model=ai_config.model_id,
                        temperature=request_data.temperature,
                        max_tokens=request_data.max_tokens,
                        user_id=request_data.user_id
                    )
                    
                    # Parse and validate AI response
                    parsed_idea = self._parse_ai_response(
                        ai_response, 
                        prompt_data['template_used'],
                        prompt_data['prompt']
                    )
                    
                    if parsed_idea:
                        generated_ideas.append(parsed_idea)
                        
                except Exception as e:
                    logger.error(f"Failed to generate idea: {str(e)}")
                    continue
            
            if not generated_ideas:
                raise ServiceUnavailableError("Failed to generate any ideas")
            
            # Apply quality filtering
            quality_ideas = self._filter_quality_ideas(generated_ideas)
            
            # Log generation metrics
            self._log_generation_metrics(request_data, len(quality_ideas))
            
            return quality_ideas
            
        except Exception as e:
            logger.error(f"Idea generation failed: {str(e)}")
            raise ServiceUnavailableError(f"Idea generation service unavailable: {str(e)}")
    
    def _validate_generation_request(self, request_data: IdeaGenerationRequest) -> None:
        """Validate generation request data"""
        if not request_data.user_id:
            raise CustomValidationError("User ID is required")
        
        if not request_data.request_id:
            raise CustomValidationError("Request ID is required")
        
        # Validate budget choice
        valid_budgets = ['low', 'moderate', 'high', 'unlimited']
        if request_data.budget not in valid_budgets:
            raise CustomValidationError(f"Invalid budget choice: {request_data.budget}")
        
        # Validate location type
        valid_locations = ['indoor', 'outdoor', 'mixed', 'any']
        if request_data.location_type not in valid_locations:
            raise CustomValidationError(f"Invalid location type: {request_data.location_type}")
        
        # Validate AI parameters
        if not (0.0 <= request_data.temperature <= 2.0):
            raise CustomValidationError("Temperature must be between 0.0 and 2.0")
        
        if not (100 <= request_data.max_tokens <= 4000):
            raise CustomValidationError("Max tokens must be between 100 and 4000")
    
    def _get_ai_model_config(self, model_name: str) -> AIModelConfiguration:
        """Get AI model configuration"""
        try:
            config = AIModelConfiguration.objects.get(
                name=model_name,
                is_active=True
            )
            return config
        except AIModelConfiguration.DoesNotExist:
            # Fallback to default model
            return AIModelConfiguration.objects.filter(
                is_active=True
            ).order_by('priority').first()
    
    def _generate_prompts(self, request_data: IdeaGenerationRequest) -> List[Dict]:
        """Generate prompts using template engine"""
        # Get suitable templates based on user preferences
        templates = self._select_templates(request_data)
        
        prompts = []
        for template in templates:
            try:
                prompt = self.prompt_engine.generate_prompt(
                    template=template,
                    user_data={
                        'occasion': request_data.occasion,
                        'partner_interests': request_data.partner_interests,
                        'user_interests': request_data.user_interests,
                        'personality_type': request_data.personality_type,
                        'budget': request_data.budget,
                        'location_type': request_data.location_type,
                        'location_city': request_data.location_city,
                        'duration': request_data.duration,
                        'special_requirements': request_data.special_requirements,
                        'custom_prompt': request_data.custom_prompt,
                    }
                )
                
                prompts.append({
                    'prompt': prompt,
                    'template_used': template,
                    'template_id': template.id if template else None
                })
                
            except Exception as e:
                logger.error(f"Failed to generate prompt for template {template}: {str(e)}")
                continue
        
        return prompts
    
    def _select_templates(self, request_data: IdeaGenerationRequest) -> List[IdeaTemplate]:
        """Select appropriate templates based on user preferences"""
        # Cache key for template selection
        cache_key = f"templates_{request_data.budget}_{request_data.location_type}"
        templates = cache.get(cache_key)
        
        if templates is None:
            # Get user to check subscription tier
            try:
                user = User.objects.get(id=request_data.user_id)
                templates = IdeaTemplate.objects.for_user_tier(user)
                
                # Filter by preferences
                if request_data.budget != 'any':
                    # Logic to match templates with budget preferences
                    budget_templates = self._filter_templates_by_budget(templates, request_data.budget)
                    templates = budget_templates if budget_templates.exists() else templates
                
                if request_data.location_type != 'any':
                    # Logic to match templates with location preferences
                    location_templates = self._filter_templates_by_location(templates, request_data.location_type)
                    templates = location_templates if location_templates.exists() else templates
                
                templates = list(templates.order_by('-usage_count', '-average_rating')[:5])
                cache.set(cache_key, templates, self.cache_timeout)
                
            except User.DoesNotExist:
                templates = list(IdeaTemplate.objects.free_templates()[:3])
        
        return templates
    
    def _filter_templates_by_budget(self, templates, budget: str):
        """Filter templates by budget preference"""
        budget_mapping = {
            'low': ['budget_friendly', 'casual'],
            'moderate': ['casual', 'romantic', 'creative'],
            'high': ['luxurious', 'romantic', 'adventurous'],
            'unlimited': ['luxurious', 'adventurous', 'creative']
        }
        
        template_types = budget_mapping.get(budget, [])
        if template_types:
            return templates.filter(template_type__in=template_types)
        return templates
    
    def _filter_templates_by_location(self, templates, location_type: str):
        """Filter templates by location type"""
        location_mapping = {
            'indoor': ['indoor', 'relaxed', 'cultural'],
            'outdoor': ['outdoor', 'adventurous', 'active'],
            'mixed': ['casual', 'romantic', 'creative']
        }
        
        template_types = location_mapping.get(location_type, [])
        if template_types:
            return templates.filter(template_type__in=template_types)
        return templates
    
    def _parse_ai_response(self, ai_response: Dict, template_used: IdeaTemplate, prompt_used: str) -> Optional[GeneratedIdeaResult]:
        """Parse AI response into structured idea data"""
        try:
            content = ai_response.get('content', '')
            
            # Try to parse as JSON first
            if self._is_json_response(content):
                parsed_data = json.loads(content)
                return self._create_idea_from_json(parsed_data, ai_response, prompt_used)
            
            # Parse as structured text
            return self._create_idea_from_text(content, ai_response, prompt_used)
            
        except Exception as e:
            logger.error(f"Failed to parse AI response: {str(e)}")
            return None
    
    def _is_json_response(self, content: str) -> bool:
        """Check if response is JSON format"""
        try:
            json.loads(content.strip())
            return True
        except json.JSONDecodeError:
            return False
    
    def _create_idea_from_json(self, data: Dict, ai_response: Dict, prompt_used: str) -> GeneratedIdeaResult:
        """Create idea result from JSON data"""
        return GeneratedIdeaResult(
            title=data.get('title', 'Untitled Idea'),
            description=data.get('description', ''),
            detailed_plan=data.get('detailed_plan', ''),
            estimated_cost=data.get('estimated_cost', ''),
            duration=data.get('duration', ''),
            location_suggestions=data.get('location_suggestions', []),
            preparation_tips=data.get('preparation_tips', ''),
            alternatives=data.get('alternatives', ''),
            content_quality_score=self._calculate_quality_score(data),
            ai_response_raw=json.dumps(ai_response),
            prompt_used=prompt_used,
            generation_tokens=ai_response.get('usage', {}).get('total_tokens', 0)
        )
    
    def _create_idea_from_text(self, content: str, ai_response: Dict, prompt_used: str) -> GeneratedIdeaResult:
        """Create idea result from text content"""
        # Extract structured information using regex patterns
        title = self._extract_title(content)
        description = self._extract_description(content)
        detailed_plan = self._extract_detailed_plan(content)
        estimated_cost = self._extract_cost(content)
        duration = self._extract_duration(content)
        location_suggestions = self._extract_locations(content)
        preparation_tips = self._extract_preparation_tips(content)
        alternatives = self._extract_alternatives(content)
        
        parsed_data = {
            'title': title,
            'description': description,
            'detailed_plan': detailed_plan,
            'estimated_cost': estimated_cost,
            'duration': duration,
            'location_suggestions': location_suggestions,
            'preparation_tips': preparation_tips,
            'alternatives': alternatives
        }
        
        return GeneratedIdeaResult(
            title=title,
            description=description,
            detailed_plan=detailed_plan,
            estimated_cost=estimated_cost,
            duration=duration,
            location_suggestions=location_suggestions,
            preparation_tips=preparation_tips,
            alternatives=alternatives,
            content_quality_score=self._calculate_quality_score(parsed_data),
            ai_response_raw=json.dumps(ai_response),
            prompt_used=prompt_used,
            generation_tokens=ai_response.get('usage', {}).get('total_tokens', 0)
        )
    
    def _extract_title(self, content: str) -> str:
        """Extract title from text content"""
        patterns = [
            r'(?:Title|TITLE):\s*(.+)',
            r'(?:Idea|IDEA):\s*(.+)',
            r'^(.+?)(?:\n|$)',  # First line
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                if len(title) > 10:  # Reasonable title length
                    return title[:300]  # Limit title length
        
        return "Creative Date Idea"
    
    def _extract_description(self, content: str) -> str:
        """Extract description from text content"""
        patterns = [
            r'(?:Description|DESCRIPTION):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
            r'(?:Summary|SUMMARY):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        
        # Fallback: use first paragraph
        paragraphs = content.split('\n\n')
        if len(paragraphs) > 1:
            return paragraphs[1].strip()
        
        return content[:500] + "..." if len(content) > 500 else content
    
    def _extract_detailed_plan(self, content: str) -> str:
        """Extract detailed plan from text content"""
        patterns = [
            r'(?:Plan|PLAN|Detailed Plan|DETAILED PLAN):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
            r'(?:Steps|STEPS):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        
        return ""
    
    def _extract_cost(self, content: str) -> str:
        """Extract cost information from text content"""
        patterns = [
            r'(?:Cost|COST|Budget|BUDGET|Price|PRICE):\s*(.+?)(?:\n|$)',
            r'\$\d+(?:\.\d{2})?(?:\s*-\s*\$\d+(?:\.\d{2})?)?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if match:
                return match.group(1).strip() if hasattr(match, 'group') else match.group(0).strip()
        
        return ""
    
    def _extract_duration(self, content: str) -> str:
        """Extract duration from text content"""
        patterns = [
            r'(?:Duration|DURATION|Time|TIME):\s*(.+?)(?:\n|$)',
            r'(?:\d+(?:\.\d+)?)\s*(?:hours?|hrs?|minutes?|mins?)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if match:
                return match.group(1).strip() if hasattr(match, 'group') else match.group(0).strip()
        
        return ""
    
    def _extract_locations(self, content: str) -> List[Dict]:
        """Extract location suggestions from text content"""
        patterns = [
            r'(?:Locations?|LOCATIONS?):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
            r'(?:Places?|PLACES?):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
        ]
        
        locations = []
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                location_text = match.group(1).strip()
                # Split by commas, newlines, or numbered lists
                location_list = re.split(r'[,\n]|\d+\.', location_text)
                for loc in location_list:
                    loc = loc.strip()
                    if loc and len(loc) > 3:
                        locations.append({
                            'name': loc,
                            'type': 'suggested',
                            'description': ''
                        })
                break
        
        return locations[:10]  # Limit to 10 locations
    
    def _extract_preparation_tips(self, content: str) -> str:
        """Extract preparation tips from text content"""
        patterns = [
            r'(?:Preparation|PREPARATION|Tips|TIPS|Prep|PREP):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
            r'(?:Before|BEFORE|Getting Ready|GETTING READY):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        
        return ""
    
    def _extract_alternatives(self, content: str) -> str:
        """Extract alternatives from text content"""
        patterns = [
            r'(?:Alternatives?|ALTERNATIVES?):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
            r'(?:Options?|OPTIONS?):\s*(.+?)(?:\n(?:[A-Z][a-z]*:|$))',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        
        return ""
    
    def _calculate_quality_score(self, data: Dict) -> float:
        """Calculate content quality score"""
        score = 0.0
        max_score = 10.0
        
        # Title quality (0-2 points)
        title = data.get('title', '')
        if title and len(title) > 10:
            score += 2.0
        elif title:
            score += 1.0
        
        # Description quality (0-3 points)
        description = data.get('description', '')
        if len(description) > 100:
            score += 3.0
        elif len(description) > 50:
            score += 2.0
        elif description:
            score += 1.0
        
        # Detailed plan quality (0-2 points)
        detailed_plan = data.get('detailed_plan', '')
        if len(detailed_plan) > 100:
            score += 2.0
        elif detailed_plan:
            score += 1.0
        
        # Cost information (0-1 point)
        if data.get('estimated_cost'):
            score += 1.0
        
        # Duration information (0-1 point)
        if data.get('duration'):
            score += 1.0
        
        # Location suggestions (0-1 point)
        locations = data.get('location_suggestions', [])
        if locations and len(locations) > 0:
            score += 1.0
        
        return round((score / max_score) * 5.0, 2)  # Convert to 5-point scale
    
    def _filter_quality_ideas(self, ideas: List[GeneratedIdeaResult]) -> List[GeneratedIdeaResult]:
        """Filter ideas based on quality score"""
        min_quality_score = getattr(settings, 'MIN_IDEA_QUALITY_SCORE', 2.0)
        return [idea for idea in ideas if idea.content_quality_score >= min_quality_score]
    
    def _log_generation_metrics(self, request_data: IdeaGenerationRequest, ideas_count: int) -> None:
        """Log generation metrics for analytics"""
        try:
            logger.info(f"Generated {ideas_count} ideas for user {request_data.user_id}")
            
            # Update usage stats (this could be done async)
            from .tasks import update_usage_stats
            update_usage_stats.delay(
                user_id=request_data.user_id,
                ideas_generated=ideas_count,
                tokens_used=request_data.max_tokens,
                model_used=request_data.ai_model
            )
            
        except Exception as e:
            logger.error(f"Failed to log generation metrics: {str(e)}")
    
    @transaction.atomic
    def save_generated_ideas(self, request_id: int, ideas: List[GeneratedIdeaResult]) -> List[GeneratedIdea]:
        """
        Save generated ideas to database
        
        Args:
            request_id: ID of the idea request
            ideas: List of generated idea results
            
        Returns:
            List of saved GeneratedIdea objects
        """
        try:
            idea_request = IdeaRequest.objects.get(id=request_id)
            saved_ideas = []
            
            for idea_result in ideas:
                generated_idea = GeneratedIdea.objects.create(
                    request=idea_request,
                    title=idea_result.title,
                    description=idea_result.description,
                    detailed_plan=idea_result.detailed_plan,
                    estimated_cost=idea_result.estimated_cost,
                    duration=idea_result.duration,
                    location_suggestions=idea_result.location_suggestions,
                    preparation_tips=idea_result.preparation_tips,
                    alternatives=idea_result.alternatives,
                    ai_model_used=idea_request.ai_model,
                    prompt_used=idea_result.prompt_used,
                    ai_response_raw=idea_result.ai_response_raw,
                    generation_tokens=idea_result.generation_tokens,
                    content_quality_score=idea_result.content_quality_score
                )
                saved_ideas.append(generated_idea)
            
            # Mark request as completed
            idea_request.mark_as_completed()
            
            logger.info(f"Saved {len(saved_ideas)} ideas for request {request_id}")
            return saved_ideas
            
        except IdeaRequest.DoesNotExist:
            logger.error(f"IdeaRequest {request_id} not found")
            raise ValidationError(f"Request {request_id} not found")
        except Exception as e:
            logger.error(f"Failed to save ideas: {str(e)}")
            raise


class IdeaAnalyticsService:
    """
    Service for idea analytics and statistics
    """
    
    @staticmethod
    def get_user_stats(user: User) -> Dict[str, Any]:
        """Get comprehensive user statistics"""
        cache_key = f"user_stats_{user.id}"
        stats = cache.get(cache_key)
        
        if stats is None:
            requests = IdeaRequest.objects.for_user(user)
            ideas = GeneratedIdea.objects.for_user(user)
            
            stats = {
                'total_requests': requests.count(),
                'completed_requests': requests.completed().count(),
                'pending_requests': requests.pending().count(),
                'failed_requests': requests.failed().count(),
                'total_ideas': ideas.count(),
                'total_views': ideas.aggregate(total=Sum('view_count'))['total'] or 0,
                'total_likes': ideas.aggregate(total=Sum('like_count'))['total'] or 0,
                'total_shares': ideas.aggregate(total=Sum('share_count'))['total'] or 0,
                'average_rating': ideas.aggregate(avg=Avg('user_rating'))['avg'] or 0,
                'bookmarked_ideas': IdeaBookmark.objects.filter(user=user).count(),
                'feedback_given': IdeaFeedback.objects.for_user(user).count(),
            }
            
            # Cache for 15 minutes
            cache.set(cache_key, stats, 900)
        
        return stats
    
    @staticmethod
    def get_user_overview(user: User) -> Dict[str, Any]:
        """Get user overview dashboard data"""
        stats = IdeaAnalyticsService.get_user_stats(user)
        
        # Recent activity
        recent_requests = IdeaRequest.objects.for_user(user).recent(days=7)
        recent_ideas = GeneratedIdea.objects.for_user(user).recent(days=7)
        
        # Top rated ideas
        top_ideas = GeneratedIdea.objects.for_user(user).top_rated(limit=5)
        
        # Most used templates
        template_usage = IdeaRequest.objects.for_user(user).values(
            'generated_ideas__template_used__name'
        ).annotate(
            usage_count=Count('generated_ideas__template_used')
        ).order_by('-usage_count')[:5]
        
        return {
            'stats': stats,
            'recent_activity': {
                'requests_this_week': recent_requests.count(),
                'ideas_this_week': recent_ideas.count(),
            },
            'top_rated_ideas': [
                {
                    'id': idea.id,
                    'title': idea.title,
                    'rating': float(idea.user_rating) if idea.user_rating else 0,
                    'views': idea.view_count,
                    'likes': idea.like_count
                }
                for idea in top_ideas
            ],
            'popular_templates': [
                {
                    'name': item['generated_ideas__template_used__name'],
                    'usage_count': item['usage_count']
                }
                for item in template_usage if item['generated_ideas__template_used__name']
            ]
        }
    
    @staticmethod
    def get_user_trends(user: User, days: int = 30) -> Dict[str, Any]:
        """Get user usage trends over time"""
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)
        
        # Daily request counts
        daily_requests = IdeaRequest.objects.for_user(user).filter(
            created_at__date__range=[start_date, end_date]
        ).extra(
            select={'day': 'date(created_at)'}
        ).values('day').annotate(
            requests=Count('id'),
            ideas=Count('generated_ideas')
        ).order_by('day')
        
        # Weekly aggregation for longer periods
        if days > 30:
            # Group by week
            weekly_data = {}
            for item in daily_requests:
                week_start = item['day'] - timedelta(days=item['day'].weekday())
                week_key = week_start.strftime('%Y-%m-%d')
                
                if week_key not in weekly_data:
                    weekly_data[week_key] = {'requests': 0, 'ideas': 0}
                
                weekly_data[week_key]['requests'] += item['requests']
                weekly_data[week_key]['ideas'] += item['ideas']
            
            trend_data = [
                {
                    'date': week_start,
                    'requests': data['requests'],
                    'ideas': data['ideas']
                }
                for week_start, data in sorted(weekly_data.items())
            ]
        else:
            trend_data = list(daily_requests)
        
        return {
            'period': f"{days} days",
            'trend_data': trend_data,
            'total_requests': sum(item['requests'] for item in trend_data),
            'total_ideas': sum(item['ideas'] for item in trend_data),
            'average_daily_requests': sum(item['requests'] for item in trend_data) / max(days, 1)
        }
    
    @staticmethod
    def get_user_popular_templates(user: User) -> List[Dict[str, Any]]:
        """Get user's most used templates"""
        template_stats = GeneratedIdea.objects.for_user(user).values(
            'template_used__id',
            'template_used__name',
            'template_used__template_type'
        ).annotate(
            usage_count=Count('id'),
            average_rating=Avg('user_rating'),
            total_views=Sum('view_count'),
            total_likes=Sum('like_count')
        ).filter(
            template_used__isnull=False
        ).order_by('-usage_count')[:10]
        
        return [
            {
                'template_id': item['template_used__id'],
                'name': item['template_used__name'],
                'type': item['template_used__template_type'],
                'usage_count': item['usage_count'],
                'average_rating': float(item['average_rating']) if item['average_rating'] else 0,
                'total_views': item['total_views'] or 0,
                'total_likes': item['total_likes'] or 0
            }
            for item in template_stats
        ]
    
    @staticmethod
    def get_global_stats() -> Dict[str, Any]:
        """Get global platform statistics"""
        cache_key = "global_stats"
        stats = cache.get(cache_key)
        
        if stats is None:
            total_requests = IdeaRequest.objects.count()
            total_ideas = GeneratedIdea.objects.count()
            total_users = User.objects.count()
            
            stats = {
                'total_requests': total_requests,
                'total_ideas': total_ideas,
                'total_users': total_users,
                'average_ideas_per_request': total_ideas / max(total_requests, 1),
                'most_popular_templates': IdeaTemplate.objects.annotate(
                    usage=Count('generateidea')
                ).order_by('-usage').values('name', 'usage')[:5],
                'average_rating': GeneratedIdea.objects.aggregate(
                    avg=Avg('user_rating')
                )['avg'] or 0,
                'completion_rate': IdeaRequest.objects.completed().count() / max(total_requests, 1) * 100
            }
            
            # Cache for 30 minutes
            cache.set(cache_key, stats, 1800)
        
        return stats


class IdeaRatingService:
    """
    Service for handling idea ratings and feedback
    """
    
    @staticmethod
    @transaction.atomic
    def rate_idea(user: User, idea_id: int, rating: int, comment: str = '') -> IdeaFeedback:
        """
        Rate an idea with validation and side effects
        
        Args:
            user: User providing the rating
            idea_id: ID of the idea being rated
            rating: Rating value (1-5)
            comment: Optional comment
            
        Returns:
            IdeaFeedback object
            
        Raises:
            ValidationError: If rating is invalid or idea not found
        """
        try:
            idea = GeneratedIdea.objects.get(id=idea_id)
            
            # Validate rating
            if not (1 <= rating <= 5):
                raise CustomValidationError("Rating must be between 1 and 5")
            
            # Check if user already rated this idea
            existing_feedback = IdeaFeedback.objects.filter(
                user=user,
                idea=idea,
                feedback_type='rating'
            ).first()
            
            if existing_feedback:
                # Update existing rating
                existing_feedback.rating = rating
                existing_feedback.comment = comment
                existing_feedback.save()
                feedback = existing_feedback
            else:
                # Create new rating
                feedback = IdeaFeedback.objects.create(
                    user=user,
                    idea=idea,
                    feedback_type='rating',
                    rating=rating,
                    comment=comment
                )
            
            # Update idea's average rating
            IdeaRatingService._update_idea_rating(idea)
            
            # Log rating event
            logger.info(f"User {user.id} rated idea {idea_id} with {rating} stars")
            
            return feedback
            
        except GeneratedIdea.DoesNotExist:
            raise CustomValidationError(f"Idea {idea_id} not found")
        except Exception as e:
            logger.error(f"Failed to rate idea {idea_id}: {str(e)}")
            raise
    
    @staticmethod
    def _update_idea_rating(idea: GeneratedIdea) -> None:
        """Update idea's average rating"""
        ratings = IdeaFeedback.objects.filter(
            idea=idea,
            feedback_type='rating',
            rating__isnull=False
        ).aggregate(
            avg_rating=Avg('rating'),
            count=Count('rating')
        )
        
        if ratings['count'] > 0:
            idea.user_rating = Decimal(str(round(ratings['avg_rating'], 2)))
            idea.save(update_fields=['user_rating'])
    
    @staticmethod
    def like_idea(user: User, idea_id: int) -> Tuple[bool, IdeaFeedback]:
        """
        Like/unlike an idea
        
        Returns:
            Tuple of (is_liked, feedback_object)
        """
        try:
            idea = GeneratedIdea.objects.get(id=idea_id)
            
            existing_like = IdeaFeedback.objects.filter(
                user=user,
                idea=idea,
                feedback_type='like'
            ).first()
            
            if existing_like:
                # Unlike - delete the feedback
                existing_like.delete()
                idea.like_count = max(0, idea.like_count - 1)
                idea.save(update_fields=['like_count'])
                return False, None
            else:
                # Like - create feedback
                feedback = IdeaFeedback.objects.create(
                    user=user,
                    idea=idea,
                    feedback_type='like'
                )
                idea.increment_like_count()
                return True, feedback
                
        except GeneratedIdea.DoesNotExist:
            raise CustomValidationError(f"Idea {idea_id} not found")
    
    @staticmethod
    def report_idea(user: User, idea_id: int, reason: str, comment: str = '') -> IdeaFeedback:
        """Report an idea for inappropriate content"""
        try:
            idea = GeneratedIdea.objects.get(id=idea_id)
            
            # Check if user already reported this idea
            existing_report = IdeaFeedback.objects.filter(
                user=user,
                idea=idea,
                feedback_type='report'
            ).first()
            
            if existing_report:
                raise CustomValidationError("You have already reported this idea")
            
            feedback = IdeaFeedback.objects.create(
                user=user,
                idea=idea,
                feedback_type='report',
                report_reason=reason,
                comment=comment
            )
            
            # Log report for moderation
            logger.warning(f"Idea {idea_id} reported by user {user.id}: {reason}")
            
            return feedback
            
        except GeneratedIdea.DoesNotExist:
            raise CustomValidationError(f"Idea {idea_id} not found")


class IdeaBookmarkService:
    """
    Service for handling idea bookmarks
    """
    
    @staticmethod
    def bookmark_idea(user: User, idea_id: int, notes: str = '') -> Tuple[bool, IdeaBookmark]:
        """
        Bookmark/unbookmark an idea
        
        Returns:
            Tuple of (is_bookmarked, bookmark_object)
        """
        try:
            idea = GeneratedIdea.objects.get(id=idea_id)
            
            existing_bookmark = IdeaBookmark.objects.filter(
                user=user,
                idea=idea
            ).first()
            
            if existing_bookmark:
                # Remove bookmark
                existing_bookmark.delete()
                return False, None
            else:
                # Create bookmark
                bookmark = IdeaBookmark.objects.create(
                    user=user,
                    idea=idea,
                    notes=notes
                )
                return True, bookmark
                
        except GeneratedIdea.DoesNotExist:
            raise CustomValidationError(f"Idea {idea_id} not found")
    
    @staticmethod
    def get_user_bookmarks(user: User, limit: int = 20) -> List[IdeaBookmark]:
        """Get user's bookmarked ideas"""
        return IdeaBookmark.objects.filter(
            user=user
        ).select_related('idea', 'idea__request').order_by('-created_at')[:limit]
    
    @staticmethod
    def update_bookmark_notes(user: User, bookmark_id: int, notes: str) -> IdeaBookmark:
        """Update bookmark notes"""
        try:
            bookmark = IdeaBookmark.objects.get(id=bookmark_id, user=user)
            bookmark.notes = notes
            bookmark.save(update_fields=['notes'])
            return bookmark
        except IdeaBookmark.DoesNotExist:
            raise CustomValidationError("Bookmark not found")


class IdeaRecommendationService:
    """
    Service for recommending ideas based on user behavior
    """
    
    @staticmethod
    def get_recommendations_for_user(user: User, limit: int = 10) -> List[GeneratedIdea]:
        """
        Get personalized recommendations for a user
        """
        # Get user's interaction history
        user_ratings = IdeaFeedback.objects.filter(
            user=user,
            feedback_type='rating',
            rating__gte=4
        ).values_list('idea_id', flat=True)
        
        user_bookmarks = IdeaBookmark.objects.filter(
            user=user
        ).values_list('idea_id', flat=True)
        
        # Get user's preferred templates
        user_requests = IdeaRequest.objects.filter(user=user)
        preferred_categories = user_requests.values_list(
            'generated_ideas__template_used__category',
            flat=True
        ).distinct()
        
        # Build recommendation query
        recommendations = GeneratedIdea.objects.exclude(
            id__in=list(user_ratings) + list(user_bookmarks)
        ).exclude(
            request__user=user  # Don't recommend user's own ideas
        ).filter(
            user_rating__gte=4.0,  # High-rated ideas
            template_used__category__in=preferred_categories
        ).order_by('-user_rating', '-like_count')[:limit]
        
        # If not enough recommendations, fall back to popular ideas
        if len(recommendations) < limit:
            popular_ideas = GeneratedIdea.objects.exclude(
                id__in=list(user_ratings) + list(user_bookmarks)
            ).exclude(
                request__user=user
            ).order_by('-like_count', '-view_count')[:(limit - len(recommendations))]
            
            recommendations = list(recommendations) + list(popular_ideas)
        
        return recommendations
    
    @staticmethod
    def get_similar_ideas(idea_id: int, limit: int = 5) -> List[GeneratedIdea]:
        """Get ideas similar to a given idea"""
        try:
            idea = GeneratedIdea.objects.get(id=idea_id)
            
            # Find similar ideas based on template and category
            similar_ideas = GeneratedIdea.objects.filter(
                template_used=idea.template_used
            ).exclude(
                id=idea_id
            ).order_by('-user_rating', '-like_count')[:limit]
            
            # If not enough similar ideas, expand to same category
            if len(similar_ideas) < limit and idea.template_used:
                category_ideas = GeneratedIdea.objects.filter(
                    template_used__category=idea.template_used.category
                ).exclude(
                    id=idea_id
                ).exclude(
                    id__in=[i.id for i in similar_ideas]
                ).order_by('-user_rating', '-like_count')[:(limit - len(similar_ideas))]
                
                similar_ideas = list(similar_ideas) + list(category_ideas)
            
            return similar_ideas
            
        except GeneratedIdea.DoesNotExist:
            return []


class IdeaSearchService:
    """
    Service for searching and filtering ideas
    """
    
    @staticmethod
    def search_ideas(
        query: str = '',
        category_id: int = None,
        template_type: str = '',
        budget: str = '',
        location_type: str = '',
        min_rating: float = 0.0,
        user: User = None,
        limit: int = 20,
        offset: int = 0
    ) -> Dict[str, Any]:
        """
        Search ideas with various filters
        """
        # Base queryset
        ideas = GeneratedIdea.objects.select_related(
            'request',
            'template_used',
            'template_used__category'
        ).filter(
            user_rating__gte=min_rating
        )
        
        # Text search
        if query:
            ideas = ideas.filter(
                Q(title__icontains=query) |
                Q(description__icontains=query) |
                Q(detailed_plan__icontains=query)
            )
        
        # Category filter
        if category_id:
            ideas = ideas.filter(template_used__category_id=category_id)
        
        # Template type filter
        if template_type:
            ideas = ideas.filter(template_used__template_type=template_type)
        
        # Budget filter
        if budget:
            ideas = ideas.filter(request__budget=budget)
        
        # Location type filter
        if location_type:
            ideas = ideas.filter(request__location_type=location_type)
        
        # Exclude user's own ideas if user is provided
        if user and user.is_authenticated:
            ideas = ideas.exclude(request__user=user)
        
        # Get total count
        total_count = ideas.count()
        
        # Apply pagination and ordering
        ideas = ideas.order_by('-user_rating', '-like_count')[offset:offset + limit]
        
        return {
            'ideas': list(ideas),
            'total_count': total_count,
            'has_more': (offset + limit) < total_count,
            'next_offset': offset + limit if (offset + limit) < total_count else None
        }
    
    @staticmethod
    def get_trending_ideas(days: int = 7, limit: int = 10) -> List[GeneratedIdea]:
        """Get trending ideas based on recent engagement"""
        cutoff_date = timezone.now() - timedelta(days=days)
        
        return GeneratedIdea.objects.filter(
            created_at__gte=cutoff_date
        ).annotate(
            engagement_score=F('like_count') * 2 + F('view_count') + F('share_count') * 3
        ).order_by('-engagement_score', '-user_rating')[:limit]
    
    @staticmethod
    def get_popular_ideas(limit: int = 10) -> List[GeneratedIdea]:
        """Get most popular ideas of all time"""
        return GeneratedIdea.objects.order_by(
            '-like_count',
            '-view_count',
            '-user_rating'
        )[:limit]


class IdeaCacheService:
    """
    Service for caching frequently accessed idea data
    """
    
    @staticmethod
    def get_cached_idea(idea_id: int) -> Optional[GeneratedIdea]:
        """Get idea from cache or database"""
        cache_key = f"idea_{idea_id}"
        idea = cache.get(cache_key)
        
        if idea is None:
            try:
                idea = GeneratedIdea.objects.select_related(
                    'request',
                    'template_used',
                    'template_used__category'
                ).get(id=idea_id)
                
                # Cache for 1 hour
                cache.set(cache_key, idea, 3600)
            except GeneratedIdea.DoesNotExist:
                return None
        
        return idea
    
    @staticmethod
    def invalidate_idea_cache(idea_id: int) -> None:
        """Invalidate cached idea data"""
        cache_key = f"idea_{idea_id}"
        cache.delete(cache_key)
    
    @staticmethod
    def get_cached_user_stats(user_id: int) -> Optional[Dict]:
        """Get cached user statistics"""
        cache_key = f"user_stats_{user_id}"
        return cache.get(cache_key)
    
    @staticmethod
    def set_cached_user_stats(user_id: int, stats: Dict, timeout: int = 900) -> None:
        """Cache user statistics"""
        cache_key = f"user_stats_{user_id}"
        cache.set(cache_key, stats, timeout)


class IdeaValidationService:
    """
    Service for validating idea content and requests
    """
    
    @staticmethod
    def validate_idea_content(idea_data: Dict) -> Dict[str, Any]:
        """
        Validate generated idea content for quality and appropriateness
        """
        validation_results = {
            'is_valid': True,
            'issues': [],
            'warnings': [],
            'quality_score': 0.0
        }
        
        # Check required fields
        required_fields = ['title', 'description']
        for field in required_fields:
            if not idea_data.get(field):
                validation_results['issues'].append(f"Missing required field: {field}")
                validation_results['is_valid'] = False
        
        # Validate title
        title = idea_data.get('title', '')
        if len(title) < 10:
            validation_results['warnings'].append("Title is too short")
        elif len(title) > 300:
            validation_results['issues'].append("Title is too long")
            validation_results['is_valid'] = False
        
        # Validate description
        description = idea_data.get('description', '')
        if len(description) < 50:
            validation_results['warnings'].append("Description is too short")
        elif len(description) > 2000:
            validation_results['warnings'].append("Description is very long")
        
        # Check for inappropriate content (basic filtering)
        inappropriate_keywords = ['violence', 'illegal', 'drugs', 'alcohol abuse']
        content_to_check = f"{title} {description}".lower()
        
        for keyword in inappropriate_keywords:
            if keyword in content_to_check:
                validation_results['issues'].append(f"Potentially inappropriate content detected: {keyword}")
                validation_results['is_valid'] = False
        
        # Calculate quality score
        validation_results['quality_score'] = IdeaValidationService._calculate_content_quality(idea_data)
        
        return validation_results
    
    @staticmethod
    def _calculate_content_quality(idea_data: Dict) -> float:
        """Calculate content quality score"""
        score = 0.0
        
        # Title quality
        title = idea_data.get('title', '')
        if 10 <= len(title) <= 100:
            score += 1.0
        elif title:
            score += 0.5
        
        # Description quality
        description = idea_data.get('description', '')
        if len(description) >= 100:
            score += 2.0
        elif len(description) >= 50:
            score += 1.0
        
        # Detailed plan
        if idea_data.get('detailed_plan'):
            score += 1.0
        
        # Cost and duration info
        if idea_data.get('estimated_cost'):
            score += 0.5
        if idea_data.get('duration'):
            score += 0.5
        
        return min(score, 5.0)
    
    @staticmethod
    def validate_user_request_limits(user: User) -> Dict[str, Any]:
        """
        Validate if user can make more requests based on their subscription
        """
        # Get user's subscription tier
        user_subscription = getattr(user, 'subscription', None)
        
        # Default limits for free users
        daily_limit = 5
        monthly_limit = 50
        
        # Adjust limits based on subscription
        if user_subscription and user_subscription.is_active:
            if user_subscription.tier == 'premium':
                daily_limit = 50
                monthly_limit = 500
            elif user_subscription.tier == 'pro':
                daily_limit = 100
                monthly_limit = 1000
        
        # Check current usage
        today = timezone.now().date()
        current_month_start = today.replace(day=1)
        
        daily_usage = IdeaRequest.objects.filter(
            user=user,
            created_at__date=today
        ).count()
        
        monthly_usage = IdeaRequest.objects.filter(
            user=user,
            created_at__date__gte=current_month_start
        ).count()
        
        return {
            'can_make_request': daily_usage < daily_limit and monthly_usage < monthly_limit,
            'daily_usage': daily_usage,
            'daily_limit': daily_limit,
            'monthly_usage': monthly_usage,
            'monthly_limit': monthly_limit,
            'subscription_tier': user_subscription.tier if user_subscription else 'free'
        }


# Utility functions
def log_idea_interaction(user: User, idea: GeneratedIdea, interaction_type: str, metadata: Dict = None):
    """Log user interactions with ideas for analytics"""
    try:
        from .tasks import log_interaction_async
        log_interaction_async.delay(
            user_id=user.id,
            idea_id=idea.id,
            interaction_type=interaction_type,
            metadata=metadata or {}
        )
    except Exception as e:
        logger.error(f"Failed to log interaction: {str(e)}")


def get_user_preference_keywords(user: User) -> List[str]:
    """Extract keywords from user's past requests for personalization"""
    requests = IdeaRequest.objects.filter(user=user).order_by('-created_at')[:10]
    
    keywords = []
    for request in requests:
        # Extract keywords from various fields
        text_fields = [
            request.occasion,
            request.partner_interests,
            request.user_interests,
            request.special_requirements
        ]
        
        for field in text_fields:
            if field:
                # Simple keyword extraction (in production, use NLP libraries)
                words = re.findall(r'\b\w+\b', field.lower())
                keywords.extend([word for word in words if len(word) > 3])
    
    # Return most common keywords
    from collections import Counter
    common_keywords = Counter(keywords).most_common(20)
    return [keyword for keyword, count in common_keywords if count > 1]