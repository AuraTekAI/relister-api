from rest_framework import serializers
from accounts.models import User, NotificationPreference, EmailVerificationToken
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework.exceptions import AuthenticationFailed, NotAcceptable
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.utils import timezone
from datetime import timedelta

class UserListSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)
    confirm_password = serializers.CharField(write_only=True, required=False)
    # TICKET-015: plan name derived from subscription; falls back to "Trial" if no paid subscription
    plan = serializers.SerializerMethodField()
    is_active = serializers.BooleanField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'is_superuser', 'is_approved', 'is_active',
            'first_name', 'last_name',
            'dealership_name', 'contact_person_name', 'phone_number',
            'gumtree_dealarship_url', 'facebook_dealership_url',
            'dealership_license_number', 'dealership_license_phone',
            'account_status', 'trial_start_date', 'trial_end_date',
            'plan', 'created_at',
            'password', 'confirm_password',
        ]
        read_only_fields = ['id', 'is_superuser', 'trial_start_date', 'trial_end_date', 'created_at', 'plan']

    def get_plan(self, obj):
        try:
            sub = obj.subscription
            if sub and sub.plan:
                return sub.plan.name
            if sub and sub.status == 'active':
                # Subscription exists but plan was deleted — flag it
                return 'Unknown'
        except Exception:
            pass
        return 'Trial'

    def validate(self, attrs):
        # Check if password is being updated
        if 'password' in attrs:
            if not attrs.get('confirm_password'):
                raise serializers.ValidationError({"confirm_password": "Confirm password is required when updating password."})
            if attrs['password'] != attrs['confirm_password']:
                raise serializers.ValidationError({"password": "Password fields didn't match."})

        # Remove confirm_password from attrs as it's not a model field
        attrs.pop('confirm_password', None)
        
        # Validate at least one URL is present
        gumtree_url = attrs.get('gumtree_dealarship_url', self.instance.gumtree_dealarship_url if self.instance else None)
        facebook_url = attrs.get('facebook_dealership_url', self.instance.facebook_dealership_url if self.instance else None)
        
        if not gumtree_url and not facebook_url:
            raise serializers.ValidationError("At least one dealership URL (Gumtree or Facebook) is required.")
        
        return attrs

    def update(self, instance, validated_data):
        # Handle password update if provided
        if 'password' in validated_data:
            password = validated_data.pop('password')
            instance.set_password(password)

        # Update other fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        instance.save()
        return instance

class SetNewPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)
    uid = serializers.CharField(
        min_length=1, write_only=True)
    token = serializers.CharField(
        min_length=1, write_only=True)
    

    class Meta:
        fields = ['password', 'confirm_password' ,'token', 'uid']

    def validate(self, attrs):
        try:
            password = attrs.get('password')
            confirm_password = attrs.get('confirm_password')
            token = attrs.get('token')
            uid = attrs.get('uid')

            id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.filter(id=id).first()

            if not user:
                raise AuthenticationFailed('The reset link is invalid', 401)

            if not PasswordResetTokenGenerator().check_token(user, token):
                raise AuthenticationFailed('The reset link is invalid or has expired', 401)

            if password != confirm_password:
                raise NotAcceptable("Password doesn't match confirm password", 406)

            user.set_password(password)
            user.save()
            return user

        except (AuthenticationFailed, NotAcceptable):
            raise
        except Exception:
            raise AuthenticationFailed('The reset link is invalid', 401)
