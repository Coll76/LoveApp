# apps/advertisements/managers.py
from django.db import models
from django.utils import timezone
from django.db.models import Q, Sum, Count, Avg, F, Case, When, Value, Max, Min
from datetime import timedelta, datetime
from decimal import Decimal

class AdProviderManager(models.Manager):
    """Manager for AdProvider model"""
    
    def active(self):
        """Get only active providers"""
        return self.filter(is_active=True)
    
    def by_priority(self):
        """Get providers ordered by priority"""
        return self.active().order_by('priority')
    
    def get_best_provider(self, country=None, ad_type=None):
        """Get best provider based on performance and priority"""
        queryset = self.active()
        
        if country:
            queryset = queryset.filter(
                Q(configuration__supported_countries__contains=[country]) |
                Q(configuration__supported_countries__isnull=True)
            )
        
        return queryset.order_by('priority').first()
    
    def with_revenue_stats(self, days=30):
        """Get providers with revenue statistics"""
        since = timezone.now() - timedelta(days=days)
        return self.active().annotate(
            total_revenue=Sum('ad_revenues__net_revenue', filter=Q(ad_revenues__period_start__gte=since)),
            campaign_count=Count('adcampaign', distinct=True),
            avg_revenue_share=Avg('revenue_share')
        ).order_by('-total_revenue')
    
    def top_performing(self, limit=5):
        """Get top performing providers by revenue"""
        return self.with_revenue_stats().filter(
            total_revenue__isnull=False
        )[:limit]

class AdCampaignManager(models.Manager):
    """Manager for AdCampaign model"""
    
    def active(self):
        """Get active campaigns"""
        now = timezone.now()
        return self.filter(
            status='active',
            start_date__lte=now,
            is_deleted=False
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=now)
        ).filter(
            spent_amount__lt=F('budget')
        )
    
    def for_placement(self, placement_slug):
        """Get campaigns for specific placement"""
        return self.active().filter(placement__slug=placement_slug)
    
    def for_user(self, user=None, country=None, age=None, gender=None, interests=None):
        """Get campaigns targeted to specific user"""
        queryset = self.active()
        
        # Country targeting
        if country:
            queryset = queryset.filter(
                Q(target_countries__contains=[country]) |
                Q(target_countries__isnull=True) |
                Q(target_countries=[])
            )
        
        # Age targeting
        if age:
            queryset = queryset.filter(
                Q(target_age_min__isnull=True) | Q(target_age_min__lte=age)
            ).filter(
                Q(target_age_max__isnull=True) | Q(target_age_max__gte=age)
            )
        
        # Gender targeting
        if gender:
            queryset = queryset.filter(
                Q(target_gender__isnull=True) |
                Q(target_gender='') |
                Q(target_gender=gender)
            )
        
        # Interest targeting
        if interests:
            interest_q = Q()
            for interest in interests:
                interest_q |= Q(target_interests__contains=[interest])
            queryset = queryset.filter(
                Q(target_interests__isnull=True) |
                Q(target_interests=[]) |
                interest_q
            )
        
        return queryset
    
    def top_performing(self, days=30):
        """Get top performing campaigns"""
        since = timezone.now() - timedelta(days=days)
        return self.active().annotate(
            recent_clicks=Count('clicks', filter=Q(clicks__created_at__gte=since)),
            recent_impressions=Count('impressions', filter=Q(impressions__created_at__gte=since)),
            ctr=Case(
                When(recent_impressions__gt=0, 
                     then=F('recent_clicks') * 100.0 / F('recent_impressions')),
                default=Value(0),
                output_field=models.FloatField()
            )
        ).filter(recent_impressions__gte=100).order_by('-ctr')
    
    def by_budget_remaining(self):
        """Order campaigns by remaining budget"""
        return self.active().annotate(
            budget_remaining=F('budget') - F('spent_amount')
        ).order_by('-budget_remaining')
    
    def expiring_soon(self, days=7):
        """Get campaigns expiring soon"""
        cutoff = timezone.now() + timedelta(days=days)
        return self.active().filter(
            end_date__isnull=False,
            end_date__lte=cutoff
        )
    
    def over_budget(self):
        """Get campaigns that have exceeded their budget"""
        return self.filter(spent_amount__gte=F('budget'))
    
    def with_performance_stats(self, days=30):
        """Get campaigns with detailed performance statistics"""
        since = timezone.now() - timedelta(days=days)
        return self.annotate(
            recent_impressions=Count('impressions', filter=Q(impressions__created_at__gte=since)),
            recent_clicks=Count('clicks', filter=Q(clicks__created_at__gte=since)),
            recent_conversions=Count('conversions', filter=Q(conversions__created_at__gte=since)),
            recent_revenue=Sum('revenues__net_revenue', filter=Q(revenues__period_start__gte=since)),
            ctr=Case(
                When(recent_impressions__gt=0,
                     then=F('recent_clicks') * 100.0 / F('recent_impressions')),
                default=Value(0),
                output_field=models.FloatField()
            ),
            conversion_rate=Case(
                When(recent_clicks__gt=0,
                     then=F('recent_conversions') * 100.0 / F('recent_clicks')),
                default=Value(0),
                output_field=models.FloatField()
            ),
            cost_per_click=Case(
                When(recent_clicks__gt=0,
                     then=F('spent_amount') / F('recent_clicks')),
                default=Value(0),
                output_field=models.DecimalField(max_digits=8, decimal_places=4)
            )
        )
    
    def needs_attention(self):
        """Get campaigns that need attention (low performance, budget issues, etc.)"""
        return self.with_performance_stats().filter(
            Q(ctr__lt=1.0) |  # CTR less than 1%
            Q(spent_amount__gte=F('budget') * 0.9) |  # 90% of budget spent
            Q(end_date__lte=timezone.now() + timedelta(days=3))  # Ending soon
        )

