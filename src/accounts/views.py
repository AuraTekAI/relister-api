import uuid
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework import status, generics
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.viewsets import ModelViewSet
from rest_framework import filters
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from accounts.serializers import (
    SetNewPasswordSerializer,
    UserRegistrationSerializer,
    CustomTokenObtainPairSerializer,
    UserListSerializer,
    UserProfileSerializer,
    ChangePasswordSerializer,
    ChangeEmailSerializer,
    NotificationPreferenceSerializer,
)
from accounts.models import User, NotificationPreference, EmailVerificationToken
from accounts.throttles import LoginRateThrottle, RegisterRateThrottle, PasswordResetRateThrottle
from VehicleListing.utils import send_welcome_email
from django_filters.rest_framework import DjangoFilterBackend
from VehicleListing.tasks import profile_listings_for_approved_users
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Logout",
        operation_description="Blacklists the provided refresh token to log the user out.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['refresh_token'],
            properties={
                'refresh_token': openapi.Schema(type=openapi.TYPE_STRING, description='JWT refresh token to blacklist.'),
            },
        ),
        responses={
            200: openapi.Response(description="Logged out successfully."),
            400: "Refresh token is required or invalid.",
        },
    )
    def post(self, request):
        # Get the refresh token from the request data
        refresh_token = request.data.get('refresh_token')

        if not refresh_token:
            return Response({'detail': 'Refresh token is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Attempt to create a RefreshToken instance from the provided token
            token = RefreshToken(refresh_token)
            # Blacklist the refresh token to log the user out
            token.blacklist()

            return Response({'detail': 'Logged out successfully.'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'detail': 'Invalid token or tokens have already been blacklisted.'}, status=status.HTTP_400_BAD_REQUEST)

class PasswordResetRequestView(APIView):
    """POST /api/password_reset/ — send a password reset link to the user's email."""
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    @swagger_auto_schema(
        operation_summary="Request password reset",
        operation_description="Sends a password reset link to the provided email address. Always returns 200 to avoid leaking whether the email exists.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, format='email', description='Email address to send the reset link to.'),
            },
        ),
        responses={
            200: openapi.Response(description="Reset link sent if the email exists."),
        },
    )
    def post(self, request):
        email = request.data.get('email', '').strip().lower()

        # Always return 200 — never reveal whether the email is registered
        if not email:
            return Response(
                {'message': 'If an account with that email exists, a reset link has been sent.'},
                status=status.HTTP_200_OK,
            )

        user = User.objects.filter(email=email).first()
        if user and user.is_active:
            try:
                from django.core.mail import EmailMessage
                from relister.settings import EMAIL_HOST_USER, FRONTEND_URL

                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = PasswordResetTokenGenerator().make_token(user)
                reset_url = f"{FRONTEND_URL}/reset-password?uid={uid}&token={token}"

                body = (
                    f"Hi {user.first_name or user.email},\n\n"
                    f"We received a request to reset your Relister password.\n\n"
                    f"Click the link below to set a new password:\n{reset_url}\n\n"
                    f"This link will expire after 24 hours. If you did not request a password reset, "
                    f"please ignore this email — your password will remain unchanged."
                )
                email_msg = EmailMessage(
                    "Reset your Relister password",
                    body,
                    EMAIL_HOST_USER,
                    [user.email],
                )
                email_msg.send(fail_silently=True)
            except Exception:
                pass  # Never expose email errors to the caller

        return Response(
            {'message': 'If an account with that email exists, a reset link has been sent.'},
            status=status.HTTP_200_OK,
        )


class SetNewPasswordAPIView(generics.GenericAPIView):
    serializer_class = SetNewPasswordSerializer
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    @swagger_auto_schema(
        operation_summary="Reset password",
        operation_description="Sets a new password using the UID and token from a password reset email.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['uid', 'token', 'password', 'confirm_password'],
            properties={
                'uid': openapi.Schema(type=openapi.TYPE_STRING, description='Base64-encoded user ID from reset email.'),
                'token': openapi.Schema(type=openapi.TYPE_STRING, description='Password reset token from reset email.'),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description='New password.'),
                'confirm_password': openapi.Schema(type=openapi.TYPE_STRING, description='Must match password.'),
            },
        ),
        responses={
            200: openapi.Response(description="Password reset successful."),
            404: "User not found or reset link expired.",
        },
    )
    def patch(self, request):
        uid = urlsafe_base64_decode(request.data.get('uid')).decode()
        user = User.objects.filter(pk=uid).first()
        token = request.data.get('token',None)
        if not user:
            return Response({'Failed':'User Not Found'},status = status.HTTP_404_NOT_FOUND)
        if PasswordResetTokenGenerator().check_token(user, token):
            request.session['uid'] = uid
            serializer = self.serializer_class(data=request.data)
            serializer.is_valid(raise_exception=True)
            return Response({'success': True, 'message': 'Password reset success'}, status=status.HTTP_200_OK)
        return Response({'Failed': 'Link has been expired'}, status=status.HTTP_404_NOT_FOUND)

