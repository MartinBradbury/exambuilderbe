from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Case, IntegerField, Value, When
from .models import CustomUser, CustomUserProfile, QuestionUsage, Subject, SubjectEntitlement, UserEntitlement


def membership_label_for_user(user):
    if user.has_gcse_paid_access and user.has_alevel_paid_access:
        return 'GCSE + A Level'
    if user.has_gcse_paid_access:
        return 'GCSE'
    if user.has_alevel_paid_access:
        return 'A Level'
    return 'Free'


class MembershipTypeFilter(admin.SimpleListFilter):
    title = 'membership type'
    parameter_name = 'membership_type'

    def lookups(self, request, model_admin):
        return (
            ('free', 'Free'),
            ('gcse', 'GCSE'),
            ('alevel', 'A Level'),
            ('both', 'GCSE + A Level'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'free':
            return queryset.filter(
                has_gcse_paid_access=False,
                has_alevel_paid_access=False,
            )
        if value == 'gcse':
            return queryset.filter(
                has_gcse_paid_access=True,
                has_alevel_paid_access=False,
            )
        if value == 'alevel':
            return queryset.filter(
                has_gcse_paid_access=False,
                has_alevel_paid_access=True,
            )
        if value == 'both':
            return queryset.filter(
                has_gcse_paid_access=True,
                has_alevel_paid_access=True,
            )
        return queryset

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'username', 'membership_type', 'email_verified', 'email_verified_at')
    list_filter = (
        MembershipTypeFilter,
        'email',
        'username',
        'email_verified',
        'has_gcse_paid_access',
        'has_alevel_paid_access',
    )
    fieldsets = UserAdmin.fieldsets + (
        ('Verification', {'fields': ('email_verified', 'email_verified_at')}),
        ('Membership', {'fields': ('has_gcse_paid_access', 'has_alevel_paid_access')}),
    )
    readonly_fields = ('email_verified_at',)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            membership_sort_rank=Case(
                When(has_gcse_paid_access=True, has_alevel_paid_access=True, then=Value(4)),
                When(has_gcse_paid_access=True, has_alevel_paid_access=False, then=Value(3)),
                When(has_gcse_paid_access=False, has_alevel_paid_access=True, then=Value(2)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )

    @admin.display(description='Membership', ordering='membership_sort_rank')
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


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'qualification', 'is_active')
    list_filter = ('qualification', 'is_active')
    search_fields = ('name', 'code')


@admin.register(SubjectEntitlement)
class SubjectEntitlementAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject', 'granted_at')
    list_filter = ('subject',)
    search_fields = ('user__email', 'subject__code')
    list_select_related = ('user', 'subject')
    autocomplete_fields = ('user',)


@admin.register(QuestionUsage)
class QuestionUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'question_count')
    list_filter = ('date',)
    search_fields = ('user__email', 'user__username')