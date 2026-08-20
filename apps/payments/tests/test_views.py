import os
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from apps.factories import create_admin, create_order, create_user
from apps.orders.models import Order
from apps.payments.views import GatewayNotConfigured, gateway

INITIATE_URL = '/api/v1/payments/initiate/'
CALLBACK_URL = '/api/v1/payments/callback/'
CONFIG_URL = '/api/v1/payments/config/'


def status_url(order_id):
    return f'/api/v1/payments/status/{order_id}/'


class FakePayment:
    def __init__(self):
        self.lines = []

    def add(self, description, amount):
        self.lines.append((description, amount))


class FakeGateway:
    def __init__(self, success=True, poll_url='https://paynow.example/poll/1', transaction=None):
        self.response = mock.Mock(success=success, poll_url=poll_url, data={})
        self.transaction = transaction
        self.payment = FakePayment()
        self.sent = None

    def create_payment(self, reference, auth_email):
        self.reference = reference
        self.auth_email = auth_email
        return self.payment

    def send_mobile(self, payment, phone, method):
        self.sent = (phone, method)
        return self.response

    def check_transaction_status(self, poll_url):
        self.polled = poll_url
        return self.transaction


class GatewayFactoryTests(TestCase):
    def test_missing_credentials_raise_a_runtime_error(self):
        with mock.patch.dict(os.environ, {'PAYNOW_INTEGRATION_ID': '', 'PAYNOW_INTEGRATION_KEY': ''}, clear=False):
            with self.assertRaises(RuntimeError):
                gateway()

    def test_credentials_are_passed_to_paynow(self):
        env = {
            'PAYNOW_INTEGRATION_ID': '1234',
            'PAYNOW_INTEGRATION_KEY': 'secret',
            'PAYNOW_RETURN_URL': 'https://shop.example/return',
            'PAYNOW_RESULT_URL': 'https://shop.example/result',
        }
        with mock.patch.dict(os.environ, env, clear=False):
            with mock.patch('apps.payments.views.Paynow') as paynow:
                gateway()

        paynow.assert_called_once_with('1234', 'secret', 'https://shop.example/return', 'https://shop.example/result')


class InitiatePaymentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.order = create_order(self.user, total='9.00')
        self.client.force_authenticate(self.user)

    def _post(self, payload=None, fake=None):
        with mock.patch('apps.payments.views.gateway', return_value=fake or FakeGateway()):
            return self.client.post(INITIATE_URL, payload or {
                'order_id': self.order.pk,
                'method': 'ecocash',
                'phone': '0771234567',
            }, format='json')

    def test_authentication_is_required(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.post(INITIATE_URL, {}, format='json').status_code, 401)

    def test_unknown_order_returns_not_found(self):
        response = self._post({'order_id': 9999, 'method': 'ecocash', 'phone': '0771234567'})

        self.assertEqual(response.status_code, 404)

    def test_other_customers_orders_are_not_payable(self):
        other_order = create_order(create_user(username='other', phone_number='0770000002'))

        response = self._post({'order_id': other_order.pk, 'method': 'ecocash', 'phone': '0771234567'})

        self.assertEqual(response.status_code, 404)

    def test_already_paid_order_short_circuits(self):
        self.order.payment_status = 'paid'
        self.order.save(update_fields=['payment_status'])

        response = self._post()

        self.assertEqual(response.data, {'paid': True, 'status': 'paid'})

    def test_unsupported_method_is_rejected(self):
        response = self._post({'order_id': self.order.pk, 'method': 'card', 'phone': '0771234567'})

        self.assertEqual(response.status_code, 400)

    def test_phone_number_is_required(self):
        response = self._post({'order_id': self.order.pk, 'method': 'ecocash', 'phone': ''})

        self.assertEqual(response.status_code, 400)

    def test_account_without_email_cannot_pay(self):
        self.user.email = ''
        self.user.save(update_fields=['email'])
        with mock.patch.dict(os.environ, {'PAYNOW_AUTH_EMAIL': ''}, clear=False):
            response = self._post()

        self.assertEqual(response.status_code, 400)

    def test_successful_request_stores_the_poll_url(self):
        fake = FakeGateway()

        with mock.patch.dict(os.environ, {'PAYNOW_TEST_MODE': 'False'}, clear=False):
            response = self._post(fake=fake)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'pending')
        self.assertFalse(response.data['test_mode'])
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_method, 'EcoCash')
        self.assertEqual(self.order.payment_status, 'pending')
        self.assertEqual(self.order.paynow_poll_url, 'https://paynow.example/poll/1')
        self.assertEqual(fake.reference, f'MAP-{self.order.id:06d}')
        self.assertEqual(fake.payment.lines, [(f'Maphric Express order MAP-{self.order.id:06d}', 9.0)])
        self.assertEqual(fake.sent, ('0771234567', 'ecocash'))

    def test_test_mode_uses_the_paynow_sandbox_number(self):
        fake = FakeGateway()

        with mock.patch.dict(os.environ, {'PAYNOW_TEST_MODE': 'true'}, clear=False):
            response = self._post(fake=fake)

        self.assertTrue(response.data['test_mode'])
        self.assertEqual(fake.sent, ('0771111111', 'ecocash'))

    def test_rejected_request_returns_bad_request(self):
        response = self._post(fake=FakeGateway(success=False))

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'unpaid')

    def test_missing_poll_url_returns_bad_gateway(self):
        response = self._post(fake=FakeGateway(poll_url=''))

        self.assertEqual(response.status_code, 502)

    def test_missing_credentials_return_service_unavailable(self):
        with mock.patch('apps.payments.views.gateway', side_effect=GatewayNotConfigured('nope')):
            response = self.client.post(INITIATE_URL, {
                'order_id': self.order.pk,
                'method': 'ecocash',
                'phone': '0771234567',
            }, format='json')

        self.assertEqual(response.status_code, 503)

    def test_unexpected_error_is_not_reported_as_a_gateway_failure(self):
        self.client.raise_request_exception = False

        with mock.patch('apps.payments.views.scope_to_user', side_effect=RuntimeError('db down')):
            response = self.client.post(INITIATE_URL, {
                'order_id': self.order.pk,
                'method': 'ecocash',
                'phone': '0771234567',
            }, format='json')

        self.assertEqual(response.status_code, 500)

    def test_staff_can_pay_for_any_order(self):
        other_order = create_order(create_user(username='other', phone_number='0770000002'))
        self.client.force_authenticate(create_admin())

        response = self._post({'order_id': other_order.pk, 'method': 'ecocash', 'phone': '0771234567'})

        self.assertEqual(response.status_code, 200)


class PaymentStatusTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.order = create_order(self.user, total='9.00')
        self.client.force_authenticate(self.user)

    def test_unknown_order_returns_not_found(self):
        self.assertEqual(self.client.get(status_url(9999)).status_code, 404)

    def test_paid_order_is_reported_without_polling(self):
        self.order.payment_status = 'paid'
        self.order.save(update_fields=['payment_status'])

        response = self.client.get(status_url(self.order.pk))

        self.assertEqual(response.data, {'paid': True, 'status': 'paid'})

    def test_order_without_poll_url_returns_current_state(self):
        response = self.client.get(status_url(self.order.pk))

        self.assertEqual(response.data, {'paid': False, 'status': 'unpaid'})

    def test_successful_poll_marks_the_order_paid(self):
        self.order.paynow_poll_url = 'https://paynow.example/poll/1'
        self.order.save(update_fields=['paynow_poll_url'])
        transaction = mock.Mock(paid=True, status='paid', paynow_reference='PN-1')

        with mock.patch('apps.payments.views.gateway', return_value=FakeGateway(transaction=transaction)):
            response = self.client.get(status_url(self.order.pk))

        self.assertEqual(response.data, {'paid': True, 'status': 'paid'})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'paid')
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertEqual(self.order.paynow_reference, 'PN-1')

    def test_pending_poll_leaves_the_order_unpaid(self):
        self.order.paynow_poll_url = 'https://paynow.example/poll/1'
        self.order.save(update_fields=['paynow_poll_url'])
        transaction = mock.Mock(paid=False, status='awaiting delivery')

        with mock.patch('apps.payments.views.gateway', return_value=FakeGateway(transaction=transaction)):
            response = self.client.get(status_url(self.order.pk))

        self.assertEqual(response.data, {'paid': False, 'status': 'awaiting delivery'})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'unpaid')

    def test_poll_failure_returns_bad_gateway(self):
        self.order.paynow_poll_url = 'https://paynow.example/poll/1'
        self.order.save(update_fields=['paynow_poll_url'])

        with mock.patch('apps.payments.views.gateway', side_effect=RuntimeError('boom')):
            response = self.client.get(status_url(self.order.pk))

        self.assertEqual(response.status_code, 502)


class CallbackAndConfigTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_callback_acknowledges_without_authentication(self):
        response = self.client.post(CALLBACK_URL, {'anything': True}, format='json')

        self.assertEqual(response.data, {'received': True})

    def test_config_is_staff_only(self):
        self.client.force_authenticate(create_user())

        self.assertEqual(self.client.get(CONFIG_URL).status_code, 403)

    def test_config_reports_configured_keys_and_masks_the_email(self):
        self.client.force_authenticate(create_admin())
        env = {
            'PAYNOW_INTEGRATION_ID': '1234',
            'PAYNOW_INTEGRATION_KEY': '',
            'PAYNOW_AUTH_EMAIL': 'admin@example.com',
        }

        with mock.patch.dict(os.environ, env, clear=False):
            response = self.client.get(CONFIG_URL)

        self.assertTrue(response.data['PAYNOW_INTEGRATION_ID'])
        self.assertFalse(response.data['PAYNOW_INTEGRATION_KEY'])
        self.assertEqual(response.data['PAYNOW_AUTH_EMAIL_MASKED'], 'a***n@example.com')

    def test_masked_email_is_blank_when_not_configured(self):
        self.client.force_authenticate(create_admin())

        with mock.patch.dict(os.environ, {'PAYNOW_AUTH_EMAIL': ''}, clear=False):
            response = self.client.get(CONFIG_URL)

        self.assertEqual(response.data['PAYNOW_AUTH_EMAIL_MASKED'], '')
