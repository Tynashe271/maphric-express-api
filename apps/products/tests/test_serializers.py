from django.test import TestCase

from apps.factories import create_category, create_product, create_user
from apps.products.models import Review
from apps.products.serializers import ProductSerializer, WishlistItemSerializer


class ProductSerializerTests(TestCase):
    def setUp(self):
        self.category = create_category(name='Pantry')
        self.product = create_product(category=self.category)

    def test_average_rating_is_none_without_reviews(self):
        data = ProductSerializer(self.product).data

        self.assertIsNone(data['average_rating'])
        self.assertEqual(data['category_name'], 'Pantry')

    def test_average_rating_is_rounded_to_one_decimal(self):
        for index, rating in enumerate((5, 4, 4)):
            Review.objects.create(
                product=self.product,
                user=create_user(username=f'reviewer{index}', phone_number=f'07712345{index}0'),
                rating=rating,
            )

        self.assertEqual(ProductSerializer(self.product).data['average_rating'], 4.3)


class WishlistItemSerializerTests(TestCase):
    def test_inactive_products_cannot_be_wishlisted(self):
        inactive = create_product(name='Old Stock', is_active=False)

        serializer = WishlistItemSerializer(data={'product_id': inactive.pk})

        self.assertFalse(serializer.is_valid())
        self.assertIn('product_id', serializer.errors)

    def test_active_product_is_accepted(self):
        product = create_product(name='Fresh Stock')

        serializer = WishlistItemSerializer(data={'product_id': product.pk})

        self.assertTrue(serializer.is_valid(), serializer.errors)


class ProductModelTests(TestCase):
    def test_string_representations(self):
        category = create_category(name='Drinks')
        product = create_product(category=category, name='Cola')

        self.assertEqual(str(category), 'Drinks')
        self.assertEqual(str(product), 'Cola')
