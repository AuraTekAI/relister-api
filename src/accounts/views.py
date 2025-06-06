from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated,AllowAny , IsAdminUser
from rest_framework import status, generics
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework.viewsets import ModelViewSet
from rest_framework import filters
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from accounts.models import User
from accounts.serializers import SetNewPasswordSerializer, UserRegistrationSerializer, CustomTokenObtainPairSerializer, UserListSerializer
from django_filters.rest_framework import DjangoFilterBackend
from VehicleListing.tasks import profile_listings_for_approved_users

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]
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
            
            # blacklist the associated access token
            access_token = token.access_token
            access_token.blacklist()

            return Response({'detail': 'Logged out successfully.'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'detail': 'Invalid token or tokens have already been blacklisted.'}, status=status.HTTP_400_BAD_REQUEST)

class SetNewPasswordAPIView(generics.GenericAPIView):
    serializer_class = SetNewPasswordSerializer
    permission_classes = [AllowAny]


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
    
    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            
            if not user.is_approved and not user.is_superuser:
                return Response({
                    'success': True,
                    'message': 'Registration successful. Please wait for approval.',
                }, status=status.HTTP_201_CREATED)
            
            # Generate tokens for automatic login if approved
            refresh = RefreshToken.for_user(user)
            
            return Response({
                'success': True,
                'message': 'Registration successful',
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'dealership_name': user.dealership_name,
                    'contact_person_name': user.contact_person_name,
                },
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer

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
    filterset_fields = ['is_approved', 'is_superuser', 'is_active']

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