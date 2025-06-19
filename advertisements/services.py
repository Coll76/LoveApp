#advertisementa/services.py
from django.db import transaction, models
from django.utils import timezone
from django.core.cache import cache
from django.db.models import Q, F, Sum, Count, Avg, Case, When, Value, DecimalField
from django.conf import settings
from decimal import Decimal
from datetime import timedelta, datetime
import logging
import uuid
from typing import List, Dict, Optional, Tuple, Any
import json
from dataclasses import dataclass

from .models import (
    AdProvider, AdCampaign, AdImpression, AdClick, AdConversion,
    AdRevenue, AdBlocker, RewardedAdView, AdFrequencyCap, AdPlacement
)
from .ad_providers import AdProviderRegistry

logger = logging.getLogger(__name__)


@dataclass
class AdTargetingCriteria:
    """Data class for ad targeting criteria"""
    placement_slug: str
    user_id: Optional[int] = None
    country: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    interests: Optional[List[str]] = None
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    user_agent: Optional[str] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    viewport_width: Optional[int] = None
    viewport_height: Optional[int] = None
    device_type: Optional[str] = None


class AdTargetingService:
    """Service for ad targeting and selection"""
    
    CACHE_TIMEOUT = 300  # 5 minutes
    
    @staticmethod
    def get_targeted_campaigns(
        targeting_criteria: AdTargetingCriteria,
        limit: int = 5
    ) -> List[AdCampaign]:
        """
        Get campaigns targeted to specific user criteria with enhanced targeting logic
        """
        cache_key = AdTargetingService._generate_cache_key(targeting_criteria)
        cached_result = cache.get(cache_key)
        
        if cached_result and settings.DEBUG is False:
            return cached_result
        
        try:
            # Get active campaigns for placement
            campaigns = AdCampaign.objects.select_related(
                'provider', 'category', 'placement'
            ).filter(
                placement__slug=targeting_criteria.placement_slug,
                status='active',
                start_date__lte=timezone.now(),
                end_date__gte=timezone.now(),
                spent_amount__lt=F('budget')
            )
            
            # Apply geographic targeting
            if targeting_criteria.country:
                campaigns = campaigns.filter(
                    Q(target_countries__isnull=True) |
                    Q(target_countries__contains=[targeting_criteria.country])
                )
            
            # Apply age targeting
            if targeting_criteria.age:
                campaigns = campaigns.filter(
                    Q(target_age_min__isnull=True) |
                    Q(target_age_max__isnull=True) |
                    Q(target_age_min__lte=targeting_criteria.age, 
                      target_age_max__gte=targeting_criteria.age)
                )
            
            # Apply gender targeting
            if targeting_criteria.gender:
                campaigns = campaigns.filter(
                    Q(target_gender__isnull=True) |
                    Q(target_gender='') |
                    Q(target_gender=targeting_criteria.gender)
                )
            
            # Apply interest targeting
            if targeting_criteria.interests:
                for interest in targeting_criteria.interests:
                    campaigns = campaigns.filter(
                        Q(target_interests__isnull=True) |
                        Q(target_interests__contains=[interest])
                    )
            
            # Apply frequency capping
            if targeting_criteria.user_id or targeting_criteria.ip_address:
                campaigns = AdTargetingService._apply_frequency_caps(
                    campaigns, targeting_criteria.user_id, targeting_criteria.ip_address
                )
            
            # Apply device type targeting
            if targeting_criteria.device_type:
                campaigns = AdTargetingService._apply_device_targeting(
                    campaigns, targeting_criteria.device_type
                )
            
            # Calculate relevance scores and order campaigns
            campaigns = AdTargetingService._calculate_relevance_scores(
                campaigns, targeting_criteria
            )
            
            result = list(campaigns[:limit])
            
            # Cache the result
            if settings.DEBUG is False:
                cache.set(cache_key, result, AdTargetingService.CACHE_TIMEOUT)
            
            return result
            
        except Exception as e:
            logger.error(f"Error in ad targeting: {str(e)}", exc_info=True)
            return []
    
    @staticmethod
    def _generate_cache_key(criteria: AdTargetingCriteria) -> str:
        """Generate cache key for targeting criteria"""
        key_data = {
            'placement': criteria.placement_slug,
            'country': criteria.country,
            'age': criteria.age,
            'gender': criteria.gender,
            'interests': sorted(criteria.interests) if criteria.interests else None,
            'device': criteria.device_type
        }
        return f"ad_targeting:{hash(str(key_data))}"
    
    @staticmethod
    def _apply_frequency_caps(campaigns, user_id: Optional[int], ip_address: Optional[str]):
        """Apply frequency capping to campaigns"""
        if not (user_id or ip_address):
            return campaigns
        
        valid_campaign_ids = []
        today = timezone.now().date()
        
        for campaign in campaigns:
            # Check frequency cap
            freq_cap_query = Q(campaign=campaign)
            if user_id:
                freq_cap_query &= Q(user_id=user_id)
            if ip_address:
                freq_cap_query |= Q(ip_address=ip_address)
            
            freq_cap = AdFrequencyCap.objects.filter(freq_cap_query).first()
            
            if not freq_cap:
                valid_campaign_ids.append(campaign.id)
                continue
            
            # Check if within limits
            if freq_cap.can_show_ad():
                valid_campaign_ids.append(campaign.id)
        
        return campaigns.filter(id__in=valid_campaign_ids)
    
    @staticmethod
    def _apply_device_targeting(campaigns, device_type: str):
        """Apply device-specific targeting"""
        # This could be enhanced based on campaign device preferences
        # For now, we'll just return all campaigns
        return campaigns
    
    @staticmethod
    def _calculate_relevance_scores(campaigns, criteria: AdTargetingCriteria):
        """Calculate relevance scores for campaigns"""
        return campaigns.annotate(
            priority_score=Case(
                When(provider__priority=1, then=Value(10)),
                When(provider__priority=2, then=Value(8)),
                When(provider__priority=3, then=Value(6)),
                default=Value(4),
                output_field=models.IntegerField()
            ),
            budget_score=Case(
                When(spent_amount__lt=F('budget') * 0.3, then=Value(10)),
                When(spent_amount__lt=F('budget') * 0.6, then=Value(8)),
                When(spent_amount__lt=F('budget') * 0.8, then=Value(6)),
                default=Value(2),
                output_field=models.IntegerField()
            ),
            performance_score=Case(
                When(total_clicks__gt=100, then=F('total_clicks') / F('total_impressions') * 100),
                default=Value(5),
                output_field=models.IntegerField()
            )
        ).annotate(
            total_score=F('priority_score') + F('budget_score') + F('performance_score')
        ).order_by('-total_score', '-bid_amount', '-created_at')


