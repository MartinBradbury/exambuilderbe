from datetime import datetime, timezone as datetime_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
import stripe
from accounts.models import (
    CustomUser,
    Purchase,
    StripeWebhookEvent,
    UserEntitlement,
)
from accounts.services.entitlements import (
    ensure_subject_entitlement_rows,
    subject_codes_for_purchase,
)


ACTIVE_SUBSCRIPTION_STATUSES = {'active', 'trialing', 'past_due'}
NEW_PAYMENT_ONLY_QUALIFICATIONS = {'ALEVEL_CHEMISTRY', 'ALEVEL_PHYSICS'}
SUPPORTED_CHECKOUT_MODES = {'payment', 'subscription'}


TRANSIENT_SUBSCRIPTION_STATUSES = {'incomplete'}


class CheckoutNotAllowedError(Exception):
    pass


class TransientWebhookError(Exception):
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
        raise ValueError(
            "Invalid qualification. Use 'GCSE_SCIENCE', 'ALEVEL_BIOLOGY', 'ALEVEL_CHEMISTRY', or 'ALEVEL_PHYSICS'."
        )
    if normalized == 'BOTH':
        raise CheckoutNotAllowedError('The combined purchase is no longer available. Buy each subject individually.')

    if normalized == 'GCSE_SCIENCE':
        price_id = getattr(settings, 'STRIPE_PRICE_ID_GCSE', None) or settings.STRIPE_PRICE_ID
    elif normalized == 'ALEVEL_BIOLOGY':
        price_id = getattr(settings, 'STRIPE_PRICE_ID_ALEVEL', None) or settings.STRIPE_PRICE_ID
    elif normalized == 'ALEVEL_CHEMISTRY':
        # No shared fallback: a missing price must never charge another product's price.
        price_id = getattr(settings, 'STRIPE_PRICE_ID_ALEVEL_CHEMISTRY', None)
    else:
        price_id = getattr(settings, 'STRIPE_PRICE_ID_ALEVEL_PHYSICS', None)

    if not price_id:
        raise ValueError(f'Stripe is not configured: a price id is missing for {normalized}.')
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

    entitlement, _ = UserEntitlement.objects.get_or_create(user=user)
    if user.has_paid_access_for_qualification(normalized_qualification):
        raise CheckoutNotAllowedError('This account already has paid access for this qualification.')

    # The four modules are always one-off payments, regardless of STRIPE_CHECKOUT_MODE.
    mode = 'payment'
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


def _purchase_timestamp(session):
    created = stripe_value(session, 'created')
    if created is None:
        return timezone.now()
    return datetime.fromtimestamp(created, tz=datetime_timezone.utc)


def _record_paid_checkout_purchase(session, user, qualification):
    session_id = stripe_value(session, 'id')
    if not session_id:
        return False

    payment_intent = stripe_value(session, 'payment_intent')
    payment_intent_id = stripe_value(payment_intent, 'id') if not isinstance(payment_intent, str) else payment_intent
    purchase_query = Q(stripe_checkout_session_id=session_id)
    if payment_intent_id:
        purchase_query |= Q(stripe_payment_intent_id=payment_intent_id)
    matches = Purchase.objects.select_for_update().filter(purchase_query)
    purchases = list(matches[:2])
    if len(purchases) > 1:
        return False

    product_identifiers = {
        'GCSE_SCIENCE': 'gcse_science',
        'ALEVEL_BIOLOGY': 'alevel_biology',
        'BOTH': 'both',
        'ALEVEL_CHEMISTRY': 'alevel_chemistry',
        'ALEVEL_PHYSICS': 'alevel_physics',
    }
    product_identifier = product_identifiers[qualification]
    purchase_data = {
        'user': user,
        'product_identifier': product_identifier,
        'stripe_checkout_session_id': session_id,
        'stripe_payment_intent_id': payment_intent_id or None,
        'payment_status': Purchase.PaymentStatus.PAID,
        'amount': stripe_value(session, 'amount_total'),
        'currency': stripe_value(session, 'currency'),
        'purchased_at': _purchase_timestamp(session),
        'source': Purchase.Source.STRIPE,
    }

    if purchases:
        purchase = purchases[0]
        if (
            (purchase.user_id is not None and purchase.user_id != user.pk)
            or purchase.product_identifier != product_identifier
            or (
                purchase.stripe_checkout_session_id
                and purchase.stripe_checkout_session_id != session_id
            )
            or (
                purchase.stripe_payment_intent_id
                and payment_intent_id
                and purchase.stripe_payment_intent_id != payment_intent_id
            )
        ):
            return False
        if purchase.payment_status == Purchase.PaymentStatus.PAID:
            return True
        for field, value in purchase_data.items():
            if field in {'stripe_checkout_session_id', 'stripe_payment_intent_id'}:
                if getattr(purchase, field) not in (None, '', value):
                    return False
            setattr(purchase, field, value)
        purchase.save()
        return True

    Purchase.objects.create(**purchase_data)
    return True


