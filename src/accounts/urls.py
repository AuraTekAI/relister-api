from rest_framework_simplejwt import views as jwt_views
from accounts.views import LogoutView,SetNewPasswordAPIView
from django.urls import path

urlpatterns = [
    path("login/", jwt_views.TokenObtainPairView.as_view(), name="login"),
    path("refresh-token/", jwt_views.TokenRefreshView.as_view(), name="refresh_token"),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('password_reset_confirm/', SetNewPasswordAPIView.as_view(),name='password-reset-confirm'),
]