class AdImpressionManager(models.Manager):
    """Manager for AdImpression model"""
    
    def today(self):
        """Get impressions from today"""
        today = timezone.now().date()
        return self.filter(created_at__date=today)
    
    def this_week(self):
        """Get impressions from this week"""
        week_ago = timezone.now() - timedelta(days=7)
        return self.filter(created_at__gte=week_ago)
    
    def this_month(self):
        """Get impressions from this month"""
        month_ago = timezone.now() - timedelta(days=30)
        return self.filter(created_at__gte=month_ago)
    
    def by_campaign(self, campaign_id):
        """Get impressions for specific campaign"""
        return self.filter(campaign_id=campaign_id)
    
    def by_user(self, user):
        """Get impressions for specific user"""
        return self.filter(user=user)
    
    def by_country(self, country):
        """Get impressions by country"""
        return self.filter(user_country=country)
    
    def by_device_type(self, device_type):
        """Get impressions by device type"""
        return self.filter(device_type=device_type)
    
    def unique_users(self):
        """Get count of unique users who saw ads"""
        return self.exclude(user__isnull=True).values('user').distinct().count()
    
    def with_clicks(self):
        """Get impressions that resulted in clicks"""
        return self.filter(click__isnull=False)
    
    def without_clicks(self):
        """Get impressions that didn't result in clicks"""
        return self.filter(click__isnull=True)
    
    def high_value(self, min_cost=Decimal('0.01')):
        """Get high-value impressions"""
        return self.filter(cost__gte=min_cost)
    
    def by_date_range(self, start_date, end_date):
        """Get impressions within date range"""
        return self.filter(created_at__date__range=[start_date, end_date])
    
    def analytics_summary(self, days=30):
        """Get analytics summary for impressions"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since).aggregate(
            total_impressions=Count('id'),
            unique_users=Count('user', distinct=True),
            total_cost=Sum('cost'),
            avg_cost=Avg('cost'),
            clicks_count=Count('click'),
            ctr=Case(
                When(total_impressions__gt=0,
                     then=F('clicks_count') * 100.0 / F('total_impressions')),
                default=Value(0),
                output_field=models.FloatField()
            )
        )
    
    def by_user_demographics(self):
        """Group impressions by user demographics"""
        return self.values('user_country', 'user_age', 'user_gender', 'device_type').annotate(
            count=Count('id'),
            clicks=Count('click'),
            total_cost=Sum('cost')
        ).order_by('-count')

class AdClickManager(models.Manager):
    """Manager for AdClick model"""
    
    def valid_clicks(self):
        """Get only valid clicks (not fraud)"""
        return self.filter(is_valid=True)
    
    def today(self):
        """Get clicks from today"""
        today = timezone.now().date()
        return self.filter(created_at__date=today)
    
    def this_week(self):
        """Get clicks from this week"""
        week_ago = timezone.now() - timedelta(days=7)
        return self.filter(created_at__gte=week_ago)
    
    def this_month(self):
        """Get clicks from this month"""
        month_ago = timezone.now() - timedelta(days=30)
        return self.filter(created_at__gte=month_ago)
    
    def by_campaign(self, campaign_id):
        """Get clicks for specific campaign"""
        return self.filter(campaign_id=campaign_id)
    
    def by_user(self, user):
        """Get clicks for specific user"""
        return self.filter(user=user)
    
    def with_conversions(self):
        """Get clicks that resulted in conversions"""
        return self.filter(conversion__isnull=False)
    
    def without_conversions(self):
        """Get clicks that didn't result in conversions"""
        return self.filter(conversion__isnull=True)
    
    def quick_clicks(self, max_seconds=2):
        """Get clicks that happened very quickly (potential fraud)"""
        max_milliseconds = max_seconds * 1000
        return self.filter(time_to_click__lte=max_milliseconds)
    
    def high_fraud_score(self, min_score=7.0):
        """Get clicks with high fraud scores"""
        return self.filter(fraud_score__gte=min_score)
    
    def calculate_average_time_to_click(self):
        """Calculate average time from impression to click"""
        return self.exclude(time_to_click__isnull=True).aggregate(
            avg_time=Avg('time_to_click')
        )['avg_time'] or 0
    
    def suspicious_clicks(self):
        """Get potentially fraudulent clicks"""
        return self.filter(
            Q(time_to_click__lte=500) |  # Very quick clicks
            Q(fraud_score__gte=5.0) |
            Q(is_valid=False)
        )
    
    def by_date_range(self, start_date, end_date):
        """Get clicks within date range"""
        return self.filter(created_at__date__range=[start_date, end_date])
    
    def conversion_analytics(self, days=30):
        """Get conversion analytics for clicks"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since).aggregate(
            total_clicks=Count('id'),
            valid_clicks=Count('id', filter=Q(is_valid=True)),
            conversions=Count('conversion'),
            total_cost=Sum('cost'),
            avg_cost=Avg('cost'),
            conversion_rate=Case(
                When(total_clicks__gt=0,
                     then=F('conversions') * 100.0 / F('total_clicks')),
                default=Value(0),
                output_field=models.FloatField()
            ),
            avg_time_to_click=Avg('time_to_click'),
            fraud_rate=Case(
                When(total_clicks__gt=0,
                     then=(F('total_clicks') - F('valid_clicks')) * 100.0 / F('total_clicks')),
                default=Value(0),
                output_field=models.FloatField()
            )
        )

class AdRevenueQuerySet(models.QuerySet):
    """Custom QuerySet for AdRevenue"""
    
    def total_revenue(self):
        """Calculate total net revenue"""
        return self.aggregate(total=Sum('net_revenue'))['total'] or Decimal('0.00')
    
    def by_period(self, start_date, end_date):
        """Filter by date period"""
        return self.filter(
            period_start__gte=start_date,
            period_end__lte=end_date
        )
    
    def by_campaign(self, campaign_id):
        """Filter by campaign"""
        return self.filter(campaign_id=campaign_id)
    
    def by_provider(self, provider_id):
        """Filter by provider"""
        return self.filter(provider_id=provider_id)
    
    def by_revenue_type(self, revenue_type):
        """Filter by revenue type"""
        return self.filter(revenue_type=revenue_type)
    
    def this_month(self):
        """Get revenue from this month"""
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.filter(period_start__gte=start_of_month)
    
    def last_month(self):
        """Get revenue from last month"""
        now = timezone.now()
        if now.month == 1:
            last_month = now.replace(year=now.year-1, month=12, day=1)
        else:
            last_month = now.replace(month=now.month-1, day=1)
        
        # Get start and end of last month
        start_of_last_month = last_month.replace(hour=0, minute=0, second=0, microsecond=0)
        if last_month.month == 12:
            end_of_last_month = last_month.replace(year=last_month.year+1, month=1, day=1) - timedelta(seconds=1)
        else:
            end_of_last_month = last_month.replace(month=last_month.month+1, day=1) - timedelta(seconds=1)
        
        return self.filter(
            period_start__gte=start_of_last_month,
            period_end__lte=end_of_last_month
        )
    
    def top_campaigns(self, limit=10):
        """Get top campaigns by revenue"""
        return self.values('campaign__name', 'campaign_id').annotate(
            total_revenue=Sum('net_revenue'),
            total_impressions=Sum('impressions_count'),
            total_clicks=Sum('clicks_count'),
            total_conversions=Sum('conversions_count')
        ).order_by('-total_revenue')[:limit]
    
    def revenue_by_type(self):
        """Group revenue by type"""
        return self.values('revenue_type').annotate(
            total_revenue=Sum('net_revenue'),
            count=Count('id')
        ).order_by('-total_revenue')
    
    def daily_revenue(self, days=30):
        """Get daily revenue breakdown"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(period_start__gte=since).extra(
            select={'day': 'DATE(period_start)'}
        ).values('day').annotate(
            revenue=Sum('net_revenue'),
            impressions=Sum('impressions_count'),
            clicks=Sum('clicks_count')
        ).order_by('day')

