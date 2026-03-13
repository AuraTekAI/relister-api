from rest_framework_simplejwt import views as jwt_views
from accounts.views import (
    RegisterView,
    LogoutView,
    PasswordResetRequestView,
    SetNewPasswordAPIView,
    CustomTokenObtainPairView,
    UserListview,
    UserActivateView,
    UserDeactivateView,
    ProfileView,
    ChangePasswordView,
    ChangeEmailView,
    VerifyEmailChangeView,
    NotificationPreferenceView,
)
from django.urls import path

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path("login/", CustomTokenObtainPairView.as_view(), name="login"),
    path("refresh-token/", jwt_views.TokenRefreshView.as_view(), name="refresh_token"),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('password_reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('password_reset_confirm/', SetNewPasswordAPIView.as_view(), name='password-reset-confirm'),
    path('users/', UserListview.as_view({'get': 'list', 'post': 'create'}), name='users'),
    path('users/<int:pk>/', UserListview.as_view({'get': 'retrieve', 'patch': 'update', 'delete': 'destroy'}), name='users_detail'),
    # TICKET-015: Admin activate / deactivate
    path('users/<int:pk>/activate/', UserActivateView.as_view(), name='user-activate'),
    path('users/<int:pk>/deactivate/', UserDeactivateView.as_view(), name='user-deactivate'),
    # TICKET-013: Account Settings
    path('profile/', ProfileView.as_view(), name='account-profile'),
    path('change-password/', ChangePasswordView.as_view(), name='account-change-password'),
    path('change-email/', ChangeEmailView.as_view(), name='account-change-email'),
    path('verify-email-change/<str:token>/', VerifyEmailChangeView.as_view(), name='account-verify-email-change'),
    path('notification-preferences/', NotificationPreferenceView.as_view(), name='account-notification-preferences'),
]
