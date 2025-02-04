from rest_framework_simplejwt import views as jwt_views
from accounts.views import RegisterView, LogoutView, SetNewPasswordAPIView, CustomTokenObtainPairView, UserListview
from django.urls import path

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path("login/", CustomTokenObtainPairView.as_view(), name="login"),
    path("refresh-token/", jwt_views.TokenRefreshView.as_view(), name="refresh_token"),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('password_reset_confirm/', SetNewPasswordAPIView.as_view(), name='password-reset-confirm'),
    path('users/', UserListview.as_view({'get': 'list' , 'post': 'create'}), name='users'),
    path('users/<int:pk>/', UserListview.as_view({'get': 'retrieve',  'patch': 'update', 'delete': 'destroy'}), name='users_detail'),
]
