from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, CustomUserProfile, QuestionUsage, UserEntitlement

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'username',)
    list_filter = ('email', 'username',)

@admin.register(CustomUserProfile)
class CustomUserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'bio', 'created_at')
    search_fields = ('user__email', 'user__username',)


@admin.register(UserEntitlement)
class UserEntitlementAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan_type', 'lifetime_unlocked', 'paid_at')
    list_filter = ('plan_type', 'lifetime_unlocked', 'paid_at')
    search_fields = ('user__email', 'user__username', 'stripe_customer_id', 'stripe_checkout_session_id')


@admin.register(QuestionUsage)
class QuestionUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'question_count')
    list_filter = ('date',)
    search_fields = ('user__email', 'user__username')