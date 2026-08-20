from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from ..models import DeliverySettings, TransactionArchive
from ..views import order_number

User = get_user_model()


class DeliverySettingsErrorTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='boss', password='pass-1234!', is_staff=True)
        self.client.force_authenticate(self.admin)

    def test_invalid_value_reports_validation_error(self):
        response = self.client.put(
            '/api/v1/orders/delivery-settings/',
            {'delivery_fee': 'not-a-number'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('detail', response.data)

    def test_valid_value_is_saved(self):
        response = self.client.put(
            '/api/v1/orders/delivery-settings/',
            {'delivery_fee': '3.50'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(DeliverySettings.objects.get(pk=1).delivery_fee), '3.50')


class TransactionArchiveFilterTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='chief', password='pass-1234!', is_staff=True)
        self.client.force_authenticate(self.admin)
        TransactionArchive.objects.create(transaction_count=1, total_amount=10)

    def test_unparsable_date_is_a_client_error(self):
        response = self.client.get('/api/v1/orders/transaction-archives/', {'from': 'yesterday'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_date_filters(self):
        response = self.client.get('/api/v1/orders/transaction-archives/', {'from': '2000-01-01'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)


class OrderNumberTests(APITestCase):
    def test_missing_id_does_not_raise(self):
        self.assertEqual(order_number({'id': 12}), 'MAP-000012')
        self.assertEqual(order_number({}), 'MAP-UNKNOWN')
        self.assertEqual(order_number({'id': None}), 'MAP-UNKNOWN')
