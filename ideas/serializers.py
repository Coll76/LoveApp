# apps/ideas/serializers.py
from rest_framework import serializers
from rest_framework.validators import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import (
    IdeaCategory, IdeaTemplate, IdeaRequest, GeneratedIdea, 
    IdeaFeedback, IdeaBookmark, IdeaUsageStats, AIModelConfiguration
)
from .validators import validate_idea_request_data

User = get_user_model()


class IdeaCategorySerializer(serializers.ModelSerializer):
    """Serializer for IdeaCategory model"""
    
    class Meta:
        model = IdeaCategory
        fields = [
            'id', 'name', 'slug', 'description', 'icon', 
            'is_active', 'sort_order', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class IdeaTemplateListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for template listings"""
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_icon = serializers.CharField(source='category.icon', read_only=True)
    
    class Meta:
        model = IdeaTemplate
        fields = [
            'id', 'name', 'slug', 'template_type', 'category_name', 
            'category_icon', 'description', 'is_premium', 'usage_count', 
            'average_rating', 'created_at'
        ]


class IdeaTemplateDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for template detail view"""
    category = IdeaCategorySerializer(read_only=True)
    
    class Meta:
        model = IdeaTemplate
        fields = [
            'id', 'name', 'slug', 'template_type', 'category', 
            'prompt_template', 'description', 'is_premium', 'is_active',
            'usage_count', 'average_rating', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'usage_count', 'average_rating', 'created_at', 'updated_at']


class IdeaRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating idea requests"""
    
    class Meta:
        model = IdeaRequest
        fields = [
            'title', 'occasion', 'partner_interests', 'user_interests',
            'personality_type', 'budget', 'location_type', 'location_city',
            'duration', 'special_requirements', 'custom_prompt',
            'ai_model', 'temperature', 'max_tokens'
        ]
        extra_kwargs = {
            'title': {'required': False},
            'ai_model': {'required': False},
            'temperature': {'required': False},
            'max_tokens': {'required': False},
        }
    
    def validate(self, attrs):
        """Validate request data"""
        # Ensure at least some meaningful input is provided
        required_fields = ['partner_interests', 'user_interests', 'custom_prompt']
        if not any(attrs.get(field) for field in required_fields):
            raise ValidationError(
                "Please provide at least partner interests, your interests, or a custom prompt."
            )
        
        # Validate temperature range
        temperature = attrs.get('temperature', 0.7)
        if not 0.0 <= temperature <= 2.0:
            raise ValidationError("Temperature must be between 0.0 and 2.0")
        
        # Validate max_tokens
        max_tokens = attrs.get('max_tokens', 1500)
        if not 100 <= max_tokens <= 4000:
            raise ValidationError("Max tokens must be between 100 and 4000")
            
        return attrs
    
    def create(self, validated_data):
        """Create request with user from context"""
        user = self.context['request'].user
        validated_data['user'] = user
        
        # Set metadata
        request = self.context['request']
        validated_data['ip_address'] = self.get_client_ip(request)
        validated_data['user_agent'] = request.META.get('HTTP_USER_AGENT', '')
        validated_data['session_id'] = request.session.session_key or ''
        
        return super().create(validated_data)
    
    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class GeneratedIdeaSerializer(serializers.ModelSerializer):
    """Serializer for generated ideas"""
    template_name = serializers.CharField(source='template_used.name', read_only=True)
    user_rating = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    user_feedback = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()
    
    class Meta:
        model = GeneratedIdea
        fields = [
            'id', 'title', 'description', 'detailed_plan', 'estimated_cost',
            'duration', 'location_suggestions', 'preparation_tips', 'alternatives',
            'template_name', 'view_count', 'like_count', 'share_count',
            'user_rating', 'user_feedback', 'is_bookmarked', 'created_at'
        ]
        read_only_fields = [
            'id', 'view_count', 'like_count', 'share_count', 'created_at'
        ]
    
    def get_user_feedback(self, obj):
        """Get current user's feedback for this idea"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
        
        try:
            feedback = IdeaFeedback.objects.get(
                user=request.user,
                idea=obj,
                feedback_type='rating'
            )
            return {
                'rating': feedback.rating,
                'created_at': feedback.created_at
            }
        except IdeaFeedback.DoesNotExist:
            return None
    
    def get_is_bookmarked(self, obj):
        """Check if idea is bookmarked by current user"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        
        return IdeaBookmark.objects.filter(
            user=request.user,
            idea=obj
        ).exists()


class IdeaRequestSerializer(serializers.ModelSerializer):
    """Serializer for idea request with generated ideas"""
    generated_ideas = GeneratedIdeaSerializer(many=True, read_only=True)
    processing_time = serializers.SerializerMethodField()
    
    class Meta:
        model = IdeaRequest
        fields = [
            'id', 'title', 'occasion', 'partner_interests', 'user_interests',
            'personality_type', 'budget', 'location_type', 'location_city',
            'duration', 'special_requirements', 'custom_prompt', 'status',
            'processing_started_at', 'processing_completed_at', 'processing_time',
            'error_message', 'retry_count', 'generated_ideas', 'created_at'
        ]
        read_only_fields = [
            'id', 'status', 'processing_started_at', 'processing_completed_at',
            'error_message', 'retry_count', 'created_at'
        ]
    
    def get_processing_time(self, obj):
        """Get processing time in seconds"""
        return obj.get_processing_time()


class IdeaFeedbackCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating idea feedback"""
    
    class Meta:
        model = IdeaFeedback
        fields = ['feedback_type', 'rating', 'comment', 'report_reason']
        extra_kwargs = {
            'rating': {'required': False},
            'comment': {'required': False},
            'report_reason': {'required': False},
        }
    
    def validate(self, attrs):
        """Validate feedback data based on type"""
        feedback_type = attrs.get('feedback_type')
        
        if feedback_type == 'rating':
            if not attrs.get('rating'):
                raise ValidationError("Rating is required for rating feedback")
            if not 1 <= attrs.get('rating', 0) <= 5:
                raise ValidationError("Rating must be between 1 and 5")
        
        elif feedback_type == 'comment':
            if not attrs.get('comment'):
                raise ValidationError("Comment is required for comment feedback")
        
        elif feedback_type == 'report':
            if not attrs.get('report_reason'):
                raise ValidationError("Report reason is required for report feedback")
        
        return attrs
    
    def create(self, validated_data):
        """Create feedback with user and idea from context"""
        validated_data['user'] = self.context['request'].user
        validated_data['idea'] = self.context['idea']
        
        # Set metadata
        request = self.context['request']
        validated_data['ip_address'] = self.get_client_ip(request)
        validated_data['user_agent'] = request.META.get('HTTP_USER_AGENT', '')
        
        return super().create(validated_data)
    
    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


class IdeaFeedbackSerializer(serializers.ModelSerializer):
    """Serializer for reading idea feedback"""
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = IdeaFeedback
        fields = [
            'id', 'user_email', 'feedback_type', 'rating', 'comment',
            'report_reason', 'created_at'
        ]
        read_only_fields = ['id', 'user_email', 'created_at']


class IdeaBookmarkCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating bookmarks"""
    
    class Meta:
        model = IdeaBookmark
        fields = ['notes']
        extra_kwargs = {
            'notes': {'required': False},
        }
    
    def create(self, validated_data):
        """Create bookmark with user and idea from context"""
        validated_data['user'] = self.context['request'].user
        validated_data['idea'] = self.context['idea']
        return super().create(validated_data)


class IdeaBookmarkSerializer(serializers.ModelSerializer):
    """Serializer for reading bookmarks"""
    idea = GeneratedIdeaSerializer(read_only=True)
    
    class Meta:
        model = IdeaBookmark
        fields = ['id', 'idea', 'notes', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class IdeaUsageStatsSerializer(serializers.ModelSerializer):
    """Serializer for usage statistics"""
    success_rate = serializers.SerializerMethodField()
    
    class Meta:
        model = IdeaUsageStats
        fields = [
            'date', 'total_requests', 'successful_generations', 'failed_generations',
            'success_rate', 'total_users', 'free_tier_requests', 'premium_requests',
            'average_rating', 'total_tokens_used'
        ]
    
    def get_success_rate(self, obj):
        """Calculate success rate percentage"""
        if obj.total_requests == 0:
            return 0.0
        return round((obj.successful_generations / obj.total_requests) * 100, 2)


class AIModelConfigurationSerializer(serializers.ModelSerializer):
    """Serializer for AI model configurations"""
    
    class Meta:
        model = AIModelConfiguration
        fields = [
            'id', 'name', 'provider', 'model_id', 'is_active', 'is_premium_only',
            'max_tokens', 'temperature', 'cost_per_1k_tokens', 'priority'
        ]
        read_only_fields = ['id']


class QuickIdeaRequestSerializer(serializers.Serializer):
    """Simplified serializer for quick idea requests"""
    interests = serializers.CharField(max_length=500, required=True)
    budget = serializers.ChoiceField(
        choices=IdeaRequest.BUDGET_CHOICES,
        default='moderate'
    )
    location_type = serializers.ChoiceField(
        choices=IdeaRequest.LOCATION_TYPE_CHOICES,
        default='any'
    )
    location_city = serializers.CharField(max_length=100, required=False)
    
    def validate_interests(self, value):
        """Validate interests field"""
        if len(value.strip()) < 10:
            raise ValidationError("Please provide more detailed interests (at least 10 characters)")
        return value.strip()


class IdeaSearchSerializer(serializers.Serializer):
    """Serializer for idea search requests"""
    query = serializers.CharField(max_length=200, required=True)
    budget = serializers.ChoiceField(
        choices=IdeaRequest.BUDGET_CHOICES,
        required=False
    )
    location_type = serializers.ChoiceField(
        choices=IdeaRequest.LOCATION_TYPE_CHOICES,
        required=False
    )
    min_rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=2,
        min_value=1.0,
        max_value=5.0,
        required=False
    )
    
    def validate_query(self, value):
        """Validate search query"""
        if len(value.strip()) < 3:
            raise ValidationError("Search query must be at least 3 characters long")
        return value.strip()


class UserIdeaStatsSerializer(serializers.Serializer):
    """Serializer for user idea statistics"""
    total_requests = serializers.IntegerField()
    successful_requests = serializers.IntegerField()
    failed_requests = serializers.IntegerField()
    total_ideas_generated = serializers.IntegerField()
    average_rating_given = serializers.DecimalField(max_digits=3, decimal_places=2)
    favorite_budget = serializers.CharField()
    favorite_location_type = serializers.CharField()
    bookmarks_count = serializers.IntegerField()
    feedback_count = serializers.IntegerField()
    last_request_date = serializers.DateTimeField()


class BulkIdeaFeedbackSerializer(serializers.Serializer):
    """Serializer for bulk feedback operations"""
    feedback_data = serializers.ListField(
        child=serializers.DictField(),
        max_length=50  # Limit bulk operations
    )
    
    def validate_feedback_data(self, value):
        """Validate each feedback item"""
        for item in value:
            if 'idea_id' not in item or 'feedback_type' not in item:
                raise ValidationError("Each item must have 'idea_id' and 'feedback_type'")
            
            feedback_type = item['feedback_type']
            if feedback_type == 'rating' and 'rating' not in item:
                raise ValidationError("Rating feedback must include 'rating' field")
        
        return value