class AdRevenueManager(models.Manager):
    """Manager for AdRevenue model"""
    
    def get_queryset(self):
        return AdRevenueQuerySet(self.model, using=self._db)
    
    def total_revenue(self):
        return self.get_queryset().total_revenue()
    
    def by_period(self, start_date, end_date):
        return self.get_queryset().by_period(start_date, end_date)
    
    def this_month(self):
        return self.get_queryset().this_month()
    
    def last_month(self):
        return self.get_queryset().last_month()
    
    def top_campaigns(self, limit=10):
        return self.get_queryset().top_campaigns(limit)
    
    def revenue_by_type(self):
        return self.get_queryset().revenue_by_type()
    
    def daily_revenue(self, days=30):
        return self.get_queryset().daily_revenue(days)

class AdConversionManager(models.Manager):
    """Manager for AdConversion model"""
    
    def by_campaign(self, campaign_id):
        """Get conversions for specific campaign"""
        return self.filter(campaign_id=campaign_id)
    
    def by_type(self, conversion_type):
        """Get conversions by type"""
        return self.filter(conversion_type=conversion_type)
    
    def by_user(self, user):
        """Get conversions for specific user"""
        return self.filter(user=user)
    
    def today(self):
        """Get conversions from today"""
        today = timezone.now().date()
        return self.filter(created_at__date=today)
    
    def this_week(self):
        """Get conversions from this week"""
        week_ago = timezone.now() - timedelta(days=7)
        return self.filter(created_at__gte=week_ago)
    
    def this_month(self):
        """Get conversions from this month"""
        month_ago = timezone.now() - timedelta(days=30)
        return self.filter(created_at__gte=month_ago)
    
    def high_value(self, min_value=Decimal('10.00')):
        """Get high-value conversions"""
        return self.filter(conversion_value__gte=min_value)
    
    def quick_conversions(self, max_minutes=30):
        """Get conversions that happened quickly after click"""
        max_seconds = max_minutes * 60
        return self.filter(time_to_conversion__lte=max_seconds)
    
    def conversion_funnel_stats(self, days=30):
        """Get conversion funnel statistics"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since).aggregate(
            total_conversions=Count('id'),
            total_value=Sum('conversion_value'),
            avg_value=Avg('conversion_value'),
            avg_time_to_conversion=Avg('time_to_conversion'),
            signup_conversions=Count('id', filter=Q(conversion_type='signup')),
            subscription_conversions=Count('id', filter=Q(conversion_type='subscription')),
            idea_conversions=Count('id', filter=Q(conversion_type='idea_generation'))
        )
    
    def by_conversion_path(self):
        """Analyze conversion paths"""
        return self.values(
            'campaign__name',
            'conversion_type',
            'click__impression__page_url'
        ).annotate(
            count=Count('id'),
            total_value=Sum('conversion_value'),
            avg_time=Avg('time_to_conversion')
        ).order_by('-count')

class AdBlockerManager(models.Manager):
    """Manager for AdBlocker model"""
    
    def detected_today(self):
        """Get ad blocker detections from today"""
        today = timezone.now().date()
        return self.filter(created_at__date=today)
    
    def by_user(self, user):
        """Get detections for specific user"""
        return self.filter(user=user)
    
    def by_blocker_type(self, blocker_type):
        """Get detections by blocker type"""
        return self.filter(blocker_type=blocker_type)
    
    def unique_users_with_blockers(self, days=30):
        """Count unique users with ad blockers"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since).exclude(
            user__isnull=True
        ).values('user').distinct().count()
    
    def blocker_statistics(self, days=30):
        """Get ad blocker statistics"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(created_at__gte=since).aggregate(
            total_detections=Count('id'),
            unique_users=Count('user', distinct=True),
            unique_ips=Count('ip_address', distinct=True)
        )
    
    def popular_blockers(self, days=30):
        """Get most popular ad blockers"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(
            created_at__gte=since,
            blocker_type__isnull=False
        ).exclude(blocker_type='').values('blocker_type').annotate(
            count=Count('id')
        ).order_by('-count')

