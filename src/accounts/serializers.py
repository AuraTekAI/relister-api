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
    class Meta:
        model = User
        fields = ['id', 'email', 'is_superuser']
        read_only_fields = fields

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
        
        if not attrs.get('gumtree_dealarship_url') and not attrs.get('facebook_dealership_url'):
            raise serializers.ValidationError("At least one dealership URL (Gumtree or Facebook) is required.")
        
        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password', None)
        return User.objects.create_user(**validated_data)

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        
        if not user.is_approved:
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
