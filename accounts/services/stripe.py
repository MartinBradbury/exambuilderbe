from django.conf import settings
from django.db import transaction
from django.utils import timezone
import stripe
from accounts.models import CustomUser, UserEntitlement


ACTIVE_SUBSCRIPTION_STATUSES = {'active', 'trialing', 'past_due'}
SUPPORTED_CHECKOUT_MODES = {'payment', 'subscription'}


class CheckoutNotAllowedError(Exception):
    pass


class EmailVerificationRequiredError(Exception):
    pass


def stripe_value(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except Exception:
        return getattr(obj, key, default)


def _get_checkout_mode():
    configured_mode = (settings.STRIPE_CHECKOUT_MODE or 'payment').strip().lower()
    return configured_mode if configured_mode in SUPPORTED_CHECKOUT_MODES else 'payment'


def _configure_stripe():
    if not settings.STRIPE_SECRET_KEY:
        raise ValueError('Stripe is not configured: STRIPE_SECRET_KEY is missing.')
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _get_price_id_for_qualification(qualification):
    normalized = CustomUser.normalize_paid_access_qualification(qualification)
    if not normalized:
        raise ValueError("Invalid qualification. Use 'GCSE_SCIENCE', 'ALEVEL_BIOLOGY', or 'BOTH'.")

    if normalized == 'BOTH':
        price_id = getattr(settings, 'STRIPE_PRICE_ID_BOTH', None) or settings.STRIPE_PRICE_ID
    elif normalized == 'GCSE_SCIENCE':
        price_id = getattr(settings, 'STRIPE_PRICE_ID_GCSE', None) or settings.STRIPE_PRICE_ID
    else:
        price_id = getattr(settings, 'STRIPE_PRICE_ID_ALEVEL', None) or settings.STRIPE_PRICE_ID

    if not price_id:
        raise ValueError('Stripe is not configured: a price id is missing for the requested qualification.')
    return normalized, price_id


def _save_user_access_flags(user, *, gcse_access=None, alevel_access=None):
    updated_fields = []

    if gcse_access is not None and user.has_gcse_paid_access != gcse_access:
        user.has_gcse_paid_access = gcse_access
        updated_fields.append('has_gcse_paid_access')
    if alevel_access is not None and user.has_alevel_paid_access != alevel_access:
        user.has_alevel_paid_access = alevel_access
        updated_fields.append('has_alevel_paid_access')

    if updated_fields:
        user.save(update_fields=updated_fields)


def _sync_legacy_plan_type(entitlement):
    user = entitlement.user

    if entitlement.lifetime_unlocked:
        _save_user_access_flags(user, gcse_access=True, alevel_access=True)
        entitlement.plan_type = UserEntitlement.PlanType.LIFETIME
        return

    if user.has_gcse_paid_access or user.has_alevel_paid_access:
        entitlement.plan_type = UserEntitlement.PlanType.PAID
    else:
        entitlement.plan_type = UserEntitlement.PlanType.FREE


def create_stripe_checkout_session(user, qualification, success_url=None, cancel_url=None):
    _configure_stripe()
    normalized_qualification, price_id = _get_price_id_for_qualification(qualification)

    if not user.email_verified:
        raise EmailVerificationRequiredError('Please verify your email before starting checkout.')

    entitlement, _ = UserEntitlement.objects.get_or_create(user=user)
    if user.has_paid_access_for_qualification(normalized_qualification):
        raise CheckoutNotAllowedError('This account already has paid access for this qualification.')

    mode = _get_checkout_mode()
    checkout_session_params = {
        'mode': mode,
        'line_items': [{'price': price_id, 'quantity': 1}],
        'success_url': success_url or settings.STRIPE_SUCCESS_URL,
        'cancel_url': cancel_url or settings.STRIPE_CANCEL_URL,
        'client_reference_id': str(user.id),
        'metadata': {
            'user_id': str(user.id),
            'qualification': normalized_qualification,
            'plan_type': UserEntitlement.PlanType.PAID,
        },
        'allow_promotion_codes': True,
    }

    if entitlement.stripe_customer_id:
        checkout_session_params['customer'] = entitlement.stripe_customer_id
    else:
        checkout_session_params['customer_email'] = user.email

    session = stripe.checkout.Session.create(**checkout_session_params)
    entitlement.stripe_checkout_session_id = stripe_value(session, 'id')
    entitlement.save(update_fields=['stripe_checkout_session_id'])
    return session


def construct_stripe_event(payload, signature):
    _configure_stripe()

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise ValueError('Stripe is not configured: STRIPE_WEBHOOK_SECRET is missing.')

    return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET)