class RewardedAdViewManager(models.Manager):
    """Manager for RewardedAdView model"""
    
    def completed_views(self):
        """Get completed rewarded ad views"""
        return self.filter(completed=True)
    
    def pending_rewards(self):
        """Get views with pending rewards"""
        return self.filter(completed=True, reward_granted=False)
    
    def by_user(self, user):
        """Get views for specific user"""
        return self.filter(user=user)
    
    def by_reward_type(self, reward_type):
        """Get views by reward type"""
        return self.filter(reward_type=reward_type)
    
    def today(self):
        """Get views from today"""
        today = timezone.now().date()
        return self.filter(created_at__date=today)
    
    def user_reward_summary(self, user, days=30):
        """Get reward summary for user"""
        since = timezone.now() - timedelta(days=days)
        return self.filter(
            user=user,
            created_at__gte=since,
            reward_granted=True
        ).values('reward_type').annotate(
            count=Count('id'),
            total_amount=Sum('reward_amount')
        ).order_by('-count')
    
    def completion_rate(self, days=30):
        """Calculate completion rate for rewarded ads"""
        since = timezone.now() - timedelta(days=days)
        stats = self.filter(created_at__gte=since).aggregate(
            total_views=Count('id'),
            completed_views=Count('id', filter=Q(completed=True))
        )
        
        if stats['total_views'] > 0:
            return (stats['completed_views'] / stats['total_views']) * 100
        return 0

