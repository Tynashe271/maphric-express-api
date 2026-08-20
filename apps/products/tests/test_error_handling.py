from rest_framework import status
from rest_framework.test import APITestCase


class ProductFilterErrorTests(APITestCase):
    def test_unusable_price_filter_is_a_client_error(self):
        """A Django ValidationError from query coercion must not become a 500."""
        response = self.client.get('/api/v1/products/products/', {'min_price': 'cheap'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