class AdImpressionService:
    """Service for tracking ad impressions"""
    
    @staticmethod
    @transaction.atomic
    def record_impression(
        campaign: AdCampaign,
        targeting_criteria: AdTargetingCriteria
    ) -> Optional[AdImpression]:
        """
        Record an ad impression with comprehensive tracking
        """
        try:
            # Validate campaign can serve ads
            if not AdImpressionService._can_serve_ad(campaign):
                logger.warning(f"Campaign {campaign.id} cannot serve ads")
                return None
            
            # Calculate cost based on campaign type
            cost = AdImpressionService._calculate_impression_cost(campaign)
            
            # Create impression record
            impression = AdImpression.objects.create(
                campaign=campaign,
                user_id=targeting_criteria.user_id,
                session_id=targeting_criteria.session_id or str(uuid.uuid4()),
                ip_address=targeting_criteria.ip_address or '',
                user_agent=targeting_criteria.user_agent or '',
                referrer=targeting_criteria.referrer or '',
                page_url=targeting_criteria.page_url or '',
                user_country=targeting_criteria.country or '',
                user_age=targeting_criteria.age,
                user_gender=targeting_criteria.gender or '',
                viewport_width=targeting_criteria.viewport_width,
                viewport_height=targeting_criteria.viewport_height,
                device_type=targeting_criteria.device_type or '',
                cost=cost
            )
            
            # Update campaign stats atomically
            AdCampaign.objects.filter(id=campaign.id).update(
                total_impressions=F('total_impressions') + 1,
                spent_amount=F('spent_amount') + cost
            )
            
            # Update or create frequency cap
            AdImpressionService._update_frequency_cap(
                campaign, targeting_criteria.user_id, targeting_criteria.ip_address
            )
            
            # Update provider-specific tracking
            AdImpressionService._notify_ad_provider(campaign, impression)
            
            logger.info(f"Recorded impression for campaign {campaign.id}")
            return impression
            
        except Exception as e:
            logger.error(f"Error recording impression: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def _can_serve_ad(campaign: AdCampaign) -> bool:
        """Check if campaign can serve ads"""
        now = timezone.now()
        return (
            campaign.status == 'active' and
            campaign.start_date <= now and
            campaign.end_date >= now and
            campaign.spent_amount < campaign.budget
        )
    
    @staticmethod
    def _calculate_impression_cost(campaign: AdCampaign) -> Decimal:
        """Calculate cost for impression based on campaign type"""
        if campaign.campaign_type == 'cpm':
            return campaign.bid_amount / 1000  # CPM = cost per 1000 impressions
        elif campaign.campaign_type in ['cpc', 'cpa']:
            return Decimal('0.00')  # No cost for impression in CPC/CPA
        else:
            return campaign.bid_amount / 1000
    
    @staticmethod
    def _update_frequency_cap(
        campaign: AdCampaign,
        user_id: Optional[int],
        ip_address: Optional[str]
    ):
        """Update frequency cap for user/IP"""
        try:
            defaults = {
                'max_impressions_per_day': 10,
                'max_clicks_per_day': 3,
                'min_time_between_impressions': 300
            }
            
            if user_id:
                freq_cap, created = AdFrequencyCap.objects.get_or_create(
                    campaign=campaign,
                    user_id=user_id,
                    defaults=defaults
                )
            elif ip_address:
                freq_cap, created = AdFrequencyCap.objects.get_or_create(
                    campaign=campaign,
                    ip_address=ip_address,
                    defaults=defaults
                )
            else:
                return
            
            # Update impression count and timestamp
            today = timezone.now().date()
            if freq_cap.last_impression and freq_cap.last_impression.date() == today:
                freq_cap.impressions_today += 1
            else:
                freq_cap.impressions_today = 1
            
            freq_cap.last_impression = timezone.now()
            freq_cap.save()
            
        except Exception as e:
            logger.error(f"Error updating frequency cap: {str(e)}")
    
    @staticmethod
    def _notify_ad_provider(campaign: AdCampaign, impression: AdImpression):
        """Notify ad provider about impression"""
        try:
            provider_registry = AdProviderRegistry()
            provider_instance = provider_registry.get_provider(campaign.provider.provider_type)
            
            if provider_instance and hasattr(provider_instance, 'track_impression'):
                provider_instance.track_impression(campaign, impression)
                
        except Exception as e:
            logger.error(f"Error notifying ad provider: {str(e)}")


class AdClickService:
    """Service for tracking ad clicks with fraud detection"""
    
    @staticmethod
    @transaction.atomic
    def record_click(
        impression: AdImpression,
        click_position_x: Optional[int] = None,
        click_position_y: Optional[int] = None,
        time_to_click: Optional[int] = None
    ) -> Optional[AdClick]:
        """
        Record an ad click with fraud detection
        """
        try:
            # Calculate cost based on campaign type
            cost = AdClickService._calculate_click_cost(impression.campaign)
            
            # Detect potential fraud
            is_valid, fraud_score = AdClickService._detect_fraud(
                impression, time_to_click, click_position_x, click_position_y
            )
            
            # Create click record
            click = AdClick.objects.create(
                impression=impression,
                campaign=impression.campaign,
                user=impression.user,
                click_position_x=click_position_x,
                click_position_y=click_position_y,
                time_to_click=time_to_click,
                cost=cost,
                is_valid=is_valid,
                fraud_score=fraud_score
            )
            
            # Update campaign stats only if valid click
            if is_valid:
                AdCampaign.objects.filter(id=impression.campaign.id).update(
                    total_clicks=F('total_clicks') + 1,
                    spent_amount=F('spent_amount') + cost
                )
                
                # Update frequency cap
                AdClickService._update_frequency_cap_click(
                    impression.campaign, impression.user, impression.ip_address
                )
                
                # Notify ad provider
                AdClickService._notify_ad_provider(impression.campaign, click)
            
            logger.info(f"Recorded {'valid' if is_valid else 'invalid'} click for campaign {impression.campaign.id}")
            return click
            
        except Exception as e:
            logger.error(f"Error recording click: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def _calculate_click_cost(campaign: AdCampaign) -> Decimal:
        """Calculate cost for click based on campaign type"""
        if campaign.campaign_type == 'cpc':
            return campaign.bid_amount
        elif campaign.campaign_type in ['cpm', 'cpa']:
            return Decimal('0.00')  # No additional cost for click
        else:
            return campaign.bid_amount
    
    @staticmethod
    def _detect_fraud(
        impression: AdImpression,
        time_to_click: Optional[int],
        click_x: Optional[int],
        click_y: Optional[int]
    ) -> Tuple[bool, Optional[Decimal]]:
        """
        Enhanced fraud detection logic
        """
        fraud_score = Decimal('0.0')
        
        # Check for very quick clicks (less than 100ms)
        if time_to_click and time_to_click < 100:
            fraud_score += Decimal('4.0')
        elif time_to_click and time_to_click < 500:
            fraud_score += Decimal('2.0')
        
        # Check for click position anomalies
        if click_x is not None and click_y is not None:
            if click_x < 0 or click_y < 0:
                fraud_score += Decimal('3.0')
        
        # Check for multiple clicks from same IP in short time
        recent_clicks = AdClick.objects.filter(
            impression__ip_address=impression.ip_address,
            created_at__gte=timezone.now() - timedelta(minutes=5)
        ).count()
        
        if recent_clicks > 10:
            fraud_score += Decimal('5.0')
        elif recent_clicks > 5:
            fraud_score += Decimal('3.0')
        
        # Check for suspicious user agent patterns
        if not impression.user_agent or len(impression.user_agent) < 20:
            fraud_score += Decimal('2.0')
        
        # Check for bot-like patterns
        if impression.user_agent and any(bot in impression.user_agent.lower() for bot in ['bot', 'crawler', 'spider']):
            fraud_score += Decimal('5.0')
        
        # Check time between impression and click
        time_diff = (timezone.now() - impression.created_at).total_seconds()
        if time_diff > 3600:  # More than 1 hour
            fraud_score += Decimal('2.0')
        
        is_valid = fraud_score < Decimal('5.0')
        
        return is_valid, fraud_score if fraud_score > 0 else None
    
    @staticmethod
    def _update_frequency_cap_click(campaign: AdCampaign, user, ip_address: str):
        """Update frequency cap for click"""
        try:
            if user:
                freq_cap = AdFrequencyCap.objects.filter(
                    campaign=campaign, user=user
                ).first()
            elif ip_address:
                freq_cap = AdFrequencyCap.objects.filter(
                    campaign=campaign, ip_address=ip_address
                ).first()
            else:
                return
            
            if freq_cap:
                today = timezone.now().date()
                if freq_cap.last_click and freq_cap.last_click.date() == today:
                    freq_cap.clicks_today += 1
                else:
                    freq_cap.clicks_today = 1
                
                freq_cap.last_click = timezone.now()
                freq_cap.save()
                
        except Exception as e:
            logger.error(f"Error updating frequency cap for click: {str(e)}")
    
    @staticmethod
    def _notify_ad_provider(campaign: AdCampaign, click: AdClick):
        """Notify ad provider about click"""
        try:
            provider_registry = AdProviderRegistry()
            provider_instance = provider_registry.get_provider(campaign.provider.provider_type)
            
            if provider_instance and hasattr(provider_instance, 'track_click'):
                provider_instance.track_click(campaign, click)
                
        except Exception as e:
            logger.error(f"Error notifying ad provider: {str(e)}")


class AdConversionService:
    """Service for tracking ad conversions"""
    
    @staticmethod
    @transaction.atomic
    def record_conversion(
        click: AdClick,
        conversion_type: str,
        conversion_value: Optional[Decimal] = None,
        time_to_conversion: int = 0
    ) -> Optional[AdConversion]:
        """
        Record an ad conversion with validation
        """
        try:
            # Validate conversion
            if not AdConversionService._validate_conversion(click, conversion_type):
                logger.warning(f"Invalid conversion attempt for click {click.id}")
                return None
            
            # Create conversion record
            conversion = AdConversion.objects.create(
                click=click,
                campaign=click.campaign,
                user=click.user,
                conversion_type=conversion_type,
                conversion_value=conversion_value,
                time_to_conversion=time_to_conversion
            )
            
            # Update campaign stats
            AdCampaign.objects.filter(id=click.campaign.id).update(
                total_conversions=F('total_conversions') + 1
            )
            
            # Calculate and update CPA cost if applicable
            if click.campaign.campaign_type == 'cpa':
                cost = click.campaign.bid_amount
                AdCampaign.objects.filter(id=click.campaign.id).update(
                    spent_amount=F('spent_amount') + cost
                )
            
            # Notify ad provider
            AdConversionService._notify_ad_provider(click.campaign, conversion)
            
            logger.info(f"Recorded conversion for campaign {click.campaign.id}")
            return conversion
            
        except Exception as e:
            logger.error(f"Error recording conversion: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def _validate_conversion(click: AdClick, conversion_type: str) -> bool:
        """Validate conversion legitimacy"""
        # Check if click is valid
        if not click.is_valid:
            return False
        
        # Check if conversion already exists for this click
        existing_conversion = AdConversion.objects.filter(
            click=click,
            conversion_type=conversion_type
        ).exists()
        
        if existing_conversion:
            return False
        
        # Check time limits (e.g., conversion must happen within 30 days)
        time_limit = timezone.now() - timedelta(days=30)
        if click.created_at < time_limit:
            return False
        
        return True
    
    @staticmethod
    def _notify_ad_provider(campaign: AdCampaign, conversion: AdConversion):
        """Notify ad provider about conversion"""
        try:
            provider_registry = AdProviderRegistry()
            provider_instance = provider_registry.get_provider(campaign.provider.provider_type)
            
            if provider_instance and hasattr(provider_instance, 'track_conversion'):
                provider_instance.track_conversion(campaign, conversion)
                
        except Exception as e:
            logger.error(f"Error notifying ad provider: {str(e)}")


class RewardedAdService:
    """Service for handling rewarded ad views"""
    
    @staticmethod
    @transaction.atomic
    def start_rewarded_view(
        user_id: int,
        campaign: AdCampaign,
        reward_type: str,
        reward_amount: Decimal,
        minimum_duration: int,
        session_id: str,
        ip_address: str
    ) -> Optional[RewardedAdView]:
        """
        Start a rewarded ad view session
        """
        try:
            # Check if user is eligible for reward
            if not RewardedAdService._is_eligible_for_reward(user_id, campaign):
                logger.warning(f"User {user_id} not eligible for reward")
                return None
            
            # Create rewarded ad view record
            rewarded_view = RewardedAdView.objects.create(
                user_id=user_id,
                campaign=campaign,
                reward_type=reward_type,
                reward_amount=reward_amount,
                reward_granted=False,
                view_duration=0,
                minimum_duration=minimum_duration,
                completed=False,
                session_id=session_id,
                ip_address=ip_address
            )
            
            logger.info(f"Started rewarded ad view for user {user_id}")
            return rewarded_view
            
        except Exception as e:
            logger.error(f"Error starting rewarded ad view: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    @transaction.atomic
    def complete_rewarded_view(
        rewarded_view_id: int,
        view_duration: int,
        completed: bool
    ) -> bool:
        """
        Complete a rewarded ad view and grant reward if applicable
        """
        try:
            rewarded_view = RewardedAdView.objects.select_for_update().get(
                id=rewarded_view_id
            )
            
            # Update view details
            rewarded_view.view_duration = view_duration
            rewarded_view.completed = completed
            
            # Grant reward if conditions are met
            if completed and view_duration >= rewarded_view.minimum_duration:
                rewarded_view.reward_granted = True
                
                # Here you would integrate with your reward system
                # For example, adding points to user account, etc.
                RewardedAdService._grant_reward(rewarded_view)
                
                # Update campaign stats
                AdCampaign.objects.filter(id=rewarded_view.campaign.id).update(
                    total_impressions=F('total_impressions') + 1
                )
            
            rewarded_view.save()
            
            logger.info(f"Completed rewarded ad view {rewarded_view_id}")
            return rewarded_view.reward_granted
            
        except RewardedAdView.DoesNotExist:
            logger.error(f"Rewarded ad view {rewarded_view_id} not found")
            return False
        except Exception as e:
            logger.error(f"Error completing rewarded ad view: {str(e)}", exc_info=True)
            return False
    
    @staticmethod
    def _is_eligible_for_reward(user_id: int, campaign: AdCampaign) -> bool:
        """Check if user is eligible for reward"""
        # Check daily limits
        today = timezone.now().date()
        daily_rewards = RewardedAdView.objects.filter(
            user_id=user_id,
            campaign=campaign,
            reward_granted=True,
            created_at__date=today
        ).count()
        
        # Limit to 5 rewards per day per campaign
        if daily_rewards >= 5:
            return False
        
        # Check if user has recent pending rewarded view
        pending_view = RewardedAdView.objects.filter(
            user_id=user_id,
            campaign=campaign,
            completed=False,
            created_at__gte=timezone.now() - timedelta(minutes=10)
        ).exists()
        
        if pending_view:
            return False
        
        return True
    
    @staticmethod
    def _grant_reward(rewarded_view: RewardedAdView):
        """Grant reward to user"""
        # This is where you would integrate with your reward system
        # For example:
        # - Add points to user account
        # - Unlock premium features
        # - Add virtual currency
        # - Send notification to user
        
        logger.info(f"Granted {rewarded_view.reward_amount} {rewarded_view.reward_type} to user {rewarded_view.user_id}")


class AdBlockerService:
    """Service for tracking ad blocker usage"""
    
    @staticmethod
    def record_blocker_detection(
        user_id: Optional[int],
        session_id: str,
        ip_address: str,
        user_agent: str,
        page_url: str,
        blocker_detected: bool,
        blocker_type: str = ''
    ) -> AdBlocker:
        """
        Record ad blocker detection
        """
        try:
            ad_blocker = AdBlocker.objects.create(
                user_id=user_id,
                session_id=session_id,
                ip_address=ip_address,
                user_agent=user_agent,
                page_url=page_url,
                blocker_detected=blocker_detected,
                blocker_type=blocker_type
            )
            
            logger.info(f"Recorded ad blocker detection: {blocker_detected}")
            return ad_blocker
            
        except Exception as e:
            logger.error(f"Error recording ad blocker detection: {str(e)}", exc_info=True)
            raise
        
# Complete the missing AdRevenueService methods from services.py

class AdRevenueService:
    """Service for tracking and calculating ad revenue"""
    
    @staticmethod
    @transaction.atomic
    def calculate_revenue(
        campaign: AdCampaign,
        period_start: datetime,
        period_end: datetime
    ) -> Optional[AdRevenue]:
        """
        Calculate revenue for a campaign in a specific period
        """
        try:
            # Get campaign statistics for the period
            impressions = AdImpression.objects.filter(
                campaign=campaign,
                created_at__range=(period_start, period_end)
            )
            
            clicks = AdClick.objects.filter(
                campaign=campaign,
                is_valid=True,
                created_at__range=(period_start, period_end)
            )
            
            conversions = AdConversion.objects.filter(
                campaign=campaign,
                created_at__range=(period_start, period_end)
            )
            
            # Calculate counts
            impressions_count = impressions.count()
            clicks_count = clicks.count()
            conversions_count = conversions.count()
            
            # Calculate gross revenue
            impression_revenue = impressions.aggregate(
                total=Sum('cost', output_field=DecimalField())
            )['total'] or Decimal('0')
            
            click_revenue = clicks.aggregate(
                total=Sum('cost', output_field=DecimalField())
            )['total'] or Decimal('0')
            
            conversion_revenue = Decimal('0')
            if campaign.campaign_type == 'cpa':
                conversion_revenue = conversions_count * campaign.bid_amount
            
            gross_revenue = impression_revenue + click_revenue + conversion_revenue
            
            # Calculate net revenue (after provider's share)
            provider_share = campaign.provider.revenue_share / 100
            net_revenue = gross_revenue * (1 - provider_share)
            
            # Create or update revenue record
            revenue, created = AdRevenue.objects.get_or_create(
                campaign=campaign,
                provider=campaign.provider,
                period_start=period_start,
                period_end=period_end,
                defaults={
                    'revenue_type': 'calculated',
                    'gross_revenue': gross_revenue,
                    'net_revenue': net_revenue,
                    'currency': 'USD',  # or get from settings
                    'impressions_count': impressions_count,
                    'clicks_count': clicks_count,
                    'conversions_count': conversions_count
                }
            )
            
            if not created:
                # Update existing record
                revenue.gross_revenue = gross_revenue
                revenue.net_revenue = net_revenue
                revenue.impressions_count = impressions_count
                revenue.clicks_count = clicks_count
                revenue.conversions_count = conversions_count
                revenue.save()
            
            logger.info(f"Calculated revenue for campaign {campaign.id}: ${net_revenue}")
            return revenue
            
        except Exception as e:
            logger.error(f"Error calculating revenue: {str(e)}", exc_info=True)
            return None
    
    @staticmethod
    def generate_revenue_report(
        start_date: datetime,
        end_date: datetime,
        campaign_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Generate comprehensive revenue report
        """
        try:
            # Build base query
            revenue_query = AdRevenue.objects.filter(
                period_start__gte=start_date,
                period_end__lte=end_date
            )
            
            if campaign_ids:
                revenue_query = revenue_query.filter(campaign_id__in=campaign_ids)
            
            # Aggregate revenue data
            revenue_data = revenue_query.aggregate(
                total_gross_revenue=Sum('gross_revenue'),
                total_net_revenue=Sum('net_revenue'),
                total_impressions=Sum('impressions_count'),
                total_clicks=Sum('clicks_count'),
                total_conversions=Sum('conversions_count'),
                avg_ctr=Avg(
                    Case(
                        When(impressions_count__gt=0, 
                             then=F('clicks_count') * 100.0 / F('impressions_count')),
                        default=Value(0),
                        output_field=DecimalField()
                    )
                ),
                avg_cvr=Avg(
                    Case(
                        When(clicks_count__gt=0,
                             then=F('conversions_count') * 100.0 / F('clicks_count')),
                        default=Value(0),
                        output_field=DecimalField()
                    )
                )
            )
            
            # Get top performing campaigns
            top_campaigns = revenue_query.order_by('-net_revenue')[:10].values(
                'campaign__name',
                'campaign__id',
                'net_revenue',
                'impressions_count',
                'clicks_count',
                'conversions_count'
            )
            
            # Group by provider
            provider_breakdown = revenue_query.values(
                'provider__name',
                'provider__id'
            ).annotate(
                total_revenue=Sum('net_revenue'),
                total_impressions=Sum('impressions_count'),
                total_clicks=Sum('clicks_count')
            ).order_by('-total_revenue')
            
            # Daily breakdown
            daily_breakdown = revenue_query.extra(
                select={'day': 'DATE(period_start)'}
            ).values('day').annotate(
                daily_revenue=Sum('net_revenue'),
                daily_impressions=Sum('impressions_count'),
                daily_clicks=Sum('clicks_count')
            ).order_by('day')
            
            return {
                'period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                'summary': {
                    'total_gross_revenue': revenue_data['total_gross_revenue'] or Decimal('0'),
                    'total_net_revenue': revenue_data['total_net_revenue'] or Decimal('0'),
                    'total_impressions': revenue_data['total_impressions'] or 0,
                    'total_clicks': revenue_data['total_clicks'] or 0,
                    'total_conversions': revenue_data['total_conversions'] or 0,
                    'average_ctr': revenue_data['avg_ctr'] or Decimal('0'),
                    'average_cvr': revenue_data['avg_cvr'] or Decimal('0'),
                    'average_cpm': AdRevenueService._calculate_cpm(
                        revenue_data['total_gross_revenue'] or Decimal('0'),
                        revenue_data['total_impressions'] or 0
                    ),
                    'average_cpc': AdRevenueService._calculate_cpc(
                        revenue_data['total_gross_revenue'] or Decimal('0'),
                        revenue_data['total_clicks'] or 0
                    )
                },
                'top_campaigns': list(top_campaigns),
                'provider_breakdown': list(provider_breakdown),
                'daily_breakdown': list(daily_breakdown)
            }
            
        except Exception as e:
            logger.error(f"Error generating revenue report: {str(e)}", exc_info=True)
            return {}
    
    @staticmethod
    def _calculate_cpm(revenue: Decimal, impressions: int) -> Decimal:
        """Calculate CPM (Cost Per Mille)"""
        if impressions == 0:
            return Decimal('0')
        return (revenue / impressions) * 1000
    
    @staticmethod
    def _calculate_cpc(revenue: Decimal, clicks: int) -> Decimal:
        """Calculate CPC (Cost Per Click)"""
        if clicks == 0:
            return Decimal('0')
        return revenue / clicks
    
    @staticmethod
    def sync_provider_revenue(
        provider: AdProvider,
        start_date: datetime,
        end_date: datetime
    ) -> bool:
        """
        Sync revenue data with external ad provider
        """
        try:
            provider_registry = AdProviderRegistry()
            provider_instance = provider_registry.get_provider(provider.provider_type)
            
            if not provider_instance or not hasattr(provider_instance, 'get_revenue_data'):
                logger.warning(f"Provider {provider.name} doesn't support revenue sync")
                return False
            
            # Get revenue data from provider
            provider_revenue_data = provider_instance.get_revenue_data(
                start_date, end_date
            )
            
            if not provider_revenue_data:
                logger.warning(f"No revenue data received from provider {provider.name}")
                return False
            
            # Process and store revenue data
            for campaign_data in provider_revenue_data:
                try:
                    campaign = AdCampaign.objects.get(
                        provider=provider,
                        external_id=campaign_data.get('campaign_id')
                    )
                    
                    AdRevenue.objects.update_or_create(
                        campaign=campaign,
                        provider=provider,
                        period_start=start_date,
                        period_end=end_date,
                        defaults={
                            'revenue_type': 'synced',
                            'gross_revenue': Decimal(str(campaign_data.get('revenue', 0))),
                            'net_revenue': Decimal(str(campaign_data.get('revenue', 0))) * 
                                         (1 - provider.revenue_share / 100),
                            'currency': campaign_data.get('currency', 'USD'),
                            'impressions_count': campaign_data.get('impressions', 0),
                            'clicks_count': campaign_data.get('clicks', 0),
                            'conversions_count': campaign_data.get('conversions', 0)
                        }
                    )
                    
                except AdCampaign.DoesNotExist:
                    logger.warning(f"Campaign with external_id {campaign_data.get('campaign_id')} not found")
                    continue
            
            logger.info(f"Successfully synced revenue for provider {provider.name}")
            return True
            
        except Exception as e:
            logger.error(f"Error syncing provider revenue: {str(e)}", exc_info=True)
            return False


class AdAnalyticsService:
    """Service for advanced ad analytics and reporting"""
    
    @staticmethod
    def get_campaign_performance(
        campaign_id: int,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get detailed campaign performance metrics
        """
        try:
            campaign = AdCampaign.objects.get(id=campaign_id)
            
            # Get impressions data
            impressions = AdImpression.objects.filter(
                campaign=campaign,
                created_at__range=(start_date, end_date)
            )
            
            # Get clicks data
            clicks = AdClick.objects.filter(
                campaign=campaign,
                created_at__range=(start_date, end_date)
            )
            
            # Get conversions data
            conversions = AdConversion.objects.filter(
                campaign=campaign,
                created_at__range=(start_date, end_date)
            )
            
            # Calculate metrics
            total_impressions = impressions.count()
            total_clicks = clicks.filter(is_valid=True).count()
            total_conversions = conversions.count()
            
            ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
            cvr = (total_conversions / total_clicks * 100) if total_clicks > 0 else 0
            
            # Calculate costs
            total_cost = impressions.aggregate(
                total=Sum('cost')
            )['total'] or Decimal('0')
            
            total_cost += clicks.filter(is_valid=True).aggregate(
                total=Sum('cost')
            )['total'] or Decimal('0')
            
            # Geographic breakdown
            geo_breakdown = impressions.values('user_country').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions=Count('adclick__adconversion')
            ).order_by('-impressions')[:10]
            
            # Device breakdown
            device_breakdown = impressions.values('device_type').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions=Count('adclick__adconversion')
            ).order_by('-impressions')
            
            # Hourly breakdown
            hourly_breakdown = impressions.extra(
                select={'hour': 'EXTRACT(hour FROM created_at)'}
            ).values('hour').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True))
            ).order_by('hour')
            
            # Fraud detection summary
            fraud_summary = clicks.aggregate(
                total_clicks=Count('id'),
                valid_clicks=Count('id', filter=Q(is_valid=True)),
                fraud_clicks=Count('id', filter=Q(is_valid=False)),
                avg_fraud_score=Avg('fraud_score', filter=Q(fraud_score__isnull=False))
            )
            
            return {
                'campaign': {
                    'id': campaign.id,
                    'name': campaign.name,
                    'type': campaign.campaign_type,
                    'status': campaign.status
                },
                'period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                'metrics': {
                    'impressions': total_impressions,
                    'clicks': total_clicks,
                    'conversions': total_conversions,
                    'ctr': round(ctr, 2),
                    'cvr': round(cvr, 2),
                    'total_cost': float(total_cost),
                    'cpm': float(AdRevenueService._calculate_cpm(total_cost, total_impressions)),
                    'cpc': float(AdRevenueService._calculate_cpc(total_cost, total_clicks))
                },
                'breakdowns': {
                    'geographic': list(geo_breakdown),
                    'device': list(device_breakdown),
                    'hourly': list(hourly_breakdown)
                },
                'fraud_detection': {
                    'total_clicks': fraud_summary['total_clicks'],
                    'valid_clicks': fraud_summary['valid_clicks'],
                    'fraud_clicks': fraud_summary['fraud_clicks'],
                    'fraud_rate': round(
                        (fraud_summary['fraud_clicks'] / fraud_summary['total_clicks'] * 100)
                        if fraud_summary['total_clicks'] > 0 else 0, 2
                    ),
                    'avg_fraud_score': float(fraud_summary['avg_fraud_score'] or 0)
                }
            }
            
        except AdCampaign.DoesNotExist:
            logger.error(f"Campaign {campaign_id} not found")
            return {}
        except Exception as e:
            logger.error(f"Error getting campaign performance: {str(e)}", exc_info=True)
            return {}
    
    @staticmethod
    def get_placement_performance(
        placement_slug: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get performance metrics for a specific ad placement
        """
        try:
            placement = AdPlacement.objects.get(slug=placement_slug)
            
            # Get campaigns for this placement
            campaigns = AdCampaign.objects.filter(
                placement=placement,
                created_at__range=(start_date, end_date)
            )
            
            # Aggregate metrics across all campaigns
            total_impressions = AdImpression.objects.filter(
                campaign__placement=placement,
                created_at__range=(start_date, end_date)
            ).count()
            
            total_clicks = AdClick.objects.filter(
                campaign__placement=placement,
                is_valid=True,
                created_at__range=(start_date, end_date)
            ).count()
            
            total_conversions = AdConversion.objects.filter(
                campaign__placement=placement,
                created_at__range=(start_date, end_date)
            ).count()
            
            # Calculate revenue
            total_revenue = AdRevenue.objects.filter(
                campaign__placement=placement,
                period_start__gte=start_date,
                period_end__lte=end_date
            ).aggregate(
                total=Sum('net_revenue')
            )['total'] or Decimal('0')
            
            # Top performing campaigns
            top_campaigns = campaigns.annotate(
                impressions_count=Count('adimpression'),
                clicks_count=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions_count=Count('adclick__adconversion')
            ).order_by('-impressions_count')[:5]
            
            return {
                'placement': {
                    'slug': placement.slug,
                    'name': placement.name,
                    'type': placement.placement_type,
                    'dimensions': f"{placement.width}x{placement.height}"
                },
                'period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat()
                },
                'metrics': {
                    'total_impressions': total_impressions,
                    'total_clicks': total_clicks,
                    'total_conversions': total_conversions,
                    'total_revenue': float(total_revenue),
                    'ctr': round((total_clicks / total_impressions * 100) if total_impressions > 0 else 0, 2),
                    'cvr': round((total_conversions / total_clicks * 100) if total_clicks > 0 else 0, 2),
                    'rpm': float(total_revenue / total_impressions * 1000) if total_impressions > 0 else 0
                },
                'top_campaigns': [
                    {
                        'id': campaign.id,
                        'name': campaign.name,
                        'impressions': campaign.impressions_count,
                        'clicks': campaign.clicks_count,
                        'conversions': campaign.conversions_count
                    }
                    for campaign in top_campaigns
                ]
            }
            
        except AdPlacement.DoesNotExist:
            logger.error(f"Placement {placement_slug} not found")
            return {}
        except Exception as e:
            logger.error(f"Error getting placement performance: {str(e)}", exc_info=True)
            return {}
    
    @staticmethod
    def get_user_ad_behavior(
        user_id: int,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Analyze user's ad interaction behavior
        """
        try:
            end_date = timezone.now()
            start_date = end_date - timedelta(days=days)
            
            # Get user's ad interactions
            impressions = AdImpression.objects.filter(
                user_id=user_id,
                created_at__range=(start_date, end_date)
            )
            
            clicks = AdClick.objects.filter(
                user_id=user_id,
                created_at__range=(start_date, end_date)
            )
            
            conversions = AdConversion.objects.filter(
                user_id=user_id,
                created_at__range=(start_date, end_date)
            )
            
            # Calculate behavior metrics
            total_impressions = impressions.count()
            total_clicks = clicks.count()
            total_conversions = conversions.count()
            
            # Category preferences
            category_preferences = impressions.values(
                'campaign__category__name'
            ).annotate(
                impressions=Count('id'),
                clicks=Count('adclick')
            ).order_by('-impressions')[:5]
            
            # Time preferences
            time_preferences = impressions.extra(
                select={'hour': 'EXTRACT(hour FROM created_at)'}
            ).values('hour').annotate(
                impressions=Count('id'),
                clicks=Count('adclick')
            ).order_by('hour')
            
            # Device preferences
            device_preferences = impressions.values('device_type').annotate(
                impressions=Count('id'),
                clicks=Count('adclick')
            ).order_by('-impressions')
            
            # Rewarded ad behavior
            rewarded_views = RewardedAdView.objects.filter(
                user_id=user_id,
                created_at__range=(start_date, end_date)
            )
            
            rewarded_stats = rewarded_views.aggregate(
                total_views=Count('id'),
                completed_views=Count('id', filter=Q(completed=True)),
                rewards_earned=Count('id', filter=Q(reward_granted=True)),
                avg_duration=Avg('view_duration')
            )
            
            return {
                'user_id': user_id,
                'period_days': days,
                'ad_interaction': {
                    'total_impressions': total_impressions,
                    'total_clicks': total_clicks,
                    'total_conversions': total_conversions,
                    'click_through_rate': round(
                        (total_clicks / total_impressions * 100) if total_impressions > 0 else 0, 2
                    ),
                    'conversion_rate': round(
                        (total_conversions / total_clicks * 100) if total_clicks > 0 else 0, 2
                    )
                },
                'preferences': {
                    'categories': list(category_preferences),
                    'time_of_day': list(time_preferences),
                    'devices': list(device_preferences)
                },
                'rewarded_ads': {
                    'total_views': rewarded_stats['total_views'],
                    'completed_views': rewarded_stats['completed_views'],
                    'completion_rate': round(
                        (rewarded_stats['completed_views'] / rewarded_stats['total_views'] * 100)
                        if rewarded_stats['total_views'] > 0 else 0, 2
                    ),
                    'rewards_earned': rewarded_stats['rewards_earned'],
                    'avg_watch_duration': round(rewarded_stats['avg_duration'] or 0, 2)
                }
            }
            
        except Exception as e:
            logger.error(f"Error analyzing user ad behavior: {str(e)}", exc_info=True)
            return {}


class AdOptimizationService:
    """Service for ad campaign optimization"""
    
    @staticmethod
    def optimize_campaign_bidding(campaign_id: int) -> Dict[str, Any]:
        """
        Optimize campaign bidding based on performance data
        """
        try:
            campaign = AdCampaign.objects.get(id=campaign_id)
            
            # Get recent performance data (last 7 days)
            end_date = timezone.now()
            start_date = end_date - timedelta(days=7)
            
            performance_data = AdAnalyticsService.get_campaign_performance(
                campaign_id, start_date, end_date
            )
            
            if not performance_data:
                return {'error': 'No performance data available'}
            
            metrics = performance_data['metrics']
            current_ctr = metrics['ctr']
            current_cvr = metrics['cvr']
            current_cpm = metrics['cpm']
            
            # Calculate optimization recommendations
            recommendations = []
            
            # CTR optimization
            if current_ctr < 1.0:  # Industry average CTR is around 2%
                recommendations.append({
                    'type': 'ctr_low',
                    'message': 'Click-through rate is below average. Consider improving ad creative or targeting.',
                    'suggested_action': 'improve_creative'
                })
            
            # CVR optimization
            if current_cvr < 2.0:  # Industry average CVR is around 3-5%
                recommendations.append({
                    'type': 'cvr_low',
                    'message': 'Conversion rate is below average. Consider improving landing page or offer.',
                    'suggested_action': 'improve_landing_page'
                })
            
            # Bid optimization
            if campaign.campaign_type == 'cpc':
                # Suggest bid adjustments based on performance
                if current_ctr > 3.0 and current_cvr > 3.0:
                    # High performance - suggest increasing bid
                    suggested_bid = campaign.bid_amount * Decimal('1.2')
                    recommendations.append({
                        'type': 'bid_increase',
                        'message': f'Campaign is performing well. Consider increasing CPC bid to {suggested_bid:.2f}',
                        'suggested_bid': float(suggested_bid)
                    })
                elif current_ctr < 1.0 or current_cvr < 1.0:
                    # Poor performance - suggest decreasing bid
                    suggested_bid = campaign.bid_amount * Decimal('0.8')
                    recommendations.append({
                        'type': 'bid_decrease',
                        'message': f'Campaign is underperforming. Consider decreasing CPC bid to {suggested_bid:.2f}',
                        'suggested_bid': float(suggested_bid)
                    })
            
            # Budget optimization
            if campaign.spent_amount > campaign.budget * Decimal('0.8'):
                recommendations.append({
                    'type': 'budget_warning',
                    'message': 'Campaign has spent 80% of budget. Consider increasing budget or pausing campaign.',
                    'suggested_action': 'review_budget'
                })
            
            return {
                'campaign_id': campaign_id,
                'current_performance': metrics,
                'recommendations': recommendations,
                'optimization_score': AdOptimizationService._calculate_optimization_score(metrics)
            }
            
        except AdCampaign.DoesNotExist:
            return {'error': 'Campaign not found'}
        except Exception as e:
            logger.error(f"Error optimizing campaign: {str(e)}", exc_info=True)
            return {'error': 'Optimization failed'}
    
    @staticmethod
    def _calculate_optimization_score(metrics: Dict[str, Any]) -> int:
        """
        Calculate optimization score (0-100) based on performance metrics
        """
        score = 0
        
        # CTR component (40% weight)
        ctr = metrics.get('ctr', 0)
        if ctr >= 3.0:
            score += 40
        elif ctr >= 2.0:
            score += 30
        elif ctr >= 1.0:
            score += 20
        else:
            score += 10
        
        # CVR component (40% weight)
        cvr = metrics.get('cvr', 0)
        if cvr >= 5.0:
            score += 40
        elif cvr >= 3.0:
            score += 30
        elif cvr >= 2.0:
            score += 20
        else:
            score += 10
        
        # Volume component (20% weight)
        impressions = metrics.get('impressions', 0)
        if impressions >= 10000:
            score += 20
        elif impressions >= 5000:
            score += 15
        elif impressions >= 1000:
            score += 10
        else:
            score += 5
        
        return min(score, 100)
    
    @staticmethod
    def auto_pause_underperforming_campaigns(
        min_impressions: int = 1000,
        max_ctr: float = 0.5,
        max_cvr: float = 0.5
    ) -> List[int]:
        """
        Automatically pause campaigns that are underperforming
        """
        try:
            # Get campaigns that meet minimum impression threshold
            underperforming_campaigns = []
            
            campaigns = AdCampaign.objects.filter(
                status='active',
                total_impressions__gte=min_impressions
            )
            
            for campaign in campaigns:
                ctr = (campaign.total_clicks / campaign.total_impressions * 100) if campaign.total_impressions > 0 else 0
                cvr = (campaign.total_conversions / campaign.total_clicks * 100) if campaign.total_clicks > 0 else 0
                
                if ctr < max_ctr and cvr < max_cvr:
                    # Pause the campaign
                    campaign.status = 'paused'
                    campaign.pause_reason = f'Auto-paused: CTR={ctr:.2f}%, CVR={cvr:.2f}%'
                    campaign.save()
                    
                    underperforming_campaigns.append(campaign.id)
                    logger.info(f"Auto-paused campaign {campaign.id} due to poor performance")
            
            return underperforming_campaigns
            
        except Exception as e:
            logger.error(f"Error auto-pausing campaigns: {str(e)}", exc_info=True)
            return []
    
    @staticmethod
    def suggest_audience_expansion(campaign_id: int) -> Dict[str, Any]:
        """
        Suggest audience expansion based on high-performing segments
        """
        try:
            campaign = AdCampaign.objects.select_related('provider', 'placement').get(id=campaign_id)

            # Analyze current audience performance over the last 30 days
            end_date = timezone.now()
            start_date = end_date - timedelta(days=30)
        
            # Use prefetch_related for better performance on related queries
            impressions = AdImpression.objects.filter(
                campaign=campaign,
                created_at__range=(start_date, end_date)
            ).select_related('campaign').prefetch_related('adclick_set', 'adclick_set__adconversion_set')

            # Check if we have sufficient data for analysis
            total_impressions = impressions.count()
            if total_impressions < 1000:  # Minimum threshold for reliable analysis
                return {
                    'campaign_id': campaign_id,
                    'error': 'Insufficient data for audience expansion analysis',
                    'minimum_impressions_required': 1000,
                    'current_impressions': total_impressions
                }

            # Analyze performance by country with proper aggregation
            country_performance = impressions.values('user_country').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions=Count('adclick__adconversion'),
                total_cost=Sum('cost', output_field=DecimalField())
            ).filter(
                impressions__gte=100,  # Minimum threshold for statistical significance
                user_country__isnull=False
            ).order_by('-impressions')

            # Calculate performance metrics for each country
            high_performing_countries = []
            for country in country_performance:
                ctr = (country['clicks'] / country['impressions'] * 100) if country['impressions'] > 0 else 0
                cvr = (country['conversions'] / country['clicks'] * 100) if country['clicks'] > 0 else 0
                cpm = float((country['total_cost'] or Decimal('0')) / country['impressions'] * 1000) if country['impressions'] > 0 else 0

                country_data = {
                    'country': country['user_country'],
                    'impressions': country['impressions'],
                    'clicks': country['clicks'],
                    'conversions': country['conversions'],
                    'ctr': round(ctr, 2),
                    'cvr': round(cvr, 2),
                    'cpm': round(cpm, 2),
                    'performance_score': round((ctr * 0.6 + cvr * 0.4), 2)  # Weighted performance score
                }

                # Consider high-performing if CTR > 2% and CVR > 1%
                if ctr > 2.0 and cvr > 1.0:
                    high_performing_countries.append(country_data)

            # Analyze by age groups with proper handling of null values
            age_performance = impressions.filter(
                user_age__isnull=False,
                user_age__gte=13,  # Minimum age for ad targeting
                user_age__lte=100   # Maximum reasonable age
            ).extra(
                select={
                    'age_group': """
                        CASE 
                            WHEN user_age BETWEEN 13 AND 17 THEN '13-17'
                            WHEN user_age BETWEEN 18 AND 24 THEN '18-24'
                            WHEN user_age BETWEEN 25 AND 34 THEN '25-34'
                            WHEN user_age BETWEEN 35 AND 44 THEN '35-44'
                            WHEN user_age BETWEEN 45 AND 54 THEN '45-54'
                            WHEN user_age BETWEEN 55 AND 64 THEN '55-64'
                            WHEN user_age >= 65 THEN '65+'
                        END
                    """
                }
            ).values('age_group').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions=Count('adclick__adconversion'),
                total_cost=Sum('cost', output_field=DecimalField())
            ).filter(
                impressions__gte=50,  # Lower threshold for age groups
                age_group__isnull=False
            ).order_by('-impressions')

            # Calculate performance metrics for age groups
            high_performing_age_groups = []
            for age_group in age_performance:
                ctr = (age_group['clicks'] / age_group['impressions'] * 100) if age_group['impressions'] > 0 else 0
                cvr = (age_group['conversions'] / age_group['clicks'] * 100) if age_group['clicks'] > 0 else 0
                cpm = float((age_group['total_cost'] or Decimal('0')) / age_group['impressions'] * 1000) if age_group['impressions'] > 0 else 0
            
                age_data = {
                    'age_group': age_group['age_group'],
                    'impressions': age_group['impressions'],
                    'clicks': age_group['clicks'],
                    'conversions': age_group['conversions'],
                    'ctr': round(ctr, 2),
                    'cvr': round(cvr, 2),
                    'cpm': round(cpm, 2),
                    'performance_score': round((ctr * 0.6 + cvr * 0.4), 2)
                }

                if ctr > 1.5 and cvr > 0.8:  # Slightly lower thresholds for age groups
                    high_performing_age_groups.append(age_data)

            # Analyze by device type
            device_performance = impressions.values('device_type').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions=Count('adclick__adconversion'),
                total_cost=Sum('cost', output_field=DecimalField())
            ).filter(
                impressions__gte=50,
                device_type__isnull=False
            ).order_by('-impressions')

            high_performing_devices = []
            for device in device_performance:
                ctr = (device['clicks'] / device['impressions'] * 100) if device['impressions'] > 0 else 0
                cvr = (device['conversions'] / device['clicks'] * 100) if device['clicks'] > 0 else 0
                cpm = float((device['total_cost'] or Decimal('0')) / device['impressions'] * 1000) if device['impressions'] > 0 else 0

                device_data = {
                    'device_type': device['device_type'],
                    'impressions': device['impressions'],
                    'clicks': device['clicks'],
                    'conversions': device['conversions'],
                    'ctr': round(ctr, 2),
                    'cvr': round(cvr, 2),
                    'cpm': round(cpm, 2),
                    'performance_score': round((ctr * 0.6 + cvr * 0.4), 2)
                }

                if ctr > 1.5 and cvr > 0.8:
                    high_performing_devices.append(device_data)

            # Analyze time-based performance (hour of day)
            hourly_performance = impressions.extra(
                select={'hour': 'EXTRACT(hour FROM created_at)'}
            ).values('hour').annotate(
                impressions=Count('id'),
                clicks=Count('adclick', filter=Q(adclick__is_valid=True)),
                conversions=Count('adclick__adconversion')
            ).filter(
                impressions__gte=10  # Lower threshold for hourly data
            ).order_by('hour')

            high_performing_hours = []
            for hour_data in hourly_performance:
                ctr = (hour_data['clicks'] / hour_data['impressions'] * 100) if hour_data['impressions'] > 0 else 0
                cvr = (hour_data['conversions'] / hour_data['clicks'] * 100) if hour_data['clicks'] > 0 else 0

                if ctr > 1.0:  # Lower threshold for hourly performance
                    high_performing_hours.append({
                        'hour': int(hour_data['hour']),
                        'impressions': hour_data['impressions'],
                        'clicks': hour_data['clicks'],
                        'conversions': hour_data['conversions'],
                        'ctr': round(ctr, 2),
                        'cvr': round(cvr, 2)
                    })

            #  Generate expansion recommendations
            recommendations = []

            # Country expansion recommendations
            if high_performing_countries:
                similar_countries = AdOptimizationService._get_similar_countries(
                    [country['country'] for country in high_performing_countries[:3]]
                )
                if similar_countries:
                    recommendations.append({
                        'type': 'geographic_expansion',
                        'priority': 'high',
                        'title': 'Expand to Similar Countries',
                        'description': f'Based on strong performance in {", ".join([c["country"] for c in high_performing_countries[:3]])}, consider targeting similar markets.',
                        'suggested_countries': similar_countries,
                        'expected_improvement': '15-25% increase in reach',
                        'confidence_score': 85
                    })

            # Age group expansion recommendations
            if high_performing_age_groups:
                recommendations.append({
                    'type': 'demographic_expansion',
                    'priority': 'medium',
                    'title': 'Expand Age Targeting',
                    'description': f'Strong performance in {", ".join([ag["age_group"] for ag in high_performing_age_groups[:2]])} age groups suggests potential for broader demographic targeting.',
                    'current_high_performers': [ag['age_group'] for ag in high_performing_age_groups],
                    'suggested_expansion': AdOptimizationService._suggest_adjacent_age_groups(
                        [ag['age_group'] for ag in high_performing_age_groups]
                    ),
                    'expected_improvement': '10-20% increase in audience size',
                    'confidence_score': 75
                })

            # Device expansion recommendations
            if len(high_performing_devices) < len(device_performance):
                underperforming_devices = [
                    device for device in device_performance 
                    if device not in [d['device_type'] for d in high_performing_devices]
                ]
                if underperforming_devices:
                    recommendations.append({
                        'type': 'device_optimization',
                        'priority': 'low',
                        'title': 'Optimize Device Targeting',
                        'description': 'Consider adjusting creative or landing pages for underperforming device types.',
                        'high_performing_devices': [d['device_type'] for d in high_performing_devices],
                        'optimization_opportunity': [d['device_type'] for d in underperforming_devices[:2]],
                        'expected_improvement': '5-15% improvement in overall CTR',
                        'confidence_score': 60
                    })

            # Time-based recommendations
            if high_performing_hours:
                peak_hours = sorted(high_performing_hours, key=lambda x: x['ctr'], reverse=True)[:4]
                recommendations.append({
                    'type': 'timing_optimization',
                    'priority': 'medium',
                    'title': 'Optimize Ad Scheduling',
                    'description': 'Focus budget allocation on high-performing time periods.',
                    'peak_performance_hours': [h['hour'] for h in peak_hours],
                    'suggested_budget_shift': '30-40% of budget during peak hours',
                    'expected_improvement': '10-20% improvement in efficiency',
                    'confidence_score': 70
                })

            # Calculate overall expansion potential score
            expansion_score = AdOptimizationService._calculate_expansion_score(
                high_performing_countries, high_performing_age_groups, 
                high_performing_devices, high_performing_hours
            )

            return {
                'campaign_id': campaign_id,
                'campaign_name': campaign.name,
                'analysis_period': {
                    'start_date': start_date.isoformat(),
                    'end_date': end_date.isoformat(),
                    'total_impressions_analyzed': total_impressions
                },
                'current_performance': {
                    'countries': list(country_performance)[:10],  # Top 10 countries
                    'age_groups': list(age_performance),
                    'devices': list(device_performance),
                    'peak_hours': high_performing_hours
                },
                'high_performers': {
                    'countries': high_performing_countries,
                    'age_groups': high_performing_age_groups,
                    'devices': high_performing_devices,
                    'hours': high_performing_hours
                },
                'recommendations': recommendations,
                'expansion_potential': {
                    'score': expansion_score,
                    'level': AdOptimizationService._get_expansion_level(expansion_score),
                    'summary': AdOptimizationService._generate_expansion_summary(recommendations)
                }
            }

        except AdCampaign.DoesNotExist:
            logger.error(f"Campaign {campaign_id} not found")
            return {
                'campaign_id': campaign_id,
                'error': 'Campaign not found'
            }
        except Exception as e:
            logger.error(f"Error suggesting audience expansion for campaign {campaign_id}: {str(e)}", exc_info=True)
            return {
                'campaign_id': campaign_id,
                'error': 'Failed to analyze audience expansion opportunities',
                'details': str(e) if settings.DEBUG else 'Internal server error'
            }

    @staticmethod
    def _get_similar_countries(high_performing_countries: List[str]) -> List[str]:
        """
        Get countries similar to high-performing ones based on economic and demographic factors
        """
        # This is a simplified mapping - in production, you might use a more sophisticated
        # geo-targeting API or maintain a comprehensive country similarity database
        country_groups = {
            'US': ['CA', 'AU', 'GB', 'NZ'],
            'GB': ['IE', 'AU', 'CA', 'NZ'],
            'DE': ['AT', 'CH', 'NL', 'BE'],
            'FR': ['BE', 'CH', 'LU', 'MC'],
            'JP': ['KR', 'TW', 'SG', 'HK'],
            'AU': ['NZ', 'CA', 'GB', 'US'],
            'CA': ['US', 'AU', 'GB', 'NZ'],
            # Add more country mappings as needed
        }

        similar_countries = set()
        for country in high_performing_countries:
            if country in country_groups:
                similar_countries.update(country_groups[country])

        # Remove countries that are already high-performing
        similar_countries = similar_countries - set(high_performing_countries)

        return list(similar_countries)[:5]  # Return top 5 suggestions

    @staticmethod
    def _suggest_adjacent_age_groups(high_performing_age_groups: List[str]) -> List[str]:
        """
        Suggest adjacent age groups based on current high performers
        """
        age_group_order = ['13-17', '18-24', '25-34', '35-44', '45-54', '55-64', '65+']
        suggested_groups = set()

        for age_group in high_performing_age_groups:
            if age_group in age_group_order:
                index = age_group_order.index(age_group)
                # Add adjacent age groups
                if index > 0:
                    suggested_groups.add(age_group_order[index - 1])
                if index < len(age_group_order) - 1:
                    suggested_groups.add(age_group_order[index + 1])

        # Remove already high-performing groups
        suggested_groups = suggested_groups - set(high_performing_age_groups)

        return list(suggested_groups)

    @staticmethod
    def _calculate_expansion_score(countries, age_groups, devices, hours) -> int:
        """
        Calculate overall expansion potential score (0-100)
        """
        score = 0

        # Geographic diversity (30 points max)
        if len(countries) >= 3:
            score += 30
        elif len(countries) >= 2:
            score += 20
        elif len(countries) >= 1:
            score += 10

        # Demographic diversity (25 points max)
        if len(age_groups) >= 3:
            score += 25
        elif len(age_groups) >= 2:
            score += 15
        elif len(age_groups) >= 1:
            score += 8

        # Device performance (20 points max)
        if len(devices) >= 2:
            score += 20
        elif len(devices) >= 1:
            score += 10

        # Time optimization potential (25 points max)
        if len(hours) >= 8:
            score += 25
        elif len(hours) >= 4:
            score += 15
        elif len(hours) >= 2:
            score += 8

        return min(score, 100)

    @staticmethod
    def _get_expansion_level(score: int) -> str:
        """
        Get expansion potential level based on score
        """
        if score >= 80:
            return 'Excellent'
        elif score >= 60:
            return 'Good'
        elif score >= 40:
            return 'Moderate'
        elif score >= 20:
            return 'Limited'
        else:
            return 'Poor'

    @staticmethod
    def _generate_expansion_summary(recommendations: List[Dict[str, Any]]) -> str:
        """
        Generate a human-readable summary of expansion recommendations
        """
        if not recommendations:
            return "No significant expansion opportunities identified based on current performance data."

        high_priority = [r for r in recommendations if r.get('priority') == 'high']
        medium_priority = [r for r in recommendations if r.get('priority') == 'medium']

        summary_parts = []

        if high_priority:
            summary_parts.append(f"{len(high_priority)} high-priority expansion opportunities identified")

        if medium_priority:
            summary_parts.append(f"{len(medium_priority)} medium-priority optimizations recommended")

        if not summary_parts:
            summary_parts.append("Several low-priority optimization opportunities available")

        return ". ".join(summary_parts) + "."