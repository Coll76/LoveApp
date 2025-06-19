from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
from .models import (
    AdProvider, AdCategory, AdPlacement, AdCampaign, AdImpression,
    AdClick, AdConversion, AdRevenue, AdBlocker, RewardedAdView,
    AdFrequencyCap
)

User = get_user_model()


class AdProviderSerializer(serializers.ModelSerializer):
    """Serializer for AdProvider model"""
    
    class Meta:
        model = AdProvider
        fields = [
            'id', 'name', 'provider_type', 'is_active', 'priority',
            'revenue_share', 'configuration', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_revenue_share(self, value):
        """Validate revenue share percentage"""
        if not (0 <= value <= 100):
            raise serializers.ValidationError(
                "Revenue share must be between 0 and 100 percent"
            )
        return value


class AdCategorySerializer(serializers.ModelSerializer):
    """Serializer for AdCategory model"""
    children = serializers.SerializerMethodField()
    
    class Meta:
        model = AdCategory
        fields = [
            'id', 'name', 'slug', 'description', 'parent', 'is_active',
            'children', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'children', 'created_at', 'updated_at']
    
    def get_children(self, obj):
        """Get child categories"""
        if hasattr(obj, 'adcategory_set'):
            children = obj.adcategory_set.filter(is_active=True)
            return AdCategorySerializer(children, many=True).data
        return []


class AdPlacementSerializer(serializers.ModelSerializer):
    """Serializer for AdPlacement model"""
    
    class Meta:
        model = AdPlacement
        fields = [
            'id', 'name', 'slug', 'placement_type', 'page_location',
            'width', 'height', 'is_active', 'max_ads_per_view',
            'description', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AdCampaignSerializer(serializers.ModelSerializer):
    """Serializer for AdCampaign model"""
    advertiser_name = serializers.CharField(source='advertiser.get_full_name', read_only=True)
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    placement_name = serializers.CharField(source='placement.name', read_only=True)
    click_through_rate = serializers.ReadOnlyField()
    conversion_rate = serializers.ReadOnlyField()
    is_active = serializers.ReadOnlyField()
    budget_remaining = serializers.SerializerMethodField()
    
    class Meta:
        model = AdCampaign
        fields = [
            'id', 'name', 'slug', 'advertiser', 'advertiser_name',
            'provider', 'provider_name', 'category', 'category_name',
            'placement', 'placement_name', 'title', 'description',
            'call_to_action', 'target_url', 'image_url', 'video_url',
            'campaign_type', 'status', 'budget', 'spent_amount',
            'budget_remaining', 'bid_amount', 'start_date', 'end_date',
            'target_countries', 'target_age_min', 'target_age_max',
            'target_gender', 'target_interests', 'total_impressions',
            'total_clicks', 'total_conversions', 'click_through_rate',
            'conversion_rate', 'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'slug', 'advertiser_name', 'provider_name', 
            'category_name', 'placement_name', 'spent_amount',
            'total_impressions', 'total_clicks', 'total_conversions',
            'click_through_rate', 'conversion_rate', 'is_active',
            'budget_remaining', 'created_at', 'updated_at'
        ]
    
    def get_budget_remaining(self, obj):
        """Calculate remaining budget"""
        return float(obj.budget - obj.spent_amount)
    
    def validate(self, data):
        """Validate campaign data"""
        # Validate dates
        if data.get('end_date') and data.get('start_date'):
            if data['end_date'] <= data['start_date']:
                raise serializers.ValidationError(
                    "End date must be after start date"
                )
        
        # Validate age targeting
        target_age_min = data.get('target_age_min')
        target_age_max = data.get('target_age_max')
        if target_age_min and target_age_max:
            if target_age_min > target_age_max:
                raise serializers.ValidationError(
                    "Minimum age cannot be greater than maximum age"
                )
        
        # Validate budget
        if data.get('budget') and data['budget'] <= 0:
            raise serializers.ValidationError(
                "Budget must be greater than zero"
            )
        
        return data


class AdCampaignListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for campaign lists"""
    advertiser_name = serializers.CharField(source='advertiser.get_full_name', read_only=True)
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    click_through_rate = serializers.ReadOnlyField()
    budget_remaining = serializers.SerializerMethodField()
    
    class Meta:
        model = AdCampaign
        fields = [
            'id', 'name', 'advertiser_name', 'provider_name', 'status',
            'budget', 'spent_amount', 'budget_remaining', 'start_date',
            'end_date', 'total_impressions', 'total_clicks',
            'click_through_rate', 'created_at'
        ]
    
    def get_budget_remaining(self, obj):
        return float(obj.budget - obj.spent_amount)


class AdImpressionSerializer(serializers.ModelSerializer):
    """Serializer for AdImpression model"""
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = AdImpression
        fields = [
            'id', 'campaign', 'campaign_name', 'user', 'user_email',
            'session_id', 'ip_address', 'user_agent', 'referrer',
            'page_url', 'user_country', 'user_age', 'user_gender',
            'viewport_width', 'viewport_height', 'device_type',
            'cost', 'created_at'
        ]
        read_only_fields = ['id', 'campaign_name', 'user_email', 'created_at']


class AdClickSerializer(serializers.ModelSerializer):
    """Serializer for AdClick model"""
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = AdClick
        fields = [
            'id', 'impression', 'campaign', 'campaign_name', 'user',
            'user_email', 'click_position_x', 'click_position_y',
            'time_to_click', 'cost', 'is_valid', 'fraud_score',
            'created_at'
        ]
        read_only_fields = ['id', 'campaign_name', 'user_email', 'created_at']


class AdConversionSerializer(serializers.ModelSerializer):
    """Serializer for AdConversion model"""
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = AdConversion
        fields = [
            'id', 'click', 'campaign', 'campaign_name', 'user',
            'user_email', 'conversion_type', 'conversion_value',
            'time_to_conversion', 'created_at'
        ]
        read_only_fields = ['id', 'campaign_name', 'user_email', 'created_at']


class AdRevenueSerializer(serializers.ModelSerializer):
    """Serializer for AdRevenue model"""
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    provider_name = serializers.CharField(source='provider.name', read_only=True)
    
    class Meta:
        model = AdRevenue
        fields = [
            'id', 'campaign', 'campaign_name', 'provider', 'provider_name',
            'revenue_type', 'gross_revenue', 'net_revenue', 'currency',
            'impressions_count', 'clicks_count', 'conversions_count',
            'period_start', 'period_end', 'created_at'
        ]
        read_only_fields = ['id', 'campaign_name', 'provider_name', 'created_at']


class AdBlockerSerializer(serializers.ModelSerializer):
    """Serializer for AdBlocker model"""
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = AdBlocker
        fields = [
            'id', 'user', 'user_email', 'session_id', 'ip_address',
            'user_agent', 'page_url', 'blocker_detected', 'blocker_type',
            'created_at'
        ]
        read_only_fields = ['id', 'user_email', 'created_at']


class RewardedAdViewSerializer(serializers.ModelSerializer):
    """Serializer for RewardedAdView model"""
    user_email = serializers.CharField(source='user.email', read_only=True)
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    
    class Meta:
        model = RewardedAdView
        fields = [
            'id', 'user', 'user_email', 'campaign', 'campaign_name',
            'reward_type', 'reward_amount', 'reward_granted',
            'view_duration', 'minimum_duration', 'completed',
            'session_id', 'ip_address', 'created_at'
        ]
        read_only_fields = ['id', 'user_email', 'campaign_name', 'created_at']
    
    def validate(self, data):
        """Validate rewarded ad view data"""
        if data.get('view_duration', 0) < 0:
            raise serializers.ValidationError(
                "View duration cannot be negative"
            )
        
        if data.get('minimum_duration', 0) < 0:
            raise serializers.ValidationError(
                "Minimum duration cannot be negative"
            )
        
        return data


class AdFrequencyCapSerializer(serializers.ModelSerializer):
    """Serializer for AdFrequencyCap model"""
    campaign_name = serializers.CharField(source='campaign.name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    can_show_ad = serializers.SerializerMethodField()
    
    class Meta:
        model = AdFrequencyCap
        fields = [
            'id', 'campaign', 'campaign_name', 'user', 'user_email',
            'ip_address', 'impressions_today', 'clicks_today',
            'last_impression', 'last_click', 'max_impressions_per_day',
            'max_clicks_per_day', 'min_time_between_impressions',
            'can_show_ad', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'campaign_name', 'user_email', 'impressions_today',
            'clicks_today', 'last_impression', 'last_click',
            'can_show_ad', 'created_at', 'updated_at'
        ]
    
    def get_can_show_ad(self, obj):
        """Check if ad can be shown"""
        return obj.can_show_ad()


# Analytics Serializers
class CampaignPerformanceSerializer(serializers.Serializer):
    """Serializer for campaign performance analytics"""
    campaign_id = serializers.IntegerField()
    campaign_name = serializers.CharField()
    impressions = serializers.IntegerField()
    clicks = serializers.IntegerField()
    conversions = serializers.IntegerField()
    revenue = serializers.DecimalField(max_digits=10, decimal_places=2)
    ctr = serializers.FloatField()
    conversion_rate = serializers.FloatField()
    cost_per_click = serializers.DecimalField(max_digits=8, decimal_places=4)


class AdAnalyticsSerializer(serializers.Serializer):
    """Serializer for ad analytics summary"""
    total_impressions = serializers.IntegerField()
    total_clicks = serializers.IntegerField()
    total_conversions = serializers.IntegerField()
    total_revenue = serializers.DecimalField(max_digits=10, decimal_places=2)
    average_ctr = serializers.FloatField()
    average_conversion_rate = serializers.FloatField()
    unique_users = serializers.IntegerField()
    ad_blocker_rate = serializers.FloatField()


class AdTargetingSerializer(serializers.Serializer):
    """Serializer for ad targeting requests"""
    placement_slug = serializers.CharField(max_length=100)
    user_id = serializers.IntegerField(required=False, allow_null=True)
    country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    age = serializers.IntegerField(required=False, allow_null=True)
    gender = serializers.CharField(max_length=20, required=False, allow_blank=True)
    interests = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        allow_empty=True
    )
    session_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    ip_address = serializers.IPAddressField()
    user_agent = serializers.CharField(required=False, allow_blank=True)
    page_url = serializers.URLField()
    referrer = serializers.URLField(required=False, allow_blank=True)
    viewport_width = serializers.IntegerField(required=False, allow_null=True)
    viewport_height = serializers.IntegerField(required=False, allow_null=True)
    device_type = serializers.CharField(max_length=50, required=False, allow_blank=True)
    
    def validate_age(self, value):
        """Validate age range"""
        if value is not None and not (13 <= value <= 100):
            raise serializers.ValidationError(
                "Age must be between 13 and 100"
            )
        return value


class AdClickTrackingSerializer(serializers.Serializer):
    """Serializer for ad click tracking"""
    impression_id = serializers.IntegerField()
    click_position_x = serializers.IntegerField(required=False, allow_null=True)
    click_position_y = serializers.IntegerField(required=False, allow_null=True)
    time_to_click = serializers.IntegerField(required=False, allow_null=True)
    
    def validate_time_to_click(self, value):
        """Validate time to click"""
        if value is not None and value < 0:
            raise serializers.ValidationError(
                "Time to click cannot be negative"
            )
        return value


class AdConversionTrackingSerializer(serializers.Serializer):
    """Serializer for ad conversion tracking"""
    click_id = serializers.IntegerField()
    conversion_type = serializers.ChoiceField(choices=AdConversion.CONVERSION_TYPE_CHOICES)
    conversion_value = serializers.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        required=False, 
        allow_null=True
    )
    time_to_conversion = serializers.IntegerField()
    
    def validate_time_to_conversion(self, value):
        """Validate time to conversion"""
        if value < 0:
            raise serializers.ValidationError(
                "Time to conversion cannot be negative"
            )
        return value


class RewardedAdCompletionSerializer(serializers.Serializer):
    """Serializer for rewarded ad completion"""
    rewarded_view_id = serializers.IntegerField()
    view_duration = serializers.IntegerField()
    completed = serializers.BooleanField()
    
    def validate_view_duration(self, value):
        """Validate view duration"""
        if value < 0:
            raise serializers.ValidationError(
                "View duration cannot be negative"
            )
        return value