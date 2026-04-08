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

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username',]

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

    FREE_DAILY_QUESTION_LIMIT = 1

    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='entitlement')
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
        return self.plan_type in {self.PlanType.PAID, self.PlanType.LIFETIME} or self.lifetime_unlocked


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