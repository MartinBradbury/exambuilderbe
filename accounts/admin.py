from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Case, IntegerField, Value, When
from .services.entitlements import (
    LifetimeAccessProtectedError,
    grant_subject_access,
    revoke_subject_access,
)
from .models import (
    CustomUser,
    CustomUserProfile,
    Purchase,
    QuestionUsage,
    StripeWebhookEvent,
    Subject,
    SubjectEntitlement,
    UserEntitlement,
)


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

    actions = (
        'grant_gcse_science',
        'revoke_gcse_science',
        'grant_alevel_biology',
        'revoke_alevel_biology',
        'grant_alevel_chemistry',
        'revoke_alevel_chemistry',
        'grant_alevel_physics',
        'revoke_alevel_physics',
    )

    def _grant(self, request, queryset, code):
        for user in queryset:
            grant_subject_access(user, code)
        self.message_user(request, f'Granted {code} to {queryset.count()} user(s).')

    def _revoke(self, request, queryset, code):
        revoked, protected = 0, 0
        for user in queryset:
            try:
                revoke_subject_access(user, code)
                revoked += 1
            except LifetimeAccessProtectedError:
                protected += 1
        self.message_user(request, f'Revoked {code} for {revoked} user(s).')
        if protected:
            self.message_user(
                request,
                f'{protected} user(s) skipped: lifetime access protects {code}.',
                level=messages.WARNING,
            )

    @admin.action(description='Grant GCSE Science')
    def grant_gcse_science(self, request, queryset):
        self._grant(request, queryset, 'gcse_science')

    @admin.action(description='Revoke GCSE Science')
    def revoke_gcse_science(self, request, queryset):
        self._revoke(request, queryset, 'gcse_science')

    @admin.action(description='Grant A-level Biology')
    def grant_alevel_biology(self, request, queryset):
        self._grant(request, queryset, 'alevel_biology')

    @admin.action(description='Revoke A-level Biology')
    def revoke_alevel_biology(self, request, queryset):
        self._revoke(request, queryset, 'alevel_biology')

    @admin.action(description='Grant A-level Chemistry')
    def grant_alevel_chemistry(self, request, queryset):
        self._grant(request, queryset, 'alevel_chemistry')

    @admin.action(description='Revoke A-level Chemistry')
    def revoke_alevel_chemistry(self, request, queryset):
        self._revoke(request, queryset, 'alevel_chemistry')

    @admin.action(description='Grant A-level Physics')
    def grant_alevel_physics(self, request, queryset):
        self._grant(request, queryset, 'alevel_physics')

    @admin.action(description='Revoke A-level Physics')
    def revoke_alevel_physics(self, request, queryset):
        self._revoke(request, queryset, 'alevel_physics')

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

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        # Route through the service so legacy flags stay consistent with the row.
        grant_subject_access(obj.user, obj.subject.code)
        obj.pk = SubjectEntitlement.objects.get(user=obj.user, subject=obj.subject).pk

    def _revoke(self, request, obj):
        try:
            revoke_subject_access(obj.user, obj.subject.code)
        except LifetimeAccessProtectedError:
            self.message_user(
                request,
                f'{obj.subject.code} is protected by lifetime access for {obj.user.email}; not revoked.',
                level=messages.WARNING,
            )

    def delete_model(self, request, obj):
        self._revoke(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in list(queryset.select_related('user', 'subject')):
            self._revoke(request, obj)


@admin.register(QuestionUsage)
class QuestionUsageAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'question_count')
    list_filter = ('date',)
    search_fields = ('user__email', 'user__username')


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        'product_identifier',
        'user',
        'payment_status',
        'amount',
        'currency',
        'purchased_at',
        'source',
        'stripe_checkout_session_id',
    )
    list_filter = ('product_identifier', 'payment_status', 'source', 'currency')
    search_fields = (
        'user__email',
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
    )
    list_select_related = ('user',)
    readonly_fields = (
        'stripe_checkout_session_id',
        'stripe_payment_intent_id',
        'payment_status',
        'amount',
        'currency',
        'purchased_at',
        'source',
        'created_at',
        'updated_at',
    )


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        'stripe_event_id',
        'event_type',
        'processing_status',
        'attempt_count',
        'created_at',
        'processed_at',
    )
    list_filter = ('processing_status', 'event_type')
    search_fields = ('stripe_event_id', 'stripe_object_id')
    readonly_fields = (
        'stripe_event_id',
        'event_type',
        'processing_status',
        'attempt_count',
        'created_at',
        'processed_at',
        'updated_at',
        'error_message',
        'stripe_object_id',
        'livemode',
        'api_version',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False