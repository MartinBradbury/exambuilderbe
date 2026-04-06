from django.urls import path
from .views import (
    PasswordResetConfirmAPIView,
    PasswordResetRequestAPIView,
    StripeCheckoutSessionAPIView,
    StripeWebhookAPIView,
    UserInfoAPIView,
    UserLoginAPIView,
    UserLogoutAPIView,
    UserRegistrationAPIView,
)
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('register/', UserRegistrationAPIView.as_view(), name='user-registration'),
    path('login/', UserLoginAPIView.as_view(), name='user-login'),
    path('logout/', UserLogoutAPIView.as_view(), name='user-logout'),
    path('token/refresh/', TokenRefreshView.as_view(), name = 'token-view'),
    path('user/', UserInfoAPIView.as_view(), name="user-info"),
    path('password-reset/', PasswordResetRequestAPIView.as_view(), name='password-reset-request'),
    path('password-reset/confirm/', PasswordResetConfirmAPIView.as_view(), name='password-reset-confirm'),
    path('billing/create-checkout-session/', StripeCheckoutSessionAPIView.as_view(), name='stripe-checkout-session'),
    path('billing/webhook/', StripeWebhookAPIView.as_view(), name='stripe-webhook'),

]