def sync_entitlement_from_checkout_session(session, subscription=None):
    session_mode = stripe_value(session, 'mode')
    payment_status = stripe_value(session, 'payment_status')
    if session_mode == 'payment':
        if payment_status != 'paid':
            return None
    elif session_mode == 'subscription':
        subscription_id = stripe_value(session, 'subscription')
        customer_id = stripe_value(session, 'customer')
        if not subscription_id or not customer_id:
            return None

        subscription = subscription or stripe.Subscription.retrieve(subscription_id)
        subscription_customer_id = stripe_value(subscription, 'customer')
        if (
            (stripe_value(subscription, 'status') or '').strip().lower() not in ACTIVE_SUBSCRIPTION_STATUSES
            or subscription_customer_id != customer_id
        ):
            return None
    else:
        return None

    metadata = stripe_value(session, 'metadata', {}) or {}
    qualification = CustomUser.normalize_paid_access_qualification(stripe_value(metadata, 'qualification'))
    if qualification is None:
        return None
    if session_mode != 'payment' and qualification in NEW_PAYMENT_ONLY_QUALIFICATIONS:
        return None

    client_reference_id = stripe_value(session, 'client_reference_id')
    metadata_user_id = stripe_value(metadata, 'user_id')
    if client_reference_id and metadata_user_id and str(client_reference_id) != str(metadata_user_id):
        return None

    user_id = client_reference_id or metadata_user_id
    if not user_id:
        return None

    try:
        user = CustomUser.objects.filter(pk=user_id).first()
    except (TypeError, ValueError):
        return None
    if user is None:
        return None

    with transaction.atomic():
        entitlement, _ = UserEntitlement.objects.select_for_update().get_or_create(user=user)
        session_customer_id = stripe_value(session, 'customer')
        if (
            session_customer_id
            and entitlement.stripe_customer_id
            and session_customer_id != entitlement.stripe_customer_id
        ):
            return None

        if session_mode == 'payment' and not _record_paid_checkout_purchase(session, user, qualification):
            return None

        plan_type = stripe_value(metadata, 'plan_type', UserEntitlement.PlanType.PAID)
        if plan_type == UserEntitlement.PlanType.LIFETIME and not entitlement.lifetime_unlocked:
            plan_type = UserEntitlement.PlanType.PAID
        entitlement.plan_type = plan_type

        if entitlement.lifetime_unlocked or qualification == 'BOTH':
            # The combined purchase grants both flags together in a single save.
            _save_user_access_flags(user, gcse_access=True, alevel_access=True)
        elif qualification == 'GCSE_SCIENCE':
            _save_user_access_flags(user, gcse_access=True)
        elif qualification == 'ALEVEL_BIOLOGY':
            _save_user_access_flags(user, alevel_access=True)

        _sync_legacy_plan_type(entitlement)
        ensure_subject_entitlement_rows(
            user,
            subject_codes_for_purchase(qualification, entitlement.lifetime_unlocked),
        )
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


def _subscription_for_checkout(session):
    if stripe_value(session, 'mode') != 'subscription':
        return None
    subscription_id = stripe_value(session, 'subscription')
    customer_id = stripe_value(session, 'customer')
    if not subscription_id or not customer_id:
        return None
    # Stripe API errors propagate so the event stays retryable.
    subscription = stripe.Subscription.retrieve(subscription_id)
    status = (stripe_value(subscription, 'status') or '').strip().lower()
    if stripe_value(subscription, 'customer') != customer_id:
        return None
    if status in TRANSIENT_SUBSCRIPTION_STATUSES:
        raise TransientWebhookError(f'Checkout subscription is not yet final (status: {status}).')
    if status not in ACTIVE_SUBSCRIPTION_STATUSES:
        return None
    return subscription


