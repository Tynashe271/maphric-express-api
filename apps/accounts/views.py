from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.db import OperationalError, close_old_connections, transaction
from django.db.models import Q
from rest_framework.authtoken.models import Token
from rest_framework.throttling import AnonRateThrottle
import secrets
from apps.common import verification
from apps.common.responses import error_response, service_unavailable
from apps.common.text import mask_email, normalize_phone_number, to_international_phone_number
from .models import User
from .serializers import UserSerializer, LoginSerializer, RegisterSerializer


class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class RegisterRateThrottle(AnonRateThrottle):
    scope = 'register'


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = 'password_reset'


MESSAGING_CHANNELS = {'sms', 'whatsapp'}
RESET_TIMEOUT = 300


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action in {'register', 'login', 'password_reset_request', 'password_reset_verify', 'password_reset_confirm'}:
            return [permissions.AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        """Prevent API users from accessing other users' profiles."""
        if self.request.user.is_staff:
            return User.objects.all()
        return User.objects.filter(pk=self.request.user.pk)

    def create(self, request, *args, **kwargs):
        """Accounts must be created through the validated register endpoint."""
        raise MethodNotAllowed('POST')

    @action(detail=False, methods=['post'], throttle_classes=[RegisterRateThrottle])
    def register(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            # A newly registered user cannot already have a token. Creating it
            # directly avoids an unnecessary remote SELECT against Neon.
            token = Token.objects.create(user=user)
            serialized_user = UserSerializer(user).data
            return Response({
                'user': serialized_user,
                'token': token.key
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], throttle_classes=[LoginRateThrottle])
    def login(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            login_name = serializer.validated_data['username']
            supplied_password = serializer.validated_data['password']
            user = User.objects.select_related('auth_token').filter(
                Q(username__iexact=login_name) | Q(email__iexact=login_name)
            ).first()
            if user and user.is_active and user.check_password(supplied_password):
                try:
                    token = user.auth_token
                except Token.DoesNotExist:
                    token = Token.objects.create(user=user)
                serialized_user = UserSerializer(user).data
                return Response({
                    'user': serialized_user,
                    'token': token.key
                })
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _reset_key(reset_id):
        return f'password-reset:{reset_id}'

    @action(
        detail=False,
        methods=['post'],
        url_path='password-reset/request',
        throttle_classes=[PasswordResetRateThrottle],
    )
    def password_reset_request(self, request):
        username = str(request.data.get('username', '')).strip()
        phone = normalize_phone_number(request.data.get('phone_number', ''))
        channel = str(request.data.get('channel', 'email')).lower()
        if not username or not phone:
            return error_response('Enter your username and registered phone number.')
        user = User.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=username),
            phone_number=phone,
        ).first()
        if not user:
            return error_response('The username and phone number do not match an account.')

        code = f'{secrets.randbelow(1000000):06d}'
        reset_id = secrets.token_urlsafe(24)

        if channel in MESSAGING_CHANNELS:
            if not verification.is_configured():
                return service_unavailable('Messaging recovery is not configured yet. Choose email instead.')
            delivery_phone = to_international_phone_number(phone)
            try:
                verification.send_code(delivery_phone, channel)
            except verification.VerificationError as sms_error:
                return service_unavailable(sms_error.provider_message or sms_error.detail)
            destination = f'phone ending {delivery_phone[-4:]}'
            code_hash = None
        else:
            if not user.email:
                cache.delete(self._reset_key(reset_id))
                return error_response('This account has no recovery email. Choose text message or contact support.')
            try:
                send_mail(
                    'Your Maphric Express password reset code',
                    f'Your verification code is {code}. It expires in 5 minutes. If you did not request this, ignore this email.',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
            except Exception:
                return service_unavailable('The email could not be sent. Please try again or contact support.')
            destination = mask_email(user.email)
            code_hash = make_password(code)
        cache.set(self._reset_key(reset_id), {
            'user_id': user.id,
            'code_hash': code_hash,
            'channel': channel,
            'phone': delivery_phone if channel in MESSAGING_CHANNELS else phone,
            'attempts': 0,
            'verified': False,
        }, timeout=RESET_TIMEOUT)
        return Response({'reset_id': reset_id, 'destination': destination, 'expires_in': RESET_TIMEOUT})

    @action(
        detail=False,
        methods=['post'],
        url_path='password-reset/verify',
        throttle_classes=[PasswordResetRateThrottle],
    )
    def password_reset_verify(self, request):
        reset_id = str(request.data.get('reset_id', ''))
        code = str(request.data.get('code', '')).strip()
        data = cache.get(self._reset_key(reset_id))
        if not data:
            return error_response('This code has expired. Request a new one.')
        if data['attempts'] >= 5:
            cache.delete(self._reset_key(reset_id))
            return error_response('Too many attempts. Request a new code.', status.HTTP_429_TOO_MANY_REQUESTS)
        if data.get('channel') in MESSAGING_CHANNELS:
            try:
                code_is_valid = verification.check_code(data['phone'], code)
            except verification.VerificationError as check_error:
                return service_unavailable(check_error.detail)
        else:
            code_is_valid = check_password(code, data['code_hash'])
        if not code_is_valid:
            data['attempts'] += 1
            cache.set(self._reset_key(reset_id), data, timeout=RESET_TIMEOUT)
            return error_response('The verification code is incorrect.')
        data['verified'] = True
        cache.set(self._reset_key(reset_id), data, timeout=RESET_TIMEOUT)
        return Response({'verified': True})

    @action(
        detail=False,
        methods=['post'],
        url_path='password-reset/confirm',
        throttle_classes=[PasswordResetRateThrottle],
    )
    def password_reset_confirm(self, request):
        reset_id = str(request.data.get('reset_id', ''))
        username = str(request.data.get('username', '')).strip()
        password = str(request.data.get('password', ''))
        password2 = str(request.data.get('password2', ''))
        data = cache.get(self._reset_key(reset_id))
        if not data or not data.get('verified'):
            return error_response('Verify your code before setting a new password.')
        user = User.objects.filter(pk=data['user_id']).filter(
            Q(username__iexact=username) | Q(email__iexact=username)
        ).first()
        if not user:
            return error_response('The username does not match this recovery request.')
        if password != password2:
            return error_response('The new passwords do not match.')
        try:
            validate_password(password, user=user)
        except ValidationError as validation_error:
            return error_response(' '.join(validation_error.messages))
        def save_new_password():
            with transaction.atomic():
                user.set_password(password)
                user.save(update_fields=['password'])
                Token.objects.filter(user=user).delete()
        try:
            save_new_password()
        except OperationalError:
            close_old_connections()
            try:
                user.refresh_from_db()
                save_new_password()
            except OperationalError:
                return service_unavailable(
                    'The database is taking too long. Your password was not changed; please try again.'
                )
        cache.delete(self._reset_key(reset_id))
        return Response({'detail': 'Password updated successfully. Sign in with your new password.'})