def sync_entitlement_from_checkout_session(session):
    metadata = stripe_value(session, 'metadata', {}) or {}
    user_id = stripe_value(session, 'client_reference_id') or stripe_value(metadata, 'user_id')
    if not user_id:
        return None

    user = CustomUser.objects.filter(pk=user_id).first()
    if user is None:
        return None

    with transaction.atomic():
        entitlement, _ = UserEntitlement.objects.select_for_update().get_or_create(user=user)
        qualification = CustomUser.normalize_paid_access_qualification(stripe_value(metadata, 'qualification'))
        entitlement.plan_type = stripe_value(metadata, 'plan_type', UserEntitlement.PlanType.PAID)
        entitlement.lifetime_unlocked = entitlement.plan_type == UserEntitlement.PlanType.LIFETIME

        if entitlement.lifetime_unlocked or qualification == 'BOTH':
            # The combined purchase grants both flags together in a single save.
            _save_user_access_flags(user, gcse_access=True, alevel_access=True)
        elif qualification == 'GCSE_SCIENCE':
            _save_user_access_flags(user, gcse_access=True)
        elif qualification == 'ALEVEL_BIOLOGY':
            _save_user_access_flags(user, alevel_access=True)
        else:
            _save_user_access_flags(user, gcse_access=True, alevel_access=True)

        _sync_legacy_plan_type(entitlement)
        entitlement.stripe_customer_id = stripe_value(session, 'customer') or entitlement.stripe_customer_id
        entitlement.stripe_checkout_session_id = stripe_value(session, 'id') or entitlement.stripe_checkout_session_id
        entitlement.stripe_subscription_id = stripe_value(session, 'subscription') or entitlement.stripe_subscription_id
        entitlement.paid_at = timezone.now()
        entitlement.save(
            update_fields=[
                'plan_type',
                'lifetime_unlocked',
                'stripe_customer_id',
                'stripe_checkout_session_id',
                'stripe_subscription_id',
                'paid_at',
            ]
        )
    return entitlement


def sync_entitlement_from_subscription(subscription):
    subscription_id = stripe_value(subscription, 'id')
    customer_id = stripe_value(subscription, 'customer')
    metadata = stripe_value(subscription, 'metadata', {}) or {}

    entitlement = None
    if subscription_id:
        entitlement = UserEntitlement.objects.filter(stripe_subscription_id=subscription_id).first()
    if entitlement is None and customer_id:
        entitlement = UserEntitlement.objects.filter(stripe_customer_id=customer_id).first()
    if entitlement is None:
        return None

    with transaction.atomic():
        entitlement = UserEntitlement.objects.select_for_update().get(pk=entitlement.pk)
        qualification = CustomUser.normalize_paid_access_qualification(stripe_value(metadata, 'qualification'))
        user = entitlement.user
        status = (stripe_value(subscription, 'status') or '').strip().lower()
        if status in ACTIVE_SUBSCRIPTION_STATUSES:
            if qualification == 'BOTH':
                _save_user_access_flags(user, gcse_access=True, alevel_access=True)
            elif qualification == 'GCSE_SCIENCE':
                _save_user_access_flags(user, gcse_access=True)
            elif qualification == 'ALEVEL_BIOLOGY':
                _save_user_access_flags(user, alevel_access=True)
            entitlement.paid_at = entitlement.paid_at or timezone.now()
        else:
            if qualification == 'BOTH':
                _save_user_access_flags(user, gcse_access=False, alevel_access=False)
            elif qualification == 'GCSE_SCIENCE':
                _save_user_access_flags(user, gcse_access=False)
            elif qualification == 'ALEVEL_BIOLOGY':
                _save_user_access_flags(user, alevel_access=False)
            elif not entitlement.lifetime_unlocked:
                _save_user_access_flags(user, gcse_access=False, alevel_access=False)
            entitlement.lifetime_unlocked = False

        _sync_legacy_plan_type(entitlement)
        entitlement.stripe_subscription_id = subscription_id or entitlement.stripe_subscription_id
        entitlement.stripe_customer_id = customer_id or entitlement.stripe_customer_id
        entitlement.save(
            update_fields=[
                'plan_type',
                'lifetime_unlocked',
                'stripe_subscription_id',
                'stripe_customer_id',
                'paid_at',
            ]
        )
    return entitlement