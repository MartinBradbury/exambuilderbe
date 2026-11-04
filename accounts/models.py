from django.db import models
from django.conf import settings
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
            'CHEMISTRY': 'ALEVEL_CHEMISTRY',
            'ALEVEL_CHEMISTRY': 'ALEVEL_CHEMISTRY',
            'A_LEVEL_CHEMISTRY': 'ALEVEL_CHEMISTRY',
            'PHYSICS': 'ALEVEL_PHYSICS',
            'ALEVEL_PHYSICS': 'ALEVEL_PHYSICS',
            'A_LEVEL_PHYSICS': 'ALEVEL_PHYSICS',
        }
        return qualification_map.get(normalized)

    # Subjects that historical purchases, flags and lifetime access already cover.
    LEGACY_SUBJECT_FLAGS = {
        'gcse_science': 'has_gcse_paid_access',
        'alevel_biology': 'has_alevel_paid_access',
    }
    # Subjects that require an explicit SubjectEntitlement; no legacy flag grants them.
    EXPLICIT_ONLY_SUBJECT_CODES = ('alevel_chemistry', 'alevel_physics')

    def has_paid_access_for_qualification(self, qualification):
        normalized = self.normalize_paid_access_qualification(qualification)
        if normalized == 'BOTH':
            return self.has_entitlement_for_subject('gcse_science') and self.has_entitlement_for_subject(
                'alevel_biology'
            )
        if normalized == 'GCSE_SCIENCE':
            return self.has_entitlement_for_subject('gcse_science')
        if normalized == 'ALEVEL_BIOLOGY':
            return self.has_entitlement_for_subject('alevel_biology')
        if normalized == 'ALEVEL_CHEMISTRY':
            return self.has_entitlement_for_subject('alevel_chemistry')
        if normalized == 'ALEVEL_PHYSICS':
            return self.has_entitlement_for_subject('alevel_physics')
        return False

    def has_subject_access(self, subject_code):
        return self.subject_entitlements.filter(subject__code=subject_code).exists()

    def has_entitlement_for_subject(self, subject_code):
        """Central subject permission check.

        GCSE Science and A-level Biology accept the historical flag, lifetime access
        or an explicit row. Every other subject requires an explicit row only.
        """
        if not self.pk:
            return False
        flag_name = self.LEGACY_SUBJECT_FLAGS.get(subject_code)
        if flag_name is not None:
            if getattr(self, flag_name):
                return True
            if UserEntitlement.objects.filter(user_id=self.pk, lifetime_unlocked=True).exists():
                return True
        elif subject_code not in self.EXPLICIT_ONLY_SUBJECT_CODES:
            return False
        return self.subject_entitlements.filter(
            subject__code=subject_code, subject__is_active=True
        ).exists()

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

class Subject(models.Model):
    class Qualification(models.TextChoices):
        GCSE = 'GCSE', 'GCSE'
        A_LEVEL = 'A_LEVEL', 'A-level'

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    qualification = models.CharField(max_length=20, choices=Qualification.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['qualification', 'name']

    def __str__(self):
        return f"{self.get_qualification_display()} {self.name}"


class SubjectEntitlement(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='subject_entitlements')
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='entitlements')
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'subject'], name='uniq_subject_entitlement_per_user'),
        ]
        ordering = ['-granted_at']

    def __str__(self):
        return f"{self.user.email} | {self.subject.code}"


class Purchase(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'
        UNKNOWN = 'unknown', 'Unknown'

    class Source(models.TextChoices):
        STRIPE = 'stripe', 'Verified Stripe transaction'
        LEGACY_SNAPSHOT = 'legacy_snapshot', 'Incomplete legacy snapshot'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='purchases',
    )
    product_identifier = models.CharField(
        max_length=64,
        help_text='Extensible product key, e.g. gcse_science, alevel_biology, both, alevel_bundle.',
    )
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True)
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
    )
    amount = models.PositiveBigIntegerField(
        blank=True,
        null=True,
        help_text='Amount in the smallest currency unit.',
    )
    currency = models.CharField(max_length=3, blank=True, null=True)
    purchased_at = models.DateTimeField(blank=True, null=True)
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.STRIPE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-purchased_at', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['stripe_checkout_session_id'],
                condition=(
                    models.Q(stripe_checkout_session_id__isnull=False)
                    & ~models.Q(stripe_checkout_session_id='')
                ),
                name='uniq_purchase_checkout_session',
            ),
            models.UniqueConstraint(
                fields=['stripe_payment_intent_id'],
                condition=(
                    models.Q(stripe_payment_intent_id__isnull=False)
                    & ~models.Q(stripe_payment_intent_id='')
                ),
                name='uniq_purchase_payment_intent',
            ),
        ]
        indexes = [
            models.Index(fields=['user', '-purchased_at'], name='purchase_user_date_idx'),
            models.Index(fields=['product_identifier', 'payment_status'], name='purchase_product_status_idx'),
        ]

    def __str__(self):
        return f'{self.product_identifier} | {self.payment_status} | {self.stripe_checkout_session_id or "no session"}'


class StripeWebhookEvent(models.Model):
    class ProcessingStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSED = 'processed', 'Processed'
        FAILED = 'failed', 'Failed'

    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    error_message = models.CharField(max_length=500, blank=True)
    stripe_object_id = models.CharField(max_length=255, blank=True)
    livemode = models.BooleanField(blank=True, null=True)
    api_version = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['processing_status', 'created_at'], name='stripe_event_status_idx'),
            models.Index(fields=['event_type', 'created_at'], name='stripe_event_type_created_idx'),
        ]

    def __str__(self):
        return f'{self.event_type} | {self.stripe_event_id} | {self.processing_status}'


@receiver(post_save, sender=CustomUser)
def create_user_related_records(sender, instance, created, **kwargs):
    if created:
        CustomUserProfile.objects.get_or_create(user=instance)
        UserEntitlement.objects.get_or_create(user=instance)