class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RegisterRateThrottle]

    @swagger_auto_schema(
        operation_summary="Register new user",
        operation_description="Creates a new user account. Trial starts immediately. Admin approval required before login.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'password', 'confirm_password', 'first_name', 'last_name', 'dealership_name', 'contact_person_name', 'phone_number'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, format='email', description='User email address.'),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description='Password.'),
                'confirm_password': openapi.Schema(type=openapi.TYPE_STRING, description='Must match password.'),
                'first_name': openapi.Schema(type=openapi.TYPE_STRING, description='First name.'),
                'last_name': openapi.Schema(type=openapi.TYPE_STRING, description='Last name.'),
                'dealership_name': openapi.Schema(type=openapi.TYPE_STRING, description='Dealership name.'),
                'contact_person_name': openapi.Schema(type=openapi.TYPE_STRING, description='Contact person full name.'),
                'phone_number': openapi.Schema(type=openapi.TYPE_STRING, description='Phone number.'),
                'gumtree_dealarship_url': openapi.Schema(type=openapi.TYPE_STRING, description='Gumtree dealership profile URL (must start with https://www.gumtree.com.au/web/s-user).'),
                'facebook_dealership_url': openapi.Schema(type=openapi.TYPE_STRING, description='Facebook Marketplace profile URL (must start with https://www.facebook.com/marketplace/profile/).'),
            },
        ),
        responses={
            201: openapi.Response(description="Registration successful. Awaiting admin approval."),
            400: "Validation error.",
        },
    )
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            # Send welcome email — informs user their trial has started and
            # that they are pending admin approval to access the app.
            send_welcome_email(user)

            # No tokens issued here — user must be approved by admin first
            # (unchanged from original flow).
            return Response({
                'success': True,
                'message': 'Registration successful. Your 30-day free trial has started. Please wait for admin approval to access the app.',
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [LoginRateThrottle]

    @swagger_auto_schema(
        operation_summary="Login",
        operation_description="Authenticates the user and returns JWT access and refresh tokens. User must be approved by admin.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'password'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING, format='email', description='User email.'),
                'password': openapi.Schema(type=openapi.TYPE_STRING, description='User password.'),
            },
        ),
        responses={
            200: openapi.Response(
                description="Login successful.",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'access': openapi.Schema(type=openapi.TYPE_STRING, description='JWT access token.'),
                        'refresh': openapi.Schema(type=openapi.TYPE_STRING, description='JWT refresh token.'),
                        'status': openapi.Schema(type=openapi.TYPE_INTEGER, example=200),
                        'user': openapi.Schema(type=openapi.TYPE_OBJECT, description='Authenticated user details.'),
                    },
                ),
            ),
            401: "Invalid credentials or user not approved.",
        },
    )
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            response.data['status'] = 200
        return response
    
class UserListview(ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserListSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter, DjangoFilterBackend]
    search_fields = ['dealership_name', 'email', 'contact_person_name', 'phone_number']
    ordering_fields = ['dealership_name', 'email']
    ordering = ['dealership_name', 'email']
    filterset_fields = ['is_approved', 'is_superuser', 'is_active', 'account_status']

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        #instance before update
        instance = self.get_object()
        user_approved=instance.is_approved
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        try:
            self.perform_update(serializer)
            user=serializer.instance
            #proccess the approved user profile urls
            if user.is_approved and not user_approved:
                profile_listings_for_approved_users.delay(user.id)



            return Response({
                'status': status.HTTP_200_OK,
                'message': 'User updated successfully',
                'data': serializer.data
            })
        except Exception as e:
            return Response({
                'status': status.HTTP_400_BAD_REQUEST,
                'message': str(e),
            }, status=status.HTTP_400_BAD_REQUEST)

    def perform_update(self, serializer):
        serializer.save()


# ---------------------------------------------------------------------------
# TICKET-015: Admin – Activate / Deactivate user
# ---------------------------------------------------------------------------

