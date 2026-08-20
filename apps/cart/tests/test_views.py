from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.cart.models import CartItem
from apps.cart.serializers import CartItemSerializer
from apps.factories import create_cart_item, create_product, create_user

CART_URL = '/api/v1/cart/items/'


class CartItemModelTests(TestCase):
    def test_subtotal_multiplies_price_by_quantity(self):
        item = create_cart_item(create_user(), create_product(price='4.50'), quantity=3)

        self.assertEqual(item.subtotal, Decimal('13.50'))


class CartItemSerializerTests(TestCase):
    def test_inactive_products_are_rejected(self):
        inactive = create_product(name='Old Stock', is_active=False)

        serializer = CartItemSerializer(data={'product_id': inactive.pk, 'quantity': 1})

        self.assertFalse(serializer.is_valid())
        self.assertIn('product_id', serializer.errors)

    def test_quantity_must_be_at_least_one(self):
        serializer = CartItemSerializer(data={'product_id': create_product().pk, 'quantity': 0})

        self.assertFalse(serializer.is_valid())
        self.assertIn('quantity', serializer.errors)


class CartViewSetTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user()
        self.product = create_product(price='4.50')

    def test_authentication_is_required(self):
        self.assertEqual(self.client.get(CART_URL).status_code, 401)

    def test_adding_a_product_creates_one_item_with_subtotal(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(CART_URL, {'product_id': self.product.pk, 'quantity': 2}, format='json')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['subtotal'], '9.00')
        self.assertEqual(CartItem.objects.count(), 1)

    def test_adding_the_same_product_again_increases_the_quantity(self):
        self.client.force_authenticate(self.user)

        self.client.post(CART_URL, {'product_id': self.product.pk, 'quantity': 2}, format='json')
        response = self.client.post(CART_URL, {'product_id': self.product.pk, 'quantity': 3}, format='json')

        self.assertEqual(CartItem.objects.count(), 1)
        self.assertEqual(CartItem.objects.get().quantity, 5)
        self.assertEqual(response.data['quantity'], 5)

    def test_customers_only_see_their_own_cart(self):
        other = create_user(username='other', phone_number='0770000002')
        create_cart_item(other, self.product)
        self.client.force_authenticate(self.user)

        response = self.client.get(CART_URL)

        self.assertEqual(response.data['count'], 0)

    def test_quantity_can_be_updated_and_item_removed(self):
        item = create_cart_item(self.user, self.product, quantity=1)
        self.client.force_authenticate(self.user)

        patched = self.client.patch(f'{CART_URL}{item.pk}/', {'quantity': 4}, format='json')
        self.assertEqual(patched.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)

        deleted = self.client.delete(f'{CART_URL}{item.pk}/')
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(CartItem.objects.exists())

    def test_other_customers_items_are_not_reachable(self):
        other = create_user(username='other', phone_number='0770000002')
        item = create_cart_item(other, self.product)
        self.client.force_authenticate(self.user)

        response = self.client.delete(f'{CART_URL}{item.pk}/')

        self.assertEqual(response.status_code, 404)
