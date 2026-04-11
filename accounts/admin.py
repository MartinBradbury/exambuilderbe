from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, CustomUserProfile, QuestionUsage, UserEntitlement


def membership_label_for_user(user):
    if user.has_gcse_paid_access and user.has_alevel_paid_access:
        return 'GCSE + A Level'
    if user.has_gcse_paid_access:
        return 'GCSE'
    if user.has_alevel_paid_access:
        return 'A Level'
    return 'Free'

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'username', 'membership_type', 'email_verified', 'email_verified_at')
    list_filter = ('email', 'username', 'email_verified', 'has_gcse_paid_access', 'has_alevel_paid_access')
    fieldsets = UserAdmin.fieldsets + (
        ('Verification', {'fields': ('email_verified', 'email_verified_at')}),
        ('Membership', {'fields': ('has_gcse_paid_access', 'has_alevel_paid_access')}),
    )
    readonly_fields = ('email_verified_at',)

    @admin.display(description='Membership')
    def membership_type(self, obj):
        return membership_label_for_user(obj)

@admin.register(CustomUserProfile)
class CustomUserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'bio', 'created_at')
    search_fields = ('user__email', 'user__username',)


@admin.register(UserEntitlement)
class UserEntitlementAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'membership_type',
        'plan_type',
        'has_unlimited_access_display',
        'lifetime_unlocked',
        'stripe_customer_id',
        'stripe_checkout_session_id',
        'stripe_subscription_id',
        'paid_at',
    )
    list_filter = ('plan_type', 'lifetime_unlocked', 'paid_at')
    search_fields = (
        'user__email',
        'user__username',
        'stripe_customer_id',
        'stripe_checkout_session_id',
        'stripe_subscription_id',
    )
    readonly_fields = ('paid_at',)
    list_select_related = ('user',)
    actions = ('mark_as_free', 'mark_as_paid', 'mark_as_lifetime')
    fieldsets = (
        ('User', {'fields': ('user',)}),
        ('Plan', {'fields': ('plan_type', 'lifetime_unlocked', 'paid_at')}),
        (
            'Stripe',
            {
                'fields': (
                    'stripe_customer_id',
                    'stripe_checkout_session_id',
                    'stripe_subscription_id',
                )
            },
        ),
    )

    @admin.display(boolean=True, description='Unlimited')
    def has_unlimited_access_display(self, obj):
        return obj.has_unlimited_access

    @admin.display(description='Membership')
    def membership_type(self, obj):
        return membership_label_for_user(obj.user)

    @admin.action(description='Mark selected users as free')
    def mark_as_free(self, request, queryset):
        queryset.update(plan_type=UserEntitlement.PlanType.FREE, lifetime_unlocked=False)

    @admin.action(description='Mark selected users as paid')
    def mark_as_paid(self, request, queryset):
        queryset.update(plan_type=UserEntitlement.PlanType.PAID, lifetime_unlocked=False)

    @admin.action(description='Mark selected users as lifetime')
    def mark_as_lifetime(self, request, queryset):
        queryset.update(plan_type=UserEntitlement.PlanType.LIFETIME, lifetime_unlocked=True)


@admin.register(QuestionUsage)
class QuestionUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'question_count')
    list_filter = ('date',)
    search_fields = ('user__email', 'user__username')