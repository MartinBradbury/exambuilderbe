from django.conf import settings
from django.utils import timezone
import stripe
from accounts.models import CustomUser, UserEntitlement


ACTIVE_SUBSCRIPTION_STATUSES = {'active', 'trialing', 'past_due'}
SUPPORTED_CHECKOUT_MODES = {'payment', 'subscription'}


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


def create_stripe_checkout_session(user, success_url=None, cancel_url=None):
    _configure_stripe()

    if not settings.STRIPE_PRICE_ID:
        raise ValueError('Stripe is not configured: STRIPE_PRICE_ID is missing.')

    entitlement, _ = UserEntitlement.objects.get_or_create(user=user)
    mode = _get_checkout_mode()
    checkout_session_params = {
        'mode': mode,
        'line_items': [{'price': settings.STRIPE_PRICE_ID, 'quantity': 1}],
        'success_url': success_url or settings.STRIPE_SUCCESS_URL,
        'cancel_url': cancel_url or settings.STRIPE_CANCEL_URL,
        'client_reference_id': str(user.id),
        'metadata': {
            'user_id': str(user.id),
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

    entitlement, _ = UserEntitlement.objects.get_or_create(user=user)
    entitlement.plan_type = stripe_value(metadata, 'plan_type', UserEntitlement.PlanType.PAID)
    entitlement.lifetime_unlocked = entitlement.plan_type == UserEntitlement.PlanType.LIFETIME
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

    entitlement = None
    if subscription_id:
        entitlement = UserEntitlement.objects.filter(stripe_subscription_id=subscription_id).first()
    if entitlement is None and customer_id:
        entitlement = UserEntitlement.objects.filter(stripe_customer_id=customer_id).first()
    if entitlement is None:
        return None

    status = (stripe_value(subscription, 'status') or '').strip().lower()
    if status in ACTIVE_SUBSCRIPTION_STATUSES:
        entitlement.plan_type = UserEntitlement.PlanType.PAID
        entitlement.paid_at = entitlement.paid_at or timezone.now()
    else:
        entitlement.plan_type = UserEntitlement.PlanType.FREE
    entitlement.lifetime_unlocked = False
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