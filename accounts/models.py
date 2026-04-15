from django.db import models
from django.contrib.auth.models import AbstractUser
from cloudinary.models import CloudinaryField
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

class CustomUser(AbstractUser):
    email = models.EmailField(unique = True)
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(blank=True, null=True)
    performance_tracking_start_date = models.DateTimeField(blank=True, null=True)
    # Qualification access now unlocks independently so paid GCSE does not imply paid A level.
    has_gcse_paid_access = models.BooleanField(default=False)
    has_alevel_paid_access = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username',]

    @staticmethod
    def normalize_paid_access_qualification(raw_value):
        normalized = str(raw_value or '').strip().replace('-', '_').replace(' ', '_').upper()
        qualification_map = {
            'BOTH': 'BOTH',
            'ALL': 'BOTH',
            'GCSE_AND_ALEVEL': 'BOTH',
            'GCSE_AND_A_LEVEL': 'BOTH',
            'GCSE_ALEVEL': 'BOTH',
            'GCSE_A_LEVEL': 'BOTH',
            'GCSE': 'GCSE_SCIENCE',
            'GCSE_SCIENCE': 'GCSE_SCIENCE',
            'ALEVEL': 'ALEVEL_BIOLOGY',
            'A_LEVEL': 'ALEVEL_BIOLOGY',
            'ALEVEL_BIOLOGY': 'ALEVEL_BIOLOGY',
            'A_LEVEL_BIOLOGY': 'ALEVEL_BIOLOGY',
        }
        return qualification_map.get(normalized)

    def has_paid_access_for_qualification(self, qualification):
        normalized = self.normalize_paid_access_qualification(qualification)
        if normalized == 'BOTH':
            return self.has_full_paid_access
        if normalized == 'GCSE_SCIENCE':
            return self.has_gcse_paid_access
        if normalized == 'ALEVEL_BIOLOGY':
            return self.has_alevel_paid_access
        return False

    @property
    def has_full_paid_access(self):
        return self.has_gcse_paid_access and self.has_alevel_paid_access

    def __str__(self):
        return self.email

class CustomUserProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete = models.CASCADE)
    bio = models.TextField(blank = True, null = True)
    profile_img = CloudinaryField('image', blank = True, null = True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username}'s Profile"


class UserEntitlement(models.Model):
    class PlanType(models.TextChoices):
        FREE = 'free', 'Free'
        PAID = 'paid', 'Paid'
        LIFETIME = 'lifetime', 'Lifetime'

    FREE_DAILY_QUESTION_LIMIT = 2

    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='entitlement')
    # Legacy billing summary kept for compatibility with existing Stripe and frontend flows.
    plan_type = models.CharField(max_length=20, choices=PlanType.choices, default=PlanType.FREE)
    lifetime_unlocked = models.BooleanField(default=False)
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.user.email} | {self.plan_type}"

    @property
    def has_unlimited_access(self):
        # Legacy compatibility: true only when both qualifications are effectively unlocked.
        return self.lifetime_unlocked or self.user.has_full_paid_access


class QuestionUsage(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='question_usages')
    date = models.DateField(default=timezone.localdate)
    question_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='uniq_question_usage_per_user_per_day'),
        ]
        ordering = ['-date']

    def __str__(self):
        return f"{self.user.email} | {self.date} | {self.question_count}"

@receiver(post_save, sender=CustomUser)
def create_user_related_records(sender, instance, created, **kwargs):
    if created:
        CustomUserProfile.objects.get_or_create(user=instance)
        UserEntitlement.objects.get_or_create(user=instance)