class AdFrequencyCapManager(models.Manager):
    """Manager for AdFrequencyCap model"""
    
    def for_user(self, user):
        """Get frequency caps for user"""
        return self.filter(user=user)
    
    def for_ip(self, ip_address):
        """Get frequency caps for IP address"""
        return self.filter(ip_address=ip_address)
    
    def for_campaign(self, campaign):
        """Get frequency caps for campaign"""
        return self.filter(campaign=campaign)
    
    def exceeded_limits(self):
        """Get caps that have exceeded their limits"""
        return self.filter(
            Q(impressions_today__gte=F('max_impressions_per_day')) |
            Q(clicks_today__gte=F('max_clicks_per_day'))
        )
    
    def can_show_ads(self, campaign, user=None, ip_address=None):
        """Check if ads can be shown based on frequency caps"""
        try:
            if user:
                cap = self.get(campaign=campaign, user=user)
            elif ip_address:
                cap = self.get(campaign=campaign, ip_address=ip_address)
            else:
                return True
            
            return cap.can_show_ad()
        except self.model.DoesNotExist:
            return True
    
    def cleanup_old_caps(self, days=7):
        """Clean up old frequency cap records"""
        cutoff = timezone.now() - timedelta(days=days)
        return self.filter(
            last_impression__lt=cutoff,
            impressions_today=0
        ).delete()
    
    def daily_reset(self):
        """Reset daily counters for caps from previous days"""
        today = timezone.now().date()
        return self.filter(
            last_impression__date__lt=today
        ).update(
            impressions_today=0,
            clicks_today=0
        )