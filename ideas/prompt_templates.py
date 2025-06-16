# apps/ideas/prompt_templates.py
import logging
import json
import re
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from django.db import models
from django.conf import settings
from django.core.cache import cache
from django.template import Template, Context
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import IdeaTemplate, IdeaCategory

logger = logging.getLogger(__name__)


class PromptTemplateType(Enum):
    """Enum for different prompt template types"""
    ROMANTIC = "romantic"
    CASUAL = "casual"
    ADVENTUROUS = "adventurous"
    BUDGET_FRIENDLY = "budget_friendly"
    LUXURIOUS = "luxurious"
    CREATIVE = "creative"
    CULTURAL = "cultural"
    SEASONAL = "seasonal"
    SURPRISE = "surprise"
    ANNIVERSARY = "anniversary"


class OccasionType(Enum):
    """Enum for different occasion types"""
    FIRST_DATE = "first_date"
    DATE_NIGHT = "date_night"
    ANNIVERSARY = "anniversary"
    VALENTINE = "valentine"
    BIRTHDAY = "birthday"
    PROPOSAL = "proposal"
    MAKEUP_DATE = "makeup_date"
    SURPRISE_DATE = "surprise_date"
    HOLIDAY_DATE = "holiday_date"
    SPONTANEOUS = "spontaneous"


class BudgetRange(Enum):
    """Enum for budget ranges"""
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    UNLIMITED = "unlimited"


@dataclass
class PromptContext:
    """Data class for prompt template context"""
    user_location: str = ""
    budget_range: str = "moderate"
    personality_traits: str = ""
    occasion_type: str = "date_night"
    season_info: str = ""
    relationship_duration: str = ""
    partner_interests: str = ""
    user_interests: str = ""
    special_requirements: str = ""
    custom_preferences: Dict[str, Any] = None
    weather_considerations: str = ""
    time_of_day: str = "evening"
    duration_preference: str = "2-4 hours"