def _record_failed_event(webhook_event_id, exc):
    with transaction.atomic():
        webhook_event = StripeWebhookEvent.objects.select_for_update().get(pk=webhook_event_id)
        if webhook_event.processing_status == StripeWebhookEvent.ProcessingStatus.PROCESSED:
            return
        webhook_event.processing_status = StripeWebhookEvent.ProcessingStatus.FAILED
        webhook_event.attempt_count += 1
        webhook_event.error_message = str(exc)[:500]
        webhook_event.save(
            update_fields=['processing_status', 'attempt_count', 'error_message', 'updated_at']
        )


def process_stripe_webhook_event(event):
    event_id = stripe_value(event, 'id')
    event_type = stripe_value(event, 'type')
    if not event_id or not event_type:
        raise ValueError('Verified Stripe event is missing its ID or type.')

    event_data = stripe_value(event, 'data', {}) or {}
    event_object = stripe_value(event_data, 'object', {}) or {}
    object_id = stripe_value(event_object, 'id') or ''
    try:
        webhook_event, _ = StripeWebhookEvent.objects.get_or_create(
            stripe_event_id=event_id,
            defaults={
                'event_type': event_type,
                'stripe_object_id': object_id,
                'livemode': stripe_value(event, 'livemode'),
                'api_version': stripe_value(event, 'api_version') or '',
            },
        )
    except IntegrityError:
        # A concurrent delivery created the row first.
        webhook_event = StripeWebhookEvent.objects.get(stripe_event_id=event_id)

    if webhook_event.processing_status == StripeWebhookEvent.ProcessingStatus.PROCESSED:
        return False

    try:
        # External Stripe calls happen before any transaction or row lock.
        subscription = None
        ignore_reason = ''
        is_checkout = event_type in {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}
        if is_checkout and stripe_value(event_object, 'mode') == 'subscription':
            subscription = _subscription_for_checkout(event_object)
            if subscription is None:
                ignore_reason = 'Ignored: checkout subscription is missing, inactive, or mismatched.'

        # One transaction holds the event row lock through all updates and the PROCESSED mark.
        with transaction.atomic():
            webhook_event = StripeWebhookEvent.objects.select_for_update().get(pk=webhook_event.pk)
            if webhook_event.processing_status == StripeWebhookEvent.ProcessingStatus.PROCESSED:
                return False

            error_message = ignore_reason
            if ignore_reason:
                pass
            elif is_checkout:
                result = sync_entitlement_from_checkout_session(event_object, subscription=subscription)
                if result is None:
                    error_message = 'Ignored: checkout is unpaid, incomplete, conflicting, or unsupported.'
            elif event_type in {
                'customer.subscription.created',
                'customer.subscription.updated',
                'customer.subscription.deleted',
            }:
                result = sync_entitlement_from_subscription(event_object)
                if result is None:
                    error_message = 'Ignored: subscription is unassociated or has invalid qualification metadata.'
            else:
                error_message = 'Ignored: unsupported Stripe event type.'

            webhook_event.attempt_count += 1
            webhook_event.processing_status = StripeWebhookEvent.ProcessingStatus.PROCESSED
            webhook_event.processed_at = timezone.now()
            webhook_event.error_message = error_message[:500]
            webhook_event.save(
                update_fields=[
                    'processing_status',
                    'attempt_count',
                    'processed_at',
                    'error_message',
                    'updated_at',
                ]
            )
        return True
    except Exception as exc:
        # The processing transaction has rolled back; record the failure separately.
        _record_failed_event(webhook_event.pk, exc)
        raise


def sync_entitlement_from_subscription(subscription):
    subscription_id = stripe_value(subscription, 'id')
    customer_id = stripe_value(subscription, 'customer')
    metadata = stripe_value(subscription, 'metadata', {}) or {}

    # Only an explicitly stored, matching subscription ID links an event to an account;
    # sharing a Stripe customer ID with a one-off purchase is not enough.
    if not subscription_id:
        return None
    entitlement = UserEntitlement.objects.filter(stripe_subscription_id=subscription_id).first()
    if entitlement is None:
        return None

    qualification = CustomUser.normalize_paid_access_qualification(stripe_value(metadata, 'qualification'))
    if qualification is None or qualification in NEW_PAYMENT_ONLY_QUALIFICATIONS:
        return None

    with transaction.atomic():
        entitlement = UserEntitlement.objects.select_for_update().get(pk=entitlement.pk)
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
            ensure_subject_entitlement_rows(
                user,
                subject_codes_for_purchase(qualification, entitlement.lifetime_unlocked),
            )
        # Inactive subscriptions never revoke access: one-off purchases are permanent.

        _sync_legacy_plan_type(entitlement)
        entitlement.stripe_customer_id = entitlement.stripe_customer_id or customer_id
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