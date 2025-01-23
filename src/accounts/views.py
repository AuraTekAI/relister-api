from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated,AllowAny
from rest_framework import status, generics

from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.encoding import smart_bytes
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from accounts.models import User
from accounts.serializers import SetNewPasswordSerializer

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
    