class PromptTemplateEngine:
    """
    Advanced prompt template engine for generating personalized AI prompts
    """
    
    def __init__(self):
        self.cache_timeout = getattr(settings, 'PROMPT_CACHE_TIMEOUT', 1800)  # 30 minutes
        self.base_system_prompt = self._load_base_system_prompt()
        self.template_cache = {}
        
    def _load_base_system_prompt(self) -> str:
        """Load the base system prompt for LoveCraft AI"""
        return """You are LoveCraft AI, an expert romantic experience designer with deep knowledge of cultural nuances, local customs, and creative storytelling. You specialize in crafting unforgettable, personalized romantic experiences that feel authentic and magical.

Your responses should be creative, practical, and deeply personalized. Always consider:
- Cultural sensitivity and local customs
- Budget constraints while maximizing value
- Personal interests and personality compatibility
- Seasonal and weather considerations
- Relationship dynamics and appropriate intimacy levels
- Safety and comfort for both partners

Provide detailed, actionable plans that create memorable experiences and stories couples will cherish."""

    def generate_prompt(
        self, 
        template: IdeaTemplate, 
        user_data: Dict[str, Any],
        occasion_type: str = None
    ) -> str:
        """
        Generate a complete AI prompt using template and user data
        
        Args:
            template: IdeaTemplate instance
            user_data: Dictionary containing user preferences and context
            occasion_type: Specific occasion type override
            
        Returns:
            Complete formatted prompt for AI generation
        """
        try:
            # Create prompt context from user data
            context = self._create_prompt_context(user_data, occasion_type)
            
            # Get appropriate template content
            template_content = self._get_template_content(template, context)
            
            # Apply dynamic personalization
            personalized_template = self._personalize_template(template_content, context)
            
            # Generate final prompt
            final_prompt = self._build_final_prompt(personalized_template, context)
            
            # Validate and optimize prompt
            optimized_prompt = self._optimize_prompt(final_prompt)
            
            logger.info(f"Generated prompt for template {template.id}, length: {len(optimized_prompt)}")
            
            return optimized_prompt
            
        except Exception as e:
            logger.error(f"Failed to generate prompt: {str(e)}")
            return self._get_fallback_prompt(user_data)
    
    def _create_prompt_context(self, user_data: Dict[str, Any], occasion_type: str = None) -> PromptContext:
        """Create structured prompt context from user data"""
        return PromptContext(
            user_location=user_data.get('location_city', '') or user_data.get('location_type', ''),
            budget_range=user_data.get('budget', 'moderate'),
            personality_traits=self._combine_personality_data(user_data),
            occasion_type=occasion_type or user_data.get('occasion', 'date_night'),
            season_info=self._get_season_info(),
            relationship_duration=user_data.get('relationship_stage', ''),
            partner_interests=user_data.get('partner_interests', ''),
            user_interests=user_data.get('user_interests', ''),
            special_requirements=user_data.get('special_requirements', ''),
            custom_preferences=user_data.get('custom_preferences', {}),
            weather_considerations=user_data.get('weather', ''),
            time_of_day=user_data.get('time_preference', 'evening'),
            duration_preference=user_data.get('duration', '2-4 hours')
        )
    
    def _combine_personality_data(self, user_data: Dict[str, Any]) -> str:
        """Combine personality type and interests into coherent description"""
        personality_parts = []
        
        if user_data.get('personality_type'):
            personality_parts.append(f"Personality: {user_data['personality_type']}")
        
        if user_data.get('partner_interests'):
            personality_parts.append(f"Partner enjoys: {user_data['partner_interests']}")
            
        if user_data.get('user_interests'):
            personality_parts.append(f"User enjoys: {user_data['user_interests']}")
        
        return "; ".join(personality_parts)
    
    def _get_season_info(self) -> str:
        """Get current season information"""
        now = timezone.now()
        month = now.month
        
        if month in [12, 1, 2]:
            return "Winter - Cold weather, indoor activities preferred, holiday season"
        elif month in [3, 4, 5]:
            return "Spring - Mild weather, blooming flowers, renewal themes"
        elif month in [6, 7, 8]:
            return "Summer - Warm weather, outdoor activities, vacation vibes"
        else:
            return "Autumn - Cool weather, changing colors, cozy atmosphere"
    
    def _get_template_content(self, template: IdeaTemplate, context: PromptContext) -> str:
        """Get template content, using cache when possible"""
        cache_key = f"template_content_{template.id}_{template.updated_at.timestamp()}"
        cached_content = cache.get(cache_key)
        
        if cached_content:
            return cached_content
        
        # Select appropriate template variant based on context
        template_content = self._select_template_variant(template, context)
        
        cache.set(cache_key, template_content, self.cache_timeout)
        return template_content
    
    def _select_template_variant(self, template: IdeaTemplate, context: PromptContext) -> str:
        """Select the most appropriate template variant for the context"""
        # Use the template's content as base
        base_content = template.template_content
        
        # Apply occasion-specific modifications
        if context.occasion_type in [OccasionType.ANNIVERSARY.value, OccasionType.VALENTINE.value]:
            base_content = self._enhance_for_romantic_occasion(base_content)
        elif context.occasion_type == OccasionType.FIRST_DATE.value:
            base_content = self._adjust_for_first_date(base_content)
        elif context.occasion_type == OccasionType.PROPOSAL.value:
            base_content = self._enhance_for_proposal(base_content)
        
        return base_content
    
    def _enhance_for_romantic_occasion(self, content: str) -> str:
        """Enhance template for romantic occasions"""
        romantic_additions = """
Focus on creating deeply romantic and memorable moments. Consider:
- Intimate settings with beautiful ambiance
- Meaningful gestures that show thoughtfulness
- Opportunities for heartfelt conversation
- Elements that celebrate your relationship journey
- Special touches that make the experience uniquely yours
"""
        return content + "\n\n" + romantic_additions
    
    def _adjust_for_first_date(self, content: str) -> str:
        """Adjust template for first date comfort and safety"""
        first_date_guidance = """
For this first date, prioritize:
- Public, comfortable settings where both feel safe
- Activities that allow natural conversation
- Relaxed atmosphere without pressure
- Options to extend or gracefully end the date
- Memorable but not overwhelming experiences
- Respect for boundaries and comfort levels
"""
        return content + "\n\n" + first_date_guidance
    
    def _enhance_for_proposal(self, content: str) -> str:
        """Enhance template for proposal scenarios"""
        proposal_additions = """
This is a proposal experience! Consider:
- A meaningful location with personal significance
- Perfect timing and ambiance for the moment
- Backup plans for weather or unexpected situations
- Ways to incorporate your relationship story
- Opportunities to involve family/friends if desired
- Creating a moment that feels authentic to your love story
- Post-proposal celebration plans
"""
        return content + "\n\n" + proposal_additions
    
    def _personalize_template(self, template_content: str, context: PromptContext) -> str:
        """Apply dynamic personalization to template"""
        # Create Django template for variable substitution
        django_template = Template(template_content)
        
        # Prepare context variables
        template_context = Context({
            'location': context.user_location,
            'budget_range': context.budget_range,
            'personality_traits': context.personality_traits,
            'occasion_type': context.occasion_type,
            'season_info': context.season_info,
            'relationship_duration': context.relationship_duration,
            'partner_interests': context.partner_interests,
            'user_interests': context.user_interests,
            'special_requirements': context.special_requirements,
            'time_of_day': context.time_of_day,
            'duration_preference': context.duration_preference,
            'weather_considerations': context.weather_considerations
        })
        
        # Render template with context
        return django_template.render(template_context)
    
    def _build_final_prompt(self, template_content: str, context: PromptContext) -> str:
        """Build the final complete prompt"""
        # Start with system prompt
        prompt_parts = [self.base_system_prompt]
        
        # Add user context section
        context_section = self._build_context_section(context)
        prompt_parts.append(context_section)
        
        # Add main template content
        prompt_parts.append("**YOUR MISSION:**")
        prompt_parts.append(template_content)
        
        # Add response format requirements
        response_format = self._get_response_format_instructions()
        prompt_parts.append(response_format)
        
        # Add quality guidelines
        quality_guidelines = self._get_quality_guidelines()
        prompt_parts.append(quality_guidelines)
        
        return "\n\n".join(prompt_parts)
    
    def _build_context_section(self, context: PromptContext) -> str:
        """Build the user context section"""
        context_lines = ["**USER CONTEXT:**"]
        
        if context.user_location:
            context_lines.append(f"- Location: {context.user_location}")
        
        context_lines.append(f"- Budget: {context.budget_range}")
        
        if context.personality_traits:
            context_lines.append(f"- Partner's Personality: {context.personality_traits}")
        
        context_lines.append(f"- Occasion: {context.occasion_type}")
        context_lines.append(f"- Season/Weather: {context.season_info}")
        
        if context.relationship_duration:
            context_lines.append(f"- Relationship Stage: {context.relationship_duration}")
        
        if context.special_requirements:
            context_lines.append(f"- Special Requirements: {context.special_requirements}")
        
        context_lines.append(f"- Preferred Duration: {context.duration_preference}")
        context_lines.append(f"- Time of Day: {context.time_of_day}")
        
        return "\n".join(context_lines)
    
    def _get_response_format_instructions(self) -> str:
        """Get standardized response format instructions"""
        return """**RESPONSE FORMAT:**
Provide your response in this exact JSON structure:

{
  "experience_title": "Poetic, memorable title for the experience",
  "main_concept": "One-sentence hook that captures the magic",
  "detailed_plan": {
    "preparation": ["Step-by-step preparation items"],
    "timeline": [
      {
        "time": "Time slot",
        "activity": "What happens",
        "why_special": "What makes this moment magical"
      }
    ],
    "surprise_elements": ["Unexpected touches that elevate the experience"],
    "backup_plans": ["Weather/situation alternatives"]
  },
  "personalization_touches": [
    "Specific ways this reflects their personality/interests"
  ],
  "budget_breakdown": {
    "essential_items": {"item": "estimated_cost"},
    "optional_upgrades": {"item": "estimated_cost"}
  },
  "affiliate_recommendations": {
    "flowers": {
      "suggestion": "Specific flower type/arrangement",
      "why": "Why this choice fits the experience",
      "budget_options": ["Low/Medium/High options"]
    },
    "dining": {
      "suggestion": "Restaurant/dining experience type",
      "alternatives": ["2-3 backup options"],
      "special_requests": "How to make it memorable"
    },
    "gifts": {
      "meaningful_gift": "Thoughtful gift idea",
      "diy_option": "Budget-friendly alternative",
      "luxury_upgrade": "Premium option"
    },
    "experiences": {
      "activity_bookings": "Relevant experience to book",
      "local_services": "Local vendors/services needed"
    }
  },
  "success_tips": [
    "Professional advice for execution",
    "Common mistakes to avoid",
    "How to handle nerves/unexpected situations"
  ],
  "memorable_moments": [
    "Key photo opportunities",
    "Moments to savor and remember"
  ]
}"""
    
    def _get_quality_guidelines(self) -> str:
        """Get quality guidelines for AI responses"""
        return """**CREATIVITY GUIDELINES:**
1. **Think Beyond Clichés**: Avoid generic restaurant-and-roses. Find unique angles.
2. **Cultural Sensitivity**: Research and respect local customs and traditions.
3. **Sensory Details**: Include sounds, scents, textures, not just visuals.
4. **Story Arc**: Create a beginning, middle, and crescendo moment.
5. **Personal Stakes**: Make it feel like it could only happen to THEM.
6. **Practical Magic**: Balance dreaminess with realistic execution.
7. **Emotional Journey**: Consider the emotional beats and pacing.

**QUALITY CHECKS:**
- Would this experience create a story they'll tell for decades?
- Does it feel authentic to their personalities and relationship?
- Is it executable within their budget and location constraints?
- Have you included unique local elements they couldn't get anywhere else?
- Does it build anticipation and deliver multiple "wow" moments?"""
    
    def _optimize_prompt(self, prompt: str) -> str:
        """Optimize prompt for better AI performance"""
        # Remove excessive whitespace
        prompt = re.sub(r'\n\s*\n\s*\n', '\n\n', prompt)
        
        # Ensure proper spacing
        prompt = prompt.strip()
        
        # Add final instruction
        if not prompt.endswith(('.', '!', '?')):
            prompt += "\n\nNow create an extraordinary experience for this couple."
        
        return prompt
    
    def _get_fallback_prompt(self, user_data: Dict[str, Any]) -> str:
        """Generate a basic fallback prompt when template processing fails"""
        occasion = user_data.get('occasion', 'date night')
        budget = user_data.get('budget', 'moderate')
        location = user_data.get('location_city', 'your city')
        
        return f"""Create a personalized {occasion} experience for a couple in {location} with a {budget} budget.

Consider their interests and preferences to design something memorable and authentic.

Please provide a detailed plan in JSON format with:
- Experience title and concept
- Step-by-step timeline
- Budget breakdown
- Personalization elements
- Success tips

Make it creative, practical, and deeply personal to their relationship."""
    
    def get_template_suggestions(self, user_data: Dict[str, Any]) -> List[IdeaTemplate]:
        """Get template suggestions based on user preferences"""
        try:
            # Build query based on user preferences
            queryset = IdeaTemplate.objects.filter(is_active=True)
            
            # Filter by budget if specified
            budget = user_data.get('budget')
            if budget:
                budget_mapping = {
                    'low': ['budget_friendly', 'casual'],
                    'moderate': ['casual', 'romantic', 'creative'],
                    'high': ['luxurious', 'romantic', 'adventurous'],
                    'unlimited': ['luxurious', 'adventurous', 'creative']
                }
                template_types = budget_mapping.get(budget, [])
                if template_types:
                    queryset = queryset.filter(template_type__in=template_types)
            
            # Filter by occasion if specified
            occasion = user_data.get('occasion')
            if occasion:
                queryset = queryset.filter(
                    models.Q(occasions__icontains=occasion) |
                    models.Q(tags__icontains=occasion)
                )
            
            # Order by popularity and rating
            return list(queryset.order_by('-usage_count', '-average_rating')[:10])
            
        except Exception as e:
            logger.error(f"Failed to get template suggestions: {str(e)}")
            return list(IdeaTemplate.objects.filter(is_active=True)[:5])
    
    def validate_template_content(self, template_content: str) -> Dict[str, Any]:
        """Validate template content for required elements"""
        validation_result = {
            'is_valid': True,
            'warnings': [],
            'errors': []
        }
        
        # Check for required placeholders
        required_placeholders = [
            'location', 'budget_range', 'occasion_type'
        ]
        
        for placeholder in required_placeholders:
            if f"{{{{{ placeholder }}}}}" not in template_content:
                validation_result['warnings'].append(
                    f"Missing recommended placeholder: {placeholder}"
                )
        
        # Check template length
        if len(template_content) < 100:
            validation_result['errors'].append("Template content too short")
            validation_result['is_valid'] = False
        
        if len(template_content) > 5000:
            validation_result['warnings'].append("Template content very long, may affect performance")
        
        # Check for potentially problematic content
        problematic_patterns = [
            r'<script.*?</script>',  # Script tags
            r'javascript:',          # JavaScript URLs
            r'data:text/html',       # Data URLs
        ]
        
        for pattern in problematic_patterns:
            if re.search(pattern, template_content, re.IGNORECASE):
                validation_result['errors'].append("Template contains potentially unsafe content")
                validation_result['is_valid'] = False
        
        return validation_result
    
    def create_custom_template(
        self, 
        user_id: int, 
        template_data: Dict[str, Any]
    ) -> IdeaTemplate:
        """Create a custom template for advanced users"""
        try:
            # Validate template content
            validation = self.validate_template_content(template_data.get('content', ''))
            
            if not validation['is_valid']:
                raise ValidationError(f"Invalid template: {', '.join(validation['errors'])}")
            
            # Create template
            template = IdeaTemplate.objects.create(
                name=template_data['name'],
                description=template_data.get('description', ''),
                template_content=template_data['content'],
                template_type=template_data.get('type', 'custom'),
                created_by_id=user_id,
                is_active=True,
                is_premium=template_data.get('is_premium', False)
            )
            
            logger.info(f"Created custom template {template.id} for user {user_id}")
            return template
            
        except Exception as e:
            logger.error(f"Failed to create custom template: {str(e)}")
            raise ValidationError(f"Template creation failed: {str(e)}")
    
    def get_template_analytics(self, template_id: int) -> Dict[str, Any]:
        """Get analytics for a specific template"""
        try:
            template = IdeaTemplate.objects.get(id=template_id)
            
            # Get usage statistics from related models
            from .models import GeneratedIdea
            
            generated_ideas = GeneratedIdea.objects.filter(template_used=template)
            
            analytics = {
                'template_id': template_id,
                'total_uses': template.usage_count,
                'average_rating': float(template.average_rating or 0),
                'success_rate': self._calculate_success_rate(generated_ideas),
                'most_common_occasions': self._get_common_occasions(generated_ideas),
                'budget_distribution': self._get_budget_distribution(generated_ideas),
                'user_feedback_summary': self._get_feedback_summary(generated_ideas),
                'performance_metrics': {
                    'avg_generation_time': self._get_avg_generation_time(generated_ideas),
                    'completion_rate': self._get_completion_rate(generated_ideas),
                }
            }
            
            return analytics
            
        except IdeaTemplate.DoesNotExist:
            return {'error': 'Template not found'}
        except Exception as e:
            logger.error(f"Failed to get template analytics: {str(e)}")
            return {'error': 'Analytics unavailable'}
    
    def _calculate_success_rate(self, generated_ideas) -> float:
        """Calculate success rate based on user feedback"""
        if not generated_ideas.exists():
            return 0.0
        
        total = generated_ideas.count()
        successful = generated_ideas.filter(
            models.Q(user_rating__gte=4) | 
            models.Q(was_executed=True)
        ).count()
        
        return (successful / total) * 100 if total > 0 else 0.0
    
    def _get_common_occasions(self, generated_ideas) -> List[str]:
        """Get most common occasions for this template"""
        # This would require proper aggregation - simplified for example
        occasions = []
        for idea in generated_ideas[:20]:  # Sample
            if hasattr(idea, 'occasion_type'):
                occasions.append(idea.occasion_type)
        
        from collections import Counter
        return [item[0] for item in Counter(occasions).most_common(5)]
    
    def _get_budget_distribution(self, generated_ideas) -> Dict[str, int]:
        """Get budget distribution for generated ideas"""
        distribution = {'low': 0, 'moderate': 0, 'high': 0, 'unlimited': 0}
        
        for idea in generated_ideas:
            if hasattr(idea, 'budget_used'):
                budget = idea.budget_used or 'moderate'
                if budget in distribution:
                    distribution[budget] += 1
        
        return distribution
    
    def _get_feedback_summary(self, generated_ideas) -> Dict[str, Any]:
        """Get summary of user feedback"""
        from .models import IdeaFeedback
        
        feedbacks = IdeaFeedback.objects.filter(
            idea__in=generated_ideas
        )
        
        if not feedbacks.exists():
            return {'average_rating': 0, 'total_feedback': 0}
        
        return {
            'average_rating': feedbacks.aggregate(avg_rating=models.Avg('rating'))['avg_rating'] or 0,
            'total_feedback': feedbacks.count(),
            'positive_feedback_ratio': feedbacks.filter(rating__gte=4).count() / feedbacks.count()
        }
    
    def _get_avg_generation_time(self, generated_ideas) -> float:
        """Get average generation time for ideas using this template"""
        times = [idea.generation_time for idea in generated_ideas if hasattr(idea, 'generation_time') and idea.generation_time]
        return sum(times) / len(times) if times else 0.0
    
    def _get_completion_rate(self, generated_ideas) -> float:
        """Get completion rate (ideas that were actually executed)"""
        if not generated_ideas.exists():
            return 0.0
        
        total = generated_ideas.count()
        completed = generated_ideas.filter(was_executed=True).count()
        
        return (completed / total) * 100 if total > 0 else 0.0


# Utility functions for template management
def get_prompt_engine() -> PromptTemplateEngine:
    """Get singleton prompt template engine instance"""
    if not hasattr(get_prompt_engine, '_instance'):
        get_prompt_engine._instance = PromptTemplateEngine()
    return get_prompt_engine._instance


def validate_prompt_variables(prompt: str, required_vars: List[str]) -> Dict[str, bool]:
    """Validate that prompt contains required variables"""
    results = {}
    for var in required_vars:
        pattern = r'\{\{\s*' + re.escape(var) + r'\s*\}\}'
        results[var] = bool(re.search(pattern, prompt))
    
    return results


def extract_prompt_variables(prompt: str) -> List[str]:
    """Extract all template variables from a prompt"""
    pattern = r'\{\{\s*(\w+)\s*\}\}'
    matches = re.findall(pattern, prompt)
    return list(set(matches))  # Remove duplicates


def sanitize_user_input(user_input: str) -> str:
    """Sanitize user input for safe template processing"""
    if not user_input:
        return ""
    
    # Remove potentially dangerous content
    sanitized = re.sub(r'[<>{}]', '', user_input)
    
    # Limit length
    sanitized = sanitized[:1000]
    
    # Remove excessive whitespace
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    
    return sanitized