from django.db import transaction

from accounts.models import CustomUser, Subject, SubjectEntitlement, UserEntitlement

LEGACY_SUBJECT_FLAGS = CustomUser.LEGACY_SUBJECT_FLAGS
SUBJECT_CODES = tuple(LEGACY_SUBJECT_FLAGS) + CustomUser.EXPLICIT_ONLY_SUBJECT_CODES

QUALIFICATION_SUBJECT_CODES = {
    'GCSE_SCIENCE': ('gcse_science',),
    'ALEVEL_BIOLOGY': ('alevel_biology',),
    'BOTH': ('gcse_science', 'alevel_biology'),
}


class LifetimeAccessProtectedError(Exception):
    """Raised when a revoke would override a historical lifetime purchase."""


def _validate_code(subject_code):
    if subject_code not in SUBJECT_CODES:
        raise ValueError(f'Unsupported subject code: {subject_code}')


def ensure_subject_entitlement_rows(user, subject_codes):
    """Idempotently create rows only; never touches legacy flags."""
    created = []
    for code in subject_codes:
        _validate_code(code)
        subject = Subject.objects.get(code=code)
        _, was_created = SubjectEntitlement.objects.get_or_create(user=user, subject=subject)
        if was_created:
            created.append(code)
    return created


def grant_subject_access(user, subject_code):
    """Admin/manual grant: row plus matching legacy flag for GCSE and Biology."""
    _validate_code(subject_code)
    with transaction.atomic():
        created = ensure_subject_entitlement_rows(user, [subject_code])
        flag_name = LEGACY_SUBJECT_FLAGS.get(subject_code)
        if flag_name:
            if not getattr(user, flag_name):
                setattr(user, flag_name, True)
                user.save(update_fields=[flag_name])
            entitlement, _ = UserEntitlement.objects.get_or_create(user=user)
            if entitlement.plan_type == UserEntitlement.PlanType.FREE:
                entitlement.plan_type = UserEntitlement.PlanType.PAID
                entitlement.save(update_fields=['plan_type'])
    return bool(created)


def revoke_subject_access(user, subject_code):
    """Remove every effective permission source for one subject.

    Refuses for GCSE Science and A-level Biology when the user holds lifetime
    access: lifetime re-asserts both flags, so a revoke could not be made reliable
    and would override a historical purchase.
    """
    _validate_code(subject_code)
    flag_name = LEGACY_SUBJECT_FLAGS.get(subject_code)
    with transaction.atomic():
        entitlement, _ = UserEntitlement.objects.select_for_update().get_or_create(user=user)
        if flag_name and entitlement.lifetime_unlocked:
            raise LifetimeAccessProtectedError(subject_code)

        deleted, _ = SubjectEntitlement.objects.filter(user=user, subject__code=subject_code).delete()
        if flag_name and getattr(user, flag_name):
            setattr(user, flag_name, False)
            user.save(update_fields=[flag_name])
            deleted += 1
        if (
            flag_name
            and entitlement.plan_type == UserEntitlement.PlanType.PAID
            and not user.has_gcse_paid_access
            and not user.has_alevel_paid_access
        ):
            entitlement.plan_type = UserEntitlement.PlanType.FREE
            entitlement.save(update_fields=['plan_type'])
    return bool(deleted)


def missing_legacy_subject_codes(user):
    """Subject codes the user's historical access covers but that have no row."""
    entitlement = UserEntitlement.objects.filter(user=user).first()
    lifetime = bool(entitlement and entitlement.lifetime_unlocked)
    existing = set(user.subject_entitlements.values_list('subject__code', flat=True))
    return [
        code
        for code, flag_name in LEGACY_SUBJECT_FLAGS.items()
        if (lifetime or getattr(user, flag_name)) and code not in existing
    ]