# Add this new serializer
class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = (
            'email', 'password', 'confirm_password',
            'first_name', 'last_name',
            'dealership_name', 'contact_person_name', 'phone_number',
            'gumtree_dealarship_url', 'facebook_dealership_url',
            'dealership_license_number', 'dealership_license_phone',
        )
        extra_kwargs = {
            'email': {'required': True},
            'first_name': {'required': True},
            'last_name': {'required': True},
            'dealership_name': {'required': True},
            'contact_person_name': {'required': True},
            'phone_number': {'required': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})

        gumtree_profile_url = attrs.get('gumtree_dealarship_url')
        facebook_profile_url = attrs.get('facebook_dealership_url')

        if not gumtree_profile_url and not facebook_profile_url:
            raise serializers.ValidationError("At least one dealership URL (Gumtree or Facebook) is required.")

        # Facebook URL validation
        if facebook_profile_url:
            expected_prefixes = [
                "https://www.facebook.com/marketplace/profile/",
                "https://web.facebook.com/marketplace/profile/",
            ]
            if not any(facebook_profile_url.startswith(prefix) for prefix in expected_prefixes):
                raise serializers.ValidationError({
                    'facebook_dealership_url': 'Invalid Facebook profile URL. It must start with a valid Facebook Marketplace profile path.'
                })

        # Gumtree URL validation
        if gumtree_profile_url:
            expected_gumtree_prefix = "https://www.gumtree.com.au/web/s-user"
            if not gumtree_profile_url.startswith(expected_gumtree_prefix):
                raise serializers.ValidationError({
                    'gumtree_dealarship_url': 'Invalid Gumtree profile URL. It must start with "https://www.gumtree.com.au/web/s-user".'
                })

        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password', None)
        now = timezone.now()
        # Set trial metadata — trial starts from registration date
        validated_data['account_status'] = 'trial'
        validated_data['trial_start_date'] = now
        validated_data['trial_end_date'] = now + timedelta(days=30)
        # Mark trial as used immediately so this email can never claim another trial.
        validated_data['trial_used'] = True
        # is_approved stays False (default) — admin must approve before the
        # existing Chrome extension / listing app becomes accessible.
        return User.objects.create_user(**validated_data)

# ---------------------------------------------------------------------------
# TICKET-013: Account Settings serializers
# ---------------------------------------------------------------------------

class UserProfileSerializer(serializers.ModelSerializer):
    """User-facing profile serializer — no sensitive admin fields."""
    class Meta:
        model = User
        fields = [
            'id', 'email',
            'first_name', 'last_name',
            'dealership_name', 'contact_person_name', 'phone_number',
            'gumtree_dealarship_url', 'facebook_dealership_url',
            'dealership_license_number', 'dealership_license_phone',
            'account_status', 'trial_start_date', 'trial_end_date',
        ]
        read_only_fields = ['id', 'email', 'account_status', 'trial_start_date', 'trial_end_date']


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs


class ChangeEmailSerializer(serializers.Serializer):
    new_email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_new_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("This email address is already in use.")
        return value


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = [
            'email_approaching_limit',
            'email_quota_exceeded',
            'email_billing_reminder',
        ]


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Email case-insensitive, strip trailing spaces
        email = attrs.get('email', '').strip()
        attrs['email'] = email.lower()
        attrs['password'] = attrs.get('password', '')
        data = super().validate(attrs)
        user = self.user

        # Superusers bypass all status checks
        if not user.is_superuser:
            # Deactivated accounts — covers admin-disabled users (is_active=False).
            if not user.is_active:
                raise AuthenticationFailed(
                    'Your account has been deactivated. Please contact support.',
                    code='account_deactivated',
                )

            # Primary gate — unchanged from original behaviour.
            # Admin must approve the user before they can log in.
            if not user.is_approved:
                raise AuthenticationFailed('User is not approved.', code='user_not_approved')

            # Secondary gate — applies once approved (new dashboard use cases).
            # trial_expired is allowed through — frontend shows subscription prompt.
            if user.account_status == 'suspended':
                raise AuthenticationFailed(
                    'Your account has been suspended. Please contact support.',
                    code='account_suspended',
                )

        data.update({
            'user': {
                'id': user.id,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'dealership_name': user.dealership_name,
                'contact_person_name': user.contact_person_name,
                'phone_number': user.phone_number,
                'gumtree_dealarship_url': user.gumtree_dealarship_url,
                'facebook_dealership_url': user.facebook_dealership_url,
                'dealership_license_number': user.dealership_license_number,
                'dealership_license_phone': user.dealership_license_phone,
                'is_superuser': user.is_superuser,
                'is_approved': user.is_approved,
                'account_status': user.account_status,
                'trial_end_date': user.trial_end_date.isoformat() if user.trial_end_date else None,
            },
            'status': 200,
        })
        return data
