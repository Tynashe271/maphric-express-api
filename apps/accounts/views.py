from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action, throttle_classes
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response
from django.contrib.auth import authenticate
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
import json
import hashlib
import secrets
from urllib import error as urlerror, parse, request as urlrequest
from .models import User
from .serializers import UserSerializer, LoginSerializer, RegisterSerializer

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

    @staticmethod
    def _login_cache_key(login_name):
        digest = hashlib.sha256(login_name.strip().lower().encode()).hexdigest()
        return f'login-cache:{digest}'

    @classmethod
    def _cache_login(cls, user, token, serialized_user=None):
        cached = {
            'user': serialized_user or UserSerializer(user).data,
            'token': token.key,
        }
        cache.set(cls._login_cache_key(user.username), cached, timeout=300)
        if user.email:
            cache.set(cls._login_cache_key(user.email), cached, timeout=300)

    @classmethod
    def _clear_login_cache(cls, user):
        cache.delete(cls._login_cache_key(user.username))
        if user.email:
            cache.delete(cls._login_cache_key(user.email))

    @action(detail=False, methods=['post'])
    def register(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            # A newly registered user cannot already have a token. Creating it
            # directly avoids an unnecessary remote SELECT against Neon.
            token = Token.objects.create(user=user)
            serialized_user = UserSerializer(user).data
            self._cache_login(user, token, serialized_user)
            return Response({
                'user': serialized_user,
                'token': token.key
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
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
                self._cache_login(user, token, serialized_user)
                return Response({
                    'user': serialized_user,
                    'token': token.key
                })
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @staticmethod
    def _reset_key(reset_id):
        return f'password-reset:{reset_id}'

    @action(detail=False, methods=['post'], url_path='password-reset/request')
    @throttle_classes([AnonRateThrottle])
    def password_reset_request(self, request):
        username = str(request.data.get('username', '')).strip()
        phone = ''.join(character for character in str(request.data.get('phone_number', '')) if character.isdigit() or character == '+')
        channel = str(request.data.get('channel', 'email')).lower()
        if not username or not phone:
            return Response({'detail': 'Enter your username and registered phone number.'}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(
            Q(username__iexact=username) | Q(email__iexact=username),
            phone_number=phone,
        ).first()
        if not user:
            return Response({'detail': 'The username and phone number do not match an account.'}, status=status.HTTP_400_BAD_REQUEST)

        code = f'{secrets.randbelow(1000000):06d}'
        reset_id = secrets.token_urlsafe(24)

        if channel in {'sms', 'whatsapp'}:
            if not all((settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, settings.TWILIO_VERIFY_SERVICE_SID)):
                return Response({'detail': 'Messaging recovery is not configured yet. Choose email instead.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            delivery_phone = f'+263{phone[1:]}' if phone.startswith('0') else phone
            body = parse.urlencode({'To': delivery_phone, 'Channel': channel}).encode()
            sms_request = urlrequest.Request(
                f'https://verify.twilio.com/v2/Services/{settings.TWILIO_VERIFY_SERVICE_SID}/Verifications',
                data=body,
                method='POST',
            )
            credentials = f'{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}'.encode()
            import base64
            sms_request.add_header('Authorization', f'Basic {base64.b64encode(credentials).decode()}')
            try:
                urlrequest.urlopen(sms_request, timeout=10).read()
            except urlerror.HTTPError as sms_error:
                try:
                    provider_error = json.loads(sms_error.read().decode()).get('message', '')
                except (json.JSONDecodeError, UnicodeDecodeError):
                    provider_error = ''
                detail = provider_error or 'The text message could not be sent. Choose email or try again.'
                return Response({'detail': detail}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except Exception:
                return Response({'detail': 'The verification service could not connect. Choose email or try again.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            destination = f'phone ending {delivery_phone[-4:]}'
            code_hash = None
        else:
            if not user.email:
                cache.delete(self._reset_key(reset_id))
                return Response({'detail': 'This account has no recovery email. Choose text message or contact support.'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                send_mail(
                    'Your Maphric Express password reset code',
                    f'Your verification code is {code}. It expires in 5 minutes. If you did not request this, ignore this email.',
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=False,
                )
            except Exception:
                return Response({'detail': 'The email could not be sent. Please try again or contact support.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            parts = user.email.split('@')
            destination = f'{parts[0][:2]}***@{parts[1]}' if len(parts) == 2 else 'your registered email'
            code_hash = make_password(code)
        cache.set(self._reset_key(reset_id), {
            'user_id': user.id,
            'code_hash': code_hash,
            'channel': channel,
            'phone': delivery_phone if channel in {'sms', 'whatsapp'} else phone,
            'attempts': 0,
            'verified': False,
        }, timeout=300)
        return Response({'reset_id': reset_id, 'destination': destination, 'expires_in': 300})

    @action(detail=False, methods=['post'], url_path='password-reset/verify')
    @throttle_classes([AnonRateThrottle])
    def password_reset_verify(self, request):
        reset_id = str(request.data.get('reset_id', ''))
        code = str(request.data.get('code', '')).strip()
        data = cache.get(self._reset_key(reset_id))
        if not data:
            return Response({'detail': 'This code has expired. Request a new one.'}, status=status.HTTP_400_BAD_REQUEST)
        if data['attempts'] >= 5:
            cache.delete(self._reset_key(reset_id))
            return Response({'detail': 'Too many attempts. Request a new code.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        code_is_valid = False
        if data.get('channel') in {'sms', 'whatsapp'}:
            check_body = parse.urlencode({'To': data['phone'], 'Code': code}).encode()
            check_request = urlrequest.Request(
                f'https://verify.twilio.com/v2/Services/{settings.TWILIO_VERIFY_SERVICE_SID}/VerificationCheck',
                data=check_body,
                method='POST',
            )
            credentials = f'{settings.TWILIO_ACCOUNT_SID}:{settings.TWILIO_AUTH_TOKEN}'.encode()
            import base64
            check_request.add_header('Authorization', f'Basic {base64.b64encode(credentials).decode()}')
            try:
                check_result = json.loads(urlrequest.urlopen(check_request, timeout=10).read().decode())
                code_is_valid = check_result.get('status') == 'approved'
            except Exception:
                return Response({'detail': 'The text verification service is unavailable. Try again.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        else:
            code_is_valid = check_password(code, data['code_hash'])
        if not code_is_valid:
            data['attempts'] += 1
            cache.set(self._reset_key(reset_id), data, timeout=300)
            return Response({'detail': 'The verification code is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        data['verified'] = True
        cache.set(self._reset_key(reset_id), data, timeout=300)
        return Response({'verified': True})

    @action(detail=False, methods=['post'], url_path='password-reset/confirm')
    @throttle_classes([AnonRateThrottle])
    def password_reset_confirm(self, request):
        reset_id = str(request.data.get('reset_id', ''))
        username = str(request.data.get('username', '')).strip()
        password = str(request.data.get('password', ''))
        password2 = str(request.data.get('password2', ''))
        data = cache.get(self._reset_key(reset_id))
        if not data or not data.get('verified'):
            return Response({'detail': 'Verify your code before setting a new password.'}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(pk=data['user_id']).filter(
            Q(username__iexact=username) | Q(email__iexact=username)
        ).first()
        if not user:
            return Response({'detail': 'The username does not match this recovery request.'}, status=status.HTTP_400_BAD_REQUEST)
        if password != password2:
            return Response({'detail': 'The new passwords do not match.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(password, user=user)
        except ValidationError as validation_error:
            return Response({'detail': ' '.join(validation_error.messages)}, status=status.HTTP_400_BAD_REQUEST)
        def save_new_password():
            with transaction.atomic():
                user.set_password(password)
                user.save(update_fields=['password'])
                Token.objects.filter(user=user).delete()
        try:
            self._clear_login_cache(user)
            save_new_password()
        except OperationalError:
            close_old_connections()
            try:
                user.refresh_from_db()
                save_new_password()
            except OperationalError:
                return Response(
                    {'detail': 'The database is taking too long. Your password was not changed; please try again.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        cache.delete(self._reset_key(reset_id))
        return Response({'detail': 'Password updated successfully. Sign in with your new password.'})
