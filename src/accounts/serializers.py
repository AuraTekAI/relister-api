from rest_framework import serializers
from accounts.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework.exceptions import AuthenticationFailed,NotAcceptable
from rest_framework import serializers
from accounts.models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class UserListSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)  # Optional for updates
    confirm_password = serializers.CharField(write_only=True, required=False)  # Optional for updates

    class Meta:
        model = User
        fields = [
            'id', 'email', 'is_superuser', 'is_approved', 
            'dealership_name', 'contact_person_name', 'phone_number', 
            'gumtree_dealarship_url', 'facebook_dealership_url',
            'password', 'confirm_password'
        ]
        read_only_fields = ['id', 'is_superuser']  # These fields cannot be modified

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
            if not PasswordResetTokenGenerator().check_token(user, token):
                raise AuthenticationFailed('The reset link is invalid', 401)

            if password == confirm_password:
                user.set_password(password)
                user.save()
                return (user)
            else:
                raise NotAcceptable("Password doesn't match confirm password",406)

            
        except Exception as e:
            raise NotAcceptable("Password doesn't match confirm password",406)
# Add this new serializer
class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ('email', 'password', 'confirm_password', 'dealership_name', 'contact_person_name', 'phone_number', 'gumtree_dealarship_url', 'facebook_dealership_url')
        extra_kwargs = {
            'email': {'required': True},
            'dealership_name': {'required': True},
            'contact_person_name': {'required': True},
            'phone_number': {'required': True},
        }

    def validate(self, attrs):
        if attrs['password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        gumtree_profile_url=attrs.get('gumtree_dealarship_url')
        facebook_profile_url=attrs.get('facebook_dealership_url')
        if not gumtree_profile_url  and not facebook_profile_url:
            raise serializers.ValidationError("At least one dealership URL (Gumtree or Facebook) is required.")
        #Facebook URL Validation
        if facebook_profile_url:
            expected_prefixes = [
                "https://www.facebook.com/marketplace/profile/",
                "https://web.facebook.com/marketplace/profile/"
            ]
            if not any(facebook_profile_url.startswith(prefix) for prefix in expected_prefixes):
                raise serializers.ValidationError({
                    'facebook_dealership_url': 'Invalid Facebook profile URL. It must start with a valid Facebook Marketplace profile path.'
                })
        
        # Gumtree URL validation
        gumtree_profile_url = attrs.get('gumtree_dealarship_url')
        if gumtree_profile_url:
            expected_gumtree_prefix = "https://www.gumtree.com.au/web/s-user"
            if not gumtree_profile_url.startswith(expected_gumtree_prefix):
                raise serializers.ValidationError({
                    'gumtree_dealarship_url': 'Invalid Gumtree profile URL. It must start with "https://www.gumtree.com.au/web/s-user".'
                })

        
        
        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password', None)
        return User.objects.create_user(**validated_data)

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        #Email case-insensitive and ignore spaces at the end
        email = attrs.get('email', '').strip()  # Remove spaces at the ends
        attrs['email'] = email.lower()  # Convert to lowercase for case-insensitivity
        attrs['password'] = attrs.get("password", '')
        data = super().validate(attrs)
        user = self.user
        
        if not user.is_approved and not user.is_superuser:
            raise AuthenticationFailed('User is not approved.', code='user_not_approved')
        
        # Add custom claims
        data.update({
            'user': {
                'id': user.id,
                'email': user.email,
                'dealership_name': user.dealership_name,
                'contact_person_name': user.contact_person_name,
                'phone_number': user.phone_number,
                'gumtree_dealarship_url': user.gumtree_dealarship_url,
                'facebook_dealership_url': user.facebook_dealership_url,
                'is_superuser': user.is_superuser,
                'is_approved': user.is_approved,
            },
            'status': 200
        })
        return data
