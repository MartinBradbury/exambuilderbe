from rest_framework.generics import GenericAPIView, RetrieveAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
import stripe
import logging
from .models import CustomUser
from .services.stripe import (
    CheckoutNotAllowedError,
    create_stripe_checkout_session,
    construct_stripe_event,
    stripe_value,
    sync_entitlement_from_checkout_session,
    sync_entitlement_from_subscription,
)
from .serializers import (
    CustomUserSerializer,
    EmailVerificationConfirmSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    StripeCheckoutSessionSerializer,
    UserLoginSerializer,
    UserRegistrationSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from rest_framework import status


logger = logging.getLogger(__name__)


def send_email_verification_email(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    verification_link = f"{settings.EMAIL_VERIFICATION_URL}?uid={uid}&token={token}"
    message = (
        'Welcome to ExamBuilder.\n\n'
        f'Verify your email address using this link:\n{verification_link}\n\n'
        'If you did not create this account, you can ignore this email.'
    )
    send_mail(
        subject='Verify your ExamBuilder email',
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


class UserRegistrationAPIView(GenericAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception = True)
        user = serializer.save()
        send_email_verification_email(user)
        token = RefreshToken.for_user(user)
        data = {
            'user': CustomUserSerializer(user).data,
            'refresh': str(token),
            'access': str(token.access_token)
        }
        return Response(data, status=status.HTTP_201_CREATED)
    
class UserLoginAPIView(GenericAPIView):
    serializer_class = UserLoginSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data  # This assumes your login serializer returns the user object

        user_data = CustomUserSerializer(user).data
        token = RefreshToken.for_user(user)

        response_data = {
            'user': user_data,
            'refresh': str(token),
            'access': str(token.access_token),
        }

        return Response(response_data, status=status.HTTP_200_OK)
    
class UserLogoutAPIView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        
        try:
            refresh_token = request.data['refresh']
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response(status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        
class UserInfoAPIView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CustomUserSerializer

    def get_object(self):
        return self.request.user


class ResetPerformanceTrackingAPIView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        request.user.performance_tracking_start_date = timezone.now()
        request.user.save(update_fields=['performance_tracking_start_date'])
        return Response(
            {
                'detail': 'Performance tracking reset successfully.',
                'performance_tracking_start_date': request.user.performance_tracking_start_date,
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetRequestAPIView(GenericAPIView):
    serializer_class = PasswordResetRequestSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data['email']
        user = self._get_active_user(email)
        if user is not None:
            self._send_password_reset_email(user)

        return Response(
            {'detail': 'If an account exists for that email, a password reset link has been sent.'},
            status=status.HTTP_200_OK,
        )

    def _get_active_user(self, email):
        return (
            CustomUser.objects.filter(email__iexact=email, is_active=True)
            .order_by('id')
            .first()
        )

    def _send_password_reset_email(self, user):
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        reset_link = f"{settings.PASSWORD_RESET_URL}?uid={uid}&token={token}"
        message = (
            'You requested a password reset for your ExamBuilder account.\n\n'
            f'Use this link to reset your password:\n{reset_link}\n\n'
            'If you did not request this, you can ignore this email.'
        )
        send_mail(
            subject='Reset your ExamBuilder password',
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )


class PasswordResetConfirmAPIView(GenericAPIView):
    serializer_class = PasswordResetConfirmSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'detail': 'Password has been reset successfully.'}, status=status.HTTP_200_OK)


class EmailVerificationConfirmAPIView(GenericAPIView):
    serializer_class = EmailVerificationConfirmSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                'detail': 'Email verified successfully.',
                'email_verified': user.email_verified,
                'email_verified_at': user.email_verified_at,
            },
            status=status.HTTP_200_OK,
        )


class EmailVerificationResendAPIView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user
        if user.email_verified:
            return Response({'detail': 'Email is already verified.'}, status=status.HTTP_200_OK)

        send_email_verification_email(user)
        return Response({'detail': 'Verification email sent.'}, status=status.HTTP_200_OK)


class StripeCheckoutSessionAPIView(GenericAPIView):
    serializer_class = StripeCheckoutSessionSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            session = create_stripe_checkout_session(
                user=request.user,
                qualification=serializer.validated_data['qualification'],
                success_url=serializer.validated_data.get('success_url'),
                cancel_url=serializer.validated_data.get('cancel_url'),
            )
        except CheckoutNotAllowedError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except stripe.error.StripeError as exc:
            logger.exception('Stripe checkout session creation failed for user %s', request.user.id)
            return Response({'detail': 'Unable to create a Stripe checkout session right now.'}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:
            logger.exception('Unexpected checkout session failure for user %s', request.user.id)
            return Response({'detail': 'Unexpected error while starting checkout.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(
            {
                'checkout_url': stripe_value(session, 'url'),
                'session_id': stripe_value(session, 'id'),
                'publishable_key': settings.STRIPE_PUBLISHABLE_KEY,
                'mode': settings.STRIPE_CHECKOUT_MODE,
            },
            status=status.HTTP_200_OK,
        )


class StripeWebhookAPIView(GenericAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        signature = request.META.get('HTTP_STRIPE_SIGNATURE', '')

        try:
            event = construct_stripe_event(request.body, signature)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except stripe.error.SignatureVerificationError:
            return Response({'detail': 'Invalid Stripe signature.'}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.error.StripeError:
            return Response({'detail': 'Unable to validate Stripe webhook.'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception('Unexpected Stripe webhook validation failure')
            return Response({'detail': 'Unexpected error while validating Stripe webhook.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        event_type = stripe_value(event, 'type')
        event_data = stripe_value(event, 'data', {}) or {}
        event_object = stripe_value(event_data, 'object', {}) or {}

        try:
            if event_type in {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}:
                sync_entitlement_from_checkout_session(event_object)
            elif event_type in {'customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted'}:
                sync_entitlement_from_subscription(event_object)
        except Exception:
            logger.exception('Unexpected Stripe webhook processing failure for event %s', event_type)
            return Response({'detail': 'Unexpected error while processing Stripe webhook.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'received': True}, status=status.HTTP_200_OK)