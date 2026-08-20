import re
from unittest import mock
from urllib import error as urlerror

from django.core import mail
from django.core.cache import cache
from django.db import OperationalError, transaction
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.views import UserViewSet
from apps.factories import create_admin, create_user

BASE = '/api/v1/accounts/users/'
REGISTER_URL = f'{BASE}register/'
LOGIN_URL = f'{BASE}login/'
RESET_REQUEST_URL = f'{BASE}password-reset/request/'
RESET_VERIFY_URL = f'{BASE}password-reset/verify/'
RESET_CONFIRM_URL = f'{BASE}password-reset/confirm/'

REGISTER_PAYLOAD = {
    'username': 'newshopper',
    'email': 'newshopper@example.com',
    'phone_number': '0771234568',
    'password': 'str0ng-passw0rd!',
    'password2': 'str0ng-passw0rd!',
}


class AccountsApiTestCase(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.client = APIClient()


class RegistrationTests(AccountsApiTestCase):
    def test_register_returns_user_and_token(self):
        response = self.client.post(REGISTER_URL, REGISTER_PAYLOAD, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['user']['username'], 'newshopper')
        self.assertTrue(Token.objects.filter(key=response.data['token']).exists())

    def test_register_rejects_invalid_payload(self):
        response = self.client.post(REGISTER_URL, {**REGISTER_PAYLOAD, 'password2': 'mismatch'}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_plain_create_endpoint_is_not_allowed(self):
        admin = create_admin()
        self.client.force_authenticate(admin)

        response = self.client.post(BASE, REGISTER_PAYLOAD, format='json')

        self.assertEqual(response.status_code, 405)


class LoginTests(AccountsApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user(username='shopper', password='str0ng-passw0rd!')

    def test_login_with_username(self):
        response = self.client.post(LOGIN_URL, {'username': 'shopper', 'password': 'str0ng-passw0rd!'}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['token'], Token.objects.get(user=self.user).key)

    def test_login_with_email_is_case_insensitive(self):
        response = self.client.post(LOGIN_URL, {'username': 'SHOPPER@example.com', 'password': 'str0ng-passw0rd!'}, format='json')

        self.assertEqual(response.status_code, 200)

    def test_login_reuses_existing_token(self):
        token = Token.objects.create(user=self.user)

        response = self.client.post(LOGIN_URL, {'username': 'shopper', 'password': 'str0ng-passw0rd!'}, format='json')

        self.assertEqual(response.data['token'], token.key)

    def test_login_with_wrong_password_is_unauthorised(self):
        response = self.client.post(LOGIN_URL, {'username': 'shopper', 'password': 'wrong'}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_login_of_inactive_user_is_unauthorised(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        response = self.client.post(LOGIN_URL, {'username': 'shopper', 'password': 'str0ng-passw0rd!'}, format='json')

        self.assertEqual(response.status_code, 401)

    def test_login_requires_credentials(self):
        response = self.client.post(LOGIN_URL, {}, format='json')

        self.assertEqual(response.status_code, 400)


class QuerysetScopeTests(AccountsApiTestCase):
    def test_customers_only_see_their_own_profile(self):
        user = create_user(username='shopper')
        create_user(username='other', phone_number='0770000002')
        self.client.force_authenticate(user)

        response = self.client.get(BASE)

        self.assertEqual([entry['username'] for entry in response.data['results']], ['shopper'])

    def test_staff_see_every_profile(self):
        create_user(username='shopper')
        admin = create_admin()
        self.client.force_authenticate(admin)

        response = self.client.get(BASE)

        self.assertEqual(response.data['count'], 2)

    def test_anonymous_access_is_denied(self):
        response = self.client.get(BASE)

        self.assertEqual(response.status_code, 401)


class PasswordResetRequestTests(AccountsApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user(username='shopper', password='str0ng-passw0rd!', phone_number='0771234567')

    def test_email_channel_sends_code_and_masks_destination(self):
        response = self.client.post(
            RESET_REQUEST_URL,
            {'username': 'shopper', 'phone_number': '0771234567'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['destination'], 'sh***@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIsNotNone(cache.get(UserViewSet._reset_key(response.data['reset_id'])))

    def test_missing_fields_are_rejected(self):
        response = self.client.post(RESET_REQUEST_URL, {'username': 'shopper'}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_unknown_username_and_phone_combination_is_rejected(self):
        response = self.client.post(
            RESET_REQUEST_URL,
            {'username': 'shopper', 'phone_number': '0779999999'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_account_without_email_cannot_use_email_channel(self):
        self.user.email = ''
        self.user.save(update_fields=['email'])

        response = self.client.post(
            RESET_REQUEST_URL,
            {'username': 'shopper', 'phone_number': '0771234567'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_email_failure_returns_service_unavailable(self):
        with mock.patch('apps.accounts.views.send_mail', side_effect=OSError('smtp down')):
            response = self.client.post(
                RESET_REQUEST_URL,
                {'username': 'shopper', 'phone_number': '0771234567'},
                format='json',
            )

        self.assertEqual(response.status_code, 503)

    @override_settings(TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='', TWILIO_VERIFY_SERVICE_SID='')
    def test_sms_channel_requires_twilio_configuration(self):
        response = self.client.post(
            RESET_REQUEST_URL,
            {'username': 'shopper', 'phone_number': '0771234567', 'channel': 'sms'},
            format='json',
        )

        self.assertEqual(response.status_code, 503)

    @override_settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='token', TWILIO_VERIFY_SERVICE_SID='service')
    def test_sms_channel_sends_verification_and_masks_phone(self):
        with mock.patch('apps.accounts.views.urlrequest.urlopen') as urlopen:
            urlopen.return_value.read.return_value = b'{}'
            response = self.client.post(
                RESET_REQUEST_URL,
                {'username': 'shopper', 'phone_number': '0771234567', 'channel': 'sms'},
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['destination'], 'phone ending 4567')
        stored = cache.get(UserViewSet._reset_key(response.data['reset_id']))
        self.assertEqual(stored['phone'], '+263771234567')
        self.assertIsNone(stored['code_hash'])

    @override_settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='token', TWILIO_VERIFY_SERVICE_SID='service')
    def test_sms_provider_error_message_is_surfaced(self):
        http_error = urlerror.HTTPError('url', 400, 'Bad Request', {}, None)
        http_error.read = lambda: b'{"message": "Invalid phone number"}'
        with mock.patch('apps.accounts.views.urlrequest.urlopen', side_effect=http_error):
            response = self.client.post(
                RESET_REQUEST_URL,
                {'username': 'shopper', 'phone_number': '0771234567', 'channel': 'sms'},
                format='json',
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['detail'], 'Invalid phone number')

    @override_settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='token', TWILIO_VERIFY_SERVICE_SID='service')
    def test_unreadable_sms_provider_error_uses_a_generic_message(self):
        http_error = urlerror.HTTPError('url', 400, 'Bad Request', {}, None)
        http_error.read = lambda: b'<html>gateway error</html>'
        with mock.patch('apps.accounts.views.urlrequest.urlopen', side_effect=http_error):
            response = self.client.post(
                RESET_REQUEST_URL,
                {'username': 'shopper', 'phone_number': '0771234567', 'channel': 'sms'},
                format='json',
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['detail'], 'The text message could not be sent. Choose email or try again.')

    @override_settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='token', TWILIO_VERIFY_SERVICE_SID='service')
    def test_sms_connection_failure_returns_service_unavailable(self):
        with mock.patch('apps.accounts.views.urlrequest.urlopen', side_effect=OSError('no route')):
            response = self.client.post(
                RESET_REQUEST_URL,
                {'username': 'shopper', 'phone_number': '0771234567', 'channel': 'sms'},
                format='json',
            )

        self.assertEqual(response.status_code, 503)


class PasswordResetVerifyAndConfirmTests(AccountsApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user(username='shopper', password='str0ng-passw0rd!', phone_number='0771234567')
        self.token = Token.objects.create(user=self.user)
        request = self.client.post(
            RESET_REQUEST_URL,
            {'username': 'shopper', 'phone_number': '0771234567'},
            format='json',
        )
        self.reset_id = request.data['reset_id']
        self.code = re.search(r'code is (\d{6})', mail.outbox[0].body).group(1)

    def _verify(self, code=None):
        return self.client.post(
            RESET_VERIFY_URL,
            {'reset_id': self.reset_id, 'code': code or self.code},
            format='json',
        )

    def test_correct_code_marks_request_verified(self):
        response = self._verify()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(cache.get(UserViewSet._reset_key(self.reset_id))['verified'])

    def test_unknown_reset_id_is_rejected(self):
        response = self.client.post(RESET_VERIFY_URL, {'reset_id': 'nope', 'code': '000000'}, format='json')

        self.assertEqual(response.status_code, 400)

    def test_wrong_code_increments_attempts(self):
        response = self._verify(code='000000')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(cache.get(UserViewSet._reset_key(self.reset_id))['attempts'], 1)

    def test_sixth_attempt_is_throttled_and_request_discarded(self):
        for _ in range(5):
            self._verify(code='000000')

        response = self._verify()

        self.assertEqual(response.status_code, 429)
        self.assertIsNone(cache.get(UserViewSet._reset_key(self.reset_id)))

    @override_settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='token', TWILIO_VERIFY_SERVICE_SID='service')
    def test_sms_code_is_checked_against_twilio(self):
        data = cache.get(UserViewSet._reset_key(self.reset_id))
        data.update(channel='sms', phone='+263771234567', code_hash=None)
        cache.set(UserViewSet._reset_key(self.reset_id), data, timeout=300)

        with mock.patch('apps.accounts.views.urlrequest.urlopen') as urlopen:
            urlopen.return_value.read.return_value = b'{"status": "approved"}'
            response = self._verify(code='123456')

        self.assertEqual(response.status_code, 200)

    @override_settings(TWILIO_ACCOUNT_SID='sid', TWILIO_AUTH_TOKEN='token', TWILIO_VERIFY_SERVICE_SID='service')
    def test_twilio_outage_returns_service_unavailable(self):
        data = cache.get(UserViewSet._reset_key(self.reset_id))
        data.update(channel='sms', phone='+263771234567', code_hash=None)
        cache.set(UserViewSet._reset_key(self.reset_id), data, timeout=300)

        with mock.patch('apps.accounts.views.urlrequest.urlopen', side_effect=OSError('no route')):
            response = self._verify(code='123456')

        self.assertEqual(response.status_code, 503)

    def test_confirm_requires_verified_request(self):
        response = self.client.post(
            RESET_CONFIRM_URL,
            {'reset_id': self.reset_id, 'username': 'shopper', 'password': 'n3w-passw0rd!', 'password2': 'n3w-passw0rd!'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_confirm_sets_new_password_and_revokes_tokens(self):
        self._verify()

        response = self.client.post(
            RESET_CONFIRM_URL,
            {'reset_id': self.reset_id, 'username': 'shopper', 'password': 'n3w-passw0rd!', 'password2': 'n3w-passw0rd!'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('n3w-passw0rd!'))
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        self.assertIsNone(cache.get(UserViewSet._reset_key(self.reset_id)))

    def test_confirm_rejects_username_from_another_account(self):
        self._verify()

        response = self.client.post(
            RESET_CONFIRM_URL,
            {'reset_id': self.reset_id, 'username': 'someone-else', 'password': 'n3w-passw0rd!', 'password2': 'n3w-passw0rd!'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_confirm_rejects_mismatched_passwords(self):
        self._verify()

        response = self.client.post(
            RESET_CONFIRM_URL,
            {'reset_id': self.reset_id, 'username': 'shopper', 'password': 'n3w-passw0rd!', 'password2': 'other-passw0rd!'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)

    def test_confirm_retries_after_a_database_timeout(self):
        self._verify()

        real_atomic = transaction.atomic
        calls = []

        def fail_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError('timeout')
            return real_atomic(*args, **kwargs)

        with mock.patch('apps.accounts.views.transaction.atomic', side_effect=fail_once):
            response = self.client.post(
                RESET_CONFIRM_URL,
                {'reset_id': self.reset_id, 'username': 'shopper', 'password': 'n3w-passw0rd!', 'password2': 'n3w-passw0rd!'},
                format='json',
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('n3w-passw0rd!'))

    def test_confirm_reports_a_persistent_database_timeout(self):
        self._verify()

        with mock.patch('apps.accounts.views.transaction.atomic', side_effect=OperationalError('timeout')):
            response = self.client.post(
                RESET_CONFIRM_URL,
                {'reset_id': self.reset_id, 'username': 'shopper', 'password': 'n3w-passw0rd!', 'password2': 'n3w-passw0rd!'},
                format='json',
            )

        self.assertEqual(response.status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('str0ng-passw0rd!'))

    def test_confirm_rejects_weak_password(self):
        self._verify()

        response = self.client.post(
            RESET_CONFIRM_URL,
            {'reset_id': self.reset_id, 'username': 'shopper', 'password': '123', 'password2': '123'},
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('str0ng-passw0rd!'))