class UserActivateView(APIView):
    """POST /api/users/<pk>/activate/ — re-enable a deactivated user account."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Activate user",
        operation_description="Sets is_active=True for the user. The user can log in again immediately.",
        responses={
            200: openapi.Response(description="User activated."),
            404: "User not found.",
        },
    )
    def post(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        user.is_active = True
        user.save(update_fields=['is_active'])
        return Response(
            {'detail': f"User '{user.email}' has been activated."},
            status=status.HTTP_200_OK,
        )


class UserDeactivateView(APIView):
    """POST /api/users/<pk>/deactivate/ — disable a user account."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Deactivate user",
        operation_description="Sets is_active=False for the user. The user is immediately blocked from logging in.",
        responses={
            200: openapi.Response(description="User deactivated."),
            400: "Cannot deactivate a superuser.",
            404: "User not found.",
        },
    )
    def post(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        if user.is_superuser:
            return Response(
                {'detail': 'Cannot deactivate a superuser account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_active = False
        user.save(update_fields=['is_active'])
        return Response(
            {'detail': f"User '{user.email}' has been deactivated."},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# TICKET-013: Account Settings
# ---------------------------------------------------------------------------

class ProfileView(APIView):
    """GET/PATCH /api/accounts/profile/ — view and update own profile."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get profile",
        operation_description="Returns the authenticated user's profile.",
        responses={200: UserProfileSerializer()},
    )
    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary="Update profile",
        operation_description="Partially updates the authenticated user's profile (first_name, last_name, dealership_name, contact_person_name, phone_number, dealership URLs).",
        request_body=UserProfileSerializer,
        responses={200: UserProfileSerializer(), 400: "Validation error."},
    )
    def patch(self, request):
        serializer = UserProfileSerializer(request.user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    """POST /api/accounts/change-password/ — change password with current password verification."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Change password",
        operation_description="Changes the user's password. Requires the current password for verification. Invalidates the current refresh token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['current_password', 'new_password', 'confirm_password'],
            properties={
                'current_password': openapi.Schema(type=openapi.TYPE_STRING),
                'new_password': openapi.Schema(type=openapi.TYPE_STRING),
                'confirm_password': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={
            200: "Password changed successfully.",
            400: "Current password incorrect or passwords do not match.",
        },
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not user.check_password(serializer.validated_data['current_password']):
            return Response(
                {'current_password': 'Current password is incorrect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data['new_password'])
        user.save(update_fields=['password'])

        # Blacklist the current refresh token so the user re-authenticates
        refresh_token = request.data.get('refresh_token')
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except Exception:
                pass

        return Response({'message': 'Password changed successfully. Please log in again.'}, status=status.HTTP_200_OK)


class ChangeEmailView(APIView):
    """POST /api/accounts/change-email/ — request an email address change."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Request email change",
        operation_description="Sends a verification link to the new email address. The change is not applied until the link is clicked.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['new_email', 'password'],
            properties={
                'new_email': openapi.Schema(type=openapi.TYPE_STRING, format='email'),
                'password': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        responses={
            200: "Verification email sent.",
            400: "Validation error or wrong password.",
        },
    )
    def post(self, request):
        serializer = ChangeEmailSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not user.check_password(serializer.validated_data['password']):
            return Response(
                {'password': 'Password is incorrect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_email = serializer.validated_data['new_email']
        token_value = str(uuid.uuid4())

        EmailVerificationToken.objects.create(
            user=user,
            token=token_value,
            new_email=new_email,
        )

        # Send verification email
        try:
            from django.core.mail import EmailMessage
            from relister.settings import EMAIL_HOST_USER, FRONTEND_URL
            verify_url = f"{FRONTEND_URL}/verify-email-change?token={token_value}"
            body = (
                f"Hi {user.first_name or user.email},\n\n"
                f"Click the link below to confirm your new email address:\n{verify_url}\n\n"
                f"This link expires in 24 hours. If you did not request this change, ignore this email."
            )
            email = EmailMessage("Confirm your new email address", body, EMAIL_HOST_USER, [new_email])
            email.send(fail_silently=True)
        except Exception:
            pass  # Email sending failure should not block the response

        return Response(
            {'message': f'Verification email sent to {new_email}. Please check your inbox.'},
            status=status.HTTP_200_OK,
        )


class VerifyEmailChangeView(APIView):
    """GET /api/accounts/verify-email-change/<token>/ — confirm email change."""
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Verify email change",
        operation_description="Confirms the email address change using the token sent to the new email.",
        responses={
            200: "Email updated successfully.",
            400: "Token expired or already used.",
            404: "Token not found.",
        },
    )
    def get(self, request, token):
        try:
            verification = EmailVerificationToken.objects.select_related('user').get(token=token)
        except EmailVerificationToken.DoesNotExist:
            return Response({'detail': 'Invalid or expired token.'}, status=status.HTTP_404_NOT_FOUND)

        if not verification.is_valid():
            return Response(
                {'detail': 'This verification link has expired or already been used.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = verification.user
        user.email = verification.new_email
        user.save(update_fields=['email'])

        verification.is_used = True
        verification.save(update_fields=['is_used'])

        return Response({'message': 'Email address updated successfully.'}, status=status.HTTP_200_OK)


class NotificationPreferenceView(APIView):
    """GET/PATCH /api/accounts/notification-preferences/ — manage notification preferences."""
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get notification preferences",
        operation_description="Returns the user's email notification preferences.",
        responses={200: NotificationPreferenceSerializer()},
    )
    def get(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(prefs)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_summary="Update notification preferences",
        operation_description="Updates the user's email notification preferences.",
        request_body=NotificationPreferenceSerializer,
        responses={200: NotificationPreferenceSerializer(), 400: "Validation error."},
    )
    def patch(self, request):
        prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
        serializer = NotificationPreferenceSerializer(prefs, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)