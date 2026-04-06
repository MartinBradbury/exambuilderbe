from rest_framework.generics import GenericAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from .models import CustomUser
from .serializers import (
    CustomUserSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    UserLoginSerializer,
    UserRegistrationSerializer,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from rest_framework import status


class UserRegistrationAPIView(GenericAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception = True)
        user = serializer.save()
        token = RefreshToken.for_user(user)
        data